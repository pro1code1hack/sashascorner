"""The back office's Members screen: list, detail, manual adjust, the programme settings.

CONTRACT §6. Read models are built here as frozen dataclasses so the API layer only
converts; nothing here decides a stamp rule (that is `stamping` and the domain).

`wallet` on a row answers "where does this member keep the card": `apple` if an iPhone
registered the pass, else `google` if a Google object exists, else `web` if the web card
was ever opened, else null (joined, never looked).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from cafeops.config import settings
from cafeops.db.models import (
    LoyaltyCard,
    LoyaltyMember,
    LoyaltyPosAward,
    LoyaltyProgram,
    LoyaltyReward,
    LoyaltyRewardOption,
    LoyaltyStampEvent,
    MenuItem,
    PosAwardStatus,
    SizeCode,
    StaffDevice,
    StaffUser,
    StampReason,
)
from cafeops.domain.loyalty import mask_contact, normalise_email, normalise_phone
from cafeops.services.loyalty.common import available_rewards, default_program, now_utc
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.join import find_member_by_contact
from cafeops.services.loyalty.pos import member_receipts
from cafeops.services.loyalty.programs import program_by_slug
from cafeops.services.loyalty.staff_auth import verify_manager_pin
from cafeops.services.loyalty.stamping import adjust
from cafeops.services.loyalty.wallets import wallet_kind_by_card

__all__ = [
    "CardRow",
    "EventRow",
    "LinkInfo",
    "MemberBrief",
    "MemberDetail",
    "MemberPage",
    "MemberRow",
    "PosReceiptRow",
    "RewardRow",
    "adjust_member",
    "member_brief",
    "member_detail",
    "members_page",
]

Segment = Literal["all", "lapsed_30", "reward_ready", "opted_in", "new_30", "no_wallet"]
SEGMENTS: tuple[Segment, ...] = (
    "all",
    "reward_ready",
    "lapsed_30",
    "new_30",
    "opted_in",
    "no_wallet",
)
#: "recent" is the last visit (BACKOFFICE-V2), falling back to any activity.
Sort = Literal["recent", "joined", "stamps", "name"]


@dataclass(frozen=True, slots=True)
class MemberRow:
    member_id: int
    card_id: str
    first_name: str
    email: str | None
    phone: str | None
    source: str | None
    created_at: datetime
    last_activity_at: datetime
    stamps_current: int
    stamps_required: int
    cycles_completed: int
    rewards_redeemed: int
    reward_available: bool
    marketing_opt_in: bool
    wallet: str | None
    #: Last purchase stamp or free drink (BACKOFFICE-V2); None = not since joining.
    last_visit_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MemberPage:
    total: int
    members: tuple[MemberRow, ...]
    #: Every segment's size, ignoring the search text.
    counts: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class EventRow:
    id: int
    delta: int
    reason: str
    note: str | None
    staff_name: str | None
    device_name: str | None
    created_at: datetime
    #: Phase 3: which card (programme) the event is on.
    program_slug: str = "stamp"


@dataclass(frozen=True, slots=True)
class RewardRow:
    id: int
    kind: str
    issued_at: datetime
    expires_at: datetime | None
    redeemed_at: datetime | None
    redeemed_item: str | None
    staff_name: str | None
    voided_at: datetime | None
    program_slug: str = "stamp"
    #: The catalogue entry it was taken as ("Slice of cake"), phase 3.
    redeemed_option: str | None = None


@dataclass(frozen=True, slots=True)
class CardRow:
    """One of the member's cards (phase 3: one per programme)."""

    card_id: str
    program_slug: str
    program_name: str
    program_kind: str
    stamps_current: int
    stamps_required: int
    cycles_completed: int
    reward_available: bool
    voided: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PosReceiptRow:
    receipt_id: str
    closed_at: datetime
    total_pence: int
    #: What it earned, per card: "stamp +2", "matcha-club +1", "stamp skipped (scanned)".
    awards: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LinkInfo:
    customer_id: str
    linked_at: datetime | None
    source: str | None
    receipts: tuple[PosReceiptRow, ...]


