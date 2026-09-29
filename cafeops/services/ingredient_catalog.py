"""Ingredients: create, edit, retire, and record a price with its menu impact. Spec A4.

Four rules carry this module:

- **A price is a dated row with a source** (spec 4.1, invariant 8). Recording a price
  closes the open `ingredient_price` and opens a new one from now, signed by the
  operator. The "estimate" flag is not a checkbox: it clears only when a price with a
  real source (INVOICE or SUPPLIER_FEED) is recorded (spec C-1). Recording ESTIMATE
  keeps it an estimate, as it should.
- **The unit is converted exactly or refused.** A pack of 2 L for an ingredient costed
  per ml is converted; a pack in kg for one in litres is refused (ARCHITECTURE 3) --
  `IngredientRepository.add_price` divides pack cost by pack size without looking at
  the pack unit, which is why prices are written here and not through it.
- **Nothing history references is deleted** (spec C-5). "Delete" is retire, and it is
  refused while an open recipe line still uses the ingredient. The unit cannot change
  once anything records a quantity in it.
- **The preview writes nothing**: the after side is the same resolution with this
  ingredient's cost replaced in the price snapshot.

Supplier links (`supplier_product`) are read here and written only once: the first
link of a brand-new ingredient created with a supplier price (see `create_ingredient`).
After that the Suppliers area owns them. So a price recorded here does not update the
preferred link's pack price (spec C-8 asked for both); the link keeps what the Suppliers
screen last set.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    Ingredient,
    IngredientPrice,
    ManualRecipeLine,
    MediaAsset,
    MenuItem,
    Modifier,
    ModifierVersion,
    StockBatch,
    StockCount,
    StockMovement,
    Supplier,
    SupplierProduct,
    TemplateComponent,
    VariantAxis,
    VariantOption,
)
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.menu_cost import SqlMenuCostRepository
from cafeops.domain.composition import (
    ChangeImpact,
    ItemChange,
    gbp,
    qty_text,
    resolve_recipe,
    summarise_changes,
    unit_gbp,
    unit_label,
)
from cafeops.domain.enums import Storage
from cafeops.domain.labour import UNTIMED
from cafeops.domain.types import PriceSource, Tier, Unit
from cafeops.domain.units import IncompatibleUnitsError, convert
from cafeops.jobs.cost_rollup import (
    configured_rate_pence,
    rollup_for_ingredient,
    snapshots_at,
)
from cafeops.services.actor import require_actor
from cafeops.services.edit_composition import require_not_retroactive
from cafeops.services.reference_seed import UK14

__all__ = [
    "ALLERGENS_CHECKED_PREFIX",
    "IngredientInUseError",
    "PriceIn",
    "PricePreview",
    "apply_ingredient_price",
    "attach_ingredient_photo",
    "cost_per_unit",
    "create_ingredient",
    "ingredient_references",
    "item_ids_using",
    "open_recipe_uses",
    "preview_ingredient_price",
    "retire_ingredient",
    "set_allergens",
    "update_ingredient",
]

WINDOW_DAYS = 30


class IngredientInUseError(ValueError):
    """Refused because something still depends on the ingredient. 409, with counts."""


@dataclass(frozen=True, slots=True)
class PriceIn:
    pack_size: Decimal
    pack_unit: Unit
    pack_cost_pence: int
    source: PriceSource
    supplier_id: int | None = None
    note: str | None = None


def cost_per_unit(ingredient: Ingredient, price: PriceIn) -> Decimal:
    """Pence per ONE of the ingredient's own unit. Exact, or refused across dimensions."""
    if price.pack_size <= 0:
        raise ValueError("a pack size must be more than 0")
    if price.pack_cost_pence < 0:
        raise ValueError("a pack cost cannot be negative")
    try:
        size_in_unit = convert(price.pack_size, price.pack_unit, ingredient.unit)
    except IncompatibleUnitsError as exc:
        raise ValueError(
            f"{ingredient.name} is costed per {unit_label(ingredient.unit)}, and a pack of "
            f"{qty_text(price.pack_size)} {unit_label(price.pack_unit)} cannot be turned into "
            f"{unit_label(ingredient.unit)} without a density, which this system does not "
            "model. Give the pack size in a unit of the same kind."
        ) from exc
    return Decimal(price.pack_cost_pence) / size_in_unit


