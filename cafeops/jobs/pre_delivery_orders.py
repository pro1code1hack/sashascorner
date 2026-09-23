"""Build DRAFT orders before each supplier's own delivery day. Never a hardcoded day.

The schedule is DERIVED, per supplier, from `lead_time_days`, `delivery_weekdays` and
`cutoff_time`. `derive_schedule` is the whole idea:

```
order weekday = delivery weekday - lead_time_days      (mod 7)
order time    = cutoff_time - CUTOFF_MARGIN            (clamped into the working day)
cadence       = the longest gap between two order runs of this supplier
```

Booker delivers Tue/Thu on a 2-day lead, so drafts are built Sunday and Tuesday. Brakes
delivers Mon/Wed/Fri on a 1-day lead, so Sunday, Tuesday and Thursday. **Tesco's
`delivery_weekdays` is EMPTY** -- it is a walk-in shop and every day is a delivery day --
so it gets a daily run. None of those weekdays appears in this file as a constant.

Two consequences worth stating, because they are decisions and not mechanics:

**The cutoff is respected by the schedule, not just reported by it.** A job that fired at
17:00 for a supplier whose cutoff is noon would size a cover window that silently grew a
day (`domain/ordering.cutoff_slip_days`). Firing two hours *before* the cutoff means the
window the draft is sized on is the window the order will actually get.

**The cadence is the interval between this job's own runs.** `cover_window`'s middle term
is spec 5.4's ambiguity (`ARCHITECTURE.md` 8C, open question 7): the literal reading
assumes reordering at every delivery opportunity. Here that assumption is *made true* --
the job runs once per opportunity -- so the honest cadence is the gap to the next run, and
that is what gets passed. For Tesco the gap is one day, which is exactly the "she walks to
a shop most mornings" reading open question 7 asks the owner to confirm. It is the one
number that changes every Tesco quantity, and it is now derived from the supplier's own
terms rather than assumed to be a week.

## Idempotency

The key is **(supplier, target_delivery_date) with an order already open**. A run that
fires late, twice, or after a manual `simulate --commit` finds the existing DRAFT and
writes nothing. `OPEN_PO_STATUSES` includes DRAFT precisely so this works, and
`open_qty_for` counts DRAFT quantities as on order, so even a missed guard would not
double the quantity -- it would produce a second empty order, which is why the guard is
explicit anyway: an empty order in front of the owner asking her to confirm nothing is its
own failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import PurchaseOrder
from cafeops.db.repositories.purchase_order import OPEN_PO_STATUSES
from cafeops.db.repositories.sourcing import SqlSourcingRepository
from cafeops.domain.types import SupplierTerms, Tier
from cafeops.services.build_order import ForecastKnobs, build_split, create_draft_po

__all__ = [
    "CUTOFF_MARGIN",
    "DEFAULT_ORDER_TIME",
    "OrderSchedule",
    "PreDeliveryReport",
    "SupplierOutcome",
    "derive_schedule",
    "run_pre_delivery_orders",
]

#: How long before a supplier's cutoff the draft is built. Long enough that a person has
#: to read it and press a button before the cutoff passes -- the draft is useless if it
#: arrives after the order could have been placed.
CUTOFF_MARGIN = timedelta(hours=3)

#: Used when a supplier has no `cutoff_time`. Early, so drafts exist before the digest.
DEFAULT_ORDER_TIME = time(7, 0)

#: The schedule is clamped into these hours. A derived 03:00 run is technically correct
#: and useless: nobody confirms an order at three in the morning, and the draft would sit
#: unread until after the cutoff it was built for.
_EARLIEST = time(6, 0)
_LATEST = time(20, 0)


@dataclass(frozen=True, slots=True)
class OrderSchedule:
    """When this supplier's drafts get built, derived from its own terms."""

    supplier_id: int
    supplier_name: str
    #: ISO weekdays 1..7 to run on. Every weekday when the supplier is walk-in.
    weekdays: tuple[int, ...]
    at: time
    #: The longest gap, in days, between two consecutive runs. Feeds `cover_window`.
    cadence_days: int
    #: True when `delivery_weekdays` was empty -- walk-in, any day (Tesco).
    walk_in: bool
    terms_are_placeholders: bool

    @property
    def runs_daily(self) -> bool:
        return len(self.weekdays) == 7

    def due_on(self, day: date) -> bool:
        return day.isoweekday() in self.weekdays


def _clamp(at: time) -> time:
    if at < _EARLIEST:
        return _EARLIEST
    if at > _LATEST:
        return _LATEST
    return at


def _before_cutoff(cutoff: time | None) -> time:
    if cutoff is None:
        return DEFAULT_ORDER_TIME
    anchor = datetime.combine(date(2000, 1, 1), cutoff) - CUTOFF_MARGIN
    return _clamp(anchor.time())


def _max_gap(weekdays: tuple[int, ...]) -> int:
    """The longest wait between two consecutive runs, cyclically over a week.

    The MAXIMUM, not the mean: the cover window has to survive the longest gap or the
    supplier runs out in it. A Tue/Thu supplier waits five days over the weekend, and
    sizing to the 2-day gap would leave Monday short every week.
    """
    if not weekdays:
        return 1
    if len(weekdays) == 1:
        return 7
    ordered = sorted(weekdays)
    gaps = [b - a for a, b in pairwise(ordered)]
    gaps.append(ordered[0] + 7 - ordered[-1])
    return max(gaps)


