"""Lost phone, new phone: getting the SAME card back (SPEC user flow 6).

Two routes, because the café may never configure an email or SMS provider (CONTRACT §0):

1. **Self-service.** `POST /recover {contact}` sends a 6-digit code by email (SMTP) or SMS
   (Twilio), whichever matches the contact and is configured; `/recover/verify` trades it
   for the card link. The answer is the same whether or not the contact is a member --
   otherwise the form is a free "is this person a customer here?" oracle.
2. **At the till.** With no provider for that contact, the answer is `ask_staff`: staff
   find the member ("Find member" on the scanner) and show a recovery link as a QR for
   the customer to scan. That path is logged, because it hands a card to whoever is
   standing at the counter.

The card and its serial never change. The token is not rotated either: rotating would
also cut off the customer's other devices (a laptop web card), and a lost phone's copy is
already locked behind the phone's own lock screen.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from cafeops.config import settings
from cafeops.db.models import LoyaltyCard, LoyaltyMember, LoyaltyOtp, OtpChannel
from cafeops.domain.loyalty import mask_contact, normalise_email, normalise_phone
from cafeops.services.loyalty.common import audit, default_program, now_utc
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.join import Joined, find_member_by_contact
from cafeops.services.loyalty.messaging import Outgoing
from cafeops.services.loyalty.staff_auth import StaffActor

__all__ = [
    "BAD_CODE",
    "OTP_TTL",
    "LookupHit",
    "RecoveryRequested",
    "card_of_member",
    "lookup_members",
    "recovery_link",
    "request_recovery",
    "verify_recovery",
    "web_card_url",
]

OTP_TTL = timedelta(minutes=10)
_MAX_ATTEMPTS = 5
#: Codes per member per quarter hour: enough for "it didn't arrive", not for a flood.
_MAX_CODES_PER_WINDOW = 3
_CODE_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class RecoveryRequested:
    delivery: str  # "email" | "sms" | "ask_staff"
    outgoing: Outgoing | None


def _code_hash(member_id: int, code: str) -> str:
    msg = f"otp:{member_id}:{code}".encode()
    return hmac.new(settings.loyalty_key, msg, hashlib.sha256).hexdigest()


def web_card_url(card: LoyaltyCard) -> str:
    return f"{settings.loyalty_public_url.rstrip('/')}/c/{card.id}#t={card.auth_token}"


def card_of_member(session: Session, member: LoyaltyMember) -> LoyaltyCard:
    """The member's main (default-programme) card; phase 3: else their first live card.
    The web card lists the others, so recovering one recovers them all."""
    program = default_program(session)
    card = session.scalar(
        select(LoyaltyCard).where(
            LoyaltyCard.member_id == member.id, LoyaltyCard.program_id == program.id
        )
    )
    if card is None:
        card = session.scalar(
            select(LoyaltyCard)
            .where(LoyaltyCard.member_id == member.id, LoyaltyCard.voided_at.is_(None))
            .order_by(LoyaltyCard.created_at)
        )
    if card is None or card.voided_at is not None:
        raise LoyaltyError(404, "unknown_card", "There is no live card for that member.")
    return card


def request_recovery(session: Session, contact: str) -> RecoveryRequested:
    raw = contact.strip()
    if "@" in raw:
        if normalise_email(raw) is None:
            raise LoyaltyError(422, "bad_contact", "That email address does not look right.")
        delivery = "email" if settings.smtp_configured else "ask_staff"
    else:
        if normalise_phone(raw) is None:
            raise LoyaltyError(422, "bad_contact", "That does not look like an email or phone.")
        delivery = "sms" if settings.twilio_configured else "ask_staff"
    if delivery == "ask_staff":
        return RecoveryRequested(delivery=delivery, outgoing=None)

    member = find_member_by_contact(session, raw)
    if member is None:
        return RecoveryRequested(delivery=delivery, outgoing=None)
    now = now_utc()
    recent = session.scalar(
        select(func.count(LoyaltyOtp.id)).where(
            LoyaltyOtp.member_id == member.id, LoyaltyOtp.created_at > now - _CODE_WINDOW
        )
    )
    if (recent or 0) >= _MAX_CODES_PER_WINDOW:
        # Same answer as success: the limit must not confirm the contact exists.
        return RecoveryRequested(delivery=delivery, outgoing=None)

    code = f"{secrets.randbelow(1_000_000):06d}"
    session.add(
        LoyaltyOtp(
            member_id=member.id,
            code_hash=_code_hash(member.id, code),
            channel=OtpChannel.EMAIL if delivery == "email" else OtpChannel.SMS,
            expires_at=now + OTP_TTL,
            attempts=0,
            created_at=now,
        )
    )
    body = (
        f"Your Sasha's Corner Rewards code is {code}. It works for 10 minutes. "
        "If you did not ask for it, ignore this message."
    )
    to = member.email if delivery == "email" else member.phone
    assert to is not None  # found by that very contact
    return RecoveryRequested(
        delivery=delivery,
        outgoing=Outgoing(
            channel=delivery, to=to, subject="Your Sasha's Corner Rewards code", body=body
        ),
    )


BAD_CODE = LoyaltyError(400, "bad_code", "That code is not right or has expired.")


def verify_recovery(session: Session, contact: str, code: str) -> Joined | None:
    """The card on a right code; None on a wrong one.

    Returns rather than raises on a wrong code because the attempt counter must COMMIT:
    raising inside the unit of work rolls it back, and a counter that never counts is
    an unlimited guess budget. The caller raises `BAD_CODE` after the commit.
    """
    member = find_member_by_contact(session, contact)
    if member is None:
        return None
    now = now_utc()
    otp = session.scalar(
        select(LoyaltyOtp)
        .where(
            LoyaltyOtp.member_id == member.id,
            LoyaltyOtp.used_at.is_(None),
            LoyaltyOtp.expires_at > now,
            LoyaltyOtp.channel != OtpChannel.STAFF,
        )
        .order_by(LoyaltyOtp.created_at.desc())
    )
    if otp is None or otp.attempts >= _MAX_ATTEMPTS:
        return None
    otp.attempts += 1
    if not hmac.compare_digest(otp.code_hash, _code_hash(member.id, code.strip())):
        return None
    otp.used_at = now
    card = card_of_member(session, member)
    member.last_activity_at = now
    return Joined(card_id=card.id, token=card.auth_token, member_id=member.id)


@dataclass(frozen=True, slots=True)
class LookupHit:
    card_id: str
    first_name: str
    contact_masked: str


def lookup_members(session: Session, q: str, *, limit: int = 10) -> list[LookupHit]:
    """Find a member at the till by phone, email or first name.

    An email or a phone must match exactly (normalised); a name matches by prefix. A
    partial phone matches by its trailing digits (6 or more), which is how people read a
    number out ("ends 900123").
    """
    q = q.strip()
    if len(q) < 2:
        return []
    live = LoyaltyMember.deleted_at.is_(None)
    stmt = select(LoyaltyMember).where(live)
    if "@" in q:
        email = normalise_email(q)
        if email is None:
            return []
        stmt = stmt.where(LoyaltyMember.email == email)
    else:
        digits = "".join(ch for ch in q if ch.isdigit())
        if digits and len(digits) >= 6:
            phone = normalise_phone(q)
            conds: list[ColumnElement[bool]] = [LoyaltyMember.phone.like(f"%{digits[-9:]}")]
            if phone:
                conds.append(LoyaltyMember.phone == phone)
            stmt = stmt.where(or_(*conds))
        elif digits:
            return []
        else:
            stmt = stmt.where(func.lower(LoyaltyMember.first_name).like(f"{q.lower()}%"))
    stmt = stmt.order_by(LoyaltyMember.last_activity_at.desc()).limit(limit)
    out: list[LookupHit] = []
    for member in session.scalars(stmt):
        try:
            card = card_of_member(session, member)
        except LoyaltyError:
            continue
        out.append(
            LookupHit(
                card_id=card.id,
                first_name=member.first_name,
                contact_masked=mask_contact(member.email, member.phone),
            )
        )
    return out


def recovery_link(session: Session, actor: StaffActor, card_id: str) -> str:
    """The web-card link staff show as a QR. Logged: it hands over a card."""
    card = session.get(LoyaltyCard, card_id)
    if card is None or card.voided_at is not None:
        raise LoyaltyError(404, "unknown_card", "There is no live card with that number.")
    audit(
        session,
        "recovery_link",
        f"{actor.name} showed the card link on {actor.device_name}",
        staff_user_id=actor.user_id,
        device_id=actor.device_id,
        card_id=card.id,
        member_id=card.member_id,
    )
    card.member.last_activity_at = now_utc()
    return web_card_url(card)
