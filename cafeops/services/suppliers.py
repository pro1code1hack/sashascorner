"""Who we buy from: a supplier's profile, and what we buy there. Spec 4.3, C3, C12, C20.

Everything here is a write the Suppliers screen makes, and every one of them is shaped by
something that already exists:

* **Terms are not profile.** Lead time, delivery days, cut-off, minimum, fee and the
  free-delivery threshold are one fact confirmed together, through
  `services/confirm_terms.confirm_supplier_terms` (ARCHITECTURE 8O, CLAUDE 10.9b). A
  profile edit here cannot touch them, so a typo in a supplier's name can never clear
  the invented-terms warning. A new supplier starts with `terms_are_placeholders=True`.
* **Delete is archive** (C12). Purchase orders, prices and routings reference a supplier,
  so a hard delete would orphan history. Archiving is refused while an order is open.
  The supplier's links are archived with it and drop out of sourcing
  (`db/repositories/sourcing._active_products`).
* **The starred link is the recipe price.** Recipe costs read the ingredient's price
  history, not the supplier table. So whenever the preferred (★) link's price or pack
  changes -- or a different link becomes ★ -- a new effective-dated `ingredient_price`
  row opens from now (invariant 3's spirit: history is never rewritten) and the menu is
  re-costed through the existing cascade (`jobs.cost_rollup.rollup_for_ingredient`).
  The design's `syncPreferred` mutated the ingredient in place; that is what this
  replaces.
* **A price of £0 is refused.** Zero is not "unknown" (invariant 8); a link without a
  price is not a link.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    Ingredient,
    IngredientPrice,
    OrderChannel,
    POLine,
    POStatus,
    PriceSource,
    PurchaseOrder,
    Supplier,
    SupplierProduct,
    Unit,
)
from cafeops.domain.units import IncompatibleUnitsError, convert
from cafeops.jobs.cost_rollup import rollup_for_ingredient
from cafeops.services.confirm_terms import (
    ConfirmationRefused,
    SupplierTerms,
    confirm_supplier_terms,
)

__all__ = [
    "OPEN_STATUSES",
    "ProductChange",
    "SupplierRefused",
    "archive_product",
    "archive_supplier",
    "contact_details",
    "create_supplier",
    "edit_product",
    "link_product",
    "prefer_product",
    "update_supplier_profile",
]

#: An order in any of these still needs its supplier (and its product rows).
OPEN_STATUSES: tuple[POStatus, ...] = (
    POStatus.DRAFT,
    POStatus.PENDING_CONFIRM,
    POStatus.CONFIRMED,
    POStatus.SENT,
)

_NAME_MAX = 160
_KIND_MAX = 40
_CONTACT_MAX = 400
_URL_MAX = 500
_SKU_MAX = 80
_EMAIL_MAX = 200
_PHONE_MAX = 60

#: Email and phone live in `supplier.channel_config` (a JSON column that exists and
#: was unused) rather than new columns: they are how an order reaches the supplier,
#: which is what that column is for, and it needs no migration.
_CONTACT_KEYS = ("email", "phone")


class SupplierRefused(ValueError):
    """Nothing was saved, and the message says why. Shown verbatim."""


# ==========================================================================
# helpers
# ==========================================================================


def _who(name: str, what: str = "changed_by") -> str:
    clean = name.strip()
    if not clean:
        raise SupplierRefused(f"{what} is required: say who is making this change")
    return clean[:120]


def _text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean[:limit] or None


def _email(value: str | None) -> str | None:
    clean = _text(value, _EMAIL_MAX)
    if clean is not None and ("@" not in clean or " " in clean or clean.startswith("@")):
        raise SupplierRefused(f"{clean!r} is not an email address")
    return clean


def contact_details(row: Supplier) -> dict[str, str | None]:
    """The supplier's email and phone, from `channel_config`."""
    cfg = row.channel_config or {}
    out: dict[str, str | None] = {}
    for key in _CONTACT_KEYS:
        v = cfg.get(key)
        out[key] = v if isinstance(v, str) and v else None
    return out


