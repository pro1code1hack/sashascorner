"""Stock ledger and count queries.

The window arithmetic for theoretical on-hand happens here, in SQL, so
domain/stock.py stays a pure statement of spec 5.1's formula.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import StockCount, StockMovement
from cafeops.domain.types import ConsumptionPoint, MovementSpec, MovementType


class SqlStockRepository:
    def __init__(self, session: Session, *, tz: ZoneInfo | None = None) -> None:
        self.session = session
        self.tz = tz or settings.tz

    def latest_count(
        self, ingredient_id: int, *, before: datetime
    ) -> tuple[Decimal, datetime] | None:
        row = self.session.execute(
            select(StockCount.counted_qty, StockCount.counted_at)
            .where(StockCount.ingredient_id == ingredient_id, StockCount.counted_at <= before)
            .order_by(StockCount.counted_at.desc(), StockCount.id.desc())
            .limit(1)
        ).first()
        return None if row is None else (row[0], row[1])

    def movement_sum_between(
        self, ingredient_id: int, *, after: datetime | None, until: datetime
    ) -> tuple[Decimal, int]:
        """Signed sum over (after, until]. Summed in Python, deliberately.

        The qty column is TEXT on SQLite (see db/types.Qty), so a SQL SUM() would
        coerce through float and lose exactness. At one cafe's volume, summing
        exact Decimals in Python is correct and fast enough. On Postgres the
        column is a real NUMERIC and this can become a SQL SUM if it needs to.
        """
        stmt = select(StockMovement.qty).where(
            StockMovement.ingredient_id == ingredient_id,
            StockMovement.occurred_at <= until,
        )
        if after is not None:
            stmt = stmt.where(StockMovement.occurred_at > after)
        qtys = list(self.session.scalars(stmt))
        return sum(qtys, Decimal("0")), len(qtys)

    def append_movements(self, movements: Iterable[MovementSpec]) -> int:
        rows = [
            StockMovement(
                ingredient_id=m.ingredient_id,
                type=m.type,
                qty=m.qty,
                occurred_at=m.occurred_at,
                ref_type=m.ref_type,
                ref_id=m.ref_id,
                note=m.note,
            )
            for m in movements
        ]
        if rows:
            self.session.add_all(rows)
        return len(rows)

    def record_count(
        self,
        ingredient_id: int,
        counted_qty: Decimal,
        counted_at: datetime,
        counted_by: str,
        note: str | None = None,
    ) -> int:
        row = StockCount(
            ingredient_id=ingredient_id,
            counted_qty=counted_qty,
            counted_at=counted_at,
            counted_by=counted_by,
            note=note,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def daily_consumption(
        self,
        ingredient_id: int,
        *,
        since: date,
        until: date,
        movement_types: Sequence[MovementType] = (MovementType.SALE,),
    ) -> list[ConsumptionPoint]:
        """Positive consumption magnitudes per LOCAL calendar day.

        Local, not UTC: a 23:30 BST sale belongs to that trading day, and a
        forecast keyed on UTC days would smear weekday patterns across midnight --
        corrupting the dow_factor for exactly the late trade that distinguishes a
        Friday from a Monday.
        """
        start = datetime.combine(since, time.min, tzinfo=self.tz)
        end = datetime.combine(until, time.max, tzinfo=self.tz)
        rows = self.session.execute(
            select(StockMovement.occurred_at, StockMovement.qty).where(
                StockMovement.ingredient_id == ingredient_id,
                StockMovement.type.in_(list(movement_types)),
                StockMovement.occurred_at >= start,
                StockMovement.occurred_at <= end,
            )
        ).all()

        buckets: dict[date, Decimal] = {}
        for occurred_at, qty in rows:
            day = occurred_at.astimezone(self.tz).date()
            buckets[day] = buckets.get(day, Decimal("0")) + (-qty)
        return [ConsumptionPoint(day=d, qty=buckets[d]) for d in sorted(buckets)]

    def movement_count(self) -> int:
        return int(self.session.scalar(select(func.count(StockMovement.id))) or 0)
