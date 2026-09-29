"""Joining: one form, one member, one card (SPEC user flow 1).

Rules enforced here:

- **Terms are required, marketing is separate and unticked.** Marketing consent is
  recorded with when and where (`opt_in_at`, `opt_in_source = "join:<src>"`), PECR.
- **One card per email or phone.** Normalised first, so `07700 900123` and
  `+447700900123` are the same person; the UNIQUE columns are the real guard, this
  lookup just turns the collision into a helpful 409 (`already_member`) that the page
  answers with "recover your card".
- **A birthday is day and month only**, and its entry time is kept for the 30-day rule.
- **`ref`** is the referrer's card id. It is only recorded here; the referrer is credited
  when this member's card gets its first purchase stamp (`stamping`), so a referral
  that never walks through the door earns nothing.

Phase 3: joining always gives the default stamp card; `also_join` adds cards in other
active programmes in the same step, and `add_program_card` adds one later (from the web
card, /rewards on a device that holds a card, or the scanner). A card per programme,
never two -- the unique key on `loyalty_card` backs the check.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import LoyaltyCard, LoyaltyMember, LoyaltyProgram
from cafeops.domain.loyalty import (
    BIRTHDAY_MIN_LEAD_DAYS,
    BIRTHDAY_NOTICE_DAYS,
    birthday_in_year,
    is_valid_birthday,
    normalise_email,
    normalise_phone,
)
from cafeops.services.loyalty.common import (
    audit,
    default_program,
    enqueue_wallet_update,
    now_utc,
    touch,
)
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.stamping import welcome_stamp

__all__ = [
    "DetailsChange",
    "JoinRequest",
    "Joined",
    "add_program_card",
    "birthday_counts_from",
    "check_birthday",
    "clean_first_name",
    "contact_taken",
    "find_member_by_contact",
    "join",
    "new_card",
    "update_details",
]

_SRC_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


@dataclass(frozen=True, slots=True)
class JoinRequest:
    first_name: str
    email: str | None
    phone: str | None
    birthday_day: int | None
    birthday_month: int | None
    terms: bool
    marketing_opt_in: bool
    src: str | None
    ref: str | None
    #: Phase 3: slugs of other active programmes to join at the same time.
    also_join: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Joined:
    card_id: str
    token: str
    member_id: int
    #: Phase 3: (slug, card_id, token) of every extra card made in the same step.
    extra_cards: tuple[tuple[str, str, str], ...] = ()


def new_card(
    session: Session, member: LoyaltyMember, program: LoyaltyProgram, now: datetime
) -> LoyaltyCard:
    token = secrets.token_urlsafe(32)
    card = LoyaltyCard(
        id=str(uuid.uuid4()),
        member_id=member.id,
        program_id=program.id,
        stamps_current=0,
        cycles_completed=0,
        reward_available=False,
        auth_token=token,
        qr_secret=secrets.token_hex(16),
        created_at=now,
        updated_at=now,
    )
    session.add(card)
    session.flush()
    return card


def add_program_card(
    session: Session, member: LoyaltyMember, slug: str, *, source: str
) -> LoyaltyCard:
    """Another programme's card for an existing member. 409 if they have one already."""
    from cafeops.services.loyalty.programs import program_by_slug

    if member.deleted_at is not None:
        raise LoyaltyError(409, "card_voided", "This member's card has been deleted.")
    program = program_by_slug(session, slug)
    if not program.active:
        raise LoyaltyError(409, "program_closed", f"{program.name} is not open to new members.")
    existing = session.scalar(
        select(LoyaltyCard).where(
            LoyaltyCard.member_id == member.id, LoyaltyCard.program_id == program.id
        )
    )
    if existing is not None:
        raise LoyaltyError(
            409,
            "already_in_program",
            f"There is already a {program.name} card for this member.",
        )
    now = now_utc()
    card = new_card(session, member, program, now)
    audit(
        session,
        "program_joined",
        f"joined {program.name} ({source})",
        card_id=card.id,
        member_id=member.id,
        at=now,
    )
    member.last_activity_at = now
    return card


def _clean_src(src: str | None) -> str | None:
    """`?src=` is attribution, not free text: lower-case slug characters, 40 max."""
    if not src:
        return None
    value = "".join(ch for ch in src.strip().lower() if ch in _SRC_ALLOWED)[:40]
    return value or None


