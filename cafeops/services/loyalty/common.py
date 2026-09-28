"""Lookups and bookkeeping every loyalty service shares.

Three rules live here so no service can forget one:

1. **`updated_at` bumps on every state change** (`touch`). Apple's web service answers
   "which passes changed since X" from it; a change that forgets to bump it is a pass that
   never updates on the customer's phone.
2. **`reward_available` is recomputed, never set** (`refresh_reward_available`). It is a
   cache of "an unredeemed, unvoided, unexpired reward exists", and a cache somebody sets
   by hand is how a pass says "free drink ready" for a reward that was already given.
3. **Every card change enqueues a wallet refresh in the same transaction**
   (`enqueue_wallet_update`). The outbox row commits or rolls back with the stamp.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    LoyaltyAudit,
    LoyaltyCard,
    LoyaltyProgram,
    LoyaltyReward,
    WalletPushOutbox,
)
from cafeops.services.loyalty.errors import LoyaltyError

__all__ = [
    "DEFAULT_PROGRAM_SLUG",
    "audit",
    "authenticate_card",
    "available_rewards",
    "default_program",
    "enqueue_wallet_update",
    "load_card",
    "now_utc",
    "refresh_reward_available",
    "sha256_hex",
    "touch",
]

DEFAULT_PROGRAM_SLUG = "stamp"


def now_utc() -> datetime:
    return datetime.now(UTC)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def default_program(session: Session) -> LoyaltyProgram:
    """The stamp card. The migration seeds it; its absence is a deployment fault."""
    program = session.scalar(
        select(LoyaltyProgram).where(LoyaltyProgram.slug == DEFAULT_PROGRAM_SLUG)
    )
    if program is None:
        raise LoyaltyError(
            503,
            "program_missing",
            "The rewards programme is not set up on this server. Run the migrations "
            "(`uv run alembic upgrade head`) or `cafeops loyalty seed-demo`.",
        )
    return program


def load_card(session: Session, card_id: str) -> LoyaltyCard:
    card = session.get(LoyaltyCard, card_id)
    if card is None:
        raise LoyaltyError(404, "unknown_card", "There is no card with that number.")
    return card


def authenticate_card(session: Session, card_id: str, token: str | None) -> LoyaltyCard:
    """The card, if `token` is its `auth_token`. Constant-time.

    404 `unknown_card` for a card that does not exist, 401 `bad_token` for a missing or
    wrong token: the web card treats the first as "gone" and the second as "this browser's
    link is stale, offer recovery". Telling them apart leaks nothing useful -- card ids are
    uuid4s, so knowing one exists requires already holding it.
    """
    card = session.get(LoyaltyCard, card_id)
    if card is None:
        raise LoyaltyError(404, "unknown_card", "There is no card with that number.")
    if not token or not hmac.compare_digest(card.auth_token, token):
        raise LoyaltyError(401, "bad_token", "That card link is not valid on this device.")
    return card


def available_rewards(session: Session, card_id: str, now: datetime) -> list[LoyaltyReward]:
    """Unredeemed, unvoided, unexpired -- oldest first, so the till offers that one."""
    return list(
        session.scalars(
            select(LoyaltyReward)
            .where(
                LoyaltyReward.card_id == card_id,
                LoyaltyReward.redeemed_at.is_(None),
                LoyaltyReward.voided_at.is_(None),
                or_(LoyaltyReward.expires_at.is_(None), LoyaltyReward.expires_at > now),
            )
            .order_by(LoyaltyReward.issued_at, LoyaltyReward.id)
        )
    )


def refresh_reward_available(session: Session, card: LoyaltyCard, now: datetime) -> bool:
    """Recompute the cache. Returns True when it changed (the pass needs a refresh)."""
    session.flush()
    value = bool(available_rewards(session, card.id, now))
    changed = value != card.reward_available
    card.reward_available = value
    return changed


def touch(card: LoyaltyCard, now: datetime) -> None:
    card.updated_at = now


def enqueue_wallet_update(session: Session, card_id: str, message: str | None) -> None:
    """Queue a pass refresh (and a lock-screen line when `message` is set).

    Same transaction as the change. The wallet module's drain reads the row after commit;
    the till never waits for Apple or Google.
    """
    now = now_utc()
    session.add(
        WalletPushOutbox(
            card_id=card_id,
            message=message[:200] if message else None,
            attempts=0,
            next_attempt_at=now,
            created_at=now,
        )
    )


def audit(
    session: Session,
    kind: str,
    detail: str,
    *,
    is_alert: bool = False,
    staff_user_id: int | None = None,
    device_id: int | None = None,
    card_id: str | None = None,
    member_id: int | None = None,
    at: datetime | None = None,
) -> LoyaltyAudit:
    row = LoyaltyAudit(
        at=at or now_utc(),
        kind=kind[:40],
        detail=detail[:400],
        is_alert=is_alert,
        staff_user_id=staff_user_id,
        device_id=device_id,
        card_id=card_id,
        member_id=member_id,
    )
    session.add(row)
    return row