# --------------------------------------------------------------------------
# Where an ingredient is used
# --------------------------------------------------------------------------


def item_ids_using(session: Session, ingredient_id: int) -> list[int]:
    """Menu items whose CURRENT recipe uses the ingredient (templated or one-off)."""
    at = datetime.now(UTC)
    manual = set(
        session.scalars(
            select(ManualRecipeLine.menu_item_id).where(
                ManualRecipeLine.ingredient_id == ingredient_id,
                ManualRecipeLine.effective_from <= at,
                or_(ManualRecipeLine.effective_to.is_(None), ManualRecipeLine.effective_to > at),
            )
        )
    )
    manual_items = set(
        session.scalars(
            select(MenuItem.id).where(MenuItem.id.in_(manual), MenuItem.manual_recipe.is_(True))
        )
    )
    templates_via_component = set(
        session.scalars(
            select(TemplateComponent.template_id).where(
                TemplateComponent.ingredient_id == ingredient_id,
                TemplateComponent.effective_from <= at,
                or_(TemplateComponent.effective_to.is_(None), TemplateComponent.effective_to > at),
            )
        )
    )
    items = set(
        session.scalars(
            select(MenuItem.id).where(MenuItem.template_id.in_(templates_via_component))
        )
    )
    option_rows = session.execute(
        select(VariantOption.id, VariantOption.axis_id, VariantOption.name, VariantAxis.template_id)
        .join(VariantAxis, VariantAxis.id == VariantOption.axis_id)
        .where(
            VariantOption.ingredient_id == ingredient_id,
            VariantOption.effective_from <= at,
            or_(VariantOption.effective_to.is_(None), VariantOption.effective_to > at),
        )
    ).all()
    if option_rows:
        lineage = {(axis_id, name) for _id, axis_id, name, _t in option_rows}
        ids_in_lineage = {
            oid
            for oid, axis_id, name in session.execute(
                select(VariantOption.id, VariantOption.axis_id, VariantOption.name).where(
                    VariantOption.axis_id.in_([a for a, _ in lineage])
                )
            )
            if (axis_id, name) in lineage
        }
        for item_id, selected in session.execute(
            select(MenuItem.id, MenuItem.selected_options).where(
                MenuItem.template_id.in_({t for _i, _a, _n, t in option_rows})
            )
        ):
            if any(int(v) in ids_in_lineage for v in (selected or {}).values()):
                items.add(item_id)
    return sorted(items | manual_items)


def _count(session: Session, stmt: Select[tuple[int]]) -> int:
    return int(session.scalar(stmt) or 0)


def open_recipe_uses(session: Session, ingredient_id: int) -> dict[str, int]:
    """What would break if the ingredient disappeared from today's recipes."""
    at = datetime.now(UTC)
    return {
        "one-off recipe lines": _count(
            session,
            select(func.count(ManualRecipeLine.id))
            .join(MenuItem, MenuItem.id == ManualRecipeLine.menu_item_id)
            .where(
                ManualRecipeLine.ingredient_id == ingredient_id,
                MenuItem.active.is_(True),
                MenuItem.manual_recipe.is_(True),
                or_(ManualRecipeLine.effective_to.is_(None), ManualRecipeLine.effective_to > at),
            ),
        ),
        "recipe components": _count(
            session,
            select(func.count(TemplateComponent.id)).where(
                TemplateComponent.ingredient_id == ingredient_id,
                or_(TemplateComponent.effective_to.is_(None), TemplateComponent.effective_to > at),
            ),
        ),
        "flavours": _count(
            session,
            select(func.count(VariantOption.id)).where(
                VariantOption.ingredient_id == ingredient_id,
                or_(VariantOption.effective_to.is_(None), VariantOption.effective_to > at),
            ),
        ),
        "swaps": _count(
            session,
            select(func.count(ModifierVersion.id))
            .join(Modifier, Modifier.id == ModifierVersion.modifier_id)
            .where(
                ModifierVersion.ingredient_id == ingredient_id,
                ModifierVersion.effective_to.is_(None),
                ModifierVersion.is_active.is_(True),
            ),
        ),
    }


