"""Every Lightspeed sync attempt, recorded. shell-agents spec 3.1 / 4.3.

`sync_run` answers the question the stale-sync banner asks -- *when did sales last
come in?* -- with the right clock. `max(sale.sold_at)` is the wrong one: the café
closes in the evening and the sync runs at 02:30, so by breakfast the newest sale is
always more than 12 hours old.

The row is also the **lock** for a manual sync. SQLite has one writer (CLAUDE.md §3),
so a second sync while one is RUNNING is refused; a RUNNING row older than
`ABANDON_AFTER` is presumed dead (the process restarted mid-sync) and closed as FAILED
"abandoned" rather than blocking every sync forever.

Three sessions per run, deliberately: the RUNNING row is committed before the sync
starts (so the lock and the attempt are visible even if the sync dies), the sync
commits its own ingest, and the outcome is written last. A single transaction would
lose the attempt on the very failures worth recording.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cafeops.config import settings
from cafeops.db.base import SessionFactory, session_scope
from cafeops.db.models import SyncRun, SyncSource, SyncStatus, SyncTrigger

__all__ = [
    "ABANDON_AFTER",
    "MANUAL_COOLDOWN",
    "SyncRefused",
    "SyncRunView",
    "begin_manual_sync",
    "latest_attempt",
    "latest_ok",
    "lightspeed_missing",
    "lightspeed_ready",
    "record_cli_sync",
    "run_recorded_sync",
    "run_view",
]

ABANDON_AFTER = timedelta(minutes=15)
#: One manual sync per this long. Re-reading the window is a no-op by design
#: (`daily_sync` upserts), so a faster button only buys load on the single writer.
MANUAL_COOLDOWN = timedelta(minutes=5)

LIGHTSPEED_ENV_VARS: tuple[str, ...] = (
    "CAFEOPS_LIGHTSPEED_CLIENT_ID",
    "CAFEOPS_LIGHTSPEED_CLIENT_SECRET",
    "CAFEOPS_LIGHTSPEED_REFRESH_TOKEN",
    "CAFEOPS_LIGHTSPEED_BUSINESS_ID",
)


def lightspeed_missing() -> list[str]:
    """The Lightspeed variables that are not set. Names only, never values.

    The same four `config.Settings.lightspeed_configured` requires; this one names
    what is missing.
    """
    values = (
        settings.lightspeed_client_id,
        settings.lightspeed_client_secret,
        settings.lightspeed_refresh_token,
        settings.lightspeed_business_id,
    )
    return [name for name, value in zip(LIGHTSPEED_ENV_VARS, values, strict=True) if not value]


def lightspeed_ready() -> bool:
    return not lightspeed_missing()


class SyncRefused(Exception):
    """A manual sync was not started. `status` is the HTTP answer."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class SyncRunView:
    id: int
    trigger: str
    source: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    window_since: date
    window_until: date
    receipts_seen: int | None
    lines_ingested: int | None
    unresolved_count: int | None
    detail: str | None
    requested_by: str | None


def run_view(row: SyncRun) -> SyncRunView:
    return SyncRunView(
        id=row.id,
        trigger=row.trigger.value,
        source=row.source.value,
        status=row.status.value,
        started_at=row.started_at,
        finished_at=row.finished_at,
        window_since=row.window_since,
        window_until=row.window_until,
        receipts_seen=row.receipts_seen,
        lines_ingested=row.lines_ingested,
        unresolved_count=row.unresolved_count,
        detail=row.detail,
        requested_by=row.requested_by,
    )


def latest_ok(session: Session) -> SyncRun | None:
    """The newest LIVE sync that brought sales in (OK or PARTIAL).

    Fixture syncs are excluded: a replay of recorded receipts says nothing about
    whether the till's sales are reaching the system.
    """
    return session.scalar(
        select(SyncRun)
        .where(
            SyncRun.status.in_([SyncStatus.OK, SyncStatus.PARTIAL]),
            SyncRun.source == SyncSource.LIVE,
        )
        .order_by(SyncRun.finished_at.desc(), SyncRun.id.desc())
        .limit(1)
    )


def latest_attempt(session: Session) -> SyncRun | None:
    return session.scalar(select(SyncRun).order_by(SyncRun.started_at.desc(), SyncRun.id.desc()))


def _close_abandoned(session: Session, now: datetime) -> SyncRun | None:
    """Mark stale RUNNING rows FAILED; return a live RUNNING row if one remains."""
    live: SyncRun | None = None
    for row in session.scalars(select(SyncRun).where(SyncRun.status == SyncStatus.RUNNING)):
        if now - row.started_at > ABANDON_AFTER:
            row.status = SyncStatus.FAILED
            row.finished_at = now
            row.detail = (
                f"abandoned: still RUNNING {int((now - row.started_at).total_seconds() // 60)} "
                "min after it started, so the process running it most likely stopped"
            )
        else:
            live = row
    return live


def _start(
    session: Session,
    *,
    trigger: SyncTrigger,
    source: SyncSource,
    requested_by: str | None,
    now: datetime,
) -> SyncRun:
    from cafeops.jobs.daily_sync import sync_window_for

    today = now.astimezone(settings.tz).date()
    since, until = sync_window_for(session, today=today)
    row = SyncRun(
        trigger=trigger,
        source=source,
        started_at=now,
        status=SyncStatus.RUNNING,
        window_since=since,
        window_until=until,
        requested_by=requested_by,
    )
    session.add(row)
    session.flush()
    return row