@dataclass(frozen=True, slots=True)
class MemberDetail:
    member: MemberRow
    birthday: str | None
    opt_in_at: datetime | None
    opt_in_source: str | None
    referred_by: int | None
    events: tuple[EventRow, ...]
    rewards: tuple[RewardRow, ...]
    cards: tuple[CardRow, ...] = ()
    lightspeed: LinkInfo | None = None


def _rows(
    session: Session, pairs: list[tuple[LoyaltyMember, LoyaltyCard]], required: int
) -> list[MemberRow]:
    card_ids = [c.id for _, c in pairs]
    wallets = wallet_kind_by_card(session, card_ids)
    redeemed: dict[str, int] = {}
    if card_ids:
        redeemed = {
            str(card_id): int(n)
            for card_id, n in session.execute(
                select(LoyaltyReward.card_id, func.count(LoyaltyReward.id))
                .where(
                    LoyaltyReward.card_id.in_(card_ids),
                    LoyaltyReward.redeemed_at.is_not(None),
                )
                .group_by(LoyaltyReward.card_id)
            ).all()
        }
    visits = last_visits(session, card_ids)
    out: list[MemberRow] = []
    for member, card in pairs:
        wallet = wallets.get(card.id) or ("web" if card.web_seen_at else None)
        out.append(
            MemberRow(
                member_id=member.id,
                card_id=card.id,
                first_name=member.first_name,
                email=member.email,
                phone=member.phone,
                source=member.source,
                created_at=member.created_at,
                last_activity_at=member.last_activity_at,
                stamps_current=card.stamps_current,
                stamps_required=required,
                cycles_completed=card.cycles_completed,
                rewards_redeemed=redeemed.get(card.id, 0),
                reward_available=card.reward_available,
                marketing_opt_in=member.marketing_opt_in,
                wallet=wallet,
                last_visit_at=visits.get(card.id),
            )
        )
    return out


def last_visits(session: Session, card_ids: list[str]) -> dict[str, datetime]:
    """Each card's last visit: a purchase or paper stamp not undone, or a free drink."""
    if not card_ids:
        return {}
    undone = select(LoyaltyStampEvent.undoes_event_id).where(
        LoyaltyStampEvent.undoes_event_id.is_not(None)
    )
    out: dict[str, datetime] = {}
    for card_id, at in session.execute(
        select(LoyaltyStampEvent.card_id, func.max(LoyaltyStampEvent.created_at))
        .where(
            LoyaltyStampEvent.card_id.in_(card_ids),
            LoyaltyStampEvent.reason.in_([StampReason.PURCHASE, StampReason.PAPER_MIGRATION]),
            LoyaltyStampEvent.id.not_in(undone),
        )
        .group_by(LoyaltyStampEvent.card_id)
    ):
        out[str(card_id)] = at
    for card_id, at in session.execute(
        select(LoyaltyReward.card_id, func.max(LoyaltyReward.redeemed_at))
        .where(LoyaltyReward.card_id.in_(card_ids), LoyaltyReward.redeemed_at.is_not(None))
        .group_by(LoyaltyReward.card_id)
    ):
        if at is not None and (str(card_id) not in out or at > out[str(card_id)]):
            out[str(card_id)] = at
    return out


def _no_wallet_cards(session: Session, program_id: int) -> list[str]:
    """Live cards on the programme never opened anywhere: no web view, no wallet pass."""
    ids = list(
        session.scalars(
            select(LoyaltyCard.id).where(
                LoyaltyCard.program_id == program_id,
                LoyaltyCard.voided_at.is_(None),
                LoyaltyCard.web_seen_at.is_(None),
            )
        )
    )
    wallets = wallet_kind_by_card(session, ids)
    return [i for i in ids if i not in wallets]


