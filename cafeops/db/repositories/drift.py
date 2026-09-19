"""Drift observation queries. No business logic -- the gate lives in domain/tiers.py.

Observations are append-only (ARCHITECTURE.md 2.3): drift is stored as measured, not
recomputed on demand, so retuning `waste_factor` cannot retroactively change what a
decision was made on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import DriftObservation, StockCount, StockMovement
from cafeops.domain.types import MovementType


@dataclass(frozen=True, slots=True)
class DriftHistoryRow:
    """One stored observation, for the drift report. Not a domain type: nothing in
    `domain/` needs it, and the gate takes bare percentages."""

    id: int
    ingredient_id: int
    stock_count_id: int
    theoretical_qty: Decimal
    counted_qty: Decimal
    drift_pct: float
    waste_factor_at_count: Decimal
    observed_at: datetime


class SqlDriftRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        ingredient_id: int,
        stock_count_id: int,
        theoretical_qty: Decimal,
        counted_qty: Decimal,
        drift_pct: float,
        waste_factor_at_count: Decimal,
        observed_at: datetime,
    ) -> int:
        row = DriftObservation(
            ingredient_id=ingredient_id,
            stock_count_id=stock_count_id,
            theoretical_qty=theoretical_qty,
            counted_qty=counted_qty,
            drift_pct=drift_pct,
            waste_factor_at_count=waste_factor_at_count,
            observed_at=observed_at,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def recent_drift_pcts(self, ingredient_id: int, *, limit: int = 2) -> list[float]:
        """Most recent first. The auto-order gate reads this.

        Ordered by `observed_at` then `id`, so a backfill that writes several
        observations in one transaction still comes back in measurement order rather
        than insertion order.
        """
        stmt = (
            select(DriftObservation.drift_pct)
            .where(DriftObservation.ingredient_id == ingredient_id)
            .order_by(DriftObservation.observed_at.desc(), DriftObservation.id.desc())
            .limit(limit)
        )
        return [float(p) for p in self.session.scalars(stmt)]

    def history(self, ingredient_id: int, *, limit: int = 10) -> list[DriftHistoryRow]:
        stmt = (
            select(DriftObservation)
            .where(DriftObservation.ingredient_id == ingredient_id)
            .order_by(DriftObservation.observed_at.desc(), DriftObservation.id.desc())
            .limit(limit)
        )
        return [
            DriftHistoryRow(
                id=row.id,
                ingredient_id=row.ingredient_id,
                stock_count_id=row.stock_count_id,
                theoretical_qty=row.theoretical_qty,
                counted_qty=row.counted_qty,
                drift_pct=row.drift_pct,
                waste_factor_at_count=row.waste_factor_at_count,
                observed_at=row.observed_at,
            )
            for row in self.session.scalars(stmt)
        ]

    def observed_stock_count_ids(self) -> set[int]:
        """Counts that already carry an observation. Backfill is idempotent on this."""
        return set(self.session.scalars(select(DriftObservation.stock_count_id)))

    def counts_missing_observations(
        self, *, ingredient_id: int | None = None, limit: int | None = None
    ) -> list[tuple[int, int, Decimal, datetime]]:
        """(stock_count_id, ingredient_id, counted_qty, counted_at), oldest first.

        Counts taken before this module existed -- the demo seed writes 126 of them --
        have no observation. The backfill walks these in measurement order so each one's
        theoretical figure is anchored on the count that preceded it.
        """
        stmt = (
            select(
                StockCount.id,
                StockCount.ingredient_id,
                StockCount.counted_qty,
                StockCount.counted_at,
            )
            .outerjoin(DriftObservation, DriftObservation.stock_count_id == StockCount.id)
            .where(DriftObservation.id.is_(None))
            .order_by(StockCount.counted_at, StockCount.id)
        )
        if ingredient_id is not None:
            stmt = stmt.where(StockCount.ingredient_id == ingredient_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [(r[0], r[1], r[2], r[3]) for r in self.session.execute(stmt)]

    def consumption_between(
        self,
        ingredient_id: int,
        *,
        after: datetime | None,
        until: datetime,
        movement_types: Sequence[MovementType] = (MovementType.SALE,),
    ) -> Decimal:
        """Positive magnitude of depletion over (after, until].

        The waste-factor suggestion needs "how much did the ledger think we consumed",
        and it needs it over the exact count-to-count window rather than by calendar
        day, so `StockRepository.daily_consumption` is the wrong shape here. Summed in
        Python because the qty column is TEXT on SQLite (db/types.Qty) and a SQL SUM()
        would coerce through float.

        Belongs in `StockRepository` -- raised for the integrator rather than added to
        another agent's module.
        """
        stmt = select(StockMovement.qty).where(
            StockMovement.ingredient_id == ingredient_id,
            StockMovement.type.in_(list(movement_types)),
            StockMovement.occurred_at <= until,
        )
        if after is not None:
            stmt = stmt.where(StockMovement.occurred_at > after)
        total = sum(self.session.scalars(stmt), Decimal("0"))
        return -total if total < 0 else total
