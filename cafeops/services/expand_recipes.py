"""Turn sold lines into SALE ledger movements, and undo them when the data changes.

Four things are load-bearing here.

1. **Resolution uses the sale's own date.** `resolve_recipe(..., at=sale.sold_at)`,
   not `at=now`. Invariant 3: recomputing March's consumption with today's recipe
   produces wrong history and destroys the drift metric.
2. **Idempotency is `sale.expanded_at`**, set in the same transaction that writes
   the movements. A crash mid-run leaves the ledger and the flag consistent, so a
   re-run redoes the whole batch or none of it, never half.
3. **Refunds net out by sign.** A refund line carries a negative `qty`, so
   `depletion_movements` negates it into a POSITIVE movement and the stock goes back on
   the shelf. Nothing special-cases refunds, which is why nothing can forget to.
4. **A `SubstitutionError` sale is NOT marked expanded.** A modifier that tried to
   substitute a non-substitutable slot is a data defect a human must fix. Marking it
   expanded would drop consumption that really happened; writing the unmodified recipe
   would deplete the wrong ingredient. So it stays in the queue, is reported, and the
   next run picks it up once the data is corrected.

Corrections never amend the ledger (invariant 9). `reverse_expansion` appends
equal-and-opposite `ADJUSTMENT` rows instead.

## v2: batches are allocated here, at the time of the sale

Phase 0 wrote `SALE` movements with no `batch_id` and reconstructed batches afterwards
by replaying the finished ledger (`services/rebuild_batches.py`). That replay stays as
a backfill tool; live expansion now does the allocation itself, and keeps the replay's
two rules because both of them are load-bearing.

- **Sweep expiry BEFORE allocating.** At each sale instant, any lot already past its
  effective expiry is written off first. Allocating first would quietly sell expired
  stock: the quantity would come off the right batch, the ledger would balance, and the
  loss would never appear anywhere. The ordering is the only reason the waste figure is
  honest.
- **A shortfall is data, not an exception.** When the batch records cannot account for
  stock that demonstrably left the building, the `SALE` movement is still written --
  with `batch_id = NULL` -- and the gap is counted in the report. The sale happened;
  refusing to record it would lose real consumption, and the shortfall is itself the
  signal that a count is wrong or a delivery was never entered.

Two smaller decisions:

- **One movement keeps one `batch_id`** (`ARCHITECTURE.md` 8F.2). A consumption spanning
  several lots is attributed to the lot that supplied the most. The remaining quantities
  are exact either way; splitting one sale across several ledger rows would misrepresent
  one event as several.
- **A refund does not put stock back into a lot.** A refunded latte's milk is not back
  in the carton, so the positive movement carries no `batch_id` and no batch regains
  quantity. The ledger still nets out for on-hand; the batches stay a record of physical
  lots rather than of accounting reversals. The report counts these so the resulting gap
  between on-hand and batch totals is explained rather than mysterious.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import Sale, StockBatch, StockMovement
from cafeops.db.repositories.batch import SqlBatchRepository, batch_spec
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.sale import SqlSaleRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.composition import resolve_recipe
from cafeops.domain.stock import (
    allocate_fifo,
    depletion_movements,
    find_expiry_losses,
    reversal_movements,
)
from cafeops.domain.types import (
    BatchSpec,
    DepletionAllocation,
    MovementSpec,
    MovementType,
    SubstitutionError,
)

#: `stock_movement.ref_type` values. A reversal is retyped rather than reusing "sale",
#: so `_already_reversed` can tell a correction from the thing it corrected and no
#: second run reverses the same sale twice.
SALE_REF = "sale"
SALE_REVERSAL_REF = "sale_reversal"


@dataclass
class ExpansionReport:
    sales_expanded: int = 0
    movements_written: int = 0
    skipped_no_recipe: int = 0
    substitution_errors: int = 0
    refund_lines: int = 0
    #: Sales left in the queue on purpose. A human has to fix the data behind these.
    blocked_sale_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # --- batch allocation (v2) ------------------------------------------------
    allocations: int = 0
    batches_opened: int = 0
    expiry_write_offs: int = 0
    expired_qty: Decimal = field(default_factory=lambda: Decimal("0"))
    expired_value_pence: Decimal = field(default_factory=lambda: Decimal("0"))
    #: Movements whose quantity no batch could account for. NOT an error -- the stock
    #: left the building, and the gap is the signal that a count or a delivery is wrong.
    shortfall_movements: int = 0
    shortfall_ingredient_ids: set[int] = field(default_factory=set)
    #: Refund movements, which put stock back on the ledger but into no lot.
    unbatched_returns: int = 0

    def summary(self) -> str:
        parts = [
            f"expanded {self.sales_expanded} sale lines",
            f"wrote {self.movements_written} movements",
        ]
        if self.allocations:
            parts.append(f"{self.allocations} FIFO batch allocation(s)")
        if self.batches_opened:
            parts.append(f"opened {self.batches_opened} pack(s) (open-life clock started)")
        if self.expiry_write_offs:
            parts.append(
                f"{self.expiry_write_offs} expiry write-off(s) swept before allocating, worth "
                f"GBP {self.expired_value_pence / 100:.2f}"
            )
        if self.shortfall_movements:
            parts.append(
                f"{self.shortfall_movements} movement(s) exceeded batched stock "
                f"({len(self.shortfall_ingredient_ids)} ingredient(s)) -- recorded, not refused"
            )
        if self.refund_lines:
            parts.append(f"{self.refund_lines} refund line(s) returned stock")
        if self.skipped_no_recipe:
            parts.append(f"{self.skipped_no_recipe} had no recipe")
        if self.substitution_errors:
            parts.append(
                f"{self.substitution_errors} substitution error(s) LEFT PENDING for a human"
            )
        return "; ".join(parts)


@dataclass
class ReversalReport:
    """The append-only correction path. Invariant 9: nothing is updated or deleted."""

    sales_reversed: int = 0
    adjustments_written: int = 0
    already_reversed: int = 0
    nothing_to_reverse: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"reversed {self.sales_reversed} sale(s)",
            f"wrote {self.adjustments_written} ADJUSTMENT movements",
        ]
        if self.already_reversed:
            parts.append(f"{self.already_reversed} already reversed")
        if self.nothing_to_reverse:
            parts.append(f"{self.nothing_to_reverse} had no movements to reverse")
        return "; ".join(parts)


def expand_pending(
    session: Session,
    *,
    limit: int | None = None,
    only_tracked: bool = True,
    allocate_batches: bool = True,
) -> ExpansionReport:
    """Expand every unexpanded, non-voided sale line into the ledger.

    `allocate_batches=False` writes the movements without touching batches. It exists
    for exactly one caller: the demo seed, which expands 60 days of sales BEFORE the
    deliveries that supplied them have been generated, and then builds batches by
    replaying the finished ledger (`ARCHITECTURE.md` 8F.2). Allocating there would
    report every one of ~17,000 movements as a shortfall against batches that do not
    exist yet. Live expansion always allocates.
    """
    report = ExpansionReport()

    sales_repo = SqlSaleRepository(session)
    composition_repo = SqlCompositionRepository(session)
    ingredient_repo = SqlIngredientRepository(session)
    stock_repo = SqlStockRepository(session)
    allocator = _BatchAllocator(session, report) if allocate_batches else None

    sales = sales_repo.pending_expansion(limit=limit)
    if not sales:
        return report

    snapshots = ingredient_repo.snapshots_by_id()
    waste_factors = {i: s.waste_factor for i, s in snapshots.items()}
    tracked_ids = {s.id for s in snapshots.values() if s.tracking_enabled} if only_tracked else None

    expanded_ids: list[int] = []
    for sale in sales:
        # Resolution is per-sale because it is date-dependent. Grouping by
        # (menu_item, date) would be the optimisation if this ever gets slow.
        spec = composition_repo.item_spec(sale.menu_item_id, sale.sold_at)
        if spec is None:
            report.skipped_no_recipe += 1
            expanded_ids.append(sale.sale_id)
            continue

        modifiers = composition_repo.modifiers(list(sale.modifier_ids), sale.sold_at)
        try:
            recipe = resolve_recipe(spec, modifiers, sale.sold_at, ingredients=snapshots)
        except SubstitutionError as exc:
            # Do NOT mark expanded: this is a data error a human must fix, and
            # silently dropping the sale would lose real consumption.
            report.substitution_errors += 1
            report.blocked_sale_ids.append(sale.sale_id)
            report.warnings.append(f"sale {sale.sale_id}: {exc}")
            continue

        if not recipe.lines:
            # No recipe at this date: a gift card, a meal deal, an unconfigured
            # one-off. Mark expanded anyway -- it depletes nothing, and leaving it
            # pending would make the queue grow without bound.
            report.skipped_no_recipe += 1
            expanded_ids.append(sale.sale_id)
            continue

        movements = depletion_movements(
            sale_id=sale.sale_id,
            sold_qty=sale.qty,
            sold_at=sale.sold_at,
            # depletion_lines already carry waste; pass the recipe lines and let
            # the ledger apply waste once, from a single place.
            lines=recipe.lines,
            waste_factors=waste_factors,
            tracked_ingredient_ids=tracked_ids,
        )
        if sale.qty < 0:
            # A refund. The sign is authoritative and already inverted every movement;
            # counted here only so the report can say it happened rather than leaving a
            # run that put stock BACK looking identical to one that took stock off.
            report.refund_lines += 1

        if allocator is None:
            report.movements_written += stock_repo.append_movements(movements)
        else:
            report.movements_written += allocator.write(movements)
        expanded_ids.append(sale.sale_id)

    sales_repo.mark_expanded(expanded_ids, datetime.now(UTC))
    report.sales_expanded = len(expanded_ids)
    return report


class _BatchAllocator:
    """FIFO allocation at the moment of the sale, with the expiry sweep in front.

    Holds each ingredient's live batch rows for the duration of one expansion run.
    They are ORM objects, so decrements and `opened_at` stamps are visible to the next
    sale immediately -- which is the whole point: 30 lattes through one carton must see
    it empty, not each see it full.

    The `BatchSpec`s handed to the domain are rebuilt from those rows on every call
    rather than cached, so a stale quantity cannot survive a decrement. At a few lots
    per ingredient that costs nothing, and it removes the one bug this class could
    plausibly have.
    """

    def __init__(self, session: Session, report: ExpansionReport) -> None:
        self.session = session
        self.report = report
        self.repo = SqlBatchRepository(session)
        self._pool: dict[int, list[StockBatch]] = {}

    def write(self, movements: Sequence[MovementSpec]) -> int:
        """Append `movements`, each carrying the lot FIFO drew it from."""
        pairs: list[tuple[MovementSpec, int | None]] = []
        for movement in movements:
            pairs.append((movement, self._batch_for(movement)))
        return self.repo.append_linked_movements(pairs)

    def _batch_for(self, movement: MovementSpec) -> int | None:
        if movement.qty >= 0:
            # A refund. See the module docstring: no lot regains stock.
            self.report.unbatched_returns += 1
            return None
        return self._take(movement.ingredient_id, -movement.qty, movement.occurred_at)

    def _take(self, ingredient_id: int, qty: Decimal, at: datetime) -> int | None:
        rows = self._rows(ingredient_id)
        open_life = self.repo.open_life_days(ingredient_id)

        # 1. SWEEP FIRST. Allocating before this would sell expired stock and the loss
        #    would never appear anywhere.
        self._sweep(ingredient_id, rows, at, open_life)

        # 2. Allocate from what is left, soonest effective expiry first.
        specs = self._specs(rows, at)
        allocations, shortfall = allocate_fifo(
            qty=qty, batches=specs, open_life_days=open_life, at=at
        )

        opened_before = sum(1 for row in rows if row.opened_at is not None)
        self.report.allocations += self.repo.apply_allocations(allocations, at=at)
        self.report.batches_opened += (
            sum(1 for row in rows if row.opened_at is not None) - opened_before
        )

        if shortfall > 0:
            self.report.shortfall_movements += 1
            self.report.shortfall_ingredient_ids.add(ingredient_id)

        self._prune(ingredient_id, rows)
        return _primary_batch(allocations)

    def _sweep(
        self,
        ingredient_id: int,
        rows: list[StockBatch],
        at: datetime,
        open_life: int | None,
    ) -> None:
        losses = find_expiry_losses(batches=self._specs(rows, at), at=at, open_life_days=open_life)
        if not losses:
            return
        written = self.repo.mark_expired(losses, at=at)
        if not written:
            return
        self.report.expiry_write_offs += written
        for loss in losses:
            self.report.expired_qty += loss.qty
            if loss.loss_pence is not None:
                self.report.expired_value_pence += loss.loss_pence
        self._prune(ingredient_id, rows)

    def _rows(self, ingredient_id: int) -> list[StockBatch]:
        if ingredient_id not in self._pool:
            self._pool[ingredient_id] = self.repo.open_batch_rows(ingredient_id)
        return self._pool[ingredient_id]

    def _prune(self, ingredient_id: int, rows: list[StockBatch]) -> None:
        self._pool[ingredient_id] = [
            row for row in rows if row.qty_remaining > 0 and row.expired_at is None
        ]

    @staticmethod
    def _specs(rows: Sequence[StockBatch], at: datetime) -> list[BatchSpec]:
        """Only lots that had already been received. A batch delivered this morning
        cannot have supplied yesterday's latte."""
        return [
            batch_spec(row)
            for row in rows
            if row.received_at <= at and row.qty_remaining > 0 and row.expired_at is None
        ]


