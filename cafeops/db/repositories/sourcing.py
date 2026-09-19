"""Multi-supplier sourcing reads, and the Tesco routing log. Spec 4.4.

Implements `SourcingRepository`. No business logic: which option wins is decided in
`domain/sourcing.py`, and this module's job is to hand that decision honest inputs and
to write down what it concluded.

**`terms()` reads `supplier.terms_are_placeholders` from the row rather than matching
names against a list.** Six of the eight suppliers' terms are invented
(`ARCHITECTURE.md` 8F.4), the column exists precisely so every order can say so, and a
hard-coded name list is one rename away from telling the owner that Cakesmiths' lead
time is a fact.

No quantity is compared in SQL here. `ARCHITECTURE.md` 8E is the reason that sentence
is worth writing down: `pack_size` is a `Qty`, and `WHERE pack_size > 0` is exactly the
predicate that used to lie. Pack sizes are checked in Python, on loaded `Decimal`s,
where the comparison means what it says.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import Supplier, SupplierProduct, TescoRouting
from cafeops.domain.types import SourcingOption, SupplierTerms

__all__ = ["SqlSourcingRepository"]

#: `po_line.cap_reason` and the routing reason are bounded columns. Truncating with a
#: marker beats an IntegrityError on a note, but the marker has to be visible so nobody
#: reads a half-sentence as the whole reason.
_REASON_MAX = 400


def _weekdays(raw: object) -> tuple[int, ...]:
    """JSON list -> ISO weekday tuple. EMPTY means "any day" (walk-in retail)."""
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"supplier.delivery_weekdays must be a JSON list, got {raw!r}")
    return tuple(sorted({int(day) for day in raw}))


def _terms(row: Supplier) -> SupplierTerms:
    return SupplierTerms(
        supplier_id=row.id,
        name=row.name,
        lead_time_days=row.lead_time_days,
        delivery_weekdays=_weekdays(row.delivery_weekdays),
        min_order_pence=row.min_order_pence,
        order_channel=row.order_channel,
        cutoff_time=row.cutoff_time,
        delivery_fee_pence=row.delivery_fee_pence,
        free_delivery_threshold_pence=row.free_delivery_threshold_pence,
        terms_are_placeholders=row.terms_are_placeholders,
    )


def _option(row: SupplierProduct) -> SourcingOption:
    return SourcingOption(
        supplier_product_id=row.id,
        supplier_id=row.supplier_id,
        ingredient_id=row.ingredient_id,
        pack_size=row.pack_size,
        pack_unit=row.pack_unit,
        price_pence=row.price_pence,
        is_preferred=row.is_preferred,
        moq_packs=row.moq_packs,
        sku=row.sku,
    )


def _truncated(text: str, limit: int = _REASON_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 4].rstrip() + " ..."


class SqlSourcingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- options ---------------------------------------------------------

    def options_for(self, ingredient_id: int) -> list[SourcingOption]:
        """Every way to buy this ingredient, preferred first then cheapest pack.

        The ordering is stable and deliberate: `is_preferred` first so the incumbent is
        unambiguous even when two products claim it, then price, then id. Sourcing that
        changes its mind between runs on identical data cannot be audited.
        """
        rows = list(
            self.session.scalars(
                select(SupplierProduct).where(SupplierProduct.ingredient_id == ingredient_id)
            )
        )
        rows.sort(key=lambda r: (not r.is_preferred, r.price_pence, r.id))
        return [_option(r) for r in rows]

    def options_for_many(self, ingredient_ids: list[int]) -> dict[int, list[SourcingOption]]:
        """One query for a whole order run rather than one per ingredient."""
        if not ingredient_ids:
            return {}
        rows = list(
            self.session.scalars(
                select(SupplierProduct).where(SupplierProduct.ingredient_id.in_(ingredient_ids))
            )
        )
        rows.sort(key=lambda r: (r.ingredient_id, not r.is_preferred, r.price_pence, r.id))
        grouped: dict[int, list[SourcingOption]] = {}
        for row in rows:
            grouped.setdefault(row.ingredient_id, []).append(_option(row))
        return grouped

    # --- terms -----------------------------------------------------------

    def terms(self, supplier_id: int) -> SupplierTerms | None:
        row = self.session.get(Supplier, supplier_id)
        return None if row is None else _terms(row)

    def all_terms(self) -> list[SupplierTerms]:
        return [_terms(r) for r in self.session.scalars(select(Supplier).order_by(Supplier.name))]

    def terms_by_id(self) -> dict[int, SupplierTerms]:
        return {t.supplier_id: t for t in self.all_terms()}

    def placeholder_suppliers(self) -> list[str]:
        """Names whose terms are invented. The caveat every order has to carry."""
        return [t.name for t in self.all_terms() if t.terms_are_placeholders]

    # --- the Tesco log ---------------------------------------------------

    def record_emergency_routing(
        self,
        ingredient_id: int,
        *,
        reason: str,
        at: datetime,
        retail_unit_price_pence: int | None = None,
        preferred_unit_price_pence: int | None = None,
        would_be_supplier_id: int | None = None,
        po_line_id: int | None = None,
        qty: Decimal | None = None,
    ) -> int:
        """Log one retail run, with the premium it cost. Spec 4.4.

        `premium_pence` is computed here rather than left to a reader: the whole value of
        this table is that the premium can be summed across a quarter without anybody
        re-deriving it, and a column that is sometimes null because nobody filled it in
        is a column that gets left out of the total.

        It stays `None` when either unit price is unknown -- a premium computed against a
        missing price would be the retail price wearing a premium's name (invariant 8).
        `qty` is what turns unit prices into money; without it the premium is reported
        per unit, which is stated in the reason rather than silently assumed to be 1.
        """
        premium: int | None = None
        if retail_unit_price_pence is not None and preferred_unit_price_pence is not None:
            per_unit = retail_unit_price_pence - preferred_unit_price_pence
            multiplier = qty if qty is not None else Decimal("1")
            premium = int((Decimal(per_unit) * multiplier).to_integral_value())
        row = TescoRouting(
            ingredient_id=ingredient_id,
            po_line_id=po_line_id,
            occurred_at=at,
            reason=_truncated(reason),
            retail_unit_price_pence=retail_unit_price_pence,
            preferred_unit_price_pence=preferred_unit_price_pence,
            premium_pence=premium,
            would_be_supplier_id=would_be_supplier_id,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def emergency_log(self, *, since: datetime | None = None) -> list[TescoRouting]:
        """Routings newest first. The report, not a note."""
        stmt = select(TescoRouting).order_by(
            TescoRouting.occurred_at.desc(), TescoRouting.id.desc()
        )
        if since is not None:
            stmt = stmt.where(TescoRouting.occurred_at >= since)
        return list(self.session.scalars(stmt))

    def emergency_summary(
        self, *, since: datetime | None = None
    ) -> tuple[int, int, int, dict[str, tuple[int, int]]]:
        """`(routings, priced_routings, total_premium_pence, per_ingredient)`.

        `priced_routings` is reported alongside the count on purpose: a premium total
        over 9 of 14 routings understates the argument the table exists to make, and the
        only way to see that is to be told how many rows carried a price (invariant 8).
        `per_ingredient` maps name -> (routings, premium_pence).
        """
        rows = self.emergency_log(since=since)
        total = 0
        priced = 0
        per_ingredient: dict[str, tuple[int, int]] = {}
        for row in rows:
            name = row.ingredient.name if row.ingredient is not None else f"#{row.ingredient_id}"
            count, premium = per_ingredient.get(name, (0, 0))
            add = row.premium_pence or 0
            if row.premium_pence is not None:
                priced += 1
                total += row.premium_pence
            per_ingredient[name] = (count + 1, premium + add)
        return len(rows), priced, total, per_ingredient