def _set_contact(row: Supplier, key: str, value: str | None) -> bool:
    cfg = dict(row.channel_config or {})
    if cfg.get(key) == value or (value is None and key not in cfg):
        return False
    if value is None:
        cfg.pop(key, None)
    else:
        cfg[key] = value
    row.channel_config = cfg  # a new dict, so the JSON change is seen
    return True


def _check_guessed_terms(terms: SupplierTerms) -> None:
    """Coherence for terms entered but NOT confirmed. An empty week is allowed:
    it means "not known yet", and the placeholder flag already says so."""
    if terms.lead_time_days < 0 or terms.lead_time_days > 60:
        raise SupplierRefused("lead time is 0 to 60 days")
    if any(d < 1 or d > 7 for d in terms.delivery_weekdays):
        raise SupplierRefused("delivery weekdays are ISO 1-7 (Mon-Sun)")
    if terms.min_order_pence < 0 or terms.delivery_fee_pence < 0:
        raise SupplierRefused("minimum order and delivery fee cannot be negative")
    if terms.free_delivery_threshold_pence is not None and terms.free_delivery_threshold_pence < 0:
        raise SupplierRefused("free-delivery threshold cannot be negative")


def _supplier(session: Session, supplier_id: int) -> Supplier:
    row = session.get(Supplier, supplier_id)
    if row is None:
        raise LookupError(f"supplier {supplier_id} not found")
    return row


def _product(session: Session, product_id: int) -> SupplierProduct:
    row = session.get(SupplierProduct, product_id)
    if row is None:
        raise LookupError(f"supplier product {product_id} not found")
    return row


def _name_taken(session: Session, name: str, *, except_id: int | None = None) -> bool:
    for row in session.scalars(select(Supplier)):
        if row.id != except_id and row.name.strip().lower() == name.lower():
            return True
    return False


def _open_orders(session: Session, supplier_id: int) -> list[int]:
    return list(
        session.scalars(
            select(PurchaseOrder.id).where(
                PurchaseOrder.supplier_id == supplier_id,
                PurchaseOrder.status.in_(list(OPEN_STATUSES)),
            )
        )
    )


def unit_price_pence(product: SupplierProduct, ingredient_unit: Unit) -> Decimal | None:
    """Pack price per ingredient stocking unit. None across dimensions or a zero pack."""
    try:
        size = convert(product.pack_size, product.pack_unit, ingredient_unit)
    except IncompatibleUnitsError:
        return None
    if size <= 0:
        return None
    return Decimal(product.price_pence) / size


# ==========================================================================
# supplier profile
# ==========================================================================


def create_supplier(
    session: Session,
    *,
    name: str,
    created_by: str,
    order_channel: OrderChannel = OrderChannel.MANUAL,
    kind: str | None = None,
    contact: str | None = None,
    order_url: str | None = None,
    notes: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    terms: SupplierTerms | None = None,
    terms_confirmed: bool = False,
) -> Supplier:
    """A new supplier. Its terms are placeholders until confirmed with them.

    `terms` may be given up front. Unless `terms_confirmed`, they are stored as a
    guess and the placeholder flag stays set. Confirmed terms go through
    `confirm_supplier_terms`, all together, exactly as the /confirm endpoint does.
    """
    _who(created_by, "created_by")
    if terms_confirmed and terms is None:
        raise SupplierRefused("there are no terms to confirm: fill them all in first")
    if terms is not None and not terms_confirmed:
        _check_guessed_terms(terms)
    clean_email = _email(email)
    clean_phone = _text(phone, _PHONE_MAX)
    clean = name.strip()
    if not clean:
        raise SupplierRefused("a supplier needs a name")
    if len(clean) > _NAME_MAX:
        raise SupplierRefused(f"a supplier name is at most {_NAME_MAX} characters")
    if _name_taken(session, clean):
        raise SupplierRefused(f"there is already a supplier called {clean!r}")
    row = Supplier(
        name=clean,
        order_channel=order_channel,
        kind=_text(kind, _KIND_MAX),
        contact=_text(contact, _CONTACT_MAX),
        order_url=_text(order_url, _URL_MAX),
        notes=(notes or "").strip() or None,
        # Nothing about this supplier's terms has been checked with them: every order
        # built on the defaults must say so (ARCHITECTURE 8F.4).
        terms_are_placeholders=True,
        lead_time_days=1,
        delivery_weekdays=[],
        min_order_pence=0,
        delivery_fee_pence=0,
        channel_config={},
    )
    if clean_email is not None:
        _set_contact(row, "email", clean_email)
    if clean_phone is not None:
        _set_contact(row, "phone", clean_phone)
    if terms is not None and not terms_confirmed:
        row.lead_time_days = terms.lead_time_days
        row.delivery_weekdays = sorted(set(terms.delivery_weekdays))
        row.min_order_pence = terms.min_order_pence
        row.delivery_fee_pence = terms.delivery_fee_pence
        row.cutoff_time = terms.cutoff_time
        row.free_delivery_threshold_pence = terms.free_delivery_threshold_pence
    session.add(row)
    session.flush()
    if terms is not None and terms_confirmed:
        try:
            confirm_supplier_terms(session, name=row.name, terms=terms)
        except ConfirmationRefused as exc:
            raise SupplierRefused(str(exc)) from None
        session.flush()
    return row