def _primary_batch(allocations: Sequence[DepletionAllocation]) -> int | None:
    """The lot that supplied the most of one consumption. `ARCHITECTURE.md` 8F.2.

    One movement keeps one `batch_id`: the remaining quantities are exact whichever lot
    is named, and splitting a single sale into several ledger rows would misrepresent
    one event as several.
    """
    primary: int | None = None
    largest = Decimal("-1")
    for allocation in allocations:
        if allocation.batch_id is None:
            continue
        if allocation.qty > largest:
            largest, primary = allocation.qty, allocation.batch_id
    return primary


def reverse_expansion(
    session: Session,
    *,
    sale_ids: Sequence[int],
    reason: str,
    at: datetime | None = None,
) -> ReversalReport:
    """Undo the expansion of specific sales by APPENDING `ADJUSTMENT` movements.

    INVARIANT 9. The original `SALE` rows stay exactly as written -- a receipt that was
    voided after it was expanded, or a window expanded twice, is corrected by an equal
    and opposite entry, never by an `UPDATE` or a `DELETE`. The ledger sums to the right
    number and still records what actually happened and when it was fixed.

    `sale.expanded_at` is deliberately left set. Clearing it would put the sale back in
    the expansion queue, and the next run would re-write the movements the reversal just
    cancelled.

    The two queries here belong in `StockRepository` (`movements_for(ref_type, ref_id)`)
    and `SaleRepository`; raised for the integrator rather than editing another agent's
    module.
    """
    report = ReversalReport()
    if not sale_ids:
        return report

    at = at or datetime.now(UTC)
    stock_repo = SqlStockRepository(session)
    reversed_already = _already_reversed(session, sale_ids)
    by_sale = _sale_movements(session, sale_ids)

    for sale_id in sale_ids:
        if sale_id in reversed_already:
            report.already_reversed += 1
            continue
        originals = by_sale.get(sale_id, [])
        if not originals:
            report.nothing_to_reverse += 1
            continue
        adjustments = reversal_movements(
            originals, occurred_at=at, note=reason, ref_type=SALE_REVERSAL_REF
        )
        report.adjustments_written += stock_repo.append_movements(adjustments)
        report.sales_reversed += 1
    return report