def derive_schedule(terms: SupplierTerms) -> OrderSchedule:
    """The supplier's own delivery calendar, walked backwards through its lead time."""
    if terms.delivery_weekdays:
        weekdays = tuple(
            sorted({((day - 1 - terms.lead_time_days) % 7) + 1 for day in terms.delivery_weekdays})
        )
    else:
        # EMPTY means any day (walk-in retail). Every day is a delivery opportunity, so
        # every day is an order opportunity.
        weekdays = (1, 2, 3, 4, 5, 6, 7)
    return OrderSchedule(
        supplier_id=terms.supplier_id,
        supplier_name=terms.name,
        weekdays=weekdays,
        at=_before_cutoff(terms.cutoff_time),
        cadence_days=_max_gap(weekdays),
        walk_in=not terms.delivery_weekdays,
        terms_are_placeholders=terms.terms_are_placeholders,
    )


def schedules(session: Session) -> list[OrderSchedule]:
    return [derive_schedule(terms) for terms in SqlSourcingRepository(session).all_terms()]


@dataclass(frozen=True, slots=True)
class SupplierOutcome:
    supplier_id: int
    supplier_name: str
    due: bool
    target_delivery_date: date | None
    po_id: int | None
    lines: int
    total_pence: int
    #: Set when nothing was written, with the reason. Never silent.
    skipped: str | None = None
    capped_lines: int = 0
    top_up_lines: int = 0
    low_confidence_lines: int = 0
    terms_are_placeholders: bool = False


@dataclass
class PreDeliveryReport:
    order_date: date
    outcomes: list[SupplierOutcome] = field(default_factory=list)
    emergency_lines: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def created(self) -> list[SupplierOutcome]:
        """Orders this run actually wrote.

        An outcome that carries a `po_id` AND a `skipped` reason is the idempotency guard
        naming the order that already existed. Counting it as created would make a
        repeated run report work it did not do -- and would send the owner a second
        notification about a basket she has already seen.
        """
        return [o for o in self.outcomes if o.po_id is not None and o.skipped is None]

    @property
    def matched_existing(self) -> list[SupplierOutcome]:
        """Suppliers where a run found an order already open and wrote nothing."""
        return [o for o in self.outcomes if o.po_id is not None and o.skipped is not None]

    def summary(self) -> str:
        created = self.created
        due = [o for o in self.outcomes if o.due]
        parts = [
            f"pre_delivery_orders {self.order_date}",
            f"{len(due)} supplier(s) due, {len(created)} draft(s) written",
        ]
        if created:
            parts.append(
                ", ".join(
                    f"{o.supplier_name} #{o.po_id} ({o.lines} line(s), "
                    f"{o.total_pence / 100:.2f} GBP)"
                    for o in created
                )
            )
        if self.matched_existing:
            parts.append(
                f"{len(self.matched_existing)} supplier(s) already had an open order for "
                "the same delivery date, so nothing was written for them"
            )
        if self.emergency_lines:
            parts.append(f"{self.emergency_lines} emergency line(s) routed to retail")
        return "; ".join(parts)


def _existing_open_order(session: Session, *, supplier_id: int, target: date) -> int | None:
    """The idempotency check. See the module docstring."""
    return session.scalar(
        select(PurchaseOrder.id).where(
            PurchaseOrder.supplier_id == supplier_id,
            PurchaseOrder.target_delivery_date == target,
            PurchaseOrder.status.in_(list(OPEN_PO_STATUSES)),
        )
    )


