"""Write stock off by hand: gone off, spilled, drunk by staff, or something else.

Spec C11 (stock-orders-suppliers.md) and DATA-MODEL 3.1. Four decisions, each load-bearing:

1. **"Went out of date" is a `WASTE` movement with `reason_code = WENT_OFF`, never an
   `EXPIRED` one.** `EXPIRED` is *derived*: the expiry sweep writes it from batch state and
   `rebuild_batches(purge=True)` deletes every one of them before replaying the ledger
   (invariant 12's one exception). A human's write-off stored as `EXPIRED` would be erased
   by the next rebuild. `WASTE`/`STAFF` rows are never purged.

2. **Oldest expiry first.** The stock that goes in the bin is the stock nearest its date,
   which is what `domain.stock.allocate_fifo` already takes, so the batches drawn are the
   ones a person at the fridge would have picked. One movement per batch drawn, each
   carrying its `batch_id`, so the loss is valued at what THAT lot cost.

3. **A shortfall is recorded, not refused.** If the batches cannot cover the quantity,
   the remainder is written as one more movement with no batch. Somebody physically threw
   the stock away; refusing would lose a real loss because the records were already wrong,
   and the unbatched remainder is itself the signal that a count is due.

4. **Signed, and coded.** `recorded_by` is required (DECISIONS 6) and `OTHER` requires a
   note: both are also CHECK constraints (`write_off_signed`, `other_needs_note`), so the
   refusal here is the readable version of what the database would do anyway.

The ledger stays append-only (invariant 12): this inserts movements and decrements
`stock_batch.qty_remaining` through the repository that FIFO depletion uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from cafeops.db.models import (
    Ingredient,
    MovementType,
    PriceSource,
    Unit,
    WriteOffReason,
)
from cafeops.db.repositories.batch import SqlBatchRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.stock import allocate_fifo
from cafeops.domain.types import MovementSpec

__all__ = ["WriteOffOutcome", "WriteOffRefused", "record_write_off"]

#: `stock_movement.ref_type` for a human write-off.
WRITE_OFF_REF = "write_off"

_NOTE_MAX = 400


class WriteOffRefused(ValueError):
    """Nothing was written off, and the message says what to fix. Shown verbatim."""


@dataclass(frozen=True, slots=True)
class DrawnBatch:
    batch_id: int
    qty: Decimal
    unit_cost_pence: Decimal | None


@dataclass(frozen=True, slots=True)
class WriteOffOutcome:
    ingredient_id: int
    ingredient_name: str
    unit: Unit
    qty: Decimal
    reason: WriteOffReason
    movement_type: MovementType
    movement_ids: tuple[int, ...]
    batches_drawn: tuple[DrawnBatch, ...]
    #: Quantity no batch accounted for. Written as its own unbatched movement.
    shortfall_qty: Decimal
    #: Exact pence: sum of drawn qty x that batch's unit cost, plus the shortfall at the
    #: ingredient's cached cost. None when any part has no price (invariant 8).
    value_pence: Decimal | None
    #: True when any part of the value came from the ingredient's cached cost rather than
    #: a batch's own, or the cache is an ESTIMATE.
    value_is_estimate: bool
    recorded_at: datetime
    recorded_by: str


def record_write_off(
    session: Session,
    *,
    ingredient_id: int,
    qty: Decimal,
    reason: WriteOffReason,
    recorded_by: str,
    note: str | None = None,
    at: datetime | None = None,
) -> WriteOffOutcome:
    """Take `qty` off the shelf for `reason`, oldest-expiring batch first."""
    if isinstance(qty, float):
        raise TypeError("qty must be Decimal, not float (invariant 11)")
    if not recorded_by.strip():
        raise WriteOffRefused(
            "recorded_by is required: a write-off is somebody's word that the stock went in the bin"
        )
    if qty <= 0:
        raise WriteOffRefused(f"a write-off must be a positive quantity, got {qty}")
    clean_note = (note or "").strip() or None
    if reason is WriteOffReason.OTHER and clean_note is None:
        raise WriteOffRefused(
            "say what happened: 'Other' needs a note, or nobody can tell this loss from a "
            "measuring problem later"
        )
    at = at or datetime.now(UTC)
    if at.tzinfo is None:
        raise WriteOffRefused("the write-off time must be timezone-aware")

    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if ingredient.unit is Unit.EACH and qty != qty.to_integral_value():
        raise WriteOffRefused(
            f"{ingredient.name} is counted in whole units: {qty} is not a whole number"
        )

    movement_type = MovementType.STAFF if reason is WriteOffReason.STAFF else MovementType.WASTE
    repo = SqlBatchRepository(session)
    open_life = repo.open_life_days(ingredient_id)
    batches = repo.open_batches(ingredient_id, at=at)
    by_id = {b.batch_id: b for b in batches}

    allocations, shortfall = allocate_fifo(qty=qty, batches=batches, open_life_days=open_life)

    signed_by = recorded_by.strip()[:120]
    stock_repo = SqlStockRepository(session)
    text = _note(reason, clean_note, signed_by)
    movement_ids: list[int] = []
    drawn: list[DrawnBatch] = []
    value: Decimal | None = Decimal("0")
    estimate = False
    cached = ingredient.current_cost_pence_per_unit
    cached_is_estimate = ingredient.current_cost_source is PriceSource.ESTIMATE

    for allocation in allocations:
        if allocation.qty <= 0:
            continue
        movement_ids.append(
            stock_repo.append_movement(
                MovementSpec(
                    ingredient_id=ingredient_id,
                    batch_id=allocation.batch_id,
                    type=movement_type,
                    qty=-allocation.qty,
                    occurred_at=at,
                    ref_type=WRITE_OFF_REF,
                    note=text,
                    reason_code=reason,
                    recorded_by=signed_by,
                )
            )
        )
        if allocation.batch_id is not None:
            spec = by_id.get(allocation.batch_id)
            unit_cost = spec.unit_cost_pence if spec is not None else None
            drawn.append(
                DrawnBatch(
                    batch_id=allocation.batch_id, qty=allocation.qty, unit_cost_pence=unit_cost
                )
            )
            # A batch costed at zero is a batch nobody priced (the count-surplus and
            # opening batches fall back to 0 when the cache is empty). Zero is not a
            # price, so it values the loss at the cache instead, flagged.
            if unit_cost is not None and unit_cost > 0:
                if cached_is_estimate:
                    estimate = True
                value = None if value is None else value + allocation.qty * unit_cost
                continue
        if cached is None:
            value = None
        else:
            estimate = True
            value = None if value is None else value + allocation.qty * cached

    repo.apply_allocations([a for a in allocations if a.batch_id is not None], at=at)
    session.flush()

    return WriteOffOutcome(
        ingredient_id=ingredient_id,
        ingredient_name=ingredient.name,
        unit=ingredient.unit,
        qty=qty,
        reason=reason,
        movement_type=movement_type,
        movement_ids=tuple(movement_ids),
        batches_drawn=tuple(drawn),
        shortfall_qty=shortfall,
        value_pence=value,
        value_is_estimate=estimate,
        recorded_at=at,
        recorded_by=signed_by,
    )


_LABEL = {
    WriteOffReason.WENT_OFF: "went out of date",
    WriteOffReason.SPILLED: "spilled / dropped",
    WriteOffReason.STAFF: "staff drinks",
    WriteOffReason.OTHER: "other",
}


def _note(reason: WriteOffReason, note: str | None, by: str) -> str:
    head = f"written off ({_LABEL[reason]}) by {by}"
    text = f"{head}: {note}" if note else head
    if len(text) > _NOTE_MAX:
        text = text[: _NOTE_MAX - 4].rstrip() + " ..."
    return text