def ingredient_references(session: Session, ingredient_id: int) -> dict[str, int]:
    """Every row that records a quantity or a price in this ingredient's unit."""
    i = ingredient_id
    return {
        "stock movements": _count(
            session, select(func.count(StockMovement.id)).where(StockMovement.ingredient_id == i)
        ),
        "counts": _count(
            session, select(func.count(StockCount.id)).where(StockCount.ingredient_id == i)
        ),
        "batches": _count(
            session, select(func.count(StockBatch.id)).where(StockBatch.ingredient_id == i)
        ),
        "prices": _count(
            session,
            select(func.count(IngredientPrice.id)).where(IngredientPrice.ingredient_id == i),
        ),
        "one-off recipe lines": _count(
            session,
            select(func.count(ManualRecipeLine.id)).where(ManualRecipeLine.ingredient_id == i),
        ),
        "recipe components": _count(
            session,
            select(func.count(TemplateComponent.id)).where(TemplateComponent.ingredient_id == i),
        ),
        "flavours": _count(
            session, select(func.count(VariantOption.id)).where(VariantOption.ingredient_id == i)
        ),
        "supplier links": _count(
            session,
            select(func.count(SupplierProduct.id)).where(SupplierProduct.ingredient_id == i),
        ),
    }


# --------------------------------------------------------------------------
# Price: preview and apply
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PricePreview:
    ingredient_id: int
    unit: Unit
    cost_per_unit_before: Decimal | None
    source_before: PriceSource | None
    cost_per_unit_after: Decimal
    source_after: PriceSource
    impact: ChangeImpact
    diff: tuple[str, ...]
    at: datetime


def _validated_price(session: Session, ingredient: Ingredient, price: PriceIn) -> Decimal:
    if price.supplier_id is not None and session.get(Supplier, price.supplier_id) is None:
        raise LookupError(f"supplier {price.supplier_id} not found")
    return cost_per_unit(ingredient, price)


def _price_diff(
    ingredient: Ingredient, before: Decimal | None, after: Decimal, price: PriceIn
) -> list[str]:
    unit = unit_label(ingredient.unit)
    was = "no price" if before is None else f"{unit_gbp(before)}/{unit}"
    line = (
        f"{ingredient.name}: {was} → {unit_gbp(after)}/{unit} "
        f"({qty_text(price.pack_size)} {unit_label(price.pack_unit)} "
        f"for {gbp(price.pack_cost_pence)})"
    )
    out = [line]
    if price.source is PriceSource.ESTIMATE:
        out.append("Still an estimate")
    elif ingredient.current_cost_source is PriceSource.ESTIMATE:
        out.append(
            "From an invoice -- no longer an estimate"
            if price.source is PriceSource.INVOICE
            else "From a supplier price list -- no longer an estimate"
        )
    return out


