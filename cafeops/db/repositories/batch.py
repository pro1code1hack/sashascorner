"""Batch reads and writes: creation, FIFO, allocation write-back and the write-off.

Implements `BatchRepository` (db/repositories/protocols.py). No business logic --
FIFO ordering and the expiry decision live in `domain/stock.py`; this module loads
the rows the domain needs and writes back what it decided.

Four things here are deliberate.

1. **Every quantity filter is checked twice.** `ARCHITECTURE.md` 8E: the old TEXT
   `Qty` made `WHERE qty_remaining > 0` match every depleted batch, because SQLite
   ranks TEXT above all numbers. The storage is fixed, but the lesson is not, so the
   SQL predicate and the Python predicate are both applied -- `_live` re-asserts in
   Python exactly what the `WHERE` clause claimed. If the two ever disagree again,
   the Python one wins and the rows simply do not appear, rather than a depleted lot
   quietly satisfying a sale.

2. **Effective expiry cannot be ordered in SQL.** `open_life_days` lives on
   `ingredient`, and the effective expiry is `min(expires_at, opened_at +
   open_life_days)` -- an expression across two tables that SQLite would have to
   compute per row anyway. The ORDER BY that matters is therefore done in Python
   through `BatchSpec.effective_expiry`, which is the same code path the domain and
   the CLI use. One definition of "soonest-expiring", not three.

3. **`apply_allocations` is what opens a pack.** The protocol passes `at` alongside
   the allocations, which a bare decrement would not need. It is used: the first
   time stock is drawn from a batch of something with an `open_life_days`, that
   batch is physically being opened, and `opened_at` is stamped with `at`. See
   `services/receive_delivery.py` for why this is the only way `opened_at` ever gets
   set reliably.

4. **`mark_expired` writes the movement and the write-off together.** They are one
   fact -- a lot reached its date with stock in it -- and splitting them across two
   callers is how a batch ends up flagged expired with no loss in the ledger, or a
   loss in the ledger against a batch still showing stock.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from cafeops.db.models import ExpirySource, Ingredient, MovementType, StockBatch, StockMovement
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.stock import expiry_movements, find_expiry_losses
from cafeops.domain.types import (
    BatchSpec,
    DepletionAllocation,
    ExpiryLoss,
    MovementSpec,
    ShelfLifeSpec,
)

__all__ = ["SqlBatchRepository", "batch_spec"]

#: `stock_movement.ref_type` for a write-off, matching what the Phase 0 replay wrote
#: so `rebuild_batches` and the live sweep produce the same shaped rows.
BATCH_REF = "stock_batch"


def batch_spec(row: StockBatch) -> BatchSpec:
    """One ORM row as the domain sees it. The only place the mapping is written."""
    return BatchSpec(
        batch_id=row.id,
        ingredient_id=row.ingredient_id,
        qty_remaining=row.qty_remaining,
        received_at=row.received_at,
        expires_at=row.expires_at,
        opened_at=row.opened_at,
        unit_cost_pence=row.unit_cost_pence,
    )


class SqlBatchRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._open_life: dict[int, int | None] = {}

    # ------------------------------------------------------------------ reads

    def shelf_life(self, ingredient_id: int) -> ShelfLifeSpec | None:
        row = self.session.get(Ingredient, ingredient_id)
        if row is None:
            return None
        return ShelfLifeSpec(
            ingredient_id=row.id,
            storage=row.storage,
            shelf_life_days=row.shelf_life_days,
            open_life_days=row.open_life_days,
            transit_buffer_days=row.transit_buffer_days,
            source=row.shelf_life_source,
        )

    def open_life_days(self, ingredient_id: int) -> int | None:
        """Cached per repository instance: allocation asks for it once per movement."""
        if ingredient_id not in self._open_life:
            row = self.session.get(Ingredient, ingredient_id)
            self._open_life[ingredient_id] = None if row is None else row.open_life_days
        return self._open_life[ingredient_id]

    def open_batches(self, ingredient_id: int, *, at: datetime) -> list[BatchSpec]:
        """Batches with stock left at `at`, soonest EFFECTIVE expiry first.

        `received_at <= at` is part of the contract, not a nicety: a lot delivered
        this morning cannot have supplied yesterday's latte, and allowing it would
        let a back-dated expansion draw on stock that did not exist.
        """
        rows = self._live(
            select(StockBatch).where(
                StockBatch.ingredient_id == ingredient_id,
                StockBatch.received_at <= at,
                StockBatch.qty_remaining > 0,
                StockBatch.expired_at.is_(None),
            )
        )
        return self.sort_by_expiry([batch_spec(r) for r in rows], ingredient_id)

    def open_batch_rows(
        self, ingredient_id: int, *, at: datetime | None = None
    ) -> list[StockBatch]:
        """The same set as `open_batches`, as ORM rows a caller can write back to."""
        stmt = select(StockBatch).where(
            StockBatch.ingredient_id == ingredient_id,
            StockBatch.qty_remaining > 0,
            StockBatch.expired_at.is_(None),
        )
        if at is not None:
            stmt = stmt.where(StockBatch.received_at <= at)
        return self._live(stmt)

    def get(self, batch_id: int) -> BatchSpec | None:
        row = self.session.get(StockBatch, batch_id)
        return None if row is None else batch_spec(row)

    def sort_by_expiry(self, specs: Sequence[BatchSpec], ingredient_id: int) -> list[BatchSpec]:
        """Soonest effective expiry first; no-expiry lots last, oldest-received first.

        Same ordering as `domain.stock.allocate_fifo` applies internally, exposed
        because `open_batches`' ordering is a documented part of the protocol and a
        caller that reads the list without allocating must see the same order.
        """
        open_life = self.open_life_days(ingredient_id)

        def key(spec: BatchSpec) -> tuple[int, float, int]:
            expiry = spec.effective_expiry(open_life)
            if expiry is None:
                return (1, spec.received_at.timestamp(), spec.batch_id)
            return (0, expiry.timestamp(), spec.batch_id)

        return sorted(specs, key=key)

    def due_for_expiry(self, *, at: datetime, ingredient_id: int | None = None) -> list[BatchSpec]:
        """Lots past their effective expiry with stock left and not yet written off."""
        losses = self.expiry_losses_due(at=at, ingredient_id=ingredient_id)
        rows = {row.id: row for row in self._expiry_candidates(at=at, ingredient_id=ingredient_id)}
        return [batch_spec(rows[loss.batch_id]) for loss in losses if loss.batch_id in rows]

    def expiry_losses_due(
        self, *, at: datetime, ingredient_id: int | None = None
    ) -> list[ExpiryLoss]:
        """The same set as `due_for_expiry`, already costed.

        The sweep wants losses, not batches: its next move is always `mark_expired`,
        and deriving the quantity and the value twice is how the figure written off
        and the figure reported drift apart. `due_for_expiry` keeps the protocol's
        declared return type for anyone coding against it.

        The SQL narrows to plausible candidates -- anything dated, or anything opened
        (an opened lot can expire through `open_life_days` with no `expires_at` at
        all). The decision itself is `domain.stock.find_expiry_losses`, because the
        effective expiry needs `open_life_days` from a different table.
        """
        by_ingredient: dict[int, list[BatchSpec]] = {}
        for row in self._expiry_candidates(at=at, ingredient_id=ingredient_id):
            by_ingredient.setdefault(row.ingredient_id, []).append(batch_spec(row))

        losses: list[ExpiryLoss] = []
        for ing_id, specs in by_ingredient.items():
            losses.extend(
                find_expiry_losses(batches=specs, at=at, open_life_days=self.open_life_days(ing_id))
            )
        return sorted(losses, key=lambda loss: (loss.expired_at, loss.batch_id))

    def _expiry_candidates(
        self, *, at: datetime, ingredient_id: int | None = None
    ) -> list[StockBatch]:
        stmt = select(StockBatch).where(
            StockBatch.qty_remaining > 0,
            StockBatch.expired_at.is_(None),
            StockBatch.received_at <= at,
            or_(StockBatch.expires_at.is_not(None), StockBatch.opened_at.is_not(None)),
        )
        if ingredient_id is not None:
            stmt = stmt.where(StockBatch.ingredient_id == ingredient_id)
        return self._live(stmt.order_by(StockBatch.ingredient_id, StockBatch.id))

    def expiring_within(self, *, at: datetime, days: int) -> list[tuple[BatchSpec, int]]:
        """(batch, days_left) for stock that has not expired but is about to.

        The actionable half of the sweep. A write-off is money already lost; this is
        the list somebody can still sell through.
        """
        rows = self._live(
            select(StockBatch).where(
                StockBatch.qty_remaining > 0,
                StockBatch.expired_at.is_(None),
                StockBatch.received_at <= at,
                or_(StockBatch.expires_at.is_not(None), StockBatch.opened_at.is_not(None)),
            )
        )
        soon: list[tuple[BatchSpec, int]] = []
        for row in rows:
            spec = batch_spec(row)
            open_life = self.open_life_days(row.ingredient_id)
            expiry = spec.effective_expiry(open_life)
            if expiry is None or expiry <= at:
                continue
            left = spec.days_left(at, open_life)
            if left is not None and left <= days:
                soon.append((spec, left))
        return sorted(soon, key=lambda pair: pair[1])

    def count_written_off(self, *, ingredient_id: int | None = None) -> int:
        stmt = select(StockBatch.id).where(StockBatch.expired_at.is_not(None))
        if ingredient_id is not None:
            stmt = stmt.where(StockBatch.ingredient_id == ingredient_id)
        return len(list(self.session.scalars(stmt)))

    # ----------------------------------------------------------------- writes

    def create_batch(
        self,
        ingredient_id: int,
        *,
        qty: Decimal,
        received_at: datetime,
        expires_at: datetime | None,
        unit_cost_pence: Decimal,
        po_line_id: int | None = None,
        note: str | None = None,
        expiry_source: ExpirySource | None = None,
        received_by: str | None = None,
    ) -> int:
        if isinstance(qty, float) or isinstance(unit_cost_pence, float):
            raise TypeError("batch quantities must be Decimal, not float (invariant 11)")
        if qty <= 0:
            raise ValueError(f"a batch must receive a positive quantity, got {qty}")
        if received_at.tzinfo is None:
            raise ValueError("received_at must be timezone-aware UTC")
        if expires_at is not None and expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware UTC")

        row = StockBatch(
            ingredient_id=ingredient_id,
            po_line_id=po_line_id,
            qty_received=qty,
            qty_remaining=qty,
            received_at=received_at,
            expires_at=expires_at,
            unit_cost_pence=unit_cost_pence,
            note=note,
            expiry_source=expiry_source if expires_at is not None else None,
            received_by=received_by.strip()[:120] if received_by and received_by.strip() else None,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def apply_allocations(self, allocations: Sequence[DepletionAllocation], *, at: datetime) -> int:
        """Decrement `qty_remaining`, never below zero. Stamps `opened_at`.

        Unbatched allocations (`batch_id is None`) are skipped, not rejected: a
        shortfall is data (spec 5.1), and the movement that carries it has already
        been written.

        Returns the number of batches touched.
        """
        touched = 0
        for allocation in allocations:
            if allocation.batch_id is None or allocation.qty <= 0:
                continue
            row = self.session.get(StockBatch, allocation.batch_id)
            if row is None:
                continue
            take = allocation.qty if allocation.qty < row.qty_remaining else row.qty_remaining
            row.qty_remaining = row.qty_remaining - take
            self._stamp_opened(row, at)
            touched += 1
        return touched

    def mark_opened(self, batch_id: int, *, at: datetime) -> bool:
        """Record that a pack was opened. Returns False if it already was.

        Never moves an existing `opened_at`: the first opening is the one that starts
        the open-life clock, and letting a second entry push the date forward would
        extend the effective expiry of stock that is already ageing.
        """
        row = self.session.get(StockBatch, batch_id)
        if row is None:
            raise LookupError(f"stock_batch {batch_id} not found")
        if row.opened_at is not None:
            return False
        row.opened_at = at
        return True

    def mark_expired(self, losses: Sequence[ExpiryLoss], *, at: datetime) -> int:
        """Write off expired lots: one `EXPIRED` movement each, `qty_remaining` to 0.

        IDEMPOTENT, on two independent checks. `expired_at` is the primary one, and a
        batch already carrying it is skipped so a sweep that runs twice on a Monday
        does not book the loss twice. The second is a query for an existing `EXPIRED`
        movement against the same batch, which catches a row written off by the
        Phase 0 replay before `expired_at` was set.

        `expired_at` is stored as the instant the stock actually expired, not the
        instant the sweep noticed. The sweep time is `at`, and it is used only to
        refuse a loss dated in the future -- writing off stock that has not expired
        yet would be a waste figure the P&L cannot defend.

        Returns the number of batches written off.
        """
        if not losses:
            return 0

        fresh: list[ExpiryLoss] = []
        for loss in losses:
            row = self.session.get(StockBatch, loss.batch_id)
            if row is None or row.expired_at is not None or row.qty_remaining <= 0:
                continue
            if loss.expired_at > at:
                raise ValueError(
                    f"stock_batch {loss.batch_id} expires at {loss.expired_at:%Y-%m-%d %H:%M}, "
                    f"after the sweep instant {at:%Y-%m-%d %H:%M}: refusing to write off "
                    "stock that is still good"
                )
            fresh.append(loss)

        already = self._batches_with_expiry_movements([loss.batch_id for loss in fresh])
        fresh = [loss for loss in fresh if loss.batch_id not in already]
        if not fresh:
            return 0

        self.append_linked_movements(
            (movement, loss.batch_id)
            for movement, loss in zip(expiry_movements(fresh), fresh, strict=True)
        )
        for loss in fresh:
            row = self.session.get(StockBatch, loss.batch_id)
            if row is None:
                continue
            row.qty_remaining = Decimal("0")
            row.expired_at = loss.expired_at
        self.session.flush()
        return len(fresh)

    def append_linked_movements(self, pairs: Iterable[tuple[MovementSpec, int | None]]) -> int:
        """Append ledger rows carrying a `batch_id`.

        `MovementSpec` now has a `batch_id` field, so this no longer needs its own
        INSERT -- it folds the pairs into specs and delegates to the single write path
        in `SqlStockRepository.append_movements`. Kept as a convenience for callers
        that naturally hold (spec, batch) pairs from an allocation.
        """
        from dataclasses import replace

        specs = [replace(spec, batch_id=batch_id) for spec, batch_id in pairs]
        return SqlStockRepository(self.session).append_movements(specs)

    # ---------------------------------------------------------------- private

    def _live(self, stmt: Select[tuple[StockBatch]]) -> list[StockBatch]:
        """Run a batch query and RE-ASSERT its quantity predicate in Python.

        `ARCHITECTURE.md` 8E. Every filter in this module that mentions
        `qty_remaining` passes through here, so a storage change that breaks SQL
        comparison again cannot silently hand a depleted lot to FIFO.
        """
        rows = list(self.session.scalars(stmt))
        return [r for r in rows if r.qty_remaining > 0 and r.expired_at is None]

    def _stamp_opened(self, row: StockBatch, at: datetime) -> None:
        """Opening the pack IS the first draw from it. See the module docstring."""
        if row.opened_at is not None:
            return
        if self.open_life_days(row.ingredient_id) is None:
            # Opening does not shorten this ingredient's life (cups, napkins, sugar).
            # Stamping a date that changes nothing would be noise in an audit.
            return
        row.opened_at = at

    def _batches_with_expiry_movements(self, batch_ids: Sequence[int]) -> set[int]:
        if not batch_ids:
            return set()
        rows = self.session.scalars(
            select(StockMovement.batch_id).where(
                StockMovement.type == MovementType.EXPIRED,
                StockMovement.batch_id.in_(list(batch_ids)),
            )
        )
        return {r for r in rows if r is not None}
