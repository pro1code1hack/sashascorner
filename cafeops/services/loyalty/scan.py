"""What the scanner shows after a scan, a stamp, a redeem or an undo (CONTRACT §5 ScanResult).

Kept apart from `stamping` and `redeem` because all of them answer with it, and each of
those imports the other for undo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    LoyaltyCard,
    LoyaltyProgram,
    LoyaltyStampEvent,
    StaffUser,
    StampReason,
)
from cafeops.domain.loyalty import COOLDOWN_MINUTES, parse_qr_payload
from cafeops.services.loyalty.card_view import RewardView, card_view_of
from cafeops.services.loyalty.common import now_utc
from cafeops.services.loyalty.errors import LoyaltyError

__all__ = [
    "LastEvent",
    "OptionView",
    "ProgramRef",
    "ScanView",
    "is_undone",
    "net_purchase_stamps_since",
    "resolve_payload",
    "scan_view",
]


@dataclass(frozen=True, slots=True)
class LastEvent:
    id: int
    delta: int
    reason: str
    at: datetime
    staff_name: str | None


@dataclass(frozen=True, slots=True)
class OptionView:
    """A reward-catalogue entry the till can redeem a ready reward as."""

    id: int
    name: str
    description: str | None
    max_price_pence: int | None
    #: "any drink" / "items with 'cake' in the name" -- for staff, not for logic.
    covers: str


@dataclass(frozen=True, slots=True)
class ProgramRef:
    slug: str
    name: str
    kind: str


@dataclass(frozen=True, slots=True)
class ScanView:
    card_id: str
    first_name: str
    member_since: date
    stamps_current: int
    stamps_required: int
    rewards: tuple[RewardView, ...]
    stamps_last_10_min: int
    last_event: LastEvent | None
    voided: bool
    # --- phase 3 ----------------------------------------------------------------
    program_slug: str = "stamp"
    program_name: str = ""
    program_kind: str = "STAMPS"
    points_per_pound: int | None = None
    reward_ready_label: str = "Free drink ready"
    max_stamps_per_scan: int = 3
    reward_options: tuple[OptionView, ...] = ()
    #: The member's other live cards (one level deep: siblings carry no siblings).
    other_cards: tuple[ScanView, ...] = ()
    #: Active programmes the member has no card in yet; staff can add one.
    joinable: tuple[ProgramRef, ...] = ()
    lightspeed_linked: bool = False


def is_undone(session: Session, event_id: int) -> bool:
    return (
        session.scalar(
            select(LoyaltyStampEvent.id).where(LoyaltyStampEvent.undoes_event_id == event_id)
        )
        is not None
    )


def net_purchase_stamps_since(session: Session, card_id: str, since: datetime) -> int:
    """PURCHASE stamps on the card since `since`, not counting ones already undone.

    An undone stamp must not count towards the cooldown: staff who tapped +3 instead of
    +1 and undid it would otherwise need a manager for the customer's next coffee.
    """
    undone = select(LoyaltyStampEvent.undoes_event_id).where(
        LoyaltyStampEvent.card_id == card_id,
        LoyaltyStampEvent.reason == StampReason.UNDO,
    )
    total = session.scalar(
        select(func.coalesce(func.sum(LoyaltyStampEvent.delta), 0)).where(
            LoyaltyStampEvent.card_id == card_id,
            LoyaltyStampEvent.reason == StampReason.PURCHASE,
            LoyaltyStampEvent.created_at > since,
            LoyaltyStampEvent.id.not_in(undone),
        )
    )
    return int(total or 0)


def _options(session: Session, card: LoyaltyCard) -> tuple[OptionView, ...]:
    # Imported here: `programs` imports `redeem`, which imports this module.
    from cafeops.services.loyalty.programs import option_rule, reward_options

    return tuple(
        OptionView(
            id=o.id,
            name=o.name,
            description=o.description,
            max_price_pence=o.max_price_pence,
            covers=option_rule(o).describe(),
        )
        for o in reward_options(session, card.program_id)
    )


def scan_view(
    session: Session,
    card: LoyaltyCard,
    *,
    now: datetime | None = None,
    with_siblings: bool = True,
) -> ScanView:
    """The card as the till sees it, and -- phase 3 -- the member's other cards with it,
    so scanning any one of a member's cards shows all of them."""
    now = now or now_utc()
    session.flush()
    view = card_view_of(session, card, now=now)
    last = session.scalar(
        select(LoyaltyStampEvent)
        .where(LoyaltyStampEvent.card_id == card.id)
        .order_by(LoyaltyStampEvent.created_at.desc(), LoyaltyStampEvent.id.desc())
    )
    last_event = None
    if last is not None:
        staff_name = None
        if last.staff_user_id is not None:
            staff = session.get(StaffUser, last.staff_user_id)
            staff_name = staff.name if staff else None
        last_event = LastEvent(
            id=last.id,
            delta=last.delta,
            reason=last.reason.value,
            at=last.created_at,
            staff_name=staff_name,
        )
    return ScanView(
        card_id=card.id,
        first_name=view.first_name,
        member_since=view.member_since,
        stamps_current=card.stamps_current,
        stamps_required=view.stamps_required,
        rewards=view.rewards,
        stamps_last_10_min=net_purchase_stamps_since(
            session,
            card.id,
            now - timedelta(minutes=card.program.cooldown_minutes or COOLDOWN_MINUTES),
        ),
        last_event=last_event,
        voided=view.voided,
        program_slug=card.program.slug,
        program_name=card.program.name,
        program_kind=card.program.kind.value,
        points_per_pound=card.program.points_per_pound,
        reward_ready_label=card.program.reward_ready_label,
        max_stamps_per_scan=card.program.max_stamps_per_scan,
        reward_options=_options(session, card),
        other_cards=_siblings(session, card, now) if with_siblings else (),
        joinable=_joinable(session, card) if with_siblings else (),
        lightspeed_linked=card.member.lightspeed_customer_id is not None,
    )