def preview_ingredient_price(
    session: Session, ingredient_id: int, price: PriceIn, *, window_days: int = WINDOW_DAYS
) -> PricePreview:
    """What recording this price would do to every menu item using it. WRITES NOTHING."""
    at = datetime.now(UTC)
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    after = _validated_price(session, ingredient, price)
    snapshots = snapshots_at(session, at)
    current = snapshots.get(ingredient_id)
    after_snapshots = dict(snapshots)
    if current is not None:
        after_snapshots[ingredient_id] = replace(
            current, cost_per_unit_pence=after, cost_source=price.source
        )

    item_ids = item_ids_using(session, ingredient_id)
    composition = SqlCompositionRepository(session)
    specs = composition.item_specs(item_ids, at)
    prep = composition.prep_times(item_ids)
    until = at.astimezone(settings.tz).date()
    since = until - timedelta(days=window_days - 1)
    volumes = SqlMenuCostRepository(session).units_sold_bulk(item_ids, since=since, until=until)
    rate = configured_rate_pence()
    changes: list[ItemChange] = []
    for item_id in item_ids:
        spec = specs.get(item_id)
        row = session.get(MenuItem, item_id)
        if spec is None or row is None:
            continue
        changes.append(
            ItemChange(
                key=f"i{item_id}",
                menu_item_id=item_id,
                name=row.name,
                size_code=row.size_code,
                before=resolve_recipe(spec, (), at, ingredients=snapshots),
                after=resolve_recipe(spec, (), at, ingredients=after_snapshots),
                price_before=row.price_pence,
                price_after=row.price_pence,
                active_before=row.active,
                active_after=row.active,
                prep_before=prep.get(item_id, UNTIMED),
                prep_after=prep.get(item_id, UNTIMED),
                units_sold=volumes.get(item_id, Decimal("0")),
                loaded_hourly_rate_pence=rate,
            )
        )
    warnings: list[str] = []
    if price.source is PriceSource.ESTIMATE:
        warnings.append(
            "Recorded as an estimate, so every cost it feeds stays marked as an estimate."
        )
    return PricePreview(
        ingredient_id=ingredient_id,
        unit=ingredient.unit,
        cost_per_unit_before=None if current is None else current.cost_per_unit_pence,
        source_before=None if current is None else current.cost_source,
        cost_per_unit_after=after,
        source_after=price.source,
        impact=summarise_changes(changes, window_days=window_days, extra_warnings=warnings),
        diff=tuple(
            _price_diff(
                ingredient, None if current is None else current.cost_per_unit_pence, after, price
            )
        ),
        at=at,
    )


@dataclass(frozen=True, slots=True)
class PriceApplied:
    price_id: int
    effective_from: datetime
    cost_per_unit_pence: Decimal
    source: PriceSource
    rollup_items_recosted: int
    rollup_summary: str


def _record_price(
    session: Session, ingredient: Ingredient, price: PriceIn, *, at: datetime, actor: str
) -> IngredientPrice:
    unit_cost = _validated_price(session, ingredient, price)
    current = session.scalar(
        select(IngredientPrice)
        .where(
            IngredientPrice.ingredient_id == ingredient.id,
            IngredientPrice.effective_to.is_(None),
        )
        .order_by(IngredientPrice.effective_from.desc())
        .limit(1)
    )
    if current is not None:
        if current.effective_from >= at:
            raise ValueError("a price was already recorded at this instant; try again")
        current.effective_to = at
    row = IngredientPrice(
        ingredient_id=ingredient.id,
        supplier_id=price.supplier_id,
        pack_size=price.pack_size,
        pack_unit=price.pack_unit,
        pack_cost_pence=price.pack_cost_pence,
        cost_per_unit_pence=unit_cost,
        effective_from=at,
        source=price.source,
        note=(price.note or "").strip()[:400] or None,
        recorded_by=actor,
    )
    session.add(row)
    session.flush()
    ingredient.current_cost_pence_per_unit = unit_cost
    ingredient.current_cost_source = price.source
    return row


def apply_ingredient_price(
    session: Session,
    ingredient_id: int,
    price: PriceIn,
    *,
    actor: str,
    effective_from: datetime | None = None,
) -> PriceApplied:
    """Record the price from now and recost every item using it. Flushes; the caller commits."""
    at = require_not_retroactive(effective_from or datetime.now(UTC))
    actor = require_actor(actor)
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if ingredient.retired_at is not None:
        raise IngredientInUseError(f"{ingredient.name} is retired; nothing was recorded")
    row = _record_price(session, ingredient, price, at=at, actor=actor)
    rollup = rollup_for_ingredient(
        session, ingredient_id, at=at, trigger=f"price recorded by {actor}"
    )
    session.flush()
    return PriceApplied(
        price_id=row.id,
        effective_from=at,
        cost_per_unit_pence=row.cost_per_unit_pence,
        source=row.source,
        rollup_items_recosted=rollup.costed,
        rollup_summary=rollup.summary(),
    )