def record_cli_sync(
    session: Session,
    *,
    fixtures: bool,
    since: date,
    until: date,
    started_at: datetime,
    receipts_seen: int,
    lines_ingested: int | None,
    unresolved_count: int | None,
    partial_reason: str | None,
) -> SyncRun:
    """Record a committed `cafeops sync --from/--to` run after the fact.

    The CLI takes an explicit window and has its own dry-run path, so it does not go
    through `run_recorded_sync`; it still belongs in the history, or a manual live
    backfill would leave the stale banner up. Dry runs are not recorded.
    """
    row = SyncRun(
        trigger=SyncTrigger.CLI,
        source=SyncSource.FIXTURES if fixtures else SyncSource.LIVE,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        status=SyncStatus.PARTIAL if partial_reason else SyncStatus.OK,
        window_since=since,
        window_until=until,
        receipts_seen=receipts_seen,
        lines_ingested=lines_ingested,
        unresolved_count=unresolved_count,
        detail=partial_reason,
        requested_by="cli",
    )
    session.add(row)
    session.flush()
    return row


def begin_manual_sync(session: Session, *, requested_by: str | None) -> int:
    """Take the lock for a web "Sync now" and return the new run's id.

    Refuses (412) when Lightspeed is not configured -- the web never runs a fixtures
    sync, which would replay sample receipts into a real database -- and (409) while
    another sync is running or one was requested in the last `MANUAL_COOLDOWN`.
    The caller commits, then calls `run_recorded_sync(run_id=...)`.
    """
    missing = lightspeed_missing()
    if missing:
        raise SyncRefused(
            "Lightspeed isn't connected, so there is nothing to sync from. Set "
            + ", ".join(missing)
            + " on the server. The web never replays fixture receipts into the database.",
            status=412,
        )
    now = datetime.now(UTC)
    live = _close_abandoned(session, now)
    if live is not None:
        raise SyncRefused(
            f"A sync is already running (started {int((now - live.started_at).total_seconds())}s "
            "ago). It will finish on its own; there is only one writer.",
            status=409,
        )
    recent = session.scalar(
        select(SyncRun)
        .where(
            SyncRun.trigger == SyncTrigger.MANUAL_WEB, SyncRun.started_at > now - MANUAL_COOLDOWN
        )
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    )
    if recent is not None:
        wait = int((recent.started_at + MANUAL_COOLDOWN - now).total_seconds()) + 1
        raise SyncRefused(
            f"Synced a moment ago. Try again in {wait}s: re-reading the same window "
            "changes nothing.",
            status=409,
        )
    row = _start(
        session,
        trigger=SyncTrigger.MANUAL_WEB,
        source=SyncSource.LIVE,
        requested_by=requested_by,
        now=now,
    )
    return row.id


def run_recorded_sync(
    *,
    trigger: SyncTrigger,
    fixtures: bool,
    requested_by: str | None,
    run_id: int | None = None,
    expand_after: bool = False,
    factory: sessionmaker[Session] | None = None,
) -> SyncRunView:
    """Run `daily_sync` and record it in `sync_run`. Blocking; call it on a thread.

    `run_id` names a RUNNING row already created by `begin_manual_sync`; without it a
    row is created here (the scheduled path), refusing to start -- recorded as
    SKIPPED -- while another sync holds the lock. `expand_after` then turns the new
    sales into stock movements, so "Sync now" updates the stock estimate the banner
    said was behind, not just the sales table.
    """
    from cafeops.jobs.daily_sync import run_daily_sync
    from cafeops.jobs.nightly_expand import run_nightly_expand

    factory = factory or SessionFactory
    source = SyncSource.FIXTURES if fixtures else SyncSource.LIVE

    with session_scope(factory) as session:
        now = datetime.now(UTC)
        if run_id is None:
            live = _close_abandoned(session, now)
            row = _start(
                session, trigger=trigger, source=source, requested_by=requested_by, now=now
            )
            if live is not None:
                row.status = SyncStatus.SKIPPED
                row.finished_at = now
                row.detail = f"another sync (run {live.id}) was still running"
                return run_view(row)
            run_id = row.id

    status = SyncStatus.OK
    detail_parts: list[str] = []
    receipts: int | None = None
    ingested: int | None = None
    unresolved: int | None = None
    try:
        with session_scope(factory) as session:
            report = run_daily_sync(session, fixtures=fixtures)
            if report.skipped_reason:
                status = SyncStatus.SKIPPED
                detail_parts.append(report.skipped_reason)
            elif report.result is not None:
                result = report.result
                receipts = result.receipts_seen
                if result.ingest is not None:
                    ingested = result.ingest.inserted
                    unresolved = len(result.ingest.unresolved_items)
                if result.partial_reason:
                    status = SyncStatus.PARTIAL
                    detail_parts.append(result.partial_reason)
            detail_parts.extend(report.warnings)
        if expand_after and status in (SyncStatus.OK, SyncStatus.PARTIAL):
            with session_scope(factory) as session:
                expand = run_nightly_expand(session)
                detail_parts.append(f"stock: {expand.summary()}")
                detail_parts.extend(expand.warnings)
    except Exception as exc:  # recorded, then re-raised for the scheduler's log
        with session_scope(factory) as session:
            failed = session.get(SyncRun, run_id)
            if failed is not None:
                row = failed
                row.status = SyncStatus.FAILED
                row.finished_at = datetime.now(UTC)
                row.receipts_seen = receipts
                row.lines_ingested = ingested
                row.unresolved_count = unresolved
                row.detail = f"{type(exc).__name__}: {exc}"[:2000]
        raise

    with session_scope(factory) as session:
        done = session.get(SyncRun, run_id)
        if done is None:  # pragma: no cover - the row was created above
            raise LookupError(f"sync_run {run_id} vanished")
        row = done
        row.status = status
        row.finished_at = datetime.now(UTC)
        row.receipts_seen = receipts
        row.lines_ingested = ingested
        row.unresolved_count = unresolved
        row.detail = "\n".join(detail_parts)[:4000] or None
        return run_view(row)
