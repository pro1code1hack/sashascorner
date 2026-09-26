"""APScheduler wiring. Every trigger is derived or configured; none is a magic weekday.

Five scheduled things, in the order the data flows:

| job | when | idempotency key |
|---|---|---|
| `daily_sync` | nightly | `sale.lightspeed_line_id` (upsert) |
| `nightly_expand` | nightly, after the sync | `sale.expanded_at` |
| `expiry_sweep` | early morning, after expansion | `stock_batch.expired_at` |
| `pre_delivery_orders` | **per supplier**, from its terms | open PO for (supplier, date) |
| morning digest | once, after the drafts exist | read-only, writes nothing |
| `drift_report` | weekly | absence of a `drift_observation` row |

The ordering inside the night is not cosmetic. Expansion has to follow the sync or it has
nothing new to expand; the sweep has to follow expansion or the morning's waste figure is
yesterday's; the drafts have to follow the sweep or they are sized against stock that has
already gone in the bin; and the digest has to follow the drafts or it reports an empty
queue every morning.

**`pre_delivery_orders` gets one cron trigger per supplier**, built by
`jobs.pre_delivery_orders.derive_schedule` from `lead_time_days`, `delivery_weekdays` and
`cutoff_time`. Tesco's `delivery_weekdays` is empty -- walk-in, any day -- so it gets a
daily trigger. No weekday is written down in this file.

`coalesce=True` with a generous `misfire_grace_time` is the deliberate pairing with
idempotency: a box that was asleep runs each missed job **once**, late, rather than seven
times or not at all. That is only safe because every job's key is in the data, which is why
the table above exists.

Nothing here sends a Telegram message unless a token and an owner chat id are both
configured; `notify.notifier_for_settings` returns a console notifier otherwise, so a
misconfigured deployment logs the digest instead of dropping it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from cafeops.bot import formatters as fmt
from cafeops.bot.notify import Notifier, notifier_for_settings
from cafeops.bot.views import build_digest, build_order_view
from cafeops.config import settings
from cafeops.db.base import SessionFactory, session_scope
from cafeops.jobs.drift_report import run_drift_report
from cafeops.jobs.expiry_sweep import sweep_expiry
from cafeops.jobs.nightly_expand import run_nightly_expand
from cafeops.jobs.pre_delivery_orders import run_pre_delivery_orders, schedules

__all__ = ["JobTimes", "build_scheduler", "describe_schedule", "main", "run"]

log = logging.getLogger("cafeops.jobs")


#: APScheduler names weekdays `mon`..`sun`; the domain uses ISO 1..7. Named rather than
#: numbered because APScheduler counts from 0 = Monday and ISO counts from 1 = Monday, and
#: an off-by-one here silently orders on the wrong day for the rest of the deployment.
_APS_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: A missed job may fire this late and still run. Six hours covers an overnight reboot.
#: Safe only because every job's idempotency key lives in the data -- see the module
#: docstring's table.
MISFIRE_GRACE_SECONDS = 6 * 60 * 60


@dataclass(frozen=True, slots=True)
class JobTimes:
    """The fixed points of the day, in LOCAL time. The order between them is load-bearing.

    Only these are configurable-by-constant; `pre_delivery_orders` derives its own times
    from each supplier's cutoff and slots in between the sweep and the digest.
    """

    sync_hour: int = 2
    sync_minute: int = 30
    expand_hour: int = 3
    expand_minute: int = 0
    sweep_hour: int = 5
    sweep_minute: int = 30
    #: Channels run AFTER the POS sync. Deliberately once a day: the source is a file the
    #: owner exports by hand (spec 4.6 -- partner APIs are gated to certified
    #: integrators), so polling faster than she produces it just re-imports the same
    #: figures.
    channel_hour: int = 2
    channel_minute: int = 50
    digest_hour: int = 7
    digest_minute: int = 30
    #: Monday, after the weekend's counts have been taken.
    drift_weekday: int = 1
    drift_hour: int = 8
    drift_minute: int = 0


def _run_sync[T](
    fn: Callable[..., T], factory: sessionmaker[Session] | None = None, **kwargs: Any
) -> T:
    with session_scope(factory or SessionFactory) as session:
        return fn(session, **kwargs)


async def _to_thread[T](
    fn: Callable[..., T], factory: sessionmaker[Session] | None = None, **kwargs: Any
) -> T:
    return await asyncio.to_thread(_run_sync, fn, factory, **kwargs)


# ==========================================================================
# The jobs, as the scheduler calls them
# ==========================================================================


async def job_daily_sync(factory: sessionmaker[Session] | None = None) -> None:
    """The nightly POS pull, recorded in `sync_run` (shell-agents spec 3.1).

    Live when Lightspeed is configured, the recorded fixtures otherwise -- the latter
    is what this job always did (`run_daily_sync`'s default), and it is recorded as a
    FIXTURES run so the stale-sync banner never mistakes a replay for real sales.
    Expansion is NOT run here: `nightly_expand` follows at 03:00 on its own trigger.
    """
    from cafeops.db.models import SyncTrigger
    from cafeops.services.sync_runs import lightspeed_ready, run_recorded_sync

    view = await asyncio.to_thread(
        run_recorded_sync,
        trigger=SyncTrigger.SCHEDULED,
        fixtures=not lightspeed_ready(),
        requested_by="scheduler",
        factory=factory,
    )
    log.info(
        "daily_sync run %s %s (%s): %s..%s, %s receipt(s), %s new line(s)",
        view.id,
        view.status,
        view.source,
        view.window_since,
        view.window_until,
        view.receipts_seen,
        view.lines_ingested,
    )
    if view.detail:
        log.warning(view.detail)


async def job_nightly_expand(factory: sessionmaker[Session] | None = None) -> None:
    report = await _to_thread(run_nightly_expand, factory)
    log.info(report.summary())
    for warning in report.warnings:
        log.warning(warning)


async def job_expiry_sweep(factory: sessionmaker[Session] | None = None) -> None:
    report = await _to_thread(sweep_expiry, factory)
    log.info(report.summary())


async def job_pre_delivery_orders(
    factory: sessionmaker[Session] | None = None,
    notifier: Notifier | None = None,
    supplier_ids: list[int] | None = None,
) -> None:
    """Build today's drafts and, if any were written, say so in Russian.

    `supplier_ids` is the supplier whose trigger fired. Each supplier gets its own cron
    entry derived from its own terms, so each run handles its own supplier: without this
    the first trigger of the day would build every supplier due today, at ITS cutoff
    rather than theirs, and the cover windows would be sized against the wrong time of
    day. Sourcing still runs across all suppliers inside `build_split` -- an ingredient
    two suppliers stock must be bought once -- only the persisting is narrowed.

    The message is sent only for drafts this run actually created. A run that found an
    order already open sends nothing: the owner has already been told about it, and a
    second notification for the same basket is how a queue gets ignored.
    """
    report = await _to_thread(run_pre_delivery_orders, factory, supplier_ids=supplier_ids)
    log.info(report.summary())
    for warning in report.warnings:
        log.warning(warning)
    created = [outcome.po_id for outcome in report.created]
    if not created:
        return
    views = [await _to_thread(build_order_view, factory, po_id=po_id) for po_id in created if po_id]
    text = fmt.job_new_drafts(views)
    if text:
        await (notifier or notifier_for_settings()).send(text)


async def job_digest(
    factory: sessionmaker[Session] | None = None, notifier: Notifier | None = None
) -> None:
    view = await _to_thread(build_digest, factory)
    await (notifier or notifier_for_settings()).send(fmt.digest(view))


async def job_drift_report(
    factory: sessionmaker[Session] | None = None, notifier: Notifier | None = None
) -> None:
    report = await _to_thread(run_drift_report, factory)
    log.info(report.summary())
    for warning in report.warnings:
        log.warning(warning)
    text = fmt.job_drift_report(
        report.rows,
        backfilled=report.observations_backfilled,
        total_checked=len(report.rows) or report.counts_seen,
    )
    await (notifier or notifier_for_settings()).send(text)


# ==========================================================================
# Wiring
# ==========================================================================


def build_scheduler(
    *,
    times: JobTimes | None = None,
    factory: sessionmaker[Session] | None = None,
    notifier: Notifier | None = None,
) -> AsyncIOScheduler:
    times = times or JobTimes()
    tz = settings.tz
    scheduler = AsyncIOScheduler(
        timezone=tz,
        job_defaults={
            # One late run, not a backlog. See the module docstring.
            "coalesce": True,
            "misfire_grace_time": MISFIRE_GRACE_SECONDS,
            "max_instances": 1,
        },
    )

    scheduler.add_job(
        job_daily_sync,
        CronTrigger(hour=times.sync_hour, minute=times.sync_minute, timezone=tz),
        kwargs={"factory": factory},
        id="daily_sync",
        name="daily_sync",
    )
    scheduler.add_job(
        job_nightly_expand,
        CronTrigger(hour=times.expand_hour, minute=times.expand_minute, timezone=tz),
        kwargs={"factory": factory},
        id="nightly_expand",
        name="nightly_expand",
    )
    scheduler.add_job(
        job_expiry_sweep,
        CronTrigger(hour=times.sweep_hour, minute=times.sweep_minute, timezone=tz),
        kwargs={"factory": factory},
        id="expiry_sweep",
        name="expiry_sweep",
    )
    # Channels once a day, after the POS sync. Deliberately NOT more often: the source
    # is a file the owner exports by hand (spec 4.6 -- partner APIs are gated), so
    # polling it faster than she produces it just re-imports the same figures. A missing
    # export is the normal state and yields an empty report, not an alert.
    scheduler.add_job(
        job_channel_sync,
        CronTrigger(hour=times.channel_hour, minute=times.channel_minute, timezone=tz),
        kwargs={"factory": factory},
        id="channel_sync",
        name="channel_sync",
    )

    # One trigger per supplier, from that supplier's own terms.
    for schedule in _read_schedules(factory):
        scheduler.add_job(
            job_pre_delivery_orders,
            CronTrigger(
                day_of_week=",".join(_APS_DAYS[day - 1] for day in schedule.weekdays),
                hour=schedule.at.hour,
                minute=schedule.at.minute,
                timezone=tz,
            ),
            kwargs={
                "factory": factory,
                "notifier": notifier,
                "supplier_ids": [schedule.supplier_id],
            },
            id=f"pre_delivery_orders:{schedule.supplier_id}",
            name=f"pre_delivery_orders:{schedule.supplier_name}",
        )

    scheduler.add_job(
        job_digest,
        CronTrigger(hour=times.digest_hour, minute=times.digest_minute, timezone=tz),
        kwargs={"factory": factory, "notifier": notifier},
        id="digest",
        name="digest",
    )
    scheduler.add_job(
        job_drift_report,
        CronTrigger(
            day_of_week=_APS_DAYS[times.drift_weekday - 1],
            hour=times.drift_hour,
            minute=times.drift_minute,
            timezone=tz,
        ),
        kwargs={"factory": factory, "notifier": notifier},
        id="drift_report",
        name="drift_report",
    )
    return scheduler


def _read_schedules(factory: sessionmaker[Session] | None) -> list[Any]:
    """Read supplier terms once, at wiring time.

    A change to a supplier's lead time or delivery days therefore needs a scheduler
    restart. That is the honest trade: re-reading them on every tick would mean the
    schedule silently changed under a draft that was already built, and six of the eight
    suppliers' terms are invented anyway (`ARCHITECTURE.md` 8F.4) -- they will be edited by
    a human who can restart a systemd unit.
    """
    with session_scope(factory or SessionFactory) as session:
        return schedules(session)


def describe_schedule(
    factory: sessionmaker[Session] | None = None, times: JobTimes | None = None
) -> str:
    """Every trigger, in words. What `cafeops jobs --list` prints."""
    times = times or JobTimes()
    names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    rows = [
        f"timezone           {settings.local_timezone}",
        f"daily_sync         every day {times.sync_hour:02d}:{times.sync_minute:02d}"
        "   (idempotent on sale.lightspeed_line_id)",
        f"nightly_expand     every day {times.expand_hour:02d}:{times.expand_minute:02d}"
        "   (idempotent on sale.expanded_at)",
        f"expiry_sweep       every day {times.sweep_hour:02d}:{times.sweep_minute:02d}"
        "   (idempotent on stock_batch.expired_at)",
        f"channel_sync       every day {times.channel_hour:02d}:{times.channel_minute:02d}"
        "   (idempotent on channel+date; a missing export is not an error)",
    ]
    for schedule in _read_schedules(factory):
        when = (
            "every day"
            if schedule.runs_daily
            else "/".join(names[day - 1] for day in schedule.weekdays)
        )
        note = " WALK-IN, any delivery day" if schedule.walk_in else ""
        placeholder = " TERMS INVENTED" if schedule.terms_are_placeholders else ""
        rows.append(
            f"pre_delivery       {schedule.supplier_name:<18} {when} "
            f"{schedule.at.hour:02d}:{schedule.at.minute:02d}  "
            f"cadence {schedule.cadence_days}d{note}{placeholder}"
        )
    rows += [
        f"digest             every day {times.digest_hour:02d}:{times.digest_minute:02d}"
        "   (read-only)",
        f"drift_report       {names[times.drift_weekday - 1]} "
        f"{times.drift_hour:02d}:{times.drift_minute:02d}"
        "        (idempotent: only counts with no observation)",
        "",
        f"missed runs fire once, up to {MISFIRE_GRACE_SECONDS // 3600}h late "
        "(coalesce=True). Safe because every key above is in the data.",
    ]
    return "\n".join(rows)


async def run(times: JobTimes | None = None) -> None:
    notifier = notifier_for_settings()
    scheduler = build_scheduler(times=times, notifier=notifier)
    log.info(
        "scheduler starting; notifications are %s", "LIVE" if notifier.is_live else "console-only"
    )
    log.info("\n%s", describe_schedule(times=times))
    scheduler.start()
    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        # Guarded: `shutdown` raises if the scheduler never started, and that exception
        # would replace whatever actually went wrong with a misleading one.
        if scheduler.running:
            scheduler.shutdown(wait=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("cafeops scheduler, %s", datetime.now(UTC).isoformat())
    asyncio.run(run())


#: Every job, by the name the CLI uses to fire one by hand.
async def job_channel_sync(
    factory: sessionmaker[Session] | None = None, notifier: Notifier | None = None
) -> None:
    """Pull Deliveroo and Just Eat figures for the trailing window.

    Registered because `jobs/channel_sync.py` existed but nothing ever called it, so
    `cafeops jobs --run channel_sync` answered "unknown job" and the channel screens only
    ever held whatever somebody had imported by hand.

    Two deliberate quirks. A channel with no export yields an EMPTY report rather than an
    error -- the owner exports one platform at a time, so a missing file is the normal
    state and must not turn into a nightly alert nobody reads. And the whole job is
    tolerant of the source being unavailable: spec 4.6 says the browser agent will break,
    and spec 9 says falling back to CSV is expected behaviour, not an incident.
    """
    from cafeops.integrations.channels import (
        ChannelSourceUnavailable,
        build_source,
    )
    from cafeops.jobs.channel_sync import default_window, sync_all_channels

    since, until = default_window()

    def _run() -> list[object]:
        try:
            primary, fallback = build_source()
        except ValueError as exc:
            log.warning("channel_sync: %s", exc)
            return []
        with session_scope(factory) as session:
            try:
                return list(
                    sync_all_channels(
                        session,
                        since=since,
                        until=until,
                        primary=primary,
                        fallback=fallback,
                    )
                )
            except ChannelSourceUnavailable as exc:
                log.warning("channel_sync: no source reachable: %s", exc)
                return []

    reports = await asyncio.to_thread(_run)
    if not reports:
        log.info("channel_sync %s..%s: nothing to import", since, until)
        return
    for report in reports:
        # Explicit rather than a getattr-with-lambda default: the lambda captured the
        # loop variable late, so every fallback line would have described the LAST
        # report. ruff's B023 caught it, and it would have been a quietly wrong log.
        summarise = getattr(report, "summary", None)
        log.info(summarise() if callable(summarise) else str(report))


JOBS: dict[str, Callable[..., Awaitable[None]]] = {
    "daily_sync": job_daily_sync,
    "nightly_expand": job_nightly_expand,
    "expiry_sweep": job_expiry_sweep,
    "pre_delivery_orders": job_pre_delivery_orders,
    "digest": job_digest,
    "drift_report": job_drift_report,
    "channel_sync": job_channel_sync,
}