#: Sentinel for "not given" in a PATCH, distinct from an explicit null.
UNSET: object = object()


def update_supplier_profile(
    session: Session,
    *,
    supplier_id: int,
    changed_by: str,
    name: str | None = None,
    kind: str | object | None = UNSET,
    order_channel: OrderChannel | None = None,
    contact: str | object | None = UNSET,
    order_url: str | object | None = UNSET,
    notes: str | object | None = UNSET,
    email: str | object | None = UNSET,
    phone: str | object | None = UNSET,
) -> tuple[Supplier, list[str]]:
    """Profile fields only. Terms go through `confirm_supplier_terms`, all together."""
    _who(changed_by)
    row = _supplier(session, supplier_id)
    if row.archived_at is not None:
        raise SupplierRefused(f"{row.name} is archived; it cannot be edited")
    changed: list[str] = []
    if name is not None:
        clean = name.strip()
        if not clean:
            raise SupplierRefused("a supplier needs a name")
        if len(clean) > _NAME_MAX:
            raise SupplierRefused(f"a supplier name is at most {_NAME_MAX} characters")
        if clean != row.name:
            if _name_taken(session, clean, except_id=row.id):
                raise SupplierRefused(f"there is already a supplier called {clean!r}")
            changed.append(f"name: {row.name} -> {clean}")
            row.name = clean
    if order_channel is not None and order_channel is not row.order_channel:
        changed.append(f"how we order: {row.order_channel.value} -> {order_channel.value}")
        row.order_channel = order_channel
    for field_name, value, limit in (
        ("kind", kind, _KIND_MAX),
        ("contact", contact, _CONTACT_MAX),
        ("order_url", order_url, _URL_MAX),
        ("notes", notes, 10_000),
    ):
        if value is UNSET:
            continue
        new = _text(value if isinstance(value, str) else None, limit)
        if new != getattr(row, field_name):
            changed.append(field_name)
            setattr(row, field_name, new)
    if email is not UNSET:
        if _set_contact(row, "email", _email(email if isinstance(email, str) else None)):
            changed.append("email")
    if phone is not UNSET:
        new_phone = _text(phone if isinstance(phone, str) else None, _PHONE_MAX)
        if _set_contact(row, "phone", new_phone):
            changed.append("phone")
    session.flush()
    return row, changed


def archive_supplier(
    session: Session, *, supplier_id: int, archived_by: str, at: datetime | None = None
) -> tuple[Supplier, list[str]]:
    """'Delete' = archive. Refused while an order is open. Returns (row, re-starred names)."""
    who = _who(archived_by, "archived_by")
    row = _supplier(session, supplier_id)
    if row.archived_at is not None:
        raise SupplierRefused(f"{row.name} is already archived")
    open_ids = _open_orders(session, supplier_id)
    if open_ids:
        raise SupplierRefused(
            f"{row.name} has {len(open_ids)} open order(s) "
            f"({', '.join(f'#{i}' for i in open_ids)}). Receive or cancel them first: "
            "an archived supplier cannot receive a delivery."
        )
    at = at or datetime.now(UTC)
    row.archived_at = at
    row.archived_by = who
    restarred: list[str] = []
    for product in session.scalars(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier_id, SupplierProduct.archived_at.is_(None)
        )
    ):
        was_preferred = product.is_preferred
        product.archived_at = at
        product.archived_by = who
        product.is_preferred = False
        if was_preferred:
            promoted = _promote_next(session, product.ingredient_id, by=who, at=at)
            if promoted is not None:
                restarred.append(promoted)
    session.flush()
    return row, restarred