def reverse_voided_expansions(
    session: Session, *, at: datetime | None = None, limit: int | None = None
) -> ReversalReport:
    """Reverse every sale that was voided AFTER it had been expanded.

    `SaleRepository.pending_expansion` already keeps voided lines out of the queue, but
    that only helps lines voided before expansion ran. A receipt voided a day later has
    real `SALE` movements sitting in the ledger depleting stock that was never sold, and
    nothing else in the system would ever notice.
    """
    stmt = (
        select(Sale.id)
        .where(Sale.voided.is_(True), Sale.expanded_at.is_not(None))
        .order_by(Sale.sold_at, Sale.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    sale_ids = list(session.scalars(stmt))
    return reverse_expansion(
        session,
        sale_ids=sale_ids,
        reason="receipt voided after expansion",
        at=at,
    )


def _sale_movements(session: Session, sale_ids: Sequence[int]) -> dict[int, list[MovementSpec]]:
    rows = session.scalars(
        select(StockMovement).where(
            StockMovement.ref_type == SALE_REF,
            StockMovement.ref_id.in_(list(sale_ids)),
            StockMovement.type == MovementType.SALE,
        )
    )
    grouped: dict[int, list[MovementSpec]] = {}
    for row in rows:
        if row.ref_id is None:  # pragma: no cover - ref_id is set by expansion
            continue
        grouped.setdefault(row.ref_id, []).append(
            MovementSpec(
                ingredient_id=row.ingredient_id,
                type=row.type,
                qty=row.qty,
                occurred_at=row.occurred_at,
                ref_type=row.ref_type,
                ref_id=row.ref_id,
                note=row.note,
            )
        )
    return grouped


def _already_reversed(session: Session, sale_ids: Sequence[int]) -> set[int]:
    rows = session.scalars(
        select(StockMovement.ref_id).where(
            StockMovement.ref_type == SALE_REVERSAL_REF,
            StockMovement.ref_id.in_(list(sale_ids)),
        )
    )
    return {r for r in rows if r is not None}