def _siblings(session: Session, card: LoyaltyCard, now: datetime) -> tuple[ScanView, ...]:
    if card.voided_at is not None:
        return ()
    others = session.scalars(
        select(LoyaltyCard)
        .join(LoyaltyProgram, LoyaltyProgram.id == LoyaltyCard.program_id)
        .where(
            LoyaltyCard.member_id == card.member_id,
            LoyaltyCard.id != card.id,
            LoyaltyCard.voided_at.is_(None),
        )
        .order_by(LoyaltyProgram.sort_order, LoyaltyProgram.id)
    )
    return tuple(scan_view(session, other, now=now, with_siblings=False) for other in others)


def _joinable(session: Session, card: LoyaltyCard) -> tuple[ProgramRef, ...]:
    if card.voided_at is not None:
        return ()
    held = select(LoyaltyCard.program_id).where(LoyaltyCard.member_id == card.member_id)
    return tuple(
        ProgramRef(slug=p.slug, name=p.name, kind=p.kind.value)
        for p in session.scalars(
            select(LoyaltyProgram)
            .where(LoyaltyProgram.active.is_(True), LoyaltyProgram.id.not_in(held))
            .order_by(LoyaltyProgram.sort_order, LoyaltyProgram.id)
        )
    )


def resolve_payload(session: Session, payload: str) -> LoyaltyCard:
    """The card a scanned QR names, or the right refusal.

    `unknown_card` (404) when the payload is well-formed but no such card exists;
    `bad_signature` (400) when it is malformed or the MAC does not verify -- a forged or
    edited code, or a pass signed with a different key.
    """
    parts = payload.strip().split(":")
    if len(parts) != 3 or not parts[1]:
        raise LoyaltyError(400, "bad_signature", "That is not a Sasha's Corner card.")
    card = session.get(LoyaltyCard, parts[1])
    if card is None:
        raise LoyaltyError(404, "unknown_card", "That card is not one of ours.")

    def lookup(card_id: str) -> str | None:
        return card.qr_secret if card_id == card.id else None

    if parse_qr_payload(payload, lookup, settings.loyalty_key) is None:
        raise LoyaltyError(400, "bad_signature", "That code has been altered. Do not stamp it.")
    return card
