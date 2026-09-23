"""Pull the POS window, then let expansion follow. Idempotent by upsert key.

`ingest_sale_lines` upserts on `lightspeed_line_id`, so a window that is fetched twice --
a late cron run, a manual re-run, an overlapping window on purpose -- inserts nothing the
second time and reports the rows as unchanged. That is what makes the overlap below safe,
and the overlap is the point: a receipt closed at 23:55 can reach the POS reporting
endpoints after the night's sync, and a window that started exactly where the last one
ended would lose it permanently.

So the window is deliberately wider than the day: `OVERLAP_DAYS` back from the last sale
already stored, through yesterday. Re-reading three days costs three days of upserts that
find nothing, and not re-reading them costs a receipt nobody ever notices is missing.

**`fixtures=True` is the default and the only mode that works today.** Every
`lightspeed_*` credential is optional (`ARCHITECTURE.md` 0, spec 13.3) and none is set, so
a live sync has nothing to authenticate with. Passing `fixtures=False` without credentials
fails loudly inside the client rather than here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.repositories.sale import SqlSaleRepository
from cafeops.integrations.lightspeed.sync import SyncResult, sync_window

__all__ = ["OVERLAP_DAYS", "DailySyncReport", "run_daily_sync", "sync_window_for"]

#: How far back to re-read. Three days covers a weekend of late-settling receipts.
OVERLAP_DAYS = 3


@dataclass
class DailySyncReport:
    since: date
    until: date
    fixtures: bool
    result: SyncResult | None = None
    skipped_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.skipped_reason:
            return f"daily_sync skipped: {self.skipped_reason}"
        if self.result is None:  # pragma: no cover - set together with skipped_reason
            return "daily_sync: nothing ran"
        ingest = self.result.ingest
        parts = [f"daily_sync {self.since}..{self.until}"]
        if ingest is not None:
            parts.append(
                f"{ingest.inserted} new line(s), {ingest.unchanged} already present "
                "(re-reading a window is a no-op by design)"
            )
        return "; ".join(parts)


def sync_window_for(session: Session, *, today: date, overlap_days: int = OVERLAP_DAYS) -> tuple[date, date]:
    """The window to read: `overlap_days` before the last stored sale, up to yesterday.

    Yesterday, not today: the trading day is not over, and a partial day would be
    re-read tomorrow anyway. `until` never precedes `since`.
    """
    latest = SqlSaleRepository(session).latest_sold_at()
    until = today - timedelta(days=1)
    if latest is None:
        # Nothing stored at all: read the overlap window and let a human widen it.
        since = until - timedelta(days=overlap_days)
    else:
        since = latest.astimezone(settings.tz).date() - timedelta(days=overlap_days)
    if since > until:
        since = until
    return since, until


def run_daily_sync(
    session: Session,
    *,
    today: date | None = None,
    fixtures: bool = True,
    overlap_days: int = OVERLAP_DAYS,
    now: datetime | None = None,
) -> DailySyncReport:
    today = today or datetime.now(UTC).astimezone(settings.tz).date()
    since, until = sync_window_for(session, today=today, overlap_days=overlap_days)
    report = DailySyncReport(since=since, until=until, fixtures=fixtures)

    if not fixtures and not settings.lightspeed_configured:
        report.skipped_reason = (
            "no Lightspeed credentials configured, and fixtures were not requested. "
            "Nothing was read rather than pretending a sync happened."
        )
        return report

    report.result = sync_window(
        session, since=since, until=until, fixtures=fixtures, now=now or datetime.now(UTC)
    )
    if report.result.ingest is not None and report.result.ingest.unresolved_items:
        report.warnings.append(
            f"{len(report.result.ingest.unresolved_items)} sale line(s) matched no menu "
            "item and were NOT ingested. A wrongly-matched item depletes the wrong "
            "ingredient forever, so they are left for a human."
        )
    return report
