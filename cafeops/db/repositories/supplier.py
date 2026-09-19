"""Supplier and supplier-product queries. No business logic.

The terms this repository hands out -- lead time, delivery weekdays, minimum order --
drive the whole cover window in `domain/ordering.py`. For **CakeSmiths and Cups
Direct they are invented placeholders** (`ARCHITECTURE.md` 8.2). Nothing here can fix
that; it is flagged at import, in `cafeops simulate`, and again on the way out of
`list_all`, because an order size computed from a guessed lead time is a guess.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import Supplier, SupplierProduct
from cafeops.domain.types import PackChoice, SupplierSpec

#: DEPRECATED, and wrong twice over. v2 added `supplier.terms_are_placeholders` and
#: SIX of the eight suppliers carry it, not two -- and the name here is "CakeSmiths"
#: where the seed writes "Cakesmiths", so this set never even matched the one supplier
#: it was right about. Kept only so existing importers do not break; ask
#: `has_placeholder_terms` or `SourcingRepository.terms(...).terms_are_placeholders`,
#: both of which read the column.
PLACEHOLDER_TERMS: frozenset[str] = frozenset({"CakeSmiths", "Cups Direct"})


def _weekdays(raw: object) -> tuple[int, ...]:
    """JSON list -> ISO weekday tuple. EMPTY means "any day" (walk-in retail)."""
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"supplier.delivery_weekdays must be a JSON list, got {raw!r}")
    return tuple(sorted({int(day) for day in raw}))


def _spec(row: Supplier) -> SupplierSpec:
    return SupplierSpec(
        id=row.id,
        name=row.name,
        lead_time_days=row.lead_time_days,
        delivery_weekdays=_weekdays(row.delivery_weekdays),
        min_order_pence=row.min_order_pence,
        order_channel=row.order_channel,
    )


def _pack(row: SupplierProduct) -> PackChoice:
    return PackChoice(
        supplier_product_id=row.id,
        ingredient_id=row.ingredient_id,
        pack_size=row.pack_size,
        pack_unit=row.pack_unit,
        price_pence=row.price_pence,
        sku=row.sku,
    )


class SqlSupplierRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, supplier_id: int) -> SupplierSpec | None:
        row = self.session.get(Supplier, supplier_id)
        return None if row is None else _spec(row)

    def get_by_name(self, name: str) -> SupplierSpec | None:
        row = self.session.scalar(select(Supplier).where(Supplier.name == name))
        return None if row is None else _spec(row)

    def list_all(self) -> list[SupplierSpec]:
        return [_spec(r) for r in self.session.scalars(select(Supplier).order_by(Supplier.name))]

    def has_placeholder_terms(self, supplier_id: int) -> bool:
        """True when this supplier's lead time, minimum or schedule were never confirmed.

        Reads the `terms_are_placeholders` COLUMN. It used to match the name against a
        hard-coded pair, which is exactly the bug that column exists to prevent: the pair
        was out of date (six suppliers are placeholders in v2, not two) and one of the two
        names did not match the seed's spelling, so the check answered False for every
        supplier whose terms are invented.
        """
        row = self.session.get(Supplier, supplier_id)
        return row is not None and row.terms_are_placeholders

    def preferred_pack(self, ingredient_id: int, supplier_id: int) -> PackChoice | None:
        """The pack to order this ingredient in from this supplier.

        `is_preferred` first, then the cheapest pack, then the lowest id -- so the
        answer is stable across runs rather than whatever the query planner returned.
        """
        rows = list(
            self.session.scalars(
                select(SupplierProduct).where(
                    SupplierProduct.ingredient_id == ingredient_id,
                    SupplierProduct.supplier_id == supplier_id,
                )
            )
        )
        if not rows:
            return None
        rows.sort(key=lambda r: (not r.is_preferred, r.price_pence, r.id))
        return _pack(rows[0])

    def packs_for_supplier(self, supplier_id: int) -> list[PackChoice]:
        rows = self.session.scalars(
            select(SupplierProduct)
            .where(SupplierProduct.supplier_id == supplier_id)
            .order_by(SupplierProduct.ingredient_id, SupplierProduct.id)
        )
        return [_pack(r) for r in rows]

    def supplier_id_for_ingredient(self, ingredient_id: int) -> int | None:
        """Which supplier stocks this ingredient. One each in the seeded data."""
        return self.session.scalar(
            select(SupplierProduct.supplier_id)
            .where(SupplierProduct.ingredient_id == ingredient_id)
            .order_by(SupplierProduct.is_preferred.desc(), SupplierProduct.id)
            .limit(1)
        )