def _segment_filter(
    session: Session, segment: Segment, program_id: int, now: datetime
) -> ColumnElement[bool] | None:
    if segment == "lapsed_30":
        return LoyaltyMember.last_activity_at < now - timedelta(days=30)
    if segment == "reward_ready":
        return LoyaltyCard.reward_available.is_(True)
    if segment == "opted_in":
        return LoyaltyMember.marketing_opt_in.is_(True)
    if segment == "new_30":
        return LoyaltyMember.created_at >= now - timedelta(days=30)
    if segment == "no_wallet":
        return LoyaltyCard.id.in_(_no_wallet_cards(session, program_id))
    return None


def members_page(
    session: Session,
    *,
    q: str | None = None,
    segment: Segment = "all",
    sort: Sort = "recent",
    limit: int = 50,
    offset: int = 0,
    program_slug: str | None = None,
) -> MemberPage:
    """Live members only: an erased member has nothing left to show but a number.

    Phase 3: `program_slug` lists that programme's card holders (their card on it);
    default is the main stamp card, as before.
    """
    program = program_by_slug(session, program_slug) if program_slug else default_program(session)
    now = now_utc()
    stmt = (
        select(LoyaltyMember, LoyaltyCard)
        .join(LoyaltyCard, LoyaltyCard.member_id == LoyaltyMember.id)
        .where(LoyaltyCard.program_id == program.id, LoyaltyMember.deleted_at.is_(None))
    )
    search: list[ColumnElement[bool]] = []
    if q and q.strip():
        text = q.strip()
        email = normalise_email(text) if "@" in text else None
        digits = "".join(ch for ch in text if ch.isdigit())
        conds: list[ColumnElement[bool]] = [
            func.lower(LoyaltyMember.first_name).like(f"%{text.lower()}%"),
            func.lower(LoyaltyMember.email).like(f"%{text.lower()}%"),
        ]
        if email:
            conds.append(LoyaltyMember.email == email)
        if len(digits) >= 4:
            conds.append(LoyaltyMember.phone.like(f"%{digits[-9:]}%"))
            phone = normalise_phone(text)
            if phone:
                conds.append(LoyaltyMember.phone == phone)
        search = conds
    base = stmt
    counts: dict[str, int] = {}
    for name in SEGMENTS:
        cond = _segment_filter(session, name, program.id, now)
        counted = base if cond is None else base.where(cond)
        counts[name] = int(
            session.scalar(select(func.count()).select_from(counted.subquery())) or 0
        )
    if search:
        stmt = stmt.where(or_(*search))
    chosen = _segment_filter(session, segment, program.id, now)
    if chosen is not None:
        stmt = stmt.where(chosen)
    total = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    order: tuple[Any, ...]
    if sort == "stamps":
        order = (
            LoyaltyCard.cycles_completed.desc(),
            LoyaltyCard.stamps_current.desc(),
            LoyaltyMember.id,
        )
    elif sort == "name":
        order = (func.lower(LoyaltyMember.first_name), LoyaltyMember.id)
    elif sort == "joined":
        order = (LoyaltyMember.created_at.desc(), LoyaltyMember.id.desc())
    else:
        order = (LoyaltyMember.last_activity_at.desc(), LoyaltyMember.id)
    if sort == "recent":
        # The last VISIT, which the ledger knows and the member row does not: sorted here
        # (a café's member list is hundreds, not millions), activity as the tie-break.
        pairs_all = [(m, c) for m, c in session.execute(stmt.order_by(*order))]
        visits = last_visits(session, [c.id for _, c in pairs_all])
        epoch = datetime.min.replace(tzinfo=now.tzinfo)
        pairs_all.sort(key=lambda mc: visits.get(mc[1].id) or epoch, reverse=True)
        pairs = pairs_all[offset : offset + limit]
    else:
        pairs = [
            (m, c) for m, c in session.execute(stmt.order_by(*order).limit(limit).offset(offset))
        ]
    return MemberPage(
        total=total,
        members=tuple(_rows(session, pairs, program.stamps_required)),
        counts=counts,
    )


