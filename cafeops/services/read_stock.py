"""Read theoretical on-hand for reporting.

Not in the spec's services list. It exists because invariant 4 -- every stock
figure shown to a user is labelled theoretical or counted -- is easier to keep if
exactly one place assembles that answer, rather than the CLI, the bot digest and
the read-only API each doing their own joins and each getting the labelling
subtly different.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import StockBatch
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.stock import theoretical_on_hand
from cafeops.domain.types import BatchSpec, IngredientSnapshot, OnHand, Tier


@dataclass(frozen=True, slots=True)
class StockReading:
    ingredient: IngredientSnapshot
    on_hand: OnHand
    #: Open batches, soonest-expiring first. Spec 16 requires these alongside the
    #: theoretical figure, and they are the only place spoilage risk is visible.
    batches: tuple[BatchSpec, ...] = ()
    #: Unopened shelf life in days; None when the ingredient does not expire.
    shelf_life_days: int | None = None
    open_life_days: int | None = None

    @property
    def is_anchored(self) -> bool:
        """False when no physical count sits behind the number."""
        return self.on_hand.has_count_basis

    @property
    def batch_qty(self) -> Decimal:
        return sum((b.qty_remaining for b in self.batches), Decimal("0"))

    @property
    def soonest_expiry_days(self) -> int | None:
        """Days until the first batch expires. None when nothing can expire."""
        # Collect then filter, rather than filtering on effective_expiry and trusting
        # days_left to be non-None -- the correlation is real but invisible to a type
        # checker, and a cast here would be hiding the assumption rather than stating it.
        days = [b.days_left(self.on_hand.as_of, self.open_life_days) for b in self.batches]
        known = [d for d in days if d is not None]
        return min(known) if known else None

    @property
    def batch_coverage_gap(self) -> Decimal:
        """Theoretical on-hand minus what the batches can account for.

        A positive gap means stock exists in the ledger that no batch explains --
        usually a delivery entered as a movement without a batch, or an adjustment.
        Worth surfacing rather than hiding: FIFO and expiry can only see batched
        stock, so an unbatched surplus can never expire and never will be counted
        as waste.
        """
        return self.on_hand.qty - self.batch_qty


def read_on_hand(
    session: Session,
    *,
    as_of: datetime,
    tiers: tuple[Tier, ...] | None = None,
    include_untracked: bool = False,
) -> list[StockReading]:
    ingredient_repo = SqlIngredientRepository(session)
    stock_repo = SqlStockRepository(session)

    ingredients = (
        ingredient_repo.list_all()
        if include_untracked
        else ingredient_repo.list_tracked(tiers=tiers)
    )
    if include_untracked and tiers:
        ingredients = [i for i in ingredients if i.tier in tiers]

    readings: list[StockReading] = []
    for ingredient in ingredients:
        latest = stock_repo.latest_count(ingredient.id, before=as_of)
        after = latest[1] if latest else None
        movement_sum, movement_count = stock_repo.movement_sum_between(
            ingredient.id, after=after, until=as_of
        )
        shelf_life_days, open_life_days = _shelf_life(session, ingredient.id)
        readings.append(
            StockReading(
                ingredient=ingredient,
                on_hand=theoretical_on_hand(
                    ingredient_id=ingredient.id,
                    as_of=as_of,
                    latest_count=latest,
                    movement_sum=movement_sum,
                    movement_count=movement_count,
                ),
                batches=_open_batches(session, ingredient.id, as_of, open_life_days),
                shelf_life_days=shelf_life_days,
                open_life_days=open_life_days,
            )
        )
    return readings


def _shelf_life(session: Session, ingredient_id: int) -> tuple[int | None, int | None]:
    from cafeops.db.models import Ingredient

    row = session.get(Ingredient, ingredient_id)
    if row is None:
        return None, None
    return row.shelf_life_days, row.open_life_days


def _open_batches(
    session: Session, ingredient_id: int, as_of: datetime, open_life_days: int | None
) -> tuple[BatchSpec, ...]:
    """Batches with stock left as of `as_of`, soonest effective expiry first."""
    rows = session.scalars(
        select(StockBatch).where(
            StockBatch.ingredient_id == ingredient_id,
            StockBatch.received_at <= as_of,
            StockBatch.qty_remaining > 0,
        )
    )
    specs = [
        BatchSpec(
            batch_id=b.id,
            ingredient_id=b.ingredient_id,
            qty_remaining=b.qty_remaining,
            received_at=b.received_at,
            expires_at=b.expires_at,
            opened_at=b.opened_at,
            unit_cost_pence=b.unit_cost_pence,
        )
        for b in rows
    ]

    def key(spec: BatchSpec) -> tuple[int, float, int]:
        expiry = spec.effective_expiry(open_life_days)
        if expiry is None:
            return (1, spec.received_at.timestamp(), spec.batch_id)
        return (0, expiry.timestamp(), spec.batch_id)

    return tuple(sorted(specs, key=key))
