"""The expiry sweep: write off stock that reached its date, and say what it cost.

This job produces the only honest waste figure in the system. Everything else is
either a sale or a guess: `waste_factor` absorbs spillage as a ratio, and drift
measures loss without being able to name it. An `EXPIRED` movement names it -- this
lot, this quantity, this money, on this date -- which is what lets a drift report
tell over-ordering apart from a bad recipe (spec 5.2), and the two have opposite
fixes.

Three properties.

1. **Idempotent.** `stock_batch.expired_at` exists for this. A sweep that runs twice
   on a Monday, or a late run on Wednesday covering the weekend, must book each loss
   exactly once -- a waste figure that grows every time cron fires is worse than no
   figure at all. `SqlBatchRepository.mark_expired` enforces it on two independent
   checks; this job simply must not work around them.

2. **The write-off is dated when the stock expired, not when the sweep noticed.** A
   Monday sweep that finds Saturday's milk writes a Saturday movement. Dating it
   Monday would put the loss in the wrong week's P&L and, worse, would leave Saturday
   and Sunday's theoretical on-hand overstated forever -- so a count taken on Sunday
   would show drift that the ledger then permanently disagreed with.

3. **It reports what is about to expire as well as what did.** A write-off is money
   already gone; the short-dated list is the only part anybody can still act on. They
   are separate fields because they call for opposite responses -- one is a note to
   the ordering cadence, the other is "sell this today".

`sweep_expiry` writes inside the caller's transaction and commits nothing, so the
scheduler, the CLI and the bot all get the same unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from cafeops.db.models import Ingredient, StockBatch
from cafeops.db.repositories.batch import SqlBatchRepository
from cafeops.domain.types import ExpiryLoss, Unit
from cafeops.services.receive_delivery import ASSUMED_EXPIRY_NOTE

__all__ = ["ExpirySweepReport", "ShortDated", "WriteOff", "sweep_expiry"]

#: Stock inside this many days of its effective expiry is reported as short-dated.
#: Three days is the window in which somebody can still plausibly sell it through:
#: the same threshold `cafeops stock` colours red.
DEFAULT_SHORT_DATED_DAYS = 3


@dataclass(frozen=True, slots=True)
class WriteOff:
    """One lot that reached its date with stock in it. The alert list is these."""

    batch_id: int
    ingredient_id: int
    ingredient_name: str
    unit: Unit
    qty: Decimal
    expired_at: datetime
    days_overdue: int
    #: None when the batch carries no unit cost -- invariant 8: a loss we cannot
    #: price stays unpriced rather than counting as zero in the total.
    loss_pence: Decimal | None
    #: True when `open_life_days` brought the expiry forward. That is a different
    #: problem from ordering too much: the pack was opened and then not used.
    expired_after_opening: bool
    expiry_was_assumed: bool

    def line(self) -> str:
        money = "unpriced" if self.loss_pence is None else f"GBP {self.loss_pence / 100:.2f}"
        why = " (open life)" if self.expired_after_opening else ""
        flag = " [date ASSUMED]" if self.expiry_was_assumed else ""
        return (
            f"{self.ingredient_name}: {self.qty} {self.unit.value} written off, {money}"
            f" -- expired {self.expired_at:%Y-%m-%d}, {self.days_overdue}d ago{why}{flag}"
        )


@dataclass(frozen=True, slots=True)
class ShortDated:
    """Stock that has NOT expired and still can be sold. The actionable half."""

    batch_id: int
    ingredient_id: int
    ingredient_name: str
    unit: Unit
    qty: Decimal
    expires_at: datetime
    days_left: int
    value_pence: Decimal | None

    def line(self) -> str:
        money = "unpriced" if self.value_pence is None else f"GBP {self.value_pence / 100:.2f}"
        return (
            f"{self.ingredient_name}: {self.qty} {self.unit.value} ({money}) expires in "
            f"{self.days_left}d on {self.expires_at:%Y-%m-%d}"
        )


@dataclass
class ExpirySweepReport:
    swept_at: datetime
    write_offs: list[WriteOff] = field(default_factory=list)
    short_dated: list[ShortDated] = field(default_factory=list)
    movements_written: int = 0
    batches_written_off: int = 0
    #: Batches already carrying `expired_at` before this run. Printed so a second run
    #: visibly says "nothing new" rather than looking like it did nothing at all.
    already_written_off: int = 0
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def total_qty(self) -> Decimal:
        return sum((w.qty for w in self.write_offs), Decimal("0"))

    @property
    def total_loss_pence(self) -> Decimal:
        """Priced losses only. Unpriced ones are counted separately, never as zero."""
        return sum(
            (w.loss_pence for w in self.write_offs if w.loss_pence is not None), Decimal("0")
        )

    @property
    def unpriced_write_offs(self) -> int:
        return sum(1 for w in self.write_offs if w.loss_pence is None)

    @property
    def alerts(self) -> list[str]:
        """The alert list the brief asks for: write-offs first, then what is at risk."""
        return [w.line() for w in self.write_offs] + [s.line() for s in self.short_dated]

    def summary(self) -> str:
        parts: list[str] = []
        if self.write_offs:
            parts.append(
                f"{len(self.write_offs)} expiry write-off(s) worth "
                f"GBP {self.total_loss_pence / 100:.2f}"
            )
        else:
            parts.append("nothing expired")
        if self.unpriced_write_offs:
            parts.append(f"{self.unpriced_write_offs} of them unpriced (excluded from the total)")
        if self.already_written_off:
            parts.append(f"{self.already_written_off} batch(es) already written off")
        if self.short_dated:
            parts.append(f"{len(self.short_dated)} batch(es) short-dated")
        if self.dry_run:
            parts.append("DRY RUN: nothing written")
        return "; ".join(parts)


def sweep_expiry(
    session: Session,
    *,
    at: datetime | None = None,
    ingredient_id: int | None = None,
    short_dated_days: int = DEFAULT_SHORT_DATED_DAYS,
    dry_run: bool = False,
) -> ExpirySweepReport:
    """Write off everything past its effective expiry as of `at`.

    `at` defaults to now. Passing an earlier instant is how the job is replayed for a
    past day without back-dating anything that had not yet expired; passing a later
    one is refused by `mark_expired`, which will not write off stock that is still
    good.
    """
    at = at or datetime.now(UTC)
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware (spec 4: all timestamps UTC)")

    repo = SqlBatchRepository(session)
    report = ExpirySweepReport(swept_at=at, dry_run=dry_run)
    report.already_written_off = repo.count_written_off(ingredient_id=ingredient_id)

    losses = repo.expiry_losses_due(at=at, ingredient_id=ingredient_id)
    names = _ingredient_index(session, [loss.ingredient_id for loss in losses])

    for loss in losses:
        ingredient = names.get(loss.ingredient_id)
        if ingredient is None:  # pragma: no cover - FK guarantees it
            report.warnings.append(f"batch {loss.batch_id}: ingredient row missing")
            continue
        report.write_offs.append(_write_off(repo, loss, ingredient, at))

    if not dry_run and losses:
        report.batches_written_off = repo.mark_expired(losses, at=at)
        # One EXPIRED movement per batch written off: mark_expired writes them in the
        # same call precisely so this count cannot disagree with the ledger.
        report.movements_written = report.batches_written_off

    for spec, days_left in repo.expiring_within(at=at, days=short_dated_days):
        ingredient = _ingredient(session, spec.ingredient_id)
        if ingredient is None:  # pragma: no cover
            continue
        expiry = spec.effective_expiry(repo.open_life_days(spec.ingredient_id))
        if expiry is None:  # pragma: no cover - expiring_within filtered these out
            continue
        report.short_dated.append(
            ShortDated(
                batch_id=spec.batch_id,
                ingredient_id=spec.ingredient_id,
                ingredient_name=ingredient.name,
                unit=ingredient.unit,
                qty=spec.qty_remaining,
                expires_at=expiry,
                days_left=days_left,
                value_pence=(
                    None
                    if spec.unit_cost_pence is None
                    else spec.qty_remaining * spec.unit_cost_pence
                ),
            )
        )
    return report


def _write_off(
    repo: SqlBatchRepository, loss: ExpiryLoss, ingredient: Ingredient, at: datetime
) -> WriteOff:
    spec = repo.get(loss.batch_id)
    note = "" if spec is None else _note_for(repo, loss.batch_id)
    # Opened AND the open-life deadline is what bit, rather than the carton date. That
    # is a different fault from ordering too much: the pack was opened and then not
    # used, so the fix is portioning or a smaller pack, not a shorter cover window.
    after_opening = (
        spec is not None
        and spec.opened_at is not None
        and (spec.expires_at is None or loss.expired_at < spec.expires_at)
    )
    return WriteOff(
        batch_id=loss.batch_id,
        ingredient_id=loss.ingredient_id,
        ingredient_name=ingredient.name,
        unit=ingredient.unit,
        qty=loss.qty,
        expired_at=loss.expired_at,
        days_overdue=(at - loss.expired_at).days,
        loss_pence=loss.loss_pence,
        expired_after_opening=after_opening,
        expiry_was_assumed=ASSUMED_EXPIRY_NOTE in note,
    )


def _note_for(repo: SqlBatchRepository, batch_id: int) -> str:
    row = repo.session.get(StockBatch, batch_id)
    return "" if row is None or row.note is None else row.note


def _ingredient(session: Session, ingredient_id: int) -> Ingredient | None:
    return session.get(Ingredient, ingredient_id)


def _ingredient_index(session: Session, ingredient_ids: list[int]) -> dict[int, Ingredient]:
    found: dict[int, Ingredient] = {}
    for ingredient_id in set(ingredient_ids):
        row = session.get(Ingredient, ingredient_id)
        if row is not None:
            found[ingredient_id] = row
    return found