def _card_of(
    session: Session, member_id: int, program_slug: str | None = None
) -> tuple[LoyaltyMember, LoyaltyCard, int]:
    """The member and their card on `program_slug` (default: the main card, else their
    first card -- a phase-3 member may hold only a club card)."""
    member = session.get(LoyaltyMember, member_id)
    if member is None:
        raise LoyaltyError(404, "unknown_member", "There is no member with that id.")
    program = program_by_slug(session, program_slug) if program_slug else default_program(session)
    card = session.scalar(
        select(LoyaltyCard).where(
            LoyaltyCard.member_id == member.id, LoyaltyCard.program_id == program.id
        )
    )
    if card is None and not program_slug:
        card = session.scalar(
            select(LoyaltyCard)
            .where(LoyaltyCard.member_id == member.id)
            .order_by(LoyaltyCard.created_at)
        )
    if card is None:
        raise LoyaltyError(404, "unknown_member", "That member has no card on that programme.")
    return member, card, card.program.stamps_required


def _cards(session: Session, member_id: int) -> list[LoyaltyCard]:
    return list(
        session.scalars(
            select(LoyaltyCard)
            .join(LoyaltyProgram, LoyaltyProgram.id == LoyaltyCard.program_id)
            .where(LoyaltyCard.member_id == member_id)
            .order_by(LoyaltyProgram.sort_order, LoyaltyProgram.id)
        )
    )


def _link_info(
    session: Session, member: LoyaltyMember, cards: list[LoyaltyCard]
) -> LinkInfo | None:
    if member.lightspeed_customer_id is None:
        return None
    slug_of = {c.id: c.program.name for c in cards}
    rows: list[PosReceiptRow] = []
    for receipt in member_receipts(session, member.lightspeed_customer_id):
        awards = []
        for award in session.scalars(
            select(LoyaltyPosAward).where(
                LoyaltyPosAward.lightspeed_receipt_id == receipt.lightspeed_receipt_id,
                LoyaltyPosAward.card_id.in_(list(slug_of)),
            )
        ):
            slug = slug_of[award.card_id]
            if award.status is PosAwardStatus.AWARDED:
                awards.append(f"{slug} +{award.units}")
            elif award.status is PosAwardStatus.SKIPPED_STAFF_SCAN:
                awards.append(f"{slug}: already scanned at the till")
            elif award.status is PosAwardStatus.KEPT:
                awards.append(f"{slug}: kept ({award.detail or 'frozen'})")
        rows.append(
            PosReceiptRow(
                receipt_id=receipt.lightspeed_receipt_id,
                closed_at=receipt.closed_at,
                total_pence=receipt.total_pence,
                awards=tuple(awards),
            )
        )
    return LinkInfo(
        customer_id=member.lightspeed_customer_id,
        linked_at=member.lightspeed_linked_at,
        source=member.lightspeed_link_source,
        receipts=tuple(rows),
    )


