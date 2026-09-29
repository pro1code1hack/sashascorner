"""Drain `wallet_push_outbox`: tell Apple and Google that a card changed.

`services/loyalty` writes an outbox row in the same transaction as every card change;
this sends it. Two callers: `kick()` right after a stamp (A's routes, via FastAPI
BackgroundTasks, so the pass updates "within seconds"), and the `wallet_outbox`
scheduler job every minute, which catches whatever a kick missed or a failure deferred.

Shape of one drain, chosen for SQLite's single writer:
1. a short transaction *claims* due rows by pushing `next_attempt_at` out by a lease
   (compare-and-set, so the API process and the scheduler never both send a row);
2. the network calls run with no session open;
3. a second short transaction records the outcome.

Backoff per CONTRACT §3: 30 s, 2 min, 10 min, 1 h, 6 h, then give up with the error
kept. Not configured is not a failure -- the row is marked done with the reason, or an
unconfigured wallet would retry six times for every stamp forever.

Several rows for one card in one batch are coalesced: one APNs push (the device fetches
the latest pass anyway), one Google PATCH, and only the newest message is notified --
three quick stamps should buzz a phone once, not three times.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cafeops.clock import utcnow
from cafeops.db.models.loyalty import WalletPushOutbox
from cafeops.db.models.wallet import WalletAppleRegistration, WalletGoogleObject
from cafeops.integrations.wallet import apns, google
from cafeops.integrations.wallet.config import (
    WalletNotConfigured,
    WalletSettings,
    wallet_settings,
)
from cafeops.services.loyalty.card_view import card_view

log = logging.getLogger("cafeops.wallet.sync")

BACKOFF = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
)
#: How long a claimed row is invisible to other drainers. Longer than any sane run of
#: network calls for one batch; if the process dies mid-send the row comes back then.
LEASE = timedelta(minutes=5)

SessionFactoryT = Callable[[], Session]

#: One drain at a time per process; a kick that finds one running just returns, because
#: the running drain loops until nothing is due and will pick up the new row.
_running = threading.Lock()


@dataclass(frozen=True)
class _Row:
    id: int
    card_id: str
    message: str | None
    attempts: int


@dataclass
class _Outcome:
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _claim(factory: SessionFactoryT, limit: int) -> list[_Row]:
    now = utcnow()
    claimed: list[_Row] = []
    with factory() as session:
        due = session.scalars(
            select(WalletPushOutbox)
            .where(WalletPushOutbox.done_at.is_(None), WalletPushOutbox.next_attempt_at <= now)
            .order_by(WalletPushOutbox.id)
            .limit(limit)
        ).all()
        for row in due:
            result = session.execute(
                update(WalletPushOutbox)
                .where(
                    WalletPushOutbox.id == row.id,
                    WalletPushOutbox.done_at.is_(None),
                    WalletPushOutbox.next_attempt_at <= now,
                )
                .values(next_attempt_at=now + LEASE)
                .execution_options(synchronize_session=False)
            )
            if getattr(result, "rowcount", 0) == 1:
                claimed.append(_Row(row.id, row.card_id, row.message, row.attempts))
        session.commit()
    return claimed


def _apple(factory: SessionFactoryT, card_id: str, cfg: WalletSettings, out: _Outcome) -> None:
    with factory() as session:
        regs = session.scalars(
            select(WalletAppleRegistration).where(WalletAppleRegistration.card_id == card_id)
        ).all()
        tokens = sorted({r.push_token for r in regs})
    if not cfg.apple_configured:
        # Even with registrations (made under an earlier configuration): retrying
        # cannot fix configuration, and the note is visible on the row.
        out.notes.append("apple not configured")
        return
    if not tokens:
        return
    try:
        results = apns.push(tokens, cfg=cfg)
    except (httpx.HTTPError, OSError) as exc:
        out.errors.append(f"apns: {exc}"[:200])
        return
    dead = [r.token for r in results if r.token_dead]
    if dead:
        with factory() as session:
            for reg in session.scalars(
                select(WalletAppleRegistration).where(
                    WalletAppleRegistration.card_id == card_id,
                    WalletAppleRegistration.push_token.in_(dead),
                )
            ):
                session.delete(reg)
            session.commit()
    failed = [r for r in results if not r.ok and not r.token_dead]
    if failed:
        out.errors.append(
            "apns: " + ", ".join(f"{r.status} {r.reason or ''}".strip() for r in failed[:3])
        )


def _google(
    factory: SessionFactoryT, card_id: str, rows: list[_Row], cfg: WalletSettings, out: _Outcome
) -> None:
    if not cfg.google_configured:
        out.notes.append("google not configured")
        return
    with factory() as session:
        view = card_view(session, card_id)
    newest_with_message = next((r for r in reversed(rows) if r.message), None)
    error: str | None = None
    exists = False
    try:
        exists = google.patch_object(view)
        if exists and newest_with_message is not None and newest_with_message.message:
            google.add_message(
                card_id,
                newest_with_message.message,
                message_id=f"outbox-{newest_with_message.id}",
            )
    except (google.GoogleWalletError, WalletNotConfigured) as exc:
        error = str(exc)[:400]
        out.errors.append(error[:200])
    with factory() as session:
        obj = session.get(WalletGoogleObject, card_id)
        if obj is None:
            if not exists:
                # Never saved to Google Wallet (404), or a failure before we could tell.
                # A row means "this card is in Google Wallet" to the admin list, so none.
                return
            obj = WalletGoogleObject(
                card_id=card_id,
                object_id=google.object_id(card_id, cfg),
                class_id=google.class_id(view, cfg),
            )
            session.add(obj)
        if error is None:
            obj.last_synced_at = utcnow()
        obj.last_error = error
        session.commit()


def _deliver(factory: SessionFactoryT, card_id: str, rows: list[_Row]) -> _Outcome:
    cfg = wallet_settings
    out = _Outcome()
    try:
        _apple(factory, card_id, cfg, out)
        _google(factory, card_id, rows, cfg, out)
    except LookupError:
        # The card is gone (should not happen: deletion voids, never removes). Nothing
        # will ever succeed for it, so do not retry.
        out.notes.append("card not found")
    except Exception as exc:  # a bug here must not wedge the outbox
        log.exception("wallet delivery failed for card %s", card_id)
        out.errors.append(f"{type(exc).__name__}: {exc}"[:200])
    return out


def _record(factory: SessionFactoryT, rows: list[_Row], out: _Outcome) -> None:
    now = utcnow()
    with factory() as session:
        for row in rows:
            obj = session.get(WalletPushOutbox, row.id)
            if obj is None:
                continue
            if not out.errors:
                obj.done_at = now
                obj.last_error = "; ".join(out.notes) or None
                continue
            obj.attempts = row.attempts + 1
            obj.last_error = "; ".join(out.errors)[:400]
            if obj.attempts > len(BACKOFF):
                obj.done_at = now  # gave up; last_error says why
            else:
                obj.next_attempt_at = now + BACKOFF[obj.attempts - 1]
        session.commit()


def drain_outbox_sync(session_factory: SessionFactoryT, *, limit: int = 50) -> int:
    """Send every due outbox row, up to `limit`. Returns rows processed. Sync; the
    scheduler calls it directly and `kick()` runs it on a worker thread."""
    if not _running.acquire(blocking=False):
        return 0
    processed = 0
    try:
        while processed < limit:
            batch = _claim(session_factory, limit - processed)
            if not batch:
                break
            by_card: dict[str, list[_Row]] = {}
            for row in batch:
                by_card.setdefault(row.card_id, []).append(row)
            for card_id, rows in by_card.items():
                _record(session_factory, rows, _deliver(session_factory, card_id, rows))
            processed += len(batch)
    finally:
        _running.release()
    return processed


async def kick() -> None:
    """Drain now, off the event loop. Never raises: the stamp is already committed and
    the till must not see a wallet error (SPEC: "failure never blocks the till")."""
    try:
        from cafeops.db.base import session_factory

        await asyncio.to_thread(drain_outbox_sync, session_factory())
    except Exception:
        log.exception("wallet outbox kick failed; the scheduler job will retry")


__all__ = ["BACKOFF", "drain_outbox_sync", "kick"]
