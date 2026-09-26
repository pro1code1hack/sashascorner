"""Menu items: create, edit, sizes, one-off recipes and dated sell prices. Recipes spec A3.

A design "product" is several `menu_item` rows, one per size, sharing a `name`
(`uq_menu_item_name_size`). Everything here that edits "the item" edits that group.

The rules, each from an invariant rather than a preference:

- **Nothing that history references is deleted** (invariant 12 by analogy; spec C-5).
  Removing a size or an item sets `active = false`. Sales, prices and recipe lines
  keep pointing at a row that still exists.
- **Sell prices are dated** (spec C-3): `menu_item_price` rows, closed and opened,
  never updated; `menu_item.price_pence` is a cache of the open row and is written in
  the same transaction.
- **One-off recipes are dated** like template components (invariant 3): an edit closes
  the open `manual_recipe_line` rows and opens new ones from now.
- **Only one-off items have editable lines** (spec C-2). A templated item's lines come
  from its recipe, and editing them here would be overwritten -- or worse, would fork
  the item off its recipe without anybody deciding to.
- **Units are converted exactly or refused** (ARCHITECTURE 3). A line typed in ml for
  an ingredient stocked in litres is converted; one typed in kg for an ingredient in
  litres is refused, never taken 1:1.
- Previews write nothing: the "after" side is the repository's own spec with the new
  lines substituted, resolved by the same resolver.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    Ingredient,
    ManualRecipeLine,
    MediaAsset,
    MenuCategory,
    MenuItem,
    MenuItemPrice,
    RecipeChange,
)
from cafeops.db.models.enums import MenuKind, MenuPriceSource
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.menu_cost import SqlMenuCostRepository
from cafeops.domain.composition import (
    SIZE_ORDER,
    ChangeImpact,
    ItemChange,
    gbp,
    qty_text,
    resolve_recipe,
    summarise_changes,
    unit_label,
)
from cafeops.domain.labour import UNTIMED
from cafeops.domain.types import SizeCode, Unit
from cafeops.domain.units import IncompatibleUnitsError, convert
from cafeops.jobs.cost_rollup import configured_rate_pence, rollup_menu_items, snapshots_at
from cafeops.services.edit_composition import require_not_retroactive

__all__ = [
    "LineIn",
    "LinesPreview",
    "NotManualRecipeError",
    "PricesPreview",
    "add_size",
    "apply_manual_lines",
    "apply_prices",
    "attach_photo",
    "create_category",
    "create_menu_item",
    "duplicate_item",
    "group_rows",
    "normalise_lines",
    "preview_manual_lines",
    "preview_prices",
    "remove_size",
    "set_menu_price",
    "update_group",
]

WINDOW_DAYS = 30


class NotManualRecipeError(ValueError):
    """The item's lines come from a recipe; edit the recipe instead (spec C-2). 409."""


@dataclass(frozen=True, slots=True)
class LineIn:
    """A recipe line as typed: quantity in `unit`, or in the ingredient's unit if None."""

    ingredient_id: int
    qty: Decimal
    unit: Unit | None = None


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _signed(actor: str) -> str:
    name = actor.strip()
    if not name:
        raise ValueError("say who is making this change (the operator name)")
    return name[:120]


def group_rows(session: Session, menu_item_id: int) -> list[MenuItem]:
    """Every size row of the product this row belongs to, in size order."""
    anchor = session.get(MenuItem, menu_item_id)
    if anchor is None:
        raise LookupError(f"menu item {menu_item_id} not found")
    rows = list(session.scalars(select(MenuItem).where(MenuItem.name == anchor.name)))
    rank = {s: i for i, s in enumerate(SIZE_ORDER)}
    return sorted(rows, key=lambda r: rank.get(r.size_code, 9) if r.size_code else 9)


