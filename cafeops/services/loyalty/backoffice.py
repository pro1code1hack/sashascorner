"""The Loyalty card's back office (docs/loyalty/BACKOFFICE-V2.md §3): one member's card.

The back office sits behind the shared password, so its card actions are one tap with no
PIN and no two-minute undo window -- the person at the laptop is the manager. They are
still the scanner's rules underneath: a stamp goes through `stamping._apply` (the only
writer of the ledger, which also keeps the card's stickers), a free drink through
`redeem.redeem`, an undo through `stamping.reverse_with_referral` / `redeem.unredeem`.
Nothing here edits a ledger row.

What a back-office stamp looks like in the ledger: reason PURCHASE (it is a visit), no
staff user, no device, note `BACK_OFFICE_NOTE` -- which is how history says "Back office".

Read models (`CardFace`, `HistoryEntry`, `MemberExtras`) are frozen dataclasses; the API
layer only converts them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from cafeops.config import settings
from cafeops.db.models import (
    LoyaltyCard,
    LoyaltyMember,
    LoyaltyReward,
    LoyaltyStampEvent,
    ProgramKind,
    RewardKind,
    StaffDevice,
    StaffUser,
    StampReason,
)
from cafeops.domain.loyalty import (
    STICKER_NAMES,
    fit_stickers,
    mask_contact,
    normalise_email,
    normalise_phone,
    sticker_set,
)
from cafeops.services.loyalty.common import (
    audit,
    available_rewards,
    default_program,
    enqueue_wallet_update,
    now_utc,
    touch,
)
from cafeops.services.loyalty.consent import set_marketing_opt_in
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.join import JoinRequest, check_birthday, clean_first_name, join
from cafeops.services.loyalty.messaging import Outgoing
from cafeops.services.loyalty.recovery import web_card_url
from cafeops.services.loyalty.redeem import redeem, unredeem
from cafeops.services.loyalty.stamping import (
    _apply,
    credit_referrer,
    reverse_with_referral,
)

__all__ = [
    "BACK_OFFICE_NOTE",
    "CardFace",
    "HistoryEntry",
    "MemberExtras",
    "MemberPatch",
    "SendLink",
    "add_stamp",
    "card_face",
    "create_member",
    "give_reward",
    "history",
    "main_card",
    "member_extras",
    "patch_member",
    "send_link",
    "set_sticker",
    "source_label",
    "undo_last",
]

BACK_OFFICE_NOTE = "back office"
_BACK_OFFICE = "Back office"
_VISIT_REASONS = (StampReason.PURCHASE, StampReason.PAPER_MIGRATION)
_UNDOABLE = (
    StampReason.PURCHASE,
    StampReason.PAPER_MIGRATION,
    StampReason.MANUAL_FIX,
    StampReason.WELCOME,
    StampReason.REFERRAL,
)


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CardFace:
    card_id: str
    short_id: str
    stamps_current: int
    stamps_required: int
    stickers: tuple[str, ...]
    reward_available: bool
    reward_id: int | None
    reward_text: str
    voided: bool
    can_undo: bool
    undo_label: str | None


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    kind: str
    title: str
    detail: str | None
    sticker: str | None
    at: datetime


@dataclass(frozen=True, slots=True)
class MemberExtras:
    notes: str | None
    terms_accepted_at: datetime | None
    visits_per_month: float | None
    last_visit_at: datetime | None


def main_card(session: Session, member_id: int) -> tuple[LoyaltyMember, LoyaltyCard]:
    """The member and their main-programme card (else their first card)."""
    member = session.get(LoyaltyMember, member_id)
    if member is None:
        raise LoyaltyError(404, "unknown_member", "There is no member with that id.")
    program = default_program(session)
    card = session.scalar(
        select(LoyaltyCard).where(
            LoyaltyCard.member_id == member.id, LoyaltyCard.program_id == program.id
        )
    ) or session.scalar(
        select(LoyaltyCard)
        .where(LoyaltyCard.member_id == member.id)
        .order_by(LoyaltyCard.created_at)
    )
    if card is None:
        raise LoyaltyError(404, "unknown_member", "That member has no card.")
    return member, card


def _live(session: Session, member_id: int) -> tuple[LoyaltyMember, LoyaltyCard]:
    member, card = main_card(session, member_id)
    if member.deleted_at is not None or card.voided_at is not None:
        raise LoyaltyError(409, "card_voided", "This member's card has been deleted.")
    return member, card


def source_label(source: str | None) -> str:
    """The `?src=` tag as words: None is the website itself."""
    if not source:
        return "Website"
    if source in ("back-office", "back_office"):
        return _BACK_OFFICE
    words = source.replace("-", " ").replace("_", " ").split()
    words = [w.upper() if w in ("qr", "uni") else w for w in words]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def _ago(at: datetime, now: datetime) -> str:
    secs = max(0, int((now - at).total_seconds()))
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} hour{'' if h == 1 else 's'} ago"
    d = secs // 86400
    if d == 1:
        return "yesterday"
    if d < 14:
        return f"{d} days ago"
    return f"on {at.astimezone(settings.tz):%-d %b}"


def _undo_target(
    session: Session, card: LoyaltyCard
) -> tuple[LoyaltyStampEvent | None, LoyaltyReward | None]:
    """The newest thing "Undo last" would reverse: a stamp event or a given reward."""
    undone = select(LoyaltyStampEvent.undoes_event_id).where(
        LoyaltyStampEvent.card_id == card.id, LoyaltyStampEvent.undoes_event_id.is_not(None)
    )
    event = session.scalar(
        select(LoyaltyStampEvent)
        .where(
            LoyaltyStampEvent.card_id == card.id,
            LoyaltyStampEvent.reason.in_(_UNDOABLE),
            LoyaltyStampEvent.id.not_in(undone),
        )
        .order_by(LoyaltyStampEvent.created_at.desc(), LoyaltyStampEvent.id.desc())
    )
    reward = session.scalar(
        select(LoyaltyReward)
        .where(
            LoyaltyReward.card_id == card.id,
            LoyaltyReward.redeemed_at.is_not(None),
            LoyaltyReward.voided_at.is_(None),
        )
        .order_by(LoyaltyReward.redeemed_at.desc(), LoyaltyReward.id.desc())
    )
    if reward is not None and reward.redeemed_at is not None:
        if event is None or reward.redeemed_at >= event.created_at:
            return None, reward
    return event, None


def _event_blocked(session: Session, event: LoyaltyStampEvent) -> bool:
    """Undoing it would take back a drink already given, or leave the card negative."""
    card = session.get(LoyaltyCard, event.card_id)
    assert card is not None
    issued = list(
        session.scalars(
            select(LoyaltyReward).where(
                LoyaltyReward.stamp_event_id == event.id, LoyaltyReward.voided_at.is_(None)
            )
        )
    )
    if any(r.redeemed_at is not None for r in issued):
        return True
    return card.stamps_current + card.program.stamps_required * len(issued) - event.delta < 0


def _event_words(event: LoyaltyStampEvent) -> str:
    if event.reason is StampReason.WELCOME:
        return "the welcome stamp"
    if event.reason is StampReason.PAPER_MIGRATION:
        return "the paper-card stamps"
    if event.reason is StampReason.MANUAL_FIX:
        return "the correction"
    if event.reason is StampReason.REFERRAL:
        return "the referral stamp"
    return "the stamp" if event.delta == 1 else f"the {event.delta} stamps"


def card_face(session: Session, card: LoyaltyCard, *, now: datetime | None = None) -> CardFace:
    now = now or now_utc()
    program = card.program
    voided = card.voided_at is not None
    ready = [] if voided else available_rewards(session, card.id, now)
    stickers = (
        tuple(fit_stickers(card.stickers, card.stamps_current, sticker_set(program.stickers)))
        if program.kind is ProgramKind.STAMPS
        else ()
    )
    label: str | None = None
    can_undo = False
    if not voided:
        event, reward = _undo_target(session, card)
        if reward is not None and reward.redeemed_at is not None:
            can_undo = True
            label = f"the free drink given {_ago(reward.redeemed_at, now)}"
        elif event is not None and not _event_blocked(session, event):
            can_undo = True
            label = f"{_event_words(event)} from {_ago(event.created_at, now)}"
    return CardFace(
        card_id=card.id,
        short_id=card.id.replace("-", "")[:6],
        stamps_current=card.stamps_current,
        stamps_required=program.stamps_required,
        stickers=stickers,
        reward_available=bool(ready),
        reward_id=ready[0].id if ready else None,
        reward_text=program.reward_text,
        voided=voided,
        can_undo=can_undo,
        undo_label=label,
    )


def _visit_days(session: Session, card_id: str, since: datetime) -> set[date]:
    undone = select(LoyaltyStampEvent.undoes_event_id).where(
        LoyaltyStampEvent.card_id == card_id, LoyaltyStampEvent.undoes_event_id.is_not(None)
    )
    days: set[date] = set()
    for at in session.scalars(
        select(LoyaltyStampEvent.created_at).where(
            LoyaltyStampEvent.card_id == card_id,
            LoyaltyStampEvent.reason.in_(_VISIT_REASONS),
            LoyaltyStampEvent.id.not_in(undone),
            LoyaltyStampEvent.created_at >= since,
        )
    ):
        days.add(at.astimezone(settings.tz).date())
    for redeemed in session.scalars(
        select(LoyaltyReward.redeemed_at).where(
            LoyaltyReward.card_id == card_id,
            LoyaltyReward.redeemed_at.is_not(None),
            LoyaltyReward.redeemed_at >= since,
        )
    ):
        if redeemed is not None:
            days.add(redeemed.astimezone(settings.tz).date())
    return days


def member_extras(
    session: Session,
    member: LoyaltyMember,
    card: LoyaltyCard,
    last_visit_at: datetime | None,
    *,
    now: datetime | None = None,
) -> MemberExtras:
    """Visits a month: distinct local days with a visit in the last 90 days, per 30 days.
    A member younger than 90 days is measured over their own age (at least 30 days), so
    a fortnight-old regular is not diluted by weeks before they joined."""
    now = now or now_utc()
    age = max(0, (now - member.created_at).days)
    window = min(90, max(30, age))
    visits: float | None = None
    if last_visit_at is not None:
        days = _visit_days(session, card.id, now - timedelta(days=window))
        visits = round(len(days) / (window / 30), 1)
    return MemberExtras(
        notes=member.notes,
        terms_accepted_at=member.terms_accepted_at,
        visits_per_month=visits,
        last_visit_at=last_visit_at,
    )


def _where(staff: str | None, device: str | None, note: str | None) -> str | None:
    if device:
        return device
    if staff:
        return staff
    if note == BACK_OFFICE_NOTE:
        return _BACK_OFFICE
    if note and note.lower().startswith(("receipt", "lightspeed", "till receipt")):
        return "Till receipt"
    return None


def _join(*parts: str | None) -> str | None:
    text = " · ".join(p for p in parts if p)
    return text or None


def history(session: Session, member: LoyaltyMember, card: LoyaltyCard) -> tuple[HistoryEntry, ...]:
    """Everything that happened to the card, newest first, one readable row each."""
    staff = {u.id: u.name for u in session.scalars(select(StaffUser))}
    devices = {d.id: d.name for d in session.scalars(select(StaffDevice))}
    out: list[HistoryEntry] = []
    for e in session.scalars(select(LoyaltyStampEvent).where(LoyaltyStampEvent.card_id == card.id)):
        where = _where(
            staff.get(e.staff_user_id) if e.staff_user_id else None,
            devices.get(e.device_id) if e.device_id else None,
            e.note,
        )
        name = STICKER_NAMES.get(e.sticker or "")
        n = abs(e.delta)
        if e.reason is StampReason.PURCHASE:
            title = "Stamp given" if e.delta == 1 else f"{e.delta} stamps given"
            entry = HistoryEntry("stamp", title, _join(where, name), e.sticker, e.created_at)
        elif e.reason is StampReason.WELCOME:
            entry = HistoryEntry(
                "welcome",
                "Welcome stamp",
                _join("On us for joining", name),
                e.sticker,
                e.created_at,
            )
        elif e.reason is StampReason.PAPER_MIGRATION:
            entry = HistoryEntry(
                "paper",
                "Paper card moved over",
                _join(f"{n} stamp{'' if n == 1 else 's'}", where),
                e.sticker,
                e.created_at,
            )
        elif e.reason is StampReason.REFERRAL:
            entry = HistoryEntry(
                "referral",
                "Referral stamp" if n == 1 else f"{n} referral stamps",
                "A friend they invited came in",
                e.sticker,
                e.created_at,
            )
        elif e.reason is StampReason.UNDO:
            entry = HistoryEntry(
                "undo",
                "Stamp undone" if n == 1 else f"{n} stamps undone",
                _join(where, e.note if not where else None),
                None,
                e.created_at,
            )
        elif e.reason is StampReason.MANUAL_FIX and (e.note or "").startswith("stamps expired"):
            entry = HistoryEntry(
                "expired",
                "Stamps expired",
                f"{n} stamp{'' if n == 1 else 's'} cleared, no visit for a year",
                None,
                e.created_at,
            )
        else:  # MANUAL_FIX, and the never-written REDEEM
            sign = "+" if e.delta > 0 else "-"
            entry = HistoryEntry(
                "correction", f"Correction {sign}{n}", _join(e.note, where), None, e.created_at
            )
        out.append(entry)
    for r in session.scalars(select(LoyaltyReward).where(LoyaltyReward.card_id == card.id)):
        if r.kind is RewardKind.BIRTHDAY:
            out.append(
                HistoryEntry(
                    "birthday",
                    "Birthday drink added",
                    "Ready for their birthday week",
                    None,
                    r.issued_at,
                )
            )
        if r.redeemed_at is not None and r.voided_at is None:
            who = staff.get(r.staff_user_id) if r.staff_user_id else _BACK_OFFICE
            what = {
                RewardKind.STAMP_CARD: "Card filled and reset",
                RewardKind.BIRTHDAY: "Birthday drink",
                RewardKind.REFERRAL: "Referral drink",
            }[r.kind]
            out.append(
                HistoryEntry(
                    "free_drink", "Free drink given", _join(what, who), None, r.redeemed_at
                )
            )
    out.sort(key=lambda h: h.at, reverse=True)
    out.append(
        HistoryEntry("joined", "Joined", source_label(member.source), None, member.created_at)
    )
    return tuple(out)


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


def _parse_birthday(raw: str | None) -> tuple[int | None, int | None]:
    """ "DD-MM" (or "D-M"); None/"" clears."""
    if raw is None or not raw.strip():
        return None, None
    parts = raw.strip().replace("/", "-").split("-")
    try:
        day, month = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        raise LoyaltyError(
            422, "bad_birthday", "Give the birthday as day and month, e.g. 25-03."
        ) from None
    return day, month


def create_member(
    session: Session,
    *,
    first_name: str,
    email: str | None,
    phone: str | None,
    birthday: str | None,
    marketing_opt_in: bool,
    source: str | None,
) -> int:
    """A member added at the counter or from the back office. The same join as the
    website -- one card per contact, the welcome stamp -- with the terms recorded as
    accepted now (the form says staff confirm the customer agreed)."""
    day, month = _parse_birthday(birthday)
    try:
        joined = join(
            session,
            JoinRequest(
                first_name=first_name,
                email=email,
                phone=phone,
                birthday_day=day,
                birthday_month=month,
                terms=True,
                marketing_opt_in=marketing_opt_in,
                src=source or "back-office",
                ref=None,
            ),
        )
    except LoyaltyError as exc:
        if exc.code == "already_member":
            raise LoyaltyError(
                409, "already_member", "There is already a member with that email or phone."
            ) from None
        if exc.code == "first_name_required":
            raise LoyaltyError(422, exc.code, "Give the member's name.") from None
        raise
    member = session.get(LoyaltyMember, joined.member_id)
    assert member is not None
    if member.marketing_opt_in:
        member.opt_in_source = "back office"
    audit(
        session,
        "member_added",
        "added from the back office" + (" (opted in to offers)" if marketing_opt_in else ""),
        member_id=member.id,
        card_id=joined.card_id,
    )
    return member.id


@dataclass(frozen=True, slots=True)
class MemberPatch:
    """Only the fields in `sent` are applied; a sent None clears (email, phone,
    birthday, notes)."""

    sent: frozenset[str]
    first_name: str | None = None
    email: str | None = None
    phone: str | None = None
    birthday: str | None = None
    marketing_opt_in: bool | None = None
    notes: str | None = None


def _taken(session: Session, member_id: int, cond: ColumnElement[bool]) -> bool:
    """Another member holds this contact. Erased members hold none, so they never count."""
    return (
        session.scalar(select(LoyaltyMember.id).where(cond, LoyaltyMember.id != member_id))
        is not None
    )


def patch_member(session: Session, member_id: int, patch: MemberPatch) -> None:
    member, card = _live(session, member_id)
    now = now_utc()
    changed: list[str] = []
    wallet_refresh = False
    if "first_name" in patch.sent and patch.first_name is not None:
        name = clean_first_name(patch.first_name)
        if name != member.first_name:
            member.first_name = name
            changed.append("name")
            wallet_refresh = True
    email, phone = member.email, member.phone
    if "email" in patch.sent:
        raw = (patch.email or "").strip()
        email = normalise_email(raw) if raw else None
        if raw and email is None:
            raise LoyaltyError(422, "bad_email", "That email address does not look right.")
        if email and _taken(session, member.id, LoyaltyMember.email == email):
            raise LoyaltyError(409, "contact_taken", "Another member already has that email.")
    if "phone" in patch.sent:
        raw = (patch.phone or "").strip()
        phone = normalise_phone(raw) if raw else None
        if raw and phone is None:
            raise LoyaltyError(422, "bad_phone", "That phone number does not look right.")
        if phone and _taken(session, member.id, LoyaltyMember.phone == phone):
            raise LoyaltyError(
                409, "contact_taken", "Another member already has that phone number."
            )
    if email is None and phone is None:
        raise LoyaltyError(
            422,
            "contact_required",
            "Keep an email or a phone number: it is how they find the card.",
        )
    if email != member.email:
        member.email = email
        changed.append("email")
    if phone != member.phone:
        member.phone = phone
        changed.append("phone")
    if "birthday" in patch.sent:
        day, month = _parse_birthday(patch.birthday)
        check_birthday(day, month)
        if (day, month) != (member.birthday_day, member.birthday_month):
            member.birthday_day, member.birthday_month = day, month
            member.birthday_set_at = now if day is not None else None
            changed.append("birthday" if day is not None else "birthday removed")
    if "notes" in patch.sent:
        notes = (patch.notes or "").strip()
        if len(notes) > 1000:
            raise LoyaltyError(422, "notes_too_long", "Keep notes to 1000 characters.")
        member.notes = notes or None
    if "marketing_opt_in" in patch.sent and patch.marketing_opt_in is not None:
        set_marketing_opt_in(session, card, opt_in=patch.marketing_opt_in, source="back office")
    if changed:
        audit(
            session,
            "profile",
            f"back office changed {', '.join(changed)}",
            card_id=card.id,
            member_id=member.id,
            at=now,
        )
    if wallet_refresh:
        for other in session.scalars(
            select(LoyaltyCard).where(
                LoyaltyCard.member_id == member.id, LoyaltyCard.voided_at.is_(None)
            )
        ):
            touch(other, now)
            enqueue_wallet_update(session, other.id, None)
    session.flush()


def add_stamp(session: Session, member_id: int, *, sticker: str | None = None) -> None:
    """One purchase stamp from the back office. No cooldown: it is behind the password."""
    _, card = _live(session, member_id)
    if card.program.kind is not ProgramKind.STAMPS:
        raise LoyaltyError(409, "points_card", "This is a points card: stamps do not apply.")
    if sticker is not None and sticker not in STICKER_NAMES:
        raise LoyaltyError(422, "unknown_sticker", "There is no sticker by that name.")
    now = now_utc()
    event, _ = _apply(
        session,
        card,
        1,
        StampReason.PURCHASE,
        now=now,
        note=BACK_OFFICE_NOTE,
        sticker=sticker,
    )
    credit_referrer(session, card, event, now)
    audit(
        session,
        "back_office_stamp",
        "stamp given from the back office",
        card_id=card.id,
        member_id=card.member_id,
        at=now,
    )


def give_reward(session: Session, member_id: int, *, reward_id: int | None = None) -> None:
    """Give the free drink: the named reward, else the oldest ready one. No drink is
    recorded (the till knows what was made)."""
    _, card = _live(session, member_id)
    ready = available_rewards(session, card.id, now_utc())
    if not ready:
        raise LoyaltyError(409, "no_reward", "There is no free drink ready on this card yet.")
    chosen = ready[0] if reward_id is None else next((r for r in ready if r.id == reward_id), None)
    if chosen is None:
        raise LoyaltyError(404, "unknown_reward", "That reward is not ready on this card.")
    redeem(session, None, reward_id=chosen.id, menu_item_id=None)


def undo_last(session: Session, member_id: int) -> str:
    """Reverse the newest stamp, correction or free drink on the main card. Returns what
    was undone, in words."""
    _, card = _live(session, member_id)
    now = now_utc()
    event, reward = _undo_target(session, card)
    if reward is not None:
        unredeem(session, card, reward, now=now, who="the back office")
        return "the free drink"
    if event is None:
        raise LoyaltyError(409, "nothing_to_undo", "There is nothing on this card to undo.")
    reverse_with_referral(
        session,
        card,
        event,
        now=now,
        staff_user_id=None,
        device_id=None,
        note="undone from the back office",
    )
    return _event_words(event)


def set_sticker(session: Session, member_id: int, *, slot: int, sticker: str) -> None:
    """Swap the sticker in one filled slot. Cosmetic: the ledger is untouched."""
    _, card = _live(session, member_id)
    program = card.program
    if program.kind is not ProgramKind.STAMPS:
        raise LoyaltyError(409, "points_card", "A points card has no stickers.")
    if sticker not in STICKER_NAMES:
        raise LoyaltyError(422, "unknown_sticker", "There is no sticker by that name.")
    if not 0 <= slot < card.stamps_current:
        raise LoyaltyError(422, "bad_slot", "Only a filled slot has a sticker to change.")
    slots = fit_stickers(card.stickers, card.stamps_current, sticker_set(program.stickers))
    if slots[slot] == sticker:
        return
    slots[slot] = sticker
    card.stickers = slots
    now = now_utc()
    touch(card, now)
    enqueue_wallet_update(session, card.id, None)


@dataclass(frozen=True, slots=True)
class SendLink:
    delivery: str  # "email" | "sms" | "none"
    to: str | None
    url: str
    message: str
    outgoing: Outgoing | None


def send_link(session: Session, member_id: int) -> SendLink:
    """Send the member their web-card link (which offers Apple and Google Wallet). With
    no channel configured, the link comes back for staff to copy."""
    member, card = _live(session, member_id)
    url = web_card_url(card)
    body = (
        f"Hi {member.first_name},\n\nHere is your Sasha's Corner card: {url}\n\n"
        "Open it on your phone and add it to Apple Wallet or Google Wallet, so your stamps "
        "and our news are on your lock screen.\n\nSasha's Corner, 23 Commercial Street, Dundee\n"
    )
    outgoing: Outgoing | None = None
    if member.email and settings.smtp_configured:
        outgoing = Outgoing(
            channel="email", to=member.email, subject="Your Sasha's Corner card", body=body
        )
        delivery, to = "email", mask_contact(member.email, None)
    elif member.phone and settings.twilio_configured:
        outgoing = Outgoing(
            channel="sms",
            to=member.phone,
            subject="",
            body=f"Your Sasha's Corner card: {url} — add it to your wallet from there.",
        )
        delivery, to = "sms", mask_contact(None, member.phone)
    else:
        delivery, to = "none", None
    if outgoing is not None:
        audit(
            session,
            "card_link_sent",
            f"card link sent by {delivery} from the back office",
            card_id=card.id,
            member_id=member.id,
        )
        message = f"The card link is on its way to {to}."
    elif member.email or member.phone:
        message = (
            "Email and text are not set up yet, so nothing was sent. Copy the link and send "
            "it to them yourself."
        )
    else:
        message = "This member has no email or phone. Copy the link and give it to them."
    return SendLink(delivery=delivery, to=to, url=url, message=message, outgoing=outgoing)