# ==========================================================================
# supplier products
# ==========================================================================


@dataclass(frozen=True, slots=True)
class ProductChange:
    product: SupplierProduct
    #: Menu items re-costed because the recipe price moved. 0 when it did not.
    recosted_items: int
    #: The ingredient_price row opened, if the recipe price moved.
    price_row_id: int | None
    changed: tuple[str, ...]


def _validate_pack(
    ingredient: Ingredient, pack_size: Decimal, pack_unit: Unit, price_pence: int
) -> None:
    if isinstance(pack_size, float):
        raise TypeError("pack_size must be Decimal, not float (invariant 11)")
    if pack_size <= 0:
        raise SupplierRefused(f"a pack must hold something: got {pack_size}")
    if price_pence <= 0:
        raise SupplierRefused(
            "a pack price must be more than £0. An unknown price is not a free one "
            "(invariant 8): leave the link out until the price is known."
        )
    try:
        convert(pack_size, pack_unit, ingredient.unit)
    except IncompatibleUnitsError:
        raise SupplierRefused(
            f"{ingredient.name} is stocked in {ingredient.unit.value}; a pack in "
            f"{pack_unit.value} cannot be converted to it"
        ) from None


def _record_recipe_price(
    session: Session,
    product: SupplierProduct,
    *,
    by: str,
    at: datetime,
    source: PriceSource,
) -> tuple[int, int]:
    """Open an ingredient_price from this (★) link, close the previous, re-cost the menu.

    Returns (price_row_id, recosted_items).
    """
    ingredient = session.get(Ingredient, product.ingredient_id)
    if ingredient is None:  # pragma: no cover - FK
        raise LookupError(f"ingredient {product.ingredient_id} not found")
    per_unit = unit_price_pence(product, ingredient.unit)
    if per_unit is None:  # pragma: no cover - validated on the way in
        raise SupplierRefused(f"{ingredient.name}: this pack cannot be priced per unit")
    for current in session.scalars(
        select(IngredientPrice).where(
            IngredientPrice.ingredient_id == ingredient.id,
            IngredientPrice.effective_to.is_(None),
        )
    ):
        # Never retroactive: a price opened later than `at` (clock skew) is left alone.
        if current.effective_from <= at:
            current.effective_to = at
    row = IngredientPrice(
        ingredient_id=ingredient.id,
        supplier_id=product.supplier_id,
        pack_size=product.pack_size,
        pack_unit=product.pack_unit,
        pack_cost_pence=product.price_pence,
        cost_per_unit_pence=per_unit,
        effective_from=at,
        source=source,
        note=f"from the preferred supplier link #{product.id}",
        recorded_by=by,
    )
    session.add(row)
    session.flush()
    ingredient.current_cost_pence_per_unit = per_unit
    ingredient.current_cost_source = source
    session.flush()
    report = rollup_for_ingredient(session, ingredient.id, at=at, trigger="supplier price change")
    return row.id, report.costed


def _promote_next(session: Session, ingredient_id: int, *, by: str, at: datetime) -> str | None:
    """After the ★ link goes, star the cheapest remaining link per unit and re-price."""
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        return None
    candidates: list[tuple[Decimal, int, SupplierProduct]] = []
    for product in session.scalars(
        select(SupplierProduct)
        .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
        .where(
            SupplierProduct.ingredient_id == ingredient_id,
            SupplierProduct.archived_at.is_(None),
            Supplier.archived_at.is_(None),
        )
    ):
        per_unit = unit_price_pence(product, ingredient.unit)
        if per_unit is not None:
            candidates.append((per_unit, product.id, product))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    chosen = candidates[0][2]
    chosen.is_preferred = True
    session.flush()
    _record_recipe_price(session, chosen, by=by, at=at, source=_link_source(session, chosen))
    return ingredient.name