# --------------------------------------------------------------------------
# Create, edit, retire
# --------------------------------------------------------------------------


def create_ingredient(
    session: Session,
    *,
    name: str,
    unit: Unit,
    category: str | None,
    storage: Storage,
    shelf_life_days: int | None,
    note: str | None,
    price: PriceIn | None,
    actor: str,
    open_life_days: int | None = None,
    transit_buffer_days: int = 0,
    waste_factor: Decimal = Decimal("0"),
    sku: str | None = None,
) -> int:
    """A new ingredient: tier C, not tracked, shelf life an ESTIMATE until confirmed.

    With a price from a supplier (`price.supplier_id`), the ingredient is also linked
    at that supplier with the same pack and price. The one place this module writes a
    `supplier_product`: a brand-new ingredient has no links, so this is its preferred
    one by definition, and the recipe price keeps the source the operator gave it
    (routing it through `suppliers.link_product` would re-record it as SUPPLIER_FEED,
    clearing an estimate flag nobody cleared -- invariant 8).
    """
    actor = require_actor(actor)
    clean = name.strip()
    if not clean:
        raise ValueError("an ingredient needs a name")
    if session.scalar(select(Ingredient.id).where(func.lower(Ingredient.name) == clean.lower())):
        raise ValueError(f"there is already an ingredient called {clean!r}")
    if storage in (Storage.CHILLED, Storage.FROZEN) and shelf_life_days is None:
        raise ValueError(
            f"a {storage.value.lower()} ingredient needs a shelf life: an empty one means "
            "'never expires', which would let orders buy waste"
        )
    if shelf_life_days is not None and shelf_life_days <= 0:
        raise ValueError("a shelf life must be at least 1 day")
    if open_life_days is not None:
        if open_life_days <= 0:
            raise ValueError("an opened life must be at least 1 day")
        if shelf_life_days is not None and open_life_days > shelf_life_days:
            raise ValueError("life once opened cannot be longer than the unopened shelf life")
    if transit_buffer_days < 0:
        raise ValueError("a transit buffer cannot be negative")
    if shelf_life_days is not None and transit_buffer_days >= shelf_life_days:
        raise ValueError(
            "the transit buffer must be shorter than the shelf life, or nothing could "
            "ever be ordered"
        )
    if not (Decimal("0") <= waste_factor <= Decimal("0.5")):
        raise ValueError("waste factor is a fraction between 0 and 0.5 (0.05 = 5% lost)")
    supplier: Supplier | None = None
    if price is not None and price.supplier_id is not None:
        supplier = session.get(Supplier, price.supplier_id)
        if supplier is None:
            raise LookupError(f"supplier {price.supplier_id} not found")
        if supplier.archived_at is not None:
            raise ValueError(f"{supplier.name} is archived; pick another supplier")
    at = datetime.now(UTC)
    row = Ingredient(
        name=clean,
        unit=unit,
        category=(category or "").strip() or None,
        tier=Tier.C,
        tracking_enabled=False,
        waste_factor=waste_factor,
        storage=storage,
        shelf_life_days=shelf_life_days,
        open_life_days=open_life_days,
        transit_buffer_days=transit_buffer_days,
        shelf_life_source=(
            PriceSource.ESTIMATE
            if shelf_life_days is not None or open_life_days is not None
            else None
        ),
        source_note=(note or "").strip()[:400] or None,
    )
    session.add(row)
    session.flush()
    if price is not None:
        _record_price(session, row, price, at=at, actor=actor)
        if supplier is not None:
            session.add(
                SupplierProduct(
                    supplier_id=supplier.id,
                    ingredient_id=row.id,
                    sku=(sku or "").strip()[:80],
                    pack_size=price.pack_size,
                    pack_unit=price.pack_unit,
                    price_pence=price.pack_cost_pence,
                    is_preferred=True,
                    moq_packs=1,
                    last_seen_price_at=at,
                )
            )
            session.flush()
    session.flush()
    return row.id