def set_menu_price(
    session: Session,
    item: MenuItem,
    price_pence: int,
    *,
    at: datetime,
    actor: str,
    note: str | None = None,
    force: bool = False,
) -> bool:
    """Close the open `menu_item_price` row and open a new one. Returns False if unchanged.

    `menu_item.price_pence` is a cache of the open row and is written here, in the same
    unit of work, and nowhere else.
    """
    if price_pence < 0:
        raise ValueError("a sell price cannot be negative")
    open_row = session.scalar(
        select(MenuItemPrice).where(
            MenuItemPrice.menu_item_id == item.id, MenuItemPrice.effective_to.is_(None)
        )
    )
    if open_row is not None and open_row.price_pence == price_pence and not force:
        return False
    if open_row is not None:
        if open_row.effective_from >= at:
            raise ValueError(
                f"{item.name}: a price was already set at this instant; try again in a moment"
            )
        open_row.effective_to = at
        session.flush()
    session.add(
        MenuItemPrice(
            menu_item_id=item.id,
            price_pence=price_pence,
            effective_from=at,
            source=MenuPriceSource.MANUAL,
            set_by=_signed(actor),
            note=note,
        )
    )
    item.price_pence = price_pence
    session.flush()
    return True


def normalise_lines(session: Session, lines: Sequence[LineIn]) -> list[tuple[int, Decimal]]:
    """Lines in each ingredient's own unit, merged by ingredient. Refuses what it cannot convert."""
    out: dict[int, Decimal] = {}
    for line in lines:
        ingredient = session.get(Ingredient, line.ingredient_id)
        if ingredient is None:
            raise LookupError(f"ingredient {line.ingredient_id} not found")
        if ingredient.retired_at is not None:
            raise ValueError(f"{ingredient.name} is retired and cannot go into a recipe")
        if line.qty <= 0:
            raise ValueError(f"{ingredient.name}: a quantity must be more than 0")
        qty = line.qty
        if line.unit is not None and line.unit is not ingredient.unit:
            try:
                qty = convert(line.qty, line.unit, ingredient.unit)
            except IncompatibleUnitsError as exc:
                raise ValueError(
                    f"{ingredient.name} is measured in {unit_label(ingredient.unit)}, and "
                    f"{qty_text(line.qty)} {unit_label(line.unit)} cannot be turned into "
                    f"{unit_label(ingredient.unit)} without a density, which this system does "
                    "not model. Enter it in "
                    f"{unit_label(ingredient.unit)}"
                    + (
                        " or ml"
                        if ingredient.unit is Unit.L
                        else " or L"
                        if ingredient.unit is Unit.ML
                        else " or g"
                        if ingredient.unit is Unit.KG
                        else " or kg"
                        if ingredient.unit is Unit.G
                        else ""
                    )
                    + "."
                ) from exc
        out[ingredient.id] = out.get(ingredient.id, Decimal("0")) + qty
    return list(out.items())


def _changes_for(
    session: Session,
    item_ids: Sequence[int],
    *,
    at: datetime,
    window_days: int,
    after_lines: dict[int, list[tuple[int, Decimal]]] | None = None,
    after_prices: dict[int, int] | None = None,
) -> list[ItemChange]:
    composition = SqlCompositionRepository(session)
    specs = composition.item_specs(list(item_ids), at)
    snapshots = snapshots_at(session, at)
    prep = composition.prep_times(list(item_ids))
    until = at.astimezone(settings.tz).date()
    since = until - timedelta(days=window_days - 1)
    volumes = SqlMenuCostRepository(session).units_sold_bulk(
        list(item_ids), since=since, until=until
    )
    rate = configured_rate_pence()
    out: list[ItemChange] = []
    for item_id in item_ids:
        spec = specs.get(item_id)
        row = session.get(MenuItem, item_id)
        if spec is None or row is None:
            continue
        before = resolve_recipe(spec, (), at, ingredients=snapshots)
        after_spec = spec
        if after_lines is not None and item_id in after_lines:
            after_spec = replace(spec, manual_lines=tuple(after_lines[item_id]))
        price_after = (after_prices or {}).get(item_id, row.price_pence)
        after_spec = replace(after_spec, price_pence=price_after)
        after = resolve_recipe(after_spec, (), at, ingredients=snapshots)
        out.append(
            ItemChange(
                key=f"i{item_id}",
                menu_item_id=item_id,
                name=row.name,
                size_code=row.size_code,
                before=before,
                after=after,
                price_before=row.price_pence,
                price_after=price_after,
                active_before=row.active,
                active_after=row.active,
                prep_before=prep.get(item_id, UNTIMED),
                prep_after=prep.get(item_id, UNTIMED),
                units_sold=volumes.get(item_id, Decimal("0")),
                loaded_hourly_rate_pence=rate,
                on_till=row.lightspeed_id is not None,
            )
        )
    return out