def run_pre_delivery_orders(
    session: Session,
    *,
    order_date: date | None = None,
    at: time | None = None,
    supplier_ids: list[int] | None = None,
    tz: ZoneInfo | None = None,
    force: bool = False,
    require_auto_order: bool = False,
    tiers: tuple[Tier, ...] = (Tier.A, Tier.B),
) -> PreDeliveryReport:
    """Build and persist DRAFT orders for every supplier due today.

    `require_auto_order=False`: every order this writes is a DRAFT a human must confirm
    (invariant 1), so `par_level.auto_order_enabled` is not what gates it. Pass True for a
    genuinely unattended path, where invariant 2 does.

    `force=True` ignores the idempotency guard. It exists for a re-run after an order was
    cancelled and must not be used by the scheduler.

    Sourcing runs across ALL suppliers even when only some are due, because an ingredient
    stocked by two suppliers has to be bought once (`build_split` pass two). A supplier
    that wins a line but is not due today simply orders it on its own day, and the DRAFT
    written today already counts against `open_qty_for`, so nothing is bought twice.
    """
    tz = tz or settings.tz
    order_date = order_date or datetime.now(UTC).astimezone(tz).date()
    report = PreDeliveryReport(order_date=order_date)

    all_schedules = {s.supplier_id: s for s in schedules(session)}
    due_ids = {
        sid
        for sid, schedule in all_schedules.items()
        if schedule.due_on(order_date) and (supplier_ids is None or sid in supplier_ids)
    }
    if not due_ids:
        return report

    # The time of day the order is treated as placed, which decides whether the cutoff
    # was made (`domain/ordering.cutoff_slip_days`).
    #
    # For a run happening TODAY that is the wall clock, not the schedule: a job that
    # misfired and is running four hours late has genuinely missed a noon cutoff, and
    # pretending it fired on time would size a cover window the order will not get.
    # For a REPLAY of a past date there is no real clock, so the supplier's own derived
    # schedule time is used -- inventing a time of day for a hypothetical past order
    # would change quantities on a guess.
    if at is not None:
        order_time = at
    elif order_date == datetime.now(UTC).astimezone(tz).date():
        order_time = datetime.now(UTC).astimezone(tz).time()
    else:
        order_time = min(all_schedules[sid].at for sid in due_ids)

    # The cadence differs per supplier, and `build_split` takes one. Run it per due
    # supplier's cadence would re-source repeatedly; instead the widest cadence among the
    # due suppliers is used, which is the conservative direction: a cover window sized on
    # a longer gap than the real one over-covers slightly rather than running out.
    cadence = max(all_schedules[sid].cadence_days for sid in due_ids)

    result = build_split(
        session,
        order_date=order_date,
        knobs=ForecastKnobs.from_settings(),
        tiers=tiers,
        reorder_cadence_days=cadence,
        require_auto_order=require_auto_order,
        tz=tz,
        order_time=order_time,
        respect_cutoff=True,
    )
    report.emergency_lines = len(result.split.emergency)

    for suggestion in result.split.suggestions:
        supplier = suggestion.supplier
        schedule = all_schedules.get(supplier.id)
        due = supplier.id in due_ids
        placeholders = bool(schedule and schedule.terms_are_placeholders)
        if not due:
            report.outcomes.append(
                SupplierOutcome(
                    supplier_id=supplier.id,
                    supplier_name=supplier.name,
                    due=False,
                    target_delivery_date=suggestion.target_delivery_date,
                    po_id=None,
                    lines=len(suggestion.lines),
                    total_pence=suggestion.total_pence,
                    skipped=(
                        "not this supplier's order day: its next run is derived from its "
                        "own delivery weekdays and lead time"
                    ),
                    terms_are_placeholders=placeholders,
                )
            )
            continue

        capped = sum(1 for line in suggestion.lines if line.cap_reason and not line.is_top_up)
        top_ups = sum(1 for line in suggestion.lines if line.is_top_up)
        low_conf = sum(1 for line in suggestion.lines if line.low_confidence)

        if not suggestion.lines:
            report.outcomes.append(
                SupplierOutcome(
                    supplier_id=supplier.id,
                    supplier_name=supplier.name,
                    due=True,
                    target_delivery_date=suggestion.target_delivery_date,
                    po_id=None,
                    lines=0,
                    total_pence=0,
                    skipped=(
                        "nothing is needed. An order with no lines is a message, not a "
                        "purchase order, and writing it would ask the owner to confirm "
                        "nothing"
                    ),
                    terms_are_placeholders=placeholders,
                )
            )
            continue

        existing = (
            None
            if force
            else _existing_open_order(
                session,
                supplier_id=supplier.id,
                target=suggestion.target_delivery_date,
            )
        )
        if existing is not None:
            report.outcomes.append(
                SupplierOutcome(
                    supplier_id=supplier.id,
                    supplier_name=supplier.name,
                    due=True,
                    target_delivery_date=suggestion.target_delivery_date,
                    po_id=existing,
                    lines=len(suggestion.lines),
                    total_pence=suggestion.total_pence,
                    skipped=(
                        f"order {existing} is already open for this delivery date. A late "
                        "or repeated run writes nothing"
                    ),
                    capped_lines=capped,
                    top_up_lines=top_ups,
                    low_confidence_lines=low_conf,
                    terms_are_placeholders=placeholders,
                )
            )
            continue

        plan = next((p for p in result.plans if p.suggestion.supplier.id == supplier.id), None)
        if plan is None:  # pragma: no cover - suggestions come from plans
            continue
        po_id = create_draft_po(session, plan)
        report.outcomes.append(
            SupplierOutcome(
                supplier_id=supplier.id,
                supplier_name=supplier.name,
                due=True,
                target_delivery_date=suggestion.target_delivery_date,
                po_id=po_id,
                lines=len(suggestion.lines),
                total_pence=suggestion.total_pence,
                capped_lines=capped,
                top_up_lines=top_ups,
                low_confidence_lines=low_conf,
                terms_are_placeholders=placeholders,
            )
        )

    placeholder_names = sorted(
        {o.supplier_name for o in report.created if o.terms_are_placeholders}
    )
    if placeholder_names:
        report.warnings.append(
            "Drafts were built on INVENTED terms for: "
            + ", ".join(placeholder_names)
            + ". Lead time, delivery days and cutoff were never confirmed with them, and "
            "this job's whole schedule is derived from those three numbers "
            "(ARCHITECTURE.md 8F.4)."
        )
    return report
