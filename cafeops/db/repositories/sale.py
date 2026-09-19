"""Sale line queries, including the un-expanded work queue."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from cafeops.db.models import Sale
from cafeops.domain.types import SaleLine


class SqlSaleRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def pending_expansion(self, *, limit: int | None = None) -> list[SaleLine]:
        """Sales not yet expanded into the ledger.

        Voided lines are excluded here rather than filtered downstream: a voided
        receipt must never deplete stock, and leaving them queued would mean every
        run reconsiders rows it will always reject.
        """
        stmt = (
            select(Sale)
            .where(Sale.expanded_at.is_(None), Sale.voided.is_(False))
            .order_by(Sale.sold_at, Sale.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [
            SaleLine(
                sale_id=s.id,
                menu_item_id=s.menu_item_id,
                qty=s.qty,
                sold_at=s.sold_at,
                modifier_ids=tuple(s.applied_modifiers or ()),
            )
            for s in self.session.scalars(stmt)
        ]

    def mark_expanded(self, sale_ids: Sequence[int], at: datetime) -> None:
        if not sale_ids:
            return
        self.session.execute(update(Sale).where(Sale.id.in_(list(sale_ids))).values(expanded_at=at))

    def latest_sold_at(self) -> datetime | None:
        return self.session.scalar(select(func.max(Sale.sold_at)))

    def count(self) -> int:
        return int(self.session.scalar(select(func.count(Sale.id))) or 0)
