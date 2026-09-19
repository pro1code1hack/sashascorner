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

from sqlalchemy.orm import Session

from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.stock import theoretical_on_hand
from cafeops.domain.types import IngredientSnapshot, OnHand, Tier


@dataclass(frozen=True, slots=True)
class StockReading:
    ingredient: IngredientSnapshot
    on_hand: OnHand

    @property
    def is_anchored(self) -> bool:
        """False when no physical count sits behind the number."""
        return self.on_hand.has_count_basis


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
            )
        )
    return readings
