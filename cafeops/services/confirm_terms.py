"""Record a supplier term or a shelf life that a human has actually confirmed.

Until this existed there was no operator path to either. `cafeops doctor` reported
that six of eight suppliers carry invented terms and that 113 shelf lives are
ESTIMATE defaults, and then told the reader to *edit `cafeops/seed/suppliers.py`* --
a code change, in a repository, as the remedy for a thing the café owner learns by
telephoning a supplier. That is not an operating product, and it meant the two
loudest warnings in the system had no way to ever go quiet.

Both facts are already modelled; only the write was missing:

  - `supplier.terms_are_placeholders` -- a bool, cleared once every term on the
    supplier has been confirmed with the supplier.
  - `ingredient.shelf_life_source` -- `ESTIMATE` for a seeded guess, and anything
    else for a figure somebody checked.

## Why confirming is all-or-nothing per supplier

`terms_are_placeholders` covers lead time, delivery weekdays, cutoff, minimum order,
delivery fee and free-delivery threshold together, because the *cover window* is
computed from several of them at once and an order is only as trustworthy as its
weakest input. Clearing the flag while one field is still a guess would silence the
warning on an order that is still partly fiction, so `confirm_supplier_terms` demands
every field in one call and refuses a partial confirmation.

## Why there is no new enum

`shelf_life_source` reuses `PriceSource`. `SUPPLIER_FEED` means the supplier stated
it; `INVOICE` means it was read off the packaging or the delivery note. Neither is a
perfect name for a shelf life, and a dedicated `ShelfLifeSource` would read better --
but the column is `native_enum=False`, so its values live in a CHECK constraint and
changing them means a SQLite table rebuild. The distinction the system actually acts
on is `ESTIMATE` versus not, and that works today. Recorded here rather than left for
someone to rediscover.

Nothing here computes anything. It records what a person found out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models.enums import PriceSource
from cafeops.db.models.ingredient import Ingredient
from cafeops.db.models.supplier import Supplier


class ConfirmationRefused(ValueError):
    """The confirmation was rejected. The message says what to fix."""


@dataclass(frozen=True)
class SupplierTerms:
    """Every term the cover window is built from. All of them, or none."""

    lead_time_days: int
    delivery_weekdays: tuple[int, ...]
    min_order_pence: int
    delivery_fee_pence: int
    cutoff_time: time | None
    free_delivery_threshold_pence: int | None


@dataclass(frozen=True)
class SupplierConfirmation:
    supplier_id: int
    name: str
    before: SupplierTerms
    after: SupplierTerms
    was_placeholder: bool


def _terms_of(s: Supplier) -> SupplierTerms:
    return SupplierTerms(
        lead_time_days=s.lead_time_days,
        delivery_weekdays=tuple(s.delivery_weekdays),
        min_order_pence=s.min_order_pence,
        delivery_fee_pence=s.delivery_fee_pence,
        cutoff_time=s.cutoff_time,
        free_delivery_threshold_pence=s.free_delivery_threshold_pence,
    )


def confirm_supplier_terms(
    session: Session, *, name: str, terms: SupplierTerms
) -> SupplierConfirmation:
    """Write terms confirmed with a supplier and clear the placeholder flag.

    Raises `ConfirmationRefused` rather than writing something incoherent: a
    negative lead time, an empty delivery week (which would make every cover
    window unsatisfiable), or a weekday outside ISO 1-7.
    """
    supplier = session.scalars(select(Supplier).where(Supplier.name == name)).one_or_none()
    if supplier is None:
        known = ", ".join(sorted(n for (n,) in session.execute(select(Supplier.name))))
        raise ConfirmationRefused(f"no supplier named {name!r}. Known suppliers: {known}")

    if terms.lead_time_days < 0:
        raise ConfirmationRefused("lead time cannot be negative")
    if not terms.delivery_weekdays:
        raise ConfirmationRefused(
            "a supplier with no delivery weekday can never satisfy a cover window. "
            "Use 1-7 (Mon-Sun); a walk-in supplier delivers every day, so pass all seven."
        )
    if any(d < 1 or d > 7 for d in terms.delivery_weekdays):
        raise ConfirmationRefused("delivery weekdays are ISO 1-7 (Mon-Sun)")
    if terms.min_order_pence < 0 or terms.delivery_fee_pence < 0:
        raise ConfirmationRefused("minimum order and delivery fee cannot be negative")
    if terms.free_delivery_threshold_pence is not None and terms.free_delivery_threshold_pence < 0:
        raise ConfirmationRefused("free-delivery threshold cannot be negative")

    before = _terms_of(supplier)
    was_placeholder = supplier.terms_are_placeholders

    supplier.lead_time_days = terms.lead_time_days
    supplier.delivery_weekdays = sorted(set(terms.delivery_weekdays))
    supplier.min_order_pence = terms.min_order_pence
    supplier.delivery_fee_pence = terms.delivery_fee_pence
    supplier.cutoff_time = terms.cutoff_time
    supplier.free_delivery_threshold_pence = terms.free_delivery_threshold_pence
    supplier.terms_are_placeholders = False

    return SupplierConfirmation(
        supplier_id=supplier.id,
        name=supplier.name,
        before=before,
        after=_terms_of(supplier),
        was_placeholder=was_placeholder,
    )


@dataclass(frozen=True)
class ShelfLifeConfirmation:
    ingredient_id: int
    name: str
    shelf_life_days_before: int | None
    shelf_life_days_after: int
    open_life_days_after: int | None
    transit_buffer_days: int
    source_before: PriceSource | None
    source_after: PriceSource
    usable_days_after: int

    @property
    def usable_days_changed_by(self) -> int | None:
        """How much the ORDER CAP moves, which is the point of confirming."""
        if self.shelf_life_days_before is None:
            return None
        return self.usable_days_after - (self.shelf_life_days_before - self.transit_buffer_days)


def confirm_shelf_life(
    session: Session,
    *,
    name: str,
    shelf_life_days: int,
    open_life_days: int | None = None,
    source: PriceSource = PriceSource.SUPPLIER_FEED,
) -> ShelfLifeConfirmation:
    """Record a shelf life somebody checked, and stop calling it an estimate.

    This changes order sizes immediately: `effective_cover` is capped at
    `shelf_life_days - transit_buffer_days` for anything perishable (spec 5.4,
    invariant 4), so the returned `usable_days_changed_by` is the figure worth
    reading -- it says how much more, or less, of this ingredient a single order
    may now contain.
    """
    if source is PriceSource.ESTIMATE:
        raise ConfirmationRefused(
            "confirming a shelf life AS an estimate is a no-op that would silence "
            "the warning without adding knowledge. Pass the source you actually have."
        )
    if shelf_life_days <= 0:
        raise ConfirmationRefused("a shelf life must be at least one day")
    if open_life_days is not None and open_life_days <= 0:
        raise ConfirmationRefused("an open life must be at least one day")
    if open_life_days is not None and open_life_days > shelf_life_days:
        raise ConfirmationRefused(
            f"open life {open_life_days}d cannot exceed the unopened shelf life {shelf_life_days}d"
        )

    ing = session.scalars(select(Ingredient).where(Ingredient.name == name)).one_or_none()
    if ing is None:
        raise ConfirmationRefused(f"no ingredient named {name!r}")

    if shelf_life_days <= ing.transit_buffer_days:
        raise ConfirmationRefused(
            f"{name}: a {shelf_life_days}-day life against a "
            f"{ing.transit_buffer_days}-day transit buffer leaves nothing usable on "
            "arrival, so no quantity could ever be ordered. Fix the transit buffer "
            "first, or this ingredient cannot come from this supplier at all."
        )

    before_days = ing.shelf_life_days
    before_source = ing.shelf_life_source

    ing.shelf_life_days = shelf_life_days
    if open_life_days is not None:
        ing.open_life_days = open_life_days
    ing.shelf_life_source = source

    return ShelfLifeConfirmation(
        ingredient_id=ing.id,
        name=ing.name,
        shelf_life_days_before=before_days,
        shelf_life_days_after=shelf_life_days,
        open_life_days_after=ing.open_life_days,
        transit_buffer_days=ing.transit_buffer_days,
        source_before=before_source,
        source_after=source,
        usable_days_after=shelf_life_days - ing.transit_buffer_days,
    )


@dataclass(frozen=True)
class UnconfirmedShelfLife:
    """One ingredient still on a guessed shelf life, with how much it matters."""

    ingredient_id: int
    name: str
    unit: str
    storage: str
    shelf_life_days: int | None
    transit_buffer_days: int
    usable_days: int | None
    movement_count: int


def unconfirmed_shelf_lives(
    session: Session, *, limit: int | None = None
) -> list[UnconfirmedShelfLife]:
    """Ingredients whose shelf life is still a seeded guess, busiest first.

    Ordered by how often the ingredient actually moves, because confirming the
    shelf life of something nobody buys changes nothing, and the doctor's advice
    is to start with the perishables that really move.
    """
    from sqlalchemy import func

    from cafeops.db.models.stock import StockMovement

    moves = (
        select(StockMovement.ingredient_id, func.count().label("n"))
        .group_by(StockMovement.ingredient_id)
        .subquery()
    )
    stmt = (
        select(Ingredient, func.coalesce(moves.c.n, 0).label("n"))
        .outerjoin(moves, moves.c.ingredient_id == Ingredient.id)
        .where(
            Ingredient.shelf_life_days.isnot(None),
            Ingredient.shelf_life_source == PriceSource.ESTIMATE,
        )
        .order_by(func.coalesce(moves.c.n, 0).desc(), Ingredient.name)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    out: list[UnconfirmedShelfLife] = []
    for ing, n in session.execute(stmt):
        usable = (
            None if ing.shelf_life_days is None else ing.shelf_life_days - ing.transit_buffer_days
        )
        out.append(
            UnconfirmedShelfLife(
                ingredient_id=ing.id,
                name=ing.name,
                unit=ing.unit.value,
                storage=ing.storage.value,
                shelf_life_days=ing.shelf_life_days,
                transit_buffer_days=ing.transit_buffer_days,
                usable_days=usable,
                movement_count=int(n),
            )
        )
    return out


def placeholder_suppliers(session: Session) -> list[Supplier]:
    """Suppliers whose terms nobody has confirmed. Every order built on one inherits that."""
    return list(
        session.scalars(
            select(Supplier)
            .where(Supplier.terms_are_placeholders.is_(True))
            .order_by(Supplier.name)
        )
    )


__all__ = [
    "ConfirmationRefused",
    "ShelfLifeConfirmation",
    "SupplierConfirmation",
    "SupplierTerms",
    "UnconfirmedShelfLife",
    "confirm_shelf_life",
    "confirm_supplier_terms",
    "placeholder_suppliers",
    "unconfirmed_shelf_lives",
]