def update_ingredient(
    session: Session,
    ingredient_id: int,
    *,
    actor: str,
    name: str | None = None,
    category: str | None = None,
    set_category: bool = False,
    note: str | None = None,
    set_note: bool = False,
    unit: Unit | None = None,
) -> None:
    """Name, category, notes; the unit only while nothing records a quantity in it."""
    require_actor(actor)
    row = session.get(Ingredient, ingredient_id)
    if row is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if unit is not None and unit is not row.unit:
        refs = {k: v for k, v in ingredient_references(session, ingredient_id).items() if v}
        if refs:
            raise IngredientInUseError(
                f"{row.name}'s unit cannot change: "
                + ", ".join(f"{v} {k}" for k, v in refs.items())
                + f" record quantities in {unit_label(row.unit)}, and changing the unit "
                "would silently change what every one of them means."
            )
        row.unit = unit
    if name is not None:
        clean = name.strip()
        if not clean:
            raise ValueError("an ingredient needs a name")
        clash = session.scalar(
            select(Ingredient.id).where(
                func.lower(Ingredient.name) == clean.lower(), Ingredient.id != ingredient_id
            )
        )
        if clash:
            raise ValueError(f"there is already an ingredient called {clean!r}")
        row.name = clean
    if set_category:
        row.category = (category or "").strip() or None
    if set_note:
        row.source_note = (note or "").strip()[:400] or None
    session.flush()


def retire_ingredient(session: Session, ingredient_id: int, *, actor: str) -> datetime:
    """ "Delete" = retire. Refused while a live recipe still uses it; history stays."""
    actor = require_actor(actor)
    row = session.get(Ingredient, ingredient_id)
    if row is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if row.retired_at is not None:
        raise IngredientInUseError(f"{row.name} is already retired")
    uses = {k: v for k, v in open_recipe_uses(session, ingredient_id).items() if v}
    if uses:
        raise IngredientInUseError(
            f"{row.name} is still used by "
            + ", ".join(f"{v} {k}" for k, v in uses.items())
            + ". Take it out of those recipes first; nothing was changed."
        )
    at = datetime.now(UTC)
    row.retired_at = at
    row.retired_by = actor
    session.flush()
    return at


# --------------------------------------------------------------------------
# Reference photo and allergens (the ingredient page)
# --------------------------------------------------------------------------

#: `allergens_source` written when a person records the list from the pack. The
#: reference seed writes the research page's URL instead, so a source that starts
#: with this prefix is the only one the web shows as confirmed.
ALLERGENS_CHECKED_PREFIX = "checked by "


def attach_ingredient_photo(session: Session, ingredient_id: int, asset_id: int | None) -> None:
    """Set (or clear) the ingredient's reference photo. The file itself is never deleted."""
    row = session.get(Ingredient, ingredient_id)
    if row is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if asset_id is not None and session.get(MediaAsset, asset_id) is None:
        raise LookupError(f"media asset {asset_id} not found")
    row.photo_asset_id = asset_id
    session.flush()


def set_allergens(
    session: Session, ingredient_id: int, allergens: list[str] | None, *, actor: str
) -> list[str] | None:
    """Record the UK 14 allergens a person read off the pack.

    `None` puts the ingredient back to UNKNOWN; `[]` records "checked, none". The
    source becomes "checked by <actor> on <date>", which is what marks the list as
    confirmed rather than researched.
    """
    name = require_actor(actor)
    row = session.get(Ingredient, ingredient_id)
    if row is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if allergens is None:
        row.allergens = None
        row.allergens_source = None
    else:
        clean = sorted({a.strip().lower() for a in allergens})
        bad = [a for a in clean if a not in UK14]
        if bad:
            raise ValueError(
                "not one of the UK 14 allergens: " + ", ".join(bad) + "; nothing was changed"
            )
        row.allergens = clean
        row.allergens_source = (
            f"{ALLERGENS_CHECKED_PREFIX}{name} on {datetime.now(UTC).date().isoformat()}"
        )
    session.flush()
    return row.allergens