def _link_source(session: Session, product: SupplierProduct) -> PriceSource:
    """The provenance a starred link's price carries into the recipe price.

    The newest ingredient_price at this supplier for this ingredient says how that price
    got here; with none, the link's price is somebody's typing from the supplier's list.
    """
    latest = session.scalar(
        select(IngredientPrice)
        .where(
            IngredientPrice.ingredient_id == product.ingredient_id,
            IngredientPrice.supplier_id == product.supplier_id,
        )
        .order_by(IngredientPrice.effective_from.desc(), IngredientPrice.id.desc())
        .limit(1)
    )
    if latest is not None and latest.pack_cost_pence == product.price_pence:
        return latest.source
    return PriceSource.SUPPLIER_FEED


def link_product(
    session: Session,
    *,
    supplier_id: int,
    ingredient_id: int,
    sku: str,
    pack_size: Decimal,
    pack_unit: Unit,
    price_pence: int,
    changed_by: str,
    at: datetime | None = None,
) -> ProductChange:
    """Link an ingredient to a supplier. Becomes ★ if the ingredient has no ★ link."""
    who = _who(changed_by)
    at = at or datetime.now(UTC)
    supplier = _supplier(session, supplier_id)
    if supplier.archived_at is not None:
        raise SupplierRefused(f"{supplier.name} is archived; nothing can be linked to it")
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if ingredient.retired_at is not None:
        raise SupplierRefused(f"{ingredient.name} is retired")
    _validate_pack(ingredient, pack_size, pack_unit, price_pence)
    clean_sku = (sku or "").strip()[:_SKU_MAX]

    existing = session.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.ingredient_id == ingredient_id,
            SupplierProduct.sku == clean_sku,
        )
    )
    if existing is not None and existing.archived_at is None:
        raise SupplierRefused(
            f"{ingredient.name} is already linked at {supplier.name}"
            + (f" as {clean_sku!r}" if clean_sku else "")
            + ": edit that row instead"
        )
    has_star = session.scalar(
        select(SupplierProduct.id)
        .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
        .where(
            SupplierProduct.ingredient_id == ingredient_id,
            SupplierProduct.is_preferred.is_(True),
            SupplierProduct.archived_at.is_(None),
            Supplier.archived_at.is_(None),
        )
        .limit(1)
    )
    if existing is not None:
        # The same (supplier, ingredient, sku) was unlinked before; the unique constraint
        # covers archived rows, so bring it back rather than fail.
        product = existing
        product.archived_at = None
        product.archived_by = None
        product.pack_size = pack_size
        product.pack_unit = pack_unit
        product.price_pence = price_pence
    else:
        product = SupplierProduct(
            supplier_id=supplier_id,
            ingredient_id=ingredient_id,
            sku=clean_sku,
            pack_size=pack_size,
            pack_unit=pack_unit,
            price_pence=price_pence,
            is_preferred=False,
            moq_packs=1,
        )
        session.add(product)
    product.last_seen_price_at = at
    product.is_preferred = has_star is None
    session.flush()
    price_row: int | None = None
    recosted = 0
    if product.is_preferred:
        price_row, recosted = _record_recipe_price(
            session, product, by=who, at=at, source=PriceSource.SUPPLIER_FEED
        )
    return ProductChange(
        product=product,
        recosted_items=recosted,
        price_row_id=price_row,
        changed=("linked",),
    )