def member_detail(session: Session, member_id: int) -> MemberDetail:
    member, card, required = _card_of(session, member_id)
    [row] = _rows(session, [(member, card)], required)
    cards = _cards(session, member.id)
    slug_of = {c.id: c.program.slug for c in cards}
    card_ids = list(slug_of)
    options = {
        o.id: o.name
        for o in session.scalars(
            select(LoyaltyRewardOption).where(
                LoyaltyRewardOption.id.in_(
                    select(LoyaltyReward.reward_option_id).where(
                        LoyaltyReward.card_id.in_(card_ids)
                    )
                )
            )
        )
    }
    staff = {u.id: u.name for u in session.scalars(select(StaffUser))}
    devices = {d.id: d.name for d in session.scalars(select(StaffDevice))}
    events = tuple(
        EventRow(
            id=e.id,
            delta=e.delta,
            reason=e.reason.value,
            note=e.note,
            staff_name=staff.get(e.staff_user_id) if e.staff_user_id else None,
            device_name=devices.get(e.device_id) if e.device_id else None,
            created_at=e.created_at,
            program_slug=slug_of.get(e.card_id, ""),
        )
        for e in session.scalars(
            select(LoyaltyStampEvent)
            .where(LoyaltyStampEvent.card_id.in_(card_ids))
            .order_by(LoyaltyStampEvent.created_at.desc(), LoyaltyStampEvent.id.desc())
        )
    )
    items = {
        i.id: i.name if i.size_code in (None, SizeCode.ONE) else f"{i.name} ({i.size_code.value})"
        for i in session.scalars(
            select(MenuItem).where(
                MenuItem.id.in_(
                    select(LoyaltyReward.redeemed_menu_item_id).where(
                        LoyaltyReward.card_id.in_(card_ids)
                    )
                )
            )
        )
    }
    rewards = tuple(
        RewardRow(
            id=r.id,
            kind=r.kind.value,
            issued_at=r.issued_at,
            expires_at=r.expires_at,
            redeemed_at=r.redeemed_at,
            redeemed_item=items.get(r.redeemed_menu_item_id) if r.redeemed_menu_item_id else None,
            staff_name=staff.get(r.staff_user_id) if r.staff_user_id else None,
            voided_at=r.voided_at,
            program_slug=slug_of.get(r.card_id, ""),
            redeemed_option=options.get(r.reward_option_id) if r.reward_option_id else None,
        )
        for r in session.scalars(
            select(LoyaltyReward)
            .where(LoyaltyReward.card_id.in_(card_ids))
            .order_by(LoyaltyReward.issued_at.desc(), LoyaltyReward.id.desc())
        )
    )
    birthday = (
        f"{member.birthday_day:02d}-{member.birthday_month:02d}"
        if member.birthday_day and member.birthday_month
        else None
    )
    return MemberDetail(
        member=row,
        birthday=birthday,
        opt_in_at=member.opt_in_at,
        opt_in_source=member.opt_in_source,
        referred_by=member.referred_by_member_id,
        events=events,
        rewards=rewards,
        cards=tuple(
            CardRow(
                card_id=c.id,
                program_slug=c.program.slug,
                program_name=c.program.name,
                program_kind=c.program.kind.value,
                stamps_current=c.stamps_current,
                stamps_required=c.program.stamps_required,
                cycles_completed=c.cycles_completed,
                reward_available=c.reward_available,
                voided=c.voided_at is not None,
                created_at=c.created_at,
            )
            for c in cards
        ),
        lightspeed=_link_info(session, member, cards),
    )


def adjust_member(
    session: Session,
    member_id: int,
    *,
    delta: int,
    reason: str,
    manager_pin: str,
    client_ip: str,
    program_slug: str | None = None,
) -> MemberDetail:
    _, card, _ = _card_of(session, member_id, program_slug)
    manager = verify_manager_pin(session, manager_pin, limiter_key=f"ip:{client_ip}")
    adjust(session, card_id=card.id, delta=delta, reason=reason, manager=manager)
    return member_detail(session, member_id)


@dataclass(frozen=True, slots=True)
class MemberBrief:
    """What the bot's `/member` answers: enough to settle a question at the till."""

    first_name: str
    contact_masked: str
    stamps_current: int
    stamps_required: int
    rewards: tuple[str, ...]
    last_visit: datetime | None
    member_since: date


def member_brief(session: Session, contact: str) -> MemberBrief | None:
    """A live member by phone or email, or None. "Last visit" is the last purchase scan."""
    member = find_member_by_contact(session, contact)
    if member is None:
        return None
    _, card, required = _card_of(session, member.id)
    now = now_utc()
    last = session.scalar(
        select(func.max(LoyaltyStampEvent.created_at)).where(
            LoyaltyStampEvent.card_id == card.id,
            LoyaltyStampEvent.reason == StampReason.PURCHASE,
        )
    )
    return MemberBrief(
        first_name=member.first_name,
        contact_masked=mask_contact(member.email, member.phone),
        stamps_current=card.stamps_current,
        stamps_required=required,
        rewards=tuple(r.kind.value for r in available_rewards(session, card.id, now)),
        last_visit=last,
        member_since=member.created_at.astimezone(settings.tz).date(),
    )