def find_member_by_contact(session: Session, contact: str) -> LoyaltyMember | None:
    """A live member by email or phone, normalised. None for a deleted or unknown one."""
    email = normalise_email(contact) if "@" in contact else None
    phone = None if email else normalise_phone(contact)
    if email is None and phone is None:
        return None
    cond = LoyaltyMember.email == email if email else LoyaltyMember.phone == phone
    return session.scalar(select(LoyaltyMember).where(cond, LoyaltyMember.deleted_at.is_(None)))


def clean_first_name(raw: str) -> str:
    first_name = " ".join(raw.split())
    if not first_name:
        raise LoyaltyError(422, "first_name_required", "Tell us your first name.")
    if len(first_name) > 40:
        raise LoyaltyError(422, "first_name_too_long", "A first name of up to 40 letters, please.")
    return first_name


def check_birthday(day: int | None, month: int | None) -> None:
    if (day is None) != (month is None):
        raise LoyaltyError(422, "bad_birthday", "Give both the day and the month, or neither.")
    if day is not None and month is not None and not is_valid_birthday(day, month):
        raise LoyaltyError(422, "bad_birthday", "That is not a real date.")


def join(session: Session, req: JoinRequest) -> Joined:
    first_name = clean_first_name(req.first_name)
    if not req.terms:
        raise LoyaltyError(422, "terms_required", "Please accept the terms to get a card.")

    email = phone = None
    if req.email and req.email.strip():
        email = normalise_email(req.email)
        if email is None:
            raise LoyaltyError(422, "bad_email", "That email address does not look right.")
    if req.phone and req.phone.strip():
        phone = normalise_phone(req.phone)
        if phone is None:
            raise LoyaltyError(422, "bad_phone", "That phone number does not look right.")
    if email is None and phone is None:
        raise LoyaltyError(
            422, "contact_required", "Add an email or a phone number so we can find your card."
        )

    day, month = req.birthday_day, req.birthday_month
    check_birthday(day, month)

    clauses = []
    if email:
        clauses.append(LoyaltyMember.email == email)
    if phone:
        clauses.append(LoyaltyMember.phone == phone)
    if session.scalar(select(LoyaltyMember.id).where(or_(*clauses))) is not None:
        raise LoyaltyError(
            409,
            "already_member",
            "You already have a card with that email or phone. We can send it to you again.",
        )

    program = default_program(session)
    if not program.active:
        raise LoyaltyError(503, "program_closed", "The rewards card is not open to new members.")

    referrer_id: int | None = None
    if req.ref:
        ref_card = session.get(LoyaltyCard, req.ref.strip())
        if ref_card is not None and ref_card.voided_at is None:
            referrer_id = ref_card.member_id

    now = now_utc()
    src = _clean_src(req.src)
    member = LoyaltyMember(
        first_name=first_name,
        email=email,
        phone=phone,
        birthday_day=day,
        birthday_month=month,
        birthday_set_at=now if day is not None else None,
        marketing_opt_in=req.marketing_opt_in,
        opt_in_at=now if req.marketing_opt_in else None,
        opt_in_source=f"join:{src or 'web'}" if req.marketing_opt_in else None,
        source=src,
        referred_by_member_id=referrer_id,
        terms_accepted_at=now,
        created_at=now,
        last_activity_at=now,
    )
    session.add(member)
    try:
        session.flush()
    except IntegrityError:
        # Two submissions of the same form racing past the lookup above.
        raise LoyaltyError(
            409, "already_member", "You already have a card with that email or phone."
        ) from None

    card = new_card(session, member, program, now)
    welcome_stamp(session, card, now=now)
    extra: list[tuple[str, str, str]] = []
    for slug in dict.fromkeys(s.strip() for s in req.also_join if s.strip()):
        if slug == program.slug:
            continue
        other = add_program_card(session, member, slug, source=f"join:{src or 'web'}")
        extra.append((slug, other.id, other.auth_token))
    if req.marketing_opt_in:
        audit(
            session,
            "consent",
            f"marketing opt-in at join (src={src or 'web'})",
            member_id=member.id,
            at=now,
        )
    session.flush()
    return Joined(
        card_id=card.id, token=card.auth_token, member_id=member.id, extra_cards=tuple(extra)
    )


# --------------------------------------------------------------------------
# the member's own details, from the web card
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DetailsChange:
    """What the member changed. `None` leaves a field alone; `birthday` is only applied
    when `set_birthday` is true, and `(None, None)` then removes it. Likewise `email`
    and `phone` are applied only under `set_email` / `set_phone`, and `None` then clears
    that contact; one of the two must remain, it is how a lost card is recovered."""

    first_name: str | None = None
    set_birthday: bool = False
    birthday: tuple[int | None, int | None] = (None, None)
    set_email: bool = False
    email: str | None = None
    set_phone: bool = False
    phone: str | None = None
    #: Where the member made the change, for the audit row.
    source: str = "the web card"