def _line_diff(
    session: Session, before: Sequence[tuple[int, Decimal]], after: Sequence[tuple[int, Decimal]]
) -> list[str]:
    names = {
        i: (n, u)
        for i, n, u in session.execute(
            select(Ingredient.id, Ingredient.name, Ingredient.unit).where(
                Ingredient.id.in_([i for i, _ in before] + [i for i, _ in after])
            )
        )
    }
    b = dict(before)
    a = dict(after)
    out: list[str] = []
    for ingredient_id, qty in a.items():
        name, unit = names.get(ingredient_id, (f"#{ingredient_id}", None))
        if ingredient_id not in b:
            out.append(f"Added {name} {qty_text(qty)} {unit_label(unit)}".rstrip())
        elif b[ingredient_id] != qty:
            out.append(
                f"{name}: {qty_text(b[ingredient_id])} → "
                f"{qty_text(qty)} {unit_label(unit)}".rstrip()
            )
    for ingredient_id in b:
        if ingredient_id not in a:
            out.append(f"Removed {names.get(ingredient_id, (f'#{ingredient_id}', None))[0]}")
    return out


def _open_lines(session: Session, menu_item_id: int, at: datetime) -> list[ManualRecipeLine]:
    return list(
        session.scalars(
            select(ManualRecipeLine)
            .where(
                ManualRecipeLine.menu_item_id == menu_item_id,
                ManualRecipeLine.effective_from <= at,
                or_(ManualRecipeLine.effective_to.is_(None), ManualRecipeLine.effective_to > at),
            )
            .order_by(ManualRecipeLine.id)
        )
    )


def _write_lines(
    session: Session, menu_item_id: int, lines: Sequence[tuple[int, Decimal]], at: datetime
) -> int:
    closed = 0
    for row in _open_lines(session, menu_item_id, at):
        row.effective_to = at
        closed += 1
    for ingredient_id, qty in lines:
        session.add(
            ManualRecipeLine(
                menu_item_id=menu_item_id, ingredient_id=ingredient_id, qty=qty, effective_from=at
            )
        )
    session.flush()
    return closed


# --------------------------------------------------------------------------
# One-off recipe lines
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinesPreview:
    menu_item_ids: tuple[int, ...]
    lines: tuple[tuple[int, Decimal], ...]
    diff: tuple[str, ...]
    impact: ChangeImpact
    at: datetime


def _manual_targets(session: Session, menu_item_id: int, also: Sequence[int]) -> list[MenuItem]:
    ids = [menu_item_id, *[i for i in also if i != menu_item_id]]
    rows: list[MenuItem] = []
    for item_id in ids:
        row = session.get(MenuItem, item_id)
        if row is None:
            raise LookupError(f"menu item {item_id} not found")
        if rows and row.name != rows[0].name:
            raise ValueError(
                f"{row.name} is a different item: 'copy to other sizes' only copies between "
                f"the sizes of {rows[0].name}"
            )
        if not row.manual_recipe:
            raise NotManualRecipeError(
                f"{row.name} is made from a recipe, so its lines come from that recipe. "
                "Change it on the Recipes page; nothing here was changed."
            )
        rows.append(row)
    return rows


