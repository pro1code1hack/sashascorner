"""The one read of a card that every rendering starts from (CONTRACT §3).

Apple pass, Google object, web card and scanner all show the same card, so they all read
it here. The wallet module (agent B) renders `CardView` and never queries a loyalty table
itself -- that is what keeps "the server is the single source of truth" (SPEC) true.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import LoyaltyCard, LoyaltyReward, RewardKind
from cafeops.domain.loyalty import qr_payload
from cafeops.services.loyalty.common import (
    available_rewards,
    enqueue_wallet_update,
    now_utc,
)

__all__ = [
    "CardView",
    "RewardView",
    "card_view",
    "card_view_of",
    "enqueue_wallet_update",
    "reward_label",
]

_LABELS = {
    RewardKind.STAMP_CARD: "Free drink ready",
    RewardKind.BIRTHDAY: "Birthday drink",
    RewardKind.REFERRAL: "Referral drink",
}


def reward_label(kind: RewardKind, ready_label: str | None = None) -> str:
    """The reward's name. A programme's own reward (phase 3) uses its `reward_ready_label`."""
    if kind is RewardKind.STAMP_CARD and ready_label:
        return ready_label
    return _LABELS[kind]


@dataclass(frozen=True)
class RewardView:
    id: int
    kind: str  # "STAMP_CARD" | "BIRTHDAY" | "REFERRAL"
    label: str
    issued_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True)
class CardView:
    card_id: str
    first_name: str
    member_since: date
    program_name: str
    stamps_required: int
    stamps_current: int
    reward_text: str
    #: Available (unredeemed, unexpired, unvoided) only.
    rewards: tuple[RewardView, ...]
    qr_payload: str
    auth_token: str
    updated_at: datetime
    voided: bool
    marketing_opt_in: bool
    # --- phase 3 (defaults keep every phase-1 caller and renderer working) -----------
    program_slug: str = "stamp"
    #: "STAMPS" | "POINTS". On a POINTS card the stamp fields hold points.
    program_kind: str = "STAMPS"
    points_per_pound: int | None = None
    reward_ready_label: str = "Free drink ready"
    program_description: str | None = None
    member_id: int = 0


def _reward_view(reward: LoyaltyReward, ready_label: str | None = None) -> RewardView:
    return RewardView(
        id=reward.id,
        kind=reward.kind.value,
        label=reward_label(reward.kind, ready_label),
        issued_at=reward.issued_at,
        expires_at=reward.expires_at,
    )


def card_view_of(session: Session, card: LoyaltyCard, *, now: datetime | None = None) -> CardView:
    now = now or now_utc()
    member = card.member
    program = card.program
    voided = card.voided_at is not None
    rewards = (
        ()
        if voided
        else tuple(
            _reward_view(r, program.reward_ready_label)
            for r in available_rewards(session, card.id, now)
        )
    )
    return CardView(
        card_id=card.id,
        first_name=member.first_name,
        member_since=member.created_at.astimezone(settings.tz).date(),
        program_name=program.name,
        stamps_required=program.stamps_required,
        stamps_current=card.stamps_current,
        reward_text=program.reward_text,
        rewards=rewards,
        qr_payload=qr_payload(card.id, card.qr_secret, settings.loyalty_key),
        auth_token=card.auth_token,
        updated_at=card.updated_at,
        voided=voided,
        marketing_opt_in=member.marketing_opt_in,
        program_slug=program.slug,
        program_kind=program.kind.value,
        points_per_pound=program.points_per_pound,
        reward_ready_label=program.reward_ready_label,
        program_description=program.description,
        member_id=member.id,
    )


def card_view(session: Session, card_id: str) -> CardView:
    """`LookupError` if the card does not exist (CONTRACT §3)."""
    card = session.get(LoyaltyCard, card_id)
    if card is None:
        raise LookupError(f"no loyalty card {card_id!r}")
    return card_view_of(session, card)