def contact_taken(session: Session, member_id: int, cond: ColumnElement[bool]) -> bool:
    """Another member holds this contact. Erased members hold none, so they never count.
    The one uniqueness rule for email and phone; the back office delegates here too."""
    return (
        session.scalar(select(LoyaltyMember.id).where(cond, LoyaltyMember.id != member_id))
        is not None
    )


def _member_contact(
    session: Session, member: LoyaltyMember, change: DetailsChange
) -> tuple[str | None, str | None]:
    """The email and phone the member will have after `change`, checked: well-formed,
    not another member's, and not both empty."""
    email, phone = member.email, member.phone
    if change.set_email:
        raw = (change.email or "").strip()
        email = normalise_email(raw) if raw else None
        if raw and email is None:
            raise LoyaltyError(422, "bad_email", "That email address does not look right.")
        if email and contact_taken(session, member.id, LoyaltyMember.email == email):
            raise LoyaltyError(409, "contact_taken", "Another member already has that email.")
    if change.set_phone:
        raw = (change.phone or "").strip()
        phone = normalise_phone(raw) if raw else None
        if raw and phone is None:
            raise LoyaltyError(422, "bad_phone", "That phone number does not look right.")
        if phone and contact_taken(session, member.id, LoyaltyMember.phone == phone):
            raise LoyaltyError(
                409, "contact_taken", "Another member already has that phone number."
            )
    if (change.set_email or change.set_phone) and email is None and phone is None:
        raise LoyaltyError(
            422,
            "contact_required",
            "Keep an email or a phone number: it is how you get your card back.",
        )
    return email, phone


def update_details(session: Session, card: LoyaltyCard, change: DetailsChange) -> list[str]:
    """Name, birthday and contact, edited by the member. Returns what changed (for the
    audit).

    The 30-day rule needs no check here: a new or changed birthday restarts
    `birthday_set_at`, and the birthday job refuses any occurrence fewer than 30 days
    after it (domain `birthday_decision`). So a birthday typed in for tomorrow brings
    nothing this year, and once-a-year stays the reward's unique key.
    """
    member = card.member
    if member.deleted_at is not None or card.voided_at is not None:
        raise LoyaltyError(410, "card_deleted", "This card has been deleted.")
    now = now_utc()
    changed: list[str] = []
    if change.first_name is not None:
        name = clean_first_name(change.first_name)
        if name != member.first_name:
            member.first_name = name
            changed.append("first name")
    if change.set_birthday:
        day, month = change.birthday
        check_birthday(day, month)
        if (day, month) != (member.birthday_day, member.birthday_month):
            member.birthday_day, member.birthday_month = day, month
            member.birthday_set_at = now if day is not None else None
            changed.append("birthday removed" if day is None else "birthday")
    email, phone = _member_contact(session, member, change)
    if email != member.email:
        member.email = email
        changed.append("email")
    if phone != member.phone:
        member.phone = phone
        changed.append("phone")
    if changed:
        audit(
            session,
            "profile",
            f"member changed {', '.join(changed)} from {change.source}",
            card_id=card.id,
            member_id=member.id,
            at=now,
        )
        # The name is on the wallet pass: every card of this member needs a refresh.
        for other in session.scalars(
            select(LoyaltyCard).where(
                LoyaltyCard.member_id == member.id, LoyaltyCard.voided_at.is_(None)
            )
        ):
            touch(other, now)
            enqueue_wallet_update(session, other.id, None)
        member.last_activity_at = now
    return changed


def birthday_counts_from(member: LoyaltyMember, *, today: date | None = None) -> date | None:
    """The first birthday that will bring a drink, given the 30-day rule. None: no birthday.

    Mirrors domain `birthday_decision`: an occurrence counts when it was recorded at least
    `BIRTHDAY_MIN_LEAD_DAYS` before the date, and it is still claimable until 7 days after.
    """
    if member.birthday_day is None or member.birthday_month is None:
        return None
    today = today or now_utc().astimezone(settings.tz).date()
    set_on = (
        member.birthday_set_at.astimezone(settings.tz).date() if member.birthday_set_at else None
    )
    for year in range(today.year - 1, today.year + 3):
        occurrence = birthday_in_year(member.birthday_day, member.birthday_month, year)
        if occurrence + timedelta(days=BIRTHDAY_NOTICE_DAYS) < today:
            continue
        if set_on is None or (occurrence - set_on).days >= BIRTHDAY_MIN_LEAD_DAYS:
            return occurrence
    return None