def preview_manual_lines(
    session: Session,
    menu_item_id: int,
    lines: Sequence[LineIn],
    *,
    also_menu_item_ids: Sequence[int] = (),
    window_days: int = WINDOW_DAYS,
) -> LinesPreview:
    """What replacing a one-off item's recipe would do. WRITES NOTHING."""
    at = datetime.now(UTC)
    targets = _manual_targets(session, menu_item_id, also_menu_item_ids)
    normalised = normalise_lines(session, lines)
    ids = [t.id for t in targets]
    specs = SqlCompositionRepository(session).item_specs(ids, at)
    diff: list[str] = []
    for target in targets:
        spec = specs.get(target.id)
        lines_before = list(spec.manual_lines) if spec is not None else []
        item_diff = _line_diff(session, lines_before, normalised)
        size = target.size_code.value if target.size_code else "One"
        diff.extend(f"{target.name} {size}: {line}" for line in item_diff)
    warnings: list[str] = []
    if len(targets) > 1:
        packaging = [
            name
            for name, category in session.execute(
                select(Ingredient.name, Ingredient.category).where(
                    Ingredient.id.in_([i for i, _ in normalised])
                )
            )
            if (category or "").casefold() == "packaging"
        ]
        if packaging:
            warnings.append(
                f"Copying packaging to other sizes ({', '.join(packaging)}): cups and lids "
                "usually differ by size, so check each size before applying."
            )
    changes = _changes_for(
        session, ids, at=at, window_days=window_days, after_lines=dict.fromkeys(ids, normalised)
    )
    return LinesPreview(
        menu_item_ids=tuple(ids),
        lines=tuple(normalised),
        diff=tuple(diff),
        impact=summarise_changes(changes, window_days=window_days, extra_warnings=warnings),
        at=at,
    )


@dataclass
class LinesApplied:
    effective_from: datetime
    menu_item_ids: list[int]
    lines_closed: int = 0
    lines_opened: int = 0
    rollup_items_recosted: int = 0
    diff: tuple[str, ...] = ()


def apply_manual_lines(
    session: Session,
    menu_item_id: int,
    lines: Sequence[LineIn],
    *,
    actor: str,
    also_menu_item_ids: Sequence[int] = (),
    effective_from: datetime | None = None,
) -> LinesApplied:
    """Replace a one-off item's recipe from now. One transaction; commits itself."""
    at = require_not_retroactive(effective_from or datetime.now(UTC))
    actor = _signed(actor)
    targets = _manual_targets(session, menu_item_id, also_menu_item_ids)
    normalised = normalise_lines(session, lines)
    result = LinesApplied(effective_from=at, menu_item_ids=[t.id for t in targets])
    all_diff: list[str] = []
    try:
        for target in targets:
            before = [(r.ingredient_id, r.qty) for r in _open_lines(session, target.id, at)]
            diff = _line_diff(session, before, normalised)
            if not diff:
                continue
            result.lines_closed += _write_lines(session, target.id, normalised, at)
            result.lines_opened += len(normalised)
            size = target.size_code.value if target.size_code else "One"
            all_diff.extend(f"{target.name} {size}: {line}" for line in diff)
            session.add(
                RecipeChange(
                    menu_item_id=target.id,
                    change_kind="manual_lines",
                    effective_from=at,
                    actor=actor,
                    summary=" · ".join(diff[:3])
                    + (f" · +{len(diff) - 3} more" if len(diff) > 3 else ""),
                    lines=diff,
                )
            )
        if not all_diff:
            raise ValueError("there is nothing to apply: the recipe is already exactly this")
        session.flush()
        rollup = rollup_menu_items(
            session, result.menu_item_ids, at=at, trigger=f"one-off recipe edit by {actor}"
        )
        result.rollup_items_recosted = rollup.costed
        session.commit()
    except Exception:
        session.rollback()
        raise
    result.diff = tuple(all_diff)
    return result


# --------------------------------------------------------------------------
# Sell prices (spec C-3)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PricesPreview:
    impact: ChangeImpact
    pos_actions: tuple[str, ...]
    diff: tuple[str, ...]
    at: datetime


def _price_targets(session: Session, prices: Sequence[tuple[int, int]]) -> dict[int, int]:
    out: dict[int, int] = {}
    for item_id, price in prices:
        if session.get(MenuItem, item_id) is None:
            raise LookupError(f"menu item {item_id} not found")
        if price < 0:
            raise ValueError("a sell price cannot be negative")
        out[item_id] = price
    if not out:
        raise ValueError("no prices given")
    return out


