"""Theoretical on-hand and the ledger entries that move it. Spec 5.1.

Phase 1 added the correction path (`reversal_movements`) alongside Phase 0's formula.
Drift lives in `domain/drift.py` and the auto-order gate in `domain/tiers.py`.

Two things this module deliberately does NOT do:

- **It writes no movement for a physical count.** A count is the source of truth and
  spec 5.1 re-anchors on it: the formula reads the latest count and sums only what
  happened after it, so a `COUNT_RESET` movement would be counted twice -- once as the
  new anchor and once as a delta on top of it. `MovementType.COUNT_RESET` exists for
  an explicit opening balance, not for reconciling a count.
- **It never amends a movement.** Invariant 9: the ledger is append-only, so undoing
  an expansion means appending equal-and-opposite `ADJUSTMENT` rows.

Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from cafeops.domain.types import (
    BatchSpec,
    DepletionAllocation,
    ExpiryLoss,
    IngredientSnapshot,
    MovementSpec,
    MovementType,
    OnHand,
    ResolvedLine,
    ShelfLifeSpec,
)

__all__ = [
    "CountReconciliation",
    "allocate_fifo",
    "apply_waste",
    "batch_expiry_for",
    "depletion_movements",
    "drift_attribution",
    "expiry_movements",
    "fifo_key",
    "find_expiry_losses",
    "reconcile_to_count",
    "reversal_movements",
    "theoretical_on_hand",
    "waste_factors_from",
]


def theoretical_on_hand(
    *,
    ingredient_id: int,
    as_of: datetime,
    latest_count: tuple[Decimal, datetime] | None,
    movement_sum: Decimal,
    movement_count: int = 0,
) -> OnHand:
    """on_hand = latest count before `as_of` + signed sum of movements after it.

    `movement_sum` must already be restricted to the half-open window
    (count.counted_at, as_of] by the caller. The repository does that in SQL so
    this stays a pure statement of the spec's formula.

    With no count on record the count term is zero and the result is a bare
    movement sum. That is a genuinely weaker number, and `OnHand.has_count_basis`
    reports it as such so nothing prints it as though it were anchored.
    """
    if latest_count is None:
        base, counted_at = Decimal("0"), None
    else:
        base, counted_at = latest_count

    return OnHand(
        ingredient_id=ingredient_id,
        qty=base + movement_sum,
        as_of=as_of,
        basis_count_qty=None if latest_count is None else base,
        basis_counted_at=counted_at,
        movement_sum=movement_sum,
        movement_count=movement_count,
    )


def apply_waste(qty: Decimal, waste_factor: Decimal) -> Decimal:
    """qty * (1 + waste_factor).

    INVARIANT 5: this belongs to stock depletion ONLY. Menu cost is the recipe
    without waste. Two different numbers, both needed, never conflated -- so this
    function exists separately rather than being folded into resolution.
    """
    return qty * (Decimal("1") + waste_factor)


def depletion_movements(
    *,
    sale_id: int,
    sold_qty: Decimal,
    sold_at: datetime,
    lines: Sequence[ResolvedLine],
    waste_factors: dict[int, Decimal],
    tracked_ingredient_ids: Iterable[int] | None = None,
) -> tuple[MovementSpec, ...]:
    """Turn one sold line into SALE ledger entries, one per tracked ingredient.

    Quantities are negated: consumption reduces stock. A refund arrives with a
    negative `sold_qty` and therefore produces a POSITIVE movement, which is what
    nets a refunded sale back out of the ledger.
    """
    tracked = None if tracked_ingredient_ids is None else set(tracked_ingredient_ids)
    movements: list[MovementSpec] = []

    for line in lines:
        if tracked is not None and line.ingredient_id not in tracked:
            continue
        consumed = apply_waste(
            sold_qty * line.qty, waste_factors.get(line.ingredient_id, Decimal("0"))
        )
        if consumed == 0:
            continue
        movements.append(
            MovementSpec(
                ingredient_id=line.ingredient_id,
                type=MovementType.SALE,
                qty=-consumed,
                occurred_at=sold_at,
                ref_type="sale",
                ref_id=sale_id,
            )
        )
    return tuple(movements)


def reversal_movements(
    originals: Sequence[MovementSpec],
    *,
    occurred_at: datetime,
    note: str,
    ref_type: str = "reversal",
) -> tuple[MovementSpec, ...]:
    """Equal-and-opposite `ADJUSTMENT` rows that undo `originals`.

    INVARIANT 9: the ledger is append-only. A receipt voided after it was expanded, a
    double-expanded window, a recipe that was wrong when the sale resolved -- none of
    them may update or delete the rows already written. They append the inverse, and
    the sum comes out right while the history stays readable.

    One reversal per original rather than one per ingredient, so the correction can be
    traced back to the exact row it answers. `ref_id` is carried over from the original
    and `ref_type` is retyped (`sale` -> `sale_reversal` by convention at the call
    site) so a later run can tell a reversal from the thing it reversed and never
    reverse it twice.
    """
    return tuple(
        MovementSpec(
            ingredient_id=original.ingredient_id,
            type=MovementType.ADJUSTMENT,
            qty=-original.qty,
            occurred_at=occurred_at,
            ref_type=ref_type,
            ref_id=original.ref_id,
            note=note,
        )
        for original in originals
        if original.qty != 0
    )


def waste_factors_from(snapshots: Iterable[IngredientSnapshot]) -> dict[int, Decimal]:
    return {s.id: s.waste_factor for s in snapshots}


# ==========================================================================
# FIFO batch depletion and expiry (spec 4.1, 5.1)
# ==========================================================================


def fifo_key(
    open_life_days: int | None,
) -> Callable[[BatchSpec], tuple[int, float, int]]:
    """THE batch order: soonest EFFECTIVE expiry first (spec 4.1 -- by expiry, not by
    receipt date), then no-expiry lots last, oldest-received first among them; batch
    id breaks ties so the order is total and every run is identical.

    One definition for depletion (`allocate_fifo`), count reconciliation
    (`reconcile_to_count`) and every read that lists batches in "what goes first"
    order (`SqlBatchRepository.sort_by_expiry`), so a list shown to a person is the
    order stock is actually drawn in.
    """

    def key(batch: BatchSpec) -> tuple[int, float, int]:
        expiry = batch.effective_expiry(open_life_days)
        if expiry is None:
            return (1, batch.received_at.timestamp(), batch.batch_id)
        return (0, expiry.timestamp(), batch.batch_id)

    return key


def allocate_fifo(
    *,
    qty: Decimal,
    batches: Sequence[BatchSpec],
    open_life_days: int | None = None,
) -> tuple[tuple[DepletionAllocation, ...], Decimal]:
    """Take `qty` from `batches`, soonest-expiring first.

    Returns (allocations, shortfall). A shortfall means the batch records could not
    account for stock that demonstrably left the building.

    FIFO is by **effective expiry**, not receipt date: a short-dated delivery goes out
    before older stock with a longer date, which is what a person at the fridge
    actually does. A batch with no expiry sorts last -- cups and napkins should be
    consumed only after anything that can spoil.

    The shortfall is returned rather than raised. A sale happened; refusing to record
    it would lose real consumption, and the gap is itself the signal that a count is
    wrong or a delivery was never entered.
    """
    if qty <= 0:
        return (), Decimal("0")

    remaining = qty
    allocations: list[DepletionAllocation] = []
    for batch in sorted(batches, key=fifo_key(open_life_days)):
        if remaining <= 0:
            break
        available = batch.qty_remaining
        if available <= 0:
            continue
        take = available if available < remaining else remaining
        allocations.append(DepletionAllocation(batch_id=batch.batch_id, qty=take))
        remaining -= take

    if remaining > 0:
        # Record the unaccounted part explicitly rather than silently dropping it.
        allocations.append(DepletionAllocation(batch_id=None, qty=remaining))

    return tuple(allocations), max(remaining, Decimal("0"))


def find_expiry_losses(
    *,
    batches: Sequence[BatchSpec],
    at: datetime,
    open_life_days: int | None = None,
) -> tuple[ExpiryLoss, ...]:
    """Batches past their effective expiry with stock left.

    This is the honest waste figure the P&L needs and nobody currently has. It is
    also what lets a drift report separate "the recipe is wrong" from "we are
    over-ordering" -- two problems with opposite fixes.
    """
    losses: list[ExpiryLoss] = []
    for batch in batches:
        if batch.qty_remaining <= 0:
            continue
        expiry = batch.effective_expiry(open_life_days)
        if expiry is None or expiry > at:
            continue
        losses.append(
            ExpiryLoss(
                batch_id=batch.batch_id,
                ingredient_id=batch.ingredient_id,
                qty=batch.qty_remaining,
                expired_at=expiry,
                unit_cost_pence=batch.unit_cost_pence,
            )
        )
    return tuple(losses)


def expiry_movements(losses: Sequence[ExpiryLoss]) -> tuple[MovementSpec, ...]:
    """Turn expiry losses into EXPIRED ledger entries. Negative: the stock is gone."""
    return tuple(
        MovementSpec(
            ingredient_id=loss.ingredient_id,
            type=MovementType.EXPIRED,
            qty=-loss.qty,
            occurred_at=loss.expired_at,
            ref_type="stock_batch",
            ref_id=loss.batch_id,
            note="expired with stock remaining",
        )
        for loss in losses
        if loss.qty > 0
    )


def batch_expiry_for(
    *,
    received_at: datetime,
    shelf_life: ShelfLifeSpec | None,
) -> datetime | None:
    """The expiry to stamp on a newly received batch.

    None when the ingredient does not expire. Note this uses the FULL shelf life:
    the transit buffer belongs to the *ordering* decision (spec 5.4's cover cap), not
    to the date on the carton. Subtracting it here would double-count the caution and
    write off stock that is still good.
    """
    if shelf_life is None or shelf_life.shelf_life_days is None:
        return None
    return received_at + timedelta(days=shelf_life.shelf_life_days)


def drift_attribution(
    *,
    total_gap: Decimal,
    expired_qty: Decimal,
) -> tuple[Decimal, Decimal]:
    """Split a drift gap into (measurement, expiry) magnitudes. Spec 5.2.

    If write-offs explain most of the gap, the fix is to order less. If they explain
    little, the fix is the recipe or the waste factor. Reporting one undifferentiated
    number tells the owner to do the wrong thing roughly half the time.
    """
    gap = abs(total_gap)
    expired = abs(expired_qty)
    if expired >= gap:
        return Decimal("0"), gap
    return gap - expired, expired


@dataclass(frozen=True, slots=True)
class CountReconciliation:
    """How batches must change to agree with a physical count.

    A count RE-ANCHORS theoretical on-hand (spec 5.1). Leaving the batches at their
    pre-count quantities makes the two numbers disagree permanently, and because FIFO
    and the expiry sweep can only see BATCHED stock, an unreconciled surplus can never
    expire and never be counted as waste. So truing up is not optional bookkeeping:
    without it `StockReading.batch_coverage_gap` grows with every count.

    `reductions` draw down the soonest-expiring batches first -- that stock is the most
    likely to have been thrown out unrecorded. `surplus_qty` is stock the count found
    that no batch explains, and it has to live somewhere, so the caller opens a batch
    for it.

    No ledger movement belongs with any of this: the count itself is already the
    re-anchor, and an ADJUSTMENT here would count the same correction twice.
    """

    reductions: tuple[DepletionAllocation, ...] = ()
    surplus_qty: Decimal = Decimal("0")
    batched_before: Decimal = Decimal("0")
    counted_qty: Decimal = Decimal("0")

    @property
    def is_noop(self) -> bool:
        return not self.reductions and self.surplus_qty == 0

    @property
    def delta(self) -> Decimal:
        return self.counted_qty - self.batched_before


def reconcile_to_count(
    *,
    counted_qty: Decimal,
    batches: Sequence[BatchSpec],
    open_life_days: int | None = None,
) -> CountReconciliation:
    """Decide the batch changes that make the batches match a physical count.

    Pure: returns the plan, applies nothing. Shared by the ledger replay and by
    `record_count`, because two implementations of "what does a count do to batches"
    would drift apart and only one of them would be running in production.
    """
    batched = sum((b.qty_remaining for b in batches if b.qty_remaining > 0), Decimal("0"))
    delta = counted_qty - batched

    if delta == 0:
        return CountReconciliation(batched_before=batched, counted_qty=counted_qty)

    if delta > 0:
        return CountReconciliation(
            surplus_qty=delta, batched_before=batched, counted_qty=counted_qty
        )

    shortfall = -delta
    reductions: list[DepletionAllocation] = []
    for batch in sorted(batches, key=fifo_key(open_life_days)):
        if shortfall <= 0:
            break
        available = batch.qty_remaining
        if available <= 0:
            continue
        take = available if available < shortfall else shortfall
        reductions.append(DepletionAllocation(batch_id=batch.batch_id, qty=take))
        shortfall -= take

    return CountReconciliation(
        reductions=tuple(reductions), batched_before=batched, counted_qty=counted_qty
    )
