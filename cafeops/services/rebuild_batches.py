"""Rebuild the batch ledger by replaying stock movements chronologically.

PHASE 0 SCOPE NOTE: batches and the expiry sweep belong to Agent C (spec 12,
"C -- Stock and batches"). This exists because spec 16 requires
`cafeops stock --as-of today` to print theoretical on-hand *and* open batches, and
those two numbers must agree or the deliverable actively misleads. Without it the
demo showed milk batches 54 days overdue and batch totals exceeding the ledger,
because movements depleted the ledger while batches sat untouched.

Why a replay rather than allocating during expansion: deliveries and sales
interleave across 60 days, and a batch cannot be allocated before it is received.
Expansion runs over the whole sale queue at once, so a single chronological pass over
the finished ledger is the only way to get an end state that is actually reachable.
It is also idempotent and inspectable, which a stateful interleaved build would not be.

What Agent C should take from this and what to replace:

- KEEP: FIFO by effective expiry (`domain.stock.allocate_fifo`), the expiry sweep
  writing `EXPIRED` movements, and the rule that a shortfall is recorded rather than
  raised.
- REPLACE: this rebuild is a demo/backfill tool. In production, `receive_delivery`
  creates a batch and expansion allocates from it at the time of the sale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from cafeops.db.models import (
    Ingredient,
    MovementType,
    StockBatch,
    StockCount,
    StockMovement,
)
from cafeops.domain.stock import allocate_fifo, find_expiry_losses
from cafeops.domain.types import BatchSpec

#: Movement types that ADD stock and therefore create a batch.
_INBOUND = {MovementType.DELIVERY}
#: Movement types that REMOVE stock and therefore draw from batches.
_OUTBOUND = {
    MovementType.SALE,
    MovementType.WASTE,
    MovementType.STAFF,
}


@dataclass
class RebuildReport:
    batches_created: int = 0
    allocations: int = 0
    expiry_losses: int = 0
    expired_qty_total: Decimal = Decimal("0")
    expired_value_pence: Decimal = Decimal("0")
    shortfalls: int = 0
    shortfall_ingredients: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.batches_created} batches",
            f"{self.allocations} FIFO allocations",
        ]
        if self.expiry_losses:
            parts.append(
                f"{self.expiry_losses} expiry write-off(s) worth "
                f"GBP {self.expired_value_pence / 100:.2f}"
            )
        if self.shortfalls:
            parts.append(
                f"{self.shortfalls} movement(s) exceeded batched stock "
                f"({len(set(self.shortfall_ingredients))} ingredient(s))"
            )
        return "; ".join(parts)


def rebuild_batches(
    session: Session, *, purge: bool = True, as_of: datetime | None = None
) -> RebuildReport:
    """Replay the ledger to produce a coherent batch state.

    With `purge=True` existing batches are removed first -- this is a rebuild, not an
    incremental update, and mixing the two would double-count received stock.

    ## This is the one exception to invariant 12, and it is narrow

    Invariant 12 says the stock ledger is append-only and corrections are `ADJUSTMENT`
    movements. `purge=True` deletes rows: every `StockBatch`, and every `EXPIRED`
    movement. That is allowed *only* because those rows are **derived** -- the expiry
    sweep computes them from batch state, so replaying the ledger reproduces them
    exactly and nothing observed is lost.

    It must never delete a `SALE`, `RECEIPT`, `COUNT` or `ADJUSTMENT`. Those are
    records of things that happened, and no replay can reconstruct them.

    `purge` defaults to True because the only caller today is `cafeops seed --demo`,
    where a full rebuild is the point. **Adding a caller is a decision, not a
    refactor**: on a live database this discards real expiry write-offs and rebuilds
    them from whatever the batch state currently says. Pass `purge=False` unless a
    total rebuild is genuinely what you mean.

    `as_of` is the instant of the final expiry sweep, defaulting to now. Sweeping only
    up to the last movement would leave stock that expired since then sitting open,
    which is how a demo ends up showing milk 54 days overdue.
    """
    report = RebuildReport()
    as_of = as_of or datetime.now(UTC)

    if purge:
        session.execute(
            update(StockMovement).where(StockMovement.batch_id.isnot(None)).values(batch_id=None)
        )
        session.execute(delete(StockMovement).where(StockMovement.type == MovementType.EXPIRED))
        session.execute(delete(StockBatch))
        session.flush()

    ingredients = {i.id: i for i in session.scalars(select(Ingredient))}

    for ingredient_id, ingredient in ingredients.items():
        _rebuild_one(session, ingredient_id, ingredient, report, as_of)
    session.flush()
    return report


def _rebuild_one(
    session: Session,
    ingredient_id: int,
    ingredient: Ingredient,
    report: RebuildReport,
    as_of: datetime,
) -> None:
    movements = list(
        session.scalars(
            select(StockMovement)
            .where(StockMovement.ingredient_id == ingredient_id)
            .order_by(StockMovement.occurred_at, StockMovement.id)
        )
    )
    has_count = (
        session.scalar(
            select(StockCount.id).where(StockCount.ingredient_id == ingredient_id).limit(1)
        )
        is not None
    )
    if not movements and not has_count:
        return

    unit_cost = ingredient.current_cost_pence_per_unit or Decimal("0")
    shelf_life = ingredient.shelf_life_days
    open_life = ingredient.open_life_days

    # Live batches, as domain specs so FIFO and expiry logic stay in domain/.
    live: list[BatchSpec] = []
    # batch id -> the ORM row, so quantities can be written back.
    rows: dict[int, StockBatch] = {}

    def add_batch(qty: Decimal, at: datetime, note: str) -> StockBatch:
        expires = None if shelf_life is None else at + timedelta(days=shelf_life)
        row = StockBatch(
            ingredient_id=ingredient_id,
            qty_received=qty,
            qty_remaining=qty,
            received_at=at,
            expires_at=expires,
            unit_cost_pence=unit_cost,
            note=note,
        )
        session.add(row)
        session.flush()
        rows[row.id] = row
        live.append(
            BatchSpec(
                batch_id=row.id,
                ingredient_id=ingredient_id,
                qty_remaining=qty,
                received_at=at,
                expires_at=expires,
                unit_cost_pence=unit_cost,
            )
        )
        report.batches_created += 1
        return row

    def sweep(at: datetime) -> None:
        """Write off anything expired as of `at`, before it can be consumed.

        Order matters: sweeping before allocating is what makes the waste figure
        honest. Allocating first would quietly sell expired stock and the loss would
        never appear.
        """
        losses = find_expiry_losses(batches=live, at=at, open_life_days=open_life)
        for loss in losses:
            expired_row = rows.get(loss.batch_id)
            if expired_row is None:
                continue
            session.add(
                StockMovement(
                    ingredient_id=ingredient_id,
                    batch_id=loss.batch_id,
                    type=MovementType.EXPIRED,
                    qty=-loss.qty,
                    occurred_at=loss.expired_at,
                    ref_type="stock_batch",
                    ref_id=loss.batch_id,
                    note="expired with stock remaining",
                )
            )
            expired_row.qty_remaining = Decimal("0")
            expired_row.expired_at = loss.expired_at
            report.expiry_losses += 1
            report.expired_qty_total += loss.qty
            if loss.loss_pence is not None:
                report.expired_value_pence += loss.loss_pence
        if losses:
            _sync_live(live, rows)

    def reconcile_to_count(counted: Decimal, at: datetime) -> None:
        """Make the batches agree with a physical count.

        A count RE-ANCHORS theoretical on-hand (spec 5.1), so leaving the batches at
        their pre-count quantities makes the two numbers disagree permanently -- which
        is exactly what the demo showed, batches holding 200 cups against a counted
        105. Truing up is not optional bookkeeping: FIFO and the expiry sweep can only
        see batched stock, so an unreconciled surplus can never expire and never be
        counted as waste.

        A surplus is removed oldest-expiring first (that stock is the most likely to
        have been thrown out unrecorded). A deficit becomes a new batch, because stock
        that demonstrably exists has to live somewhere.

        No movement is written: the count itself is already the re-anchor, and adding
        an ADJUSTMENT here would double-count it.
        """
        total = sum((b.qty_remaining for b in live), Decimal("0"))
        delta = counted - total
        if delta == 0:
            return
        if delta > 0:
            add_batch(delta, at, "count surplus -- stock found that no batch explained")
            return
        shortfall = -delta
        for spec in sorted(
            live,
            key=lambda b: (
                b.effective_expiry(open_life) is None,
                b.effective_expiry(open_life) or b.received_at,
            ),
        ):
            if shortfall <= 0:
                break
            row = rows.get(spec.batch_id)
            if row is None or row.qty_remaining <= 0:
                continue
            take = min(row.qty_remaining, shortfall)
            row.qty_remaining = row.qty_remaining - take
            shortfall -= take
        _sync_live(live, rows)

    counts = list(
        session.scalars(
            select(StockCount)
            .where(StockCount.ingredient_id == ingredient_id)
            .order_by(StockCount.counted_at)
        )
    )
    # Opening stock, from the first physical count.
    if counts and counts[0].counted_qty > 0:
        add_batch(counts[0].counted_qty, counts[0].counted_at, "opening stock")
    later_counts = counts[1:]
    count_index = 0

    for movement in movements:
        # Apply any counts that happened before this movement, in order.
        while (
            count_index < len(later_counts)
            and later_counts[count_index].counted_at <= movement.occurred_at
        ):
            pending = later_counts[count_index]
            sweep(pending.counted_at)
            reconcile_to_count(pending.counted_qty, pending.counted_at)
            count_index += 1

        sweep(movement.occurred_at)

        if movement.type in _INBOUND and movement.qty > 0:
            row = add_batch(movement.qty, movement.occurred_at, "delivery")
            movement.batch_id = row.id
            continue

        if movement.type in _OUTBOUND and movement.qty < 0:
            allocations, shortfall = allocate_fifo(
                qty=-movement.qty,
                batches=[b for b in live if b.qty_remaining > 0],
                open_life_days=open_life,
                at=movement.occurred_at,
            )
            primary: int | None = None
            largest = Decimal("-1")
            for alloc in allocations:
                if alloc.batch_id is None:
                    continue
                target = rows.get(alloc.batch_id)
                if target is None:
                    continue
                target.qty_remaining = target.qty_remaining - alloc.qty
                report.allocations += 1
                if alloc.qty > largest:
                    largest, primary = alloc.qty, alloc.batch_id
            # One movement, one batch_id: a consumption spanning several lots is
            # attributed to the lot that supplied the most. The remaining quantities
            # are exact either way; only the attribution is approximate, and
            # splitting one sale into several ledger rows would misrepresent the
            # ledger as several events.
            movement.batch_id = primary
            if shortfall > 0:
                report.shortfalls += 1
                report.shortfall_ingredients.append(ingredient.name)
            _sync_live(live, rows)
            continue

        # COUNT_RESET / ADJUSTMENT: about the ingredient as a whole, not one lot.
        # A positive adjustment with no batch would be invisible to FIFO, so give it
        # one; a negative one draws down like any other outbound.
        if movement.type is MovementType.ADJUSTMENT and movement.qty > 0:
            row = add_batch(movement.qty, movement.occurred_at, "adjustment")
            movement.batch_id = row.id

    # Any counts after the last movement.
    while count_index < len(later_counts):
        pending = later_counts[count_index]
        sweep(pending.counted_at)
        reconcile_to_count(pending.counted_qty, pending.counted_at)
        count_index += 1

    # Final sweep at the present, not at the last movement: stock that expired since
    # then is still expired, and leaving it open is how the demo showed milk 54 days
    # overdue with nobody writing it off.
    sweep(as_of)


def _sync_live(live: list[BatchSpec], rows: dict[int, StockBatch]) -> None:
    """Refresh the domain view from the ORM rows after quantities change."""
    refreshed: list[BatchSpec] = []
    for spec in live:
        row = rows.get(spec.batch_id)
        if row is None or row.qty_remaining <= 0:
            continue
        refreshed.append(
            BatchSpec(
                batch_id=spec.batch_id,
                ingredient_id=spec.ingredient_id,
                qty_remaining=row.qty_remaining,
                received_at=spec.received_at,
                expires_at=spec.expires_at,
                opened_at=spec.opened_at,
                unit_cost_pence=spec.unit_cost_pence,
            )
        )
    live[:] = refreshed