def _price_diff(session: Session, targets: dict[int, int]) -> tuple[list[str], list[str]]:
    diff: list[str] = []
    pos: list[str] = []
    for item_id, price in targets.items():
        row = session.get(MenuItem, item_id)
        if row is None or row.price_pence == price:
            continue
        size = row.size_code.value if row.size_code else "One"
        diff.append(f"{row.name} {size}: {gbp(row.price_pence)} → {gbp(price)}")
        if row.lightspeed_id is not None:
            pos.append(
                f"Change {row.name} {size} to {gbp(price)} in Lightspeed too: the till rings "
                "its own price, and this screen does not change it."
            )
    return diff, pos


def preview_prices(
    session: Session, prices: Sequence[tuple[int, int]], *, window_days: int = WINDOW_DAYS
) -> PricesPreview:
    """What new sell prices would do to margin and takings. WRITES NOTHING."""
    at = datetime.now(UTC)
    targets = _price_targets(session, prices)
    diff, pos = _price_diff(session, targets)
    changes = _changes_for(
        session, list(targets), at=at, window_days=window_days, after_prices=targets
    )
    return PricesPreview(
        impact=summarise_changes(changes, window_days=window_days),
        pos_actions=tuple(pos),
        diff=tuple(diff),
        at=at,
    )


@dataclass
class PricesApplied:
    effective_from: datetime
    repriced: list[int] = field(default_factory=list)
    diff: tuple[str, ...] = ()
    pos_actions: tuple[str, ...] = ()


def apply_prices(
    session: Session,
    prices: Sequence[tuple[int, int]],
    *,
    actor: str,
    effective_from: datetime | None = None,
) -> PricesApplied:
    """Set sell prices from now. Dated rows; the cache follows. Commits itself."""
    at = require_not_retroactive(effective_from or datetime.now(UTC))
    actor = _signed(actor)
    targets = _price_targets(session, prices)
    diff, pos = _price_diff(session, targets)
    if not diff:
        raise ValueError("there is nothing to apply: every price is already that")
    result = PricesApplied(effective_from=at, diff=tuple(diff), pos_actions=tuple(pos))
    try:
        for item_id, price in targets.items():
            row = session.get(MenuItem, item_id)
            if row is None:  # pragma: no cover
                continue
            before = row.price_pence
            if set_menu_price(session, row, price, at=at, actor=actor):
                result.repriced.append(item_id)
                size = row.size_code.value if row.size_code else "One"
                line = f"Price {size}: {gbp(before)} → {gbp(price)}"
                session.add(
                    RecipeChange(
                        menu_item_id=item_id,
                        template_id=row.template_id,
                        change_kind="menu_price",
                        effective_from=at,
                        actor=actor,
                        summary=line,
                        lines=[line],
                    )
                )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return result


# --------------------------------------------------------------------------
# Creating and editing products
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SizeIn:
    size_code: SizeCode | None
    price_pence: int
    lines: tuple[LineIn, ...] = ()


def _name_free(
    session: Session, name: str, size: SizeCode | None, *, except_ids: Sequence[int] = ()
) -> bool:
    stmt = select(MenuItem.id).where(MenuItem.name == name)
    stmt = stmt.where(MenuItem.size_code.is_(None) if size is None else MenuItem.size_code == size)
    found = [i for i in session.scalars(stmt) if i not in except_ids]
    return not found