def edit_product(
    session: Session,
    *,
    product_id: int,
    changed_by: str,
    sku: str | None = None,
    pack_size: Decimal | None = None,
    pack_unit: Unit | None = None,
    price_pence: int | None = None,
    at: datetime | None = None,
) -> ProductChange:
    """Edit one link. If it is ★ and the price or pack moved, the recipe price moves too."""
    who = _who(changed_by)
    at = at or datetime.now(UTC)
    product = _product(session, product_id)
    if product.archived_at is not None:
        raise SupplierRefused("this link was removed; link the ingredient again instead")
    ingredient = session.get(Ingredient, product.ingredient_id)
    if ingredient is None:  # pragma: no cover - FK
        raise LookupError(f"ingredient {product.ingredient_id} not found")
    new_size = product.pack_size if pack_size is None else pack_size
    new_unit = product.pack_unit if pack_unit is None else pack_unit
    new_price = product.price_pence if price_pence is None else price_pence
    _validate_pack(ingredient, new_size, new_unit, new_price)

    changed: list[str] = []
    if sku is not None:
        clean = sku.strip()[:_SKU_MAX]
        if clean != product.sku:
            clash = session.scalar(
                select(SupplierProduct.id).where(
                    SupplierProduct.supplier_id == product.supplier_id,
                    SupplierProduct.ingredient_id == product.ingredient_id,
                    SupplierProduct.sku == clean,
                    SupplierProduct.id != product.id,
                )
            )
            if clash is not None:
                raise SupplierRefused(f"another link here already uses the code {clean!r}")
            changed.append("sku")
            product.sku = clean
    pack_moved = new_size != product.pack_size or new_unit is not product.pack_unit
    price_moved = new_price != product.price_pence
    if pack_moved:
        changed.append("pack")
        product.pack_size = new_size
        product.pack_unit = new_unit
    if price_moved:
        changed.append("price")
        product.price_pence = new_price
    if pack_moved or price_moved:
        product.last_seen_price_at = at
    session.flush()

    price_row: int | None = None
    recosted = 0
    if product.is_preferred and (pack_moved or price_moved):
        price_row, recosted = _record_recipe_price(
            session, product, by=who, at=at, source=PriceSource.SUPPLIER_FEED
        )
    return ProductChange(
        product=product, recosted_items=recosted, price_row_id=price_row, changed=tuple(changed)
    )


def prefer_product(
    session: Session, *, product_id: int, changed_by: str, at: datetime | None = None
) -> ProductChange:
    """Make this link the ★ one for its ingredient: its price becomes the recipe price."""
    who = _who(changed_by)
    at = at or datetime.now(UTC)
    product = _product(session, product_id)
    if product.archived_at is not None:
        raise SupplierRefused("this link was removed; link the ingredient again instead")
    supplier = _supplier(session, product.supplier_id)
    if supplier.archived_at is not None:
        raise SupplierRefused(f"{supplier.name} is archived")
    if product.is_preferred:
        return ProductChange(product=product, recosted_items=0, price_row_id=None, changed=())
    for other in session.scalars(
        select(SupplierProduct).where(
            SupplierProduct.ingredient_id == product.ingredient_id,
            SupplierProduct.id != product.id,
            SupplierProduct.is_preferred.is_(True),
        )
    ):
        other.is_preferred = False
    product.is_preferred = True
    session.flush()
    price_row, recosted = _record_recipe_price(
        session, product, by=who, at=at, source=_link_source(session, product)
    )
    return ProductChange(
        product=product, recosted_items=recosted, price_row_id=price_row, changed=("preferred",)
    )


def archive_product(
    session: Session, *, product_id: int, archived_by: str, at: datetime | None = None
) -> tuple[SupplierProduct, str | None]:
    """Unlink = archive. Refused while an open order has a line on it.

    Returns (row, the ingredient re-starred to another link, if this was ★).
    """
    who = _who(archived_by, "archived_by")
    at = at or datetime.now(UTC)
    product = _product(session, product_id)
    if product.archived_at is not None:
        raise SupplierRefused("this link is already removed")
    open_orders = list(
        session.scalars(
            select(PurchaseOrder.id)
            .join(POLine, POLine.po_id == PurchaseOrder.id)
            .where(
                POLine.supplier_product_id == product_id,
                PurchaseOrder.status.in_(list(OPEN_STATUSES)),
            )
        )
    )
    if open_orders:
        raise SupplierRefused(
            f"order(s) {', '.join(f'#{i}' for i in sorted(set(open_orders)))} still have a "
            "line on this product. Receive or cancel them first."
        )
    was_preferred = product.is_preferred
    product.archived_at = at
    product.archived_by = who
    product.is_preferred = False
    session.flush()
    promoted = (
        _promote_next(session, product.ingredient_id, by=who, at=at) if was_preferred else None
    )
    return product, promoted
