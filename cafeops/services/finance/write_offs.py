"""Stock written off, valued in pence. Finance spec 2.7, 4.3(c).

Σ over EXPIRED / WASTE movements in the window of |qty| x unit cost. The batch's own
unit cost is used when the movement names a batch; otherwise the ingredient's current
cost, which is an estimate. A movement with no cost at all makes the total unknown
(`None`) rather than smaller -- invariant 8.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.clock import local_day_bounds
from cafeops.config import settings
from cafeops.db.models.batch import StockBatch
from cafeops.db.models.ingredient import Ingredient
from cafeops.db.models.stock import StockMovement
from cafeops.domain.enums import MovementType, PriceSource

__all__ = ["WriteOffs", "valued_write_offs"]


@dataclass(frozen=True, slots=True)
class WriteOffs:
    pence: int | None
    is_estimate: bool
    movements: int
    unpriced: int


def valued_write_offs(session: Session, since: date, until: date) -> WriteOffs:
    tz = settings.tz
    start, end = local_day_bounds(since, until, tz=tz)
    rows = session.execute(
        select(StockMovement, StockBatch, Ingredient)
        .join(Ingredient, Ingredient.id == StockMovement.ingredient_id)
        .outerjoin(StockBatch, StockBatch.id == StockMovement.batch_id)
        .where(
            StockMovement.type.in_((MovementType.EXPIRED, MovementType.WASTE)),
            StockMovement.occurred_at >= start,
            StockMovement.occurred_at < end,
        )
    ).all()
    total = Decimal(0)
    estimate = False
    unpriced = 0
    for mv, batch, ing in rows:
        qty = abs(mv.qty)
        if batch is not None:
            cost = batch.unit_cost_pence
            # A batch costed from an estimated price is still an estimate.
            if ing.current_cost_source is PriceSource.ESTIMATE:
                estimate = True
        elif ing.current_cost_pence_per_unit is not None:
            cost = ing.current_cost_pence_per_unit
            estimate = True
        else:
            unpriced += 1
            continue
        total += qty * cost
    pence = None if unpriced else int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return WriteOffs(pence=pence, is_estimate=estimate, movements=len(rows), unpriced=unpriced)