def create_menu_item(
    session: Session,
    *,
    name: str,
    category: str | None,
    note: str | None,
    sizes: Sequence[SizeIn],
    actor: str,
) -> list[int]:
    """A new one-off product: one row per size, dated price and lines from now."""
    at = datetime.now(UTC)
    actor = _signed(actor)
    name = name.strip()
    if not name:
        raise ValueError("an item needs a name")
    if not sizes:
        raise ValueError("an item needs at least one size")
    codes = [s.size_code for s in sizes]
    if len(set(codes)) != len(codes):
        raise ValueError("each size can appear only once")
    for size in sizes:
        if not _name_free(session, name, size.size_code):
            raise ValueError(f"there is already a menu item called {name!r} at that size")
    ids: list[int] = []
    try:
        for size in sizes:
            row = MenuItem(
                name=name,
                category=(category or "").strip() or None,
                note=(note or "").strip() or None,
                size_code=size.size_code,
                selected_options={},
                price_pence=size.price_pence,
                active=True,
                manual_recipe=True,
            )
            session.add(row)
            session.flush()
            set_menu_price(session, row, size.price_pence, at=at, actor=actor, note="new item")
            lines = normalise_lines(session, size.lines)
            if lines:
                _write_lines(session, row.id, lines, at)
                diff = _line_diff(session, [], lines)
                session.add(
                    RecipeChange(
                        menu_item_id=row.id,
                        change_kind="manual_lines",
                        effective_from=at,
                        actor=actor,
                        summary="Created",
                        lines=diff,
                    )
                )
            ids.append(row.id)
        session.flush()
        rollup_menu_items(session, ids, at=at, trigger=f"menu item created by {actor}")
        session.commit()
    except Exception:
        session.rollback()
        raise
    return ids


def update_group(
    session: Session,
    menu_item_id: int,
    *,
    actor: str,
    name: str | None = None,
    category: str | None = None,
    set_category: bool = False,
    note: str | None = None,
    set_note: bool = False,
    active: bool | None = None,
) -> list[int]:
    """Name, category, note and on/off for every size of a product.

    Not effective-dated: none of these is recipe or price. A rename changes this
    system's label only; the till keeps its own name and sales still match by
    Lightspeed id.
    """
    _signed(actor)
    rows = group_rows(session, menu_item_id)
    ids = [r.id for r in rows]
    try:
        if name is not None:
            new = name.strip()
            if not new:
                raise ValueError("an item needs a name")
            if new != rows[0].name:
                for row in rows:
                    if not _name_free(session, new, row.size_code, except_ids=ids):
                        raise ValueError(f"there is already a menu item called {new!r}")
                for row in rows:
                    row.name = new
        for row in rows:
            if set_category:
                row.category = (category or "").strip() or None
            if set_note:
                row.note = (note or "").strip()[:400] or None
            if active is not None:
                row.active = active
        session.commit()
    except Exception:
        session.rollback()
        raise
    return ids


def add_size(
    session: Session,
    menu_item_id: int,
    *,
    size_code: SizeCode | None,
    price_pence: int,
    actor: str,
    copy_from_menu_item_id: int | None = None,
) -> int:
    """Add (or bring back) a size of a one-off product, copying another size's lines."""
    at = datetime.now(UTC)
    actor = _signed(actor)
    rows = group_rows(session, menu_item_id)
    if any(not r.manual_recipe for r in rows):
        raise NotManualRecipeError(
            f"{rows[0].name} is made from a recipe, and its sizes are the recipe's sizes. "
            "Nothing was changed."
        )
    source_id = copy_from_menu_item_id or menu_item_id
    if source_id not in {r.id for r in rows}:
        raise ValueError("copy the lines from a size of the same item")
    try:
        existing = next((r for r in rows if r.size_code == size_code), None)
        if existing is not None:
            if existing.active:
                raise ValueError(f"{existing.name} already has that size")
            existing.active = True
            set_menu_price(session, existing, price_pence, at=at, actor=actor, note="size back on")
            target = existing
        else:
            anchor = rows[0]
            target = MenuItem(
                name=anchor.name,
                category=anchor.category,
                note=anchor.note,
                photo_asset_id=anchor.photo_asset_id,
                size_code=size_code,
                selected_options={},
                price_pence=price_pence,
                active=True,
                manual_recipe=True,
            )
            session.add(target)
            session.flush()
            set_menu_price(session, target, price_pence, at=at, actor=actor, note="new size")
            lines = [(r.ingredient_id, r.qty) for r in _open_lines(session, source_id, at)]
            if lines:
                _write_lines(session, target.id, lines, at)
                session.add(
                    RecipeChange(
                        menu_item_id=target.id,
                        change_kind="manual_lines",
                        effective_from=at,
                        actor=actor,
                        summary="New size, lines copied",
                        lines=_line_diff(session, [], lines),
                    )
                )
        session.flush()
        rollup_menu_items(session, [target.id], at=at, trigger=f"size added by {actor}")
        session.commit()
    except Exception:
        session.rollback()
        raise
    return target.id


