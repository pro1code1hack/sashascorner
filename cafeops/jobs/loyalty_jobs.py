"""Sasha's Corner Rewards' scheduled work (CONTRACT §7), as the scheduler calls it.

| job | when | idempotency key |
|---|---|---|
| `wallet_outbox` | every minute | `wallet_push_outbox.done_at` (the wallet module's) |
| `loyalty_campaigns` | every 5 minutes | `loyalty_campaign.sent_at`; `returned_at`; `notified_at` |
| `loyalty_birthdays` | 06:00 | unique `(card_id, kind, birthday_year)` |
| `loyalty_daily_summary` | 19:30 | read-only |
| `loyalty_retention` | Sundays 04:00 | `loyalty_member.deleted_at` |

Every key is in the data, which is what makes the scheduler's coalesce-and-run-late policy
safe here too (see `scheduler.py`).

`loyalty_campaigns` also flushes unsent fraud alerts. The stamp route sends them straight
after commit; this is the retry for the ones that failed (Telegram down, bot token unset
then set), so an alert is late at worst, never lost.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from cafeops.bot import formatters as fmt
from cafeops.bot.notify import Notifier, notifier_for_settings
from cafeops.db.base import SessionFactory, session_scope
from cafeops.services.loyalty import alerts, birthdays, campaigns, retention, stats
from cafeops.services.loyalty.common import now_utc
from cafeops.services.loyalty.messaging import Outgoing, deliver_all
from cafeops.services.loyalty.wallets import wallet_module

__all__ = [
    "flush_alerts",
    "job_loyalty_birthdays",
    "job_loyalty_campaigns",
    "job_loyalty_daily_summary",
    "job_loyalty_retention",
    "job_wallet_outbox",
]

log = logging.getLogger("cafeops.jobs.loyalty")


def _factory(factory: sessionmaker[Session] | None) -> sessionmaker[Session]:
    return factory or SessionFactory


async def job_wallet_outbox(factory: sessionmaker[Session] | None = None) -> None:
    """Drain due wallet pushes. The wallet module owns backoff and give-up."""
    sync = wallet_module("sync")
    drain = getattr(sync, "drain_outbox_sync", None) if sync else None
    if drain is None:
        log.debug("wallet_outbox: wallet module not installed; nothing drained")
        return
    processed = await asyncio.to_thread(drain, _factory(factory))
    if processed:
        log.info("wallet_outbox: processed %s row(s)", processed)


async def flush_alerts(
    factory: sessionmaker[Session] | None = None, notifier: Notifier | None = None
) -> int:
    """Send stored alerts that have not been sent. Marks each only after it went."""

    def _pending() -> list[alerts.AlertView]:
        with session_scope(_factory(factory)) as session:
            return alerts.pending(session)

    rows = await asyncio.to_thread(_pending)
    if not rows:
        return 0
    notifier = notifier or notifier_for_settings()
    # Marked after the send returns, console or Telegram: a console notifier has logged
    # it, and re-logging the same alert every five minutes would bury the new ones.
    await notifier.send(fmt.alerts(rows))

    def _mark() -> None:
        with session_scope(_factory(factory)) as session:
            alerts.mark_notified(session, [r.id for r in rows])

    await asyncio.to_thread(_mark)
    return len(rows)


async def job_loyalty_campaigns(
    factory: sessionmaker[Session] | None = None, notifier: Notifier | None = None
) -> None:
    def _run() -> tuple[list[str], list[Outgoing], int]:
        lines: list[str] = []
        outgoing: list[Outgoing] = []
        with session_scope(_factory(factory)) as session:
            now = now_utc()
            for campaign_id in campaigns.due_campaigns(session, now=now):
                result = campaigns.send_campaign(session, campaign_id, now=now)
                outgoing.extend(result.outgoing)
                lines.append(
                    f"campaign {campaign_id}: {result.recipients} recipient(s), "
                    f"{result.skipped_over_limit} skipped at the monthly promo limit"
                )
            returned = campaigns.mark_returns(session, now=now)
        return lines, outgoing, returned

    lines, outgoing, returned = await asyncio.to_thread(_run)
    for line in lines:
        log.info("loyalty_campaigns: %s", line)
    if returned:
        log.info("loyalty_campaigns: %s delivery(ies) marked returned", returned)
    if outgoing:
        sent = await asyncio.to_thread(deliver_all, outgoing)
        log.info("loyalty_campaigns: %s of %s email(s) sent", sent, len(outgoing))
    if lines:
        await job_wallet_outbox(factory)
    await flush_alerts(factory, notifier)


async def job_loyalty_birthdays(factory: sessionmaker[Session] | None = None) -> None:
    def _run() -> birthdays.BirthdayReport:
        with session_scope(_factory(factory)) as session:
            return birthdays.run_birthdays(session)

    report = await asyncio.to_thread(_run)
    log.info(report.summary())
    if report.issued or report.expired_refreshed:
        await job_wallet_outbox(factory)


async def job_loyalty_retention(factory: sessionmaker[Session] | None = None) -> None:
    def _run() -> retention.RetentionReport:
        with session_scope(_factory(factory)) as session:
            return retention.run_retention(session)

    report = await asyncio.to_thread(_run)
    log.info(report.summary())
    if report.erased:
        await job_wallet_outbox(factory)


async def job_loyalty_daily_summary(
    factory: sessionmaker[Session] | None = None,
    notifier: Notifier | None = None,
    day: date | None = None,
) -> None:
    def _run() -> stats.DailySummary:
        with session_scope(_factory(factory)) as session:
            return stats.daily_summary(session, day=day)

    summary = await asyncio.to_thread(_run)
    await (notifier or notifier_for_settings()).send(fmt.daily_summary(summary))