def remove_size(session: Session, menu_item_id: int, *, actor: str) -> None:
    """Take one size off the menu. Never deleted: sales and prices reference the row."""
    _signed(actor)
    row = session.get(MenuItem, menu_item_id)
    if row is None:
        raise LookupError(f"menu item {menu_item_id} not found")
    row.active = False
    session.commit()


def duplicate_item(session: Session, menu_item_id: int, *, actor: str) -> list[int]:
    """Copy a product as a new one-off `"{name} (copy)"`, lines and prices from now.

    A copy of a recipe item becomes a ONE-OFF item holding the recipe's lines as they
    resolve today: it is a new product the till has never sold, and tying it to the
    recipe would be deciding its recipe for it.
    """
    at = datetime.now(UTC)
    actor = _signed(actor)
    rows = [r for r in group_rows(session, menu_item_id) if r.active] or group_rows(
        session, menu_item_id
    )
    base = f"{rows[0].name} (copy)"
    name = base
    n = 2
    while any(not _name_free(session, name, r.size_code) for r in rows):
        name = f"{base[:-1]} {n})"
        n += 1
    composition = SqlCompositionRepository(session)
    specs = composition.item_specs([r.id for r in rows], at)
    snapshots = snapshots_at(session, at)
    ids: list[int] = []
    try:
        for row in rows:
            spec = specs.get(row.id)
            lines: list[tuple[int, Decimal]] = []
            if spec is not None:
                recipe = resolve_recipe(spec, (), at, ingredients=snapshots)
                merged: dict[int, Decimal] = {}
                for line in recipe.lines:
                    merged[line.ingredient_id] = (
                        merged.get(line.ingredient_id, Decimal("0")) + line.qty
                    )
                lines = list(merged.items())
            copy = MenuItem(
                name=name,
                category=row.category,
                note=row.note,
                photo_asset_id=row.photo_asset_id,
                size_code=row.size_code,
                selected_options={},
                price_pence=row.price_pence,
                active=True,
                manual_recipe=True,
            )
            session.add(copy)
            session.flush()
            set_menu_price(
                session, copy, row.price_pence, at=at, actor=actor, note=f"copy of {row.name}"
            )
            if lines:
                _write_lines(session, copy.id, lines, at)
                session.add(
                    RecipeChange(
                        menu_item_id=copy.id,
                        change_kind="manual_lines",
                        effective_from=at,
                        actor=actor,
                        summary=f"Copied from {row.name}",
                        lines=_line_diff(session, [], lines),
                    )
                )
            ids.append(copy.id)
        session.flush()
        rollup_menu_items(session, ids, at=at, trigger=f"menu item duplicated by {actor}")
        session.commit()
    except Exception:
        session.rollback()
        raise
    return ids


def create_category(session: Session, *, name: str, kind: MenuKind) -> int:
    clean = name.strip()
    if not clean:
        raise ValueError("a category needs a name")
    if session.scalar(select(MenuCategory.id).where(MenuCategory.name == clean)) is not None:
        raise ValueError(f"there is already a category called {clean!r}")
    top = session.scalar(select(MenuCategory.sort_order).order_by(MenuCategory.sort_order.desc()))
    row = MenuCategory(name=clean, kind=kind, sort_order=(top or 0) + 1)
    session.add(row)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    return row.id


def attach_photo(session: Session, menu_item_id: int, asset_id: int | None) -> list[int]:
    """Set (or clear) the photo on every size of the product. The file is never deleted."""
    rows = group_rows(session, menu_item_id)
    if asset_id is not None and session.get(MediaAsset, asset_id) is None:
        raise LookupError(f"media asset {asset_id} not found")
    for row in rows:
        row.photo_asset_id = asset_id
    session.commit()
    return [r.id for r in rows]
