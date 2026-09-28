"""The numbers the spec says to watch after 90 days, and the 19:30 summary.

SPEC success metrics: members, share of transactions with a stamp, redemptions per week,
repeat visits of members, push-message return rate. Definitions, since each could be
computed several plausible ways and the plausible-but-wrong one is the dangerous kind
(ARCHITECTURE.md 8C):

- **A visit is a purchase scan**, not a stamp: a +3 for a group order is one visit.
  Undone scans are not visits.
- **Stamp share of transactions** = visits / distinct EPOS receipts in the window. EPOS
  only: delivery orders cannot be stamped, and the loyalty £0 sales (channel OTHER) are
  not transactions. Null with no receipts -- 0% would claim nobody stamped.
- **Visits per member per month** = visits / live members / (days / 30). Null with no
  members.
- **Campaign return rate** = deliveries followed by a purchase within 7 days / deliveries
  old enough to have had their 7 days. Null with none.

Days are LOCAL days (Europe/London): a 23:30 stamp belongs to that evening.

Phase 3:

- **Per programme.** Every figure is for ONE programme's cards -- the main stamp card
  unless `program_slug` says otherwise -- because a member who shows two cards at one
  visit is one visit, and adding programmes together would count it twice. "Stamps" on
  a points programme are its points. `members_total` for the main programme is every
  live member (as before); for another, the live members holding its card.
- **Repeat-visit rate, members vs non-members** (SPEC success metric). The only
  identity a till receipt has is the Lightspeed customer attached to it
  (`loyalty_pos_receipt`; payments are ingested as daily totals, with no card
  fingerprint). So: among till customers seen in the window, the share who came back on
  a second local day -- members (customer linked to a live member holding this
  programme's card) against everyone else. Both null, with `repeat_rate_reason`, when no
  receipt in the window names a customer; `receipts_with_customer_share` says how much of
  the trade the comparison covers, because a till where staff attach customers only for
  regulars would flatter members. `repeat_rate_members_by_scans` is the members' figure
  from scans alone, which exists even without the till.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from cafeops.config import settings
from cafeops.db.models import (
    LoyaltyAudit,
    LoyaltyCampaignDelivery,
    LoyaltyCard,
    LoyaltyMember,
    LoyaltyPosReceipt,
    LoyaltyReward,
    LoyaltyStampEvent,
    Sale,
    SaleChannel,
    StampReason,
)
from cafeops.services.loyalty.campaigns import RETURN_WINDOW
from cafeops.services.loyalty.common import DEFAULT_PROGRAM_SLUG, default_program, now_utc

__all__ = ["DailySummary", "DayRow", "LoyaltyStats", "SourceRow", "daily_summary", "stats"]


@dataclass(frozen=True, slots=True)
class SourceRow:
    source: str
    members: int


@dataclass(frozen=True, slots=True)
class DayRow:
    date: date
    new_members: int
    stamps: int
    redemptions: int


@dataclass(frozen=True, slots=True)
class LoyaltyStats:
    members_total: int
    members_new_in_window: int
    opted_in_share: float
    by_source: tuple[SourceRow, ...]
    daily: tuple[DayRow, ...]
    stamp_share_of_transactions: float | None
    redemptions_per_week: float
    visits_per_member_per_month: float | None
    members_with_redemption_share: float
    campaign_return_rate: float | None
    # --- phase 3 --------------------------------------------------------------------
    program_slug: str = "stamp"
    repeat_rate_members: float | None = None
    repeat_rate_non_members: float | None = None
    repeat_customers_members: int = 0
    repeat_customers_non_members: int = 0
    repeat_rate_members_by_scans: float | None = None
    receipts_with_customer_share: float | None = None
    repeat_rate_reason: str | None = None


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=settings.tz).astimezone(UTC)


def _local_day(at: datetime) -> date:
    return at.astimezone(settings.tz).date()


def _visits(
    session: Session, since: datetime, until: datetime, program_id: int | None = None
) -> list[LoyaltyStampEvent]:
    """Purchase scans in [since, until) that were not undone (on one programme's cards)."""
    undone = select(LoyaltyStampEvent.undoes_event_id).where(
        LoyaltyStampEvent.reason == StampReason.UNDO,
        LoyaltyStampEvent.undoes_event_id.is_not(None),
    )
    stmt = select(LoyaltyStampEvent).where(
        LoyaltyStampEvent.reason == StampReason.PURCHASE,
        LoyaltyStampEvent.created_at >= since,
        LoyaltyStampEvent.created_at < until,
        LoyaltyStampEvent.id.not_in(undone),
    )
    if program_id is not None:
        stmt = stmt.where(
            LoyaltyStampEvent.card_id.in_(
                select(LoyaltyCard.id).where(LoyaltyCard.program_id == program_id)
            )
        )
    return list(session.scalars(stmt))


def _repeat_share(days_by_customer: dict[str, set[date]]) -> float | None:
    if not days_by_customer:
        return None
    back = sum(1 for d in days_by_customer.values() if len(d) >= 2)
    return round(back / len(days_by_customer), 4)


@dataclass(frozen=True, slots=True)
class _Repeat:
    members: float | None
    non_members: float | None
    n_members: int
    n_non_members: int
    by_scans: float | None
    coverage: float | None
    reason: str | None


def _repeat(
    session: Session,
    since: datetime,
    until: datetime,
    program_id: int,
    visits: list[LoyaltyStampEvent],
    receipts_total: int,
) -> _Repeat:
    card_member: dict[str, int] = {
        str(cid): int(mid)
        for cid, mid in session.execute(
            select(LoyaltyCard.id, LoyaltyCard.member_id).where(
                LoyaltyCard.program_id == program_id
            )
        ).all()
    }
    scan_days: dict[str, set[date]] = defaultdict(set)
    for event in visits:
        scan_days[str(card_member.get(event.card_id, event.card_id))].add(
            _local_day(event.created_at)
        )
    by_scans = _repeat_share(scan_days)

    member_customers = set(
        session.scalars(
            select(LoyaltyMember.lightspeed_customer_id)
            .join(LoyaltyCard, LoyaltyCard.member_id == LoyaltyMember.id)
            .where(
                LoyaltyMember.deleted_at.is_(None),
                LoyaltyMember.lightspeed_customer_id.is_not(None),
                LoyaltyCard.program_id == program_id,
                LoyaltyCard.voided_at.is_(None),
            )
        )
    )
    rows = session.execute(
        select(LoyaltyPosReceipt.lightspeed_customer_id, LoyaltyPosReceipt.closed_at).where(
            LoyaltyPosReceipt.closed_at >= since,
            LoyaltyPosReceipt.closed_at < until,
            LoyaltyPosReceipt.total_pence > 0,
        )
    ).all()
    if not rows:
        return _Repeat(
            None,
            None,
            0,
            0,
            by_scans,
            0.0 if receipts_total else None,
            "No till receipt in this window names a customer. Lightspeed only knows who "
            "bought something when staff attach a customer account to the check; payments "
            "come in as daily totals with no card fingerprint. Until the till attaches "
            "customers, non-members cannot be told apart, so there is nothing to compare.",
        )
    members: dict[str, set[date]] = defaultdict(set)
    others: dict[str, set[date]] = defaultdict(set)
    for customer_id, closed_at in rows:
        target = members if customer_id in member_customers else others
        target[customer_id].add(_local_day(closed_at))
    reason = None
    if not members:
        reason = "No member is linked to a till customer seen in this window."
    elif not others:
        reason = "Every till customer seen in this window is a member."
    return _Repeat(
        _repeat_share(members),
        _repeat_share(others),
        len(members),
        len(others),
        by_scans,
        round(len(rows) / receipts_total, 4) if receipts_total else None,
        reason,
    )


def stats(
    session: Session,
    *,
    days: int = 90,
    now: datetime | None = None,
    program_slug: str | None = None,
) -> LoyaltyStats:
    from cafeops.services.loyalty.programs import program_by_slug

    now = now or now_utc()
    program = program_by_slug(session, program_slug) if program_slug else default_program(session)
    is_default = program.slug == DEFAULT_PROGRAM_SLUG
    held = select(LoyaltyCard.member_id).where(
        LoyaltyCard.program_id == program.id, LoyaltyCard.voided_at.is_(None)
    )
    on_program = select(LoyaltyCard.id).where(LoyaltyCard.program_id == program.id)
    today = _local_day(now)
    first = today - timedelta(days=days - 1)
    since = _local_midnight(first)
    until = _local_midnight(today + timedelta(days=1))
    live: ColumnElement[bool] = LoyaltyMember.deleted_at.is_(None)
    if not is_default:
        live = and_(live, LoyaltyMember.id.in_(held))

    members_total = int(session.scalar(select(func.count(LoyaltyMember.id)).where(live)) or 0)
    opted = int(
        session.scalar(
            select(func.count(LoyaltyMember.id)).where(
                live, LoyaltyMember.marketing_opt_in.is_(True)
            )
        )
        or 0
    )
    new_stmt = select(LoyaltyMember.created_at).where(
        LoyaltyMember.created_at >= since, LoyaltyMember.created_at < until
    )
    if not is_default:
        new_stmt = select(LoyaltyCard.created_at).where(
            LoyaltyCard.program_id == program.id,
            LoyaltyCard.created_at >= since,
            LoyaltyCard.created_at < until,
        )
    new_rows = list(session.scalars(new_stmt))
    by_source = tuple(
        SourceRow(source=src or "direct", members=int(n))
        for src, n in session.execute(
            select(LoyaltyMember.source, func.count(LoyaltyMember.id))
            .where(live)
            .group_by(LoyaltyMember.source)
            .order_by(func.count(LoyaltyMember.id).desc())
        ).all()
    )

    visits = _visits(session, since, until, program.id)
    redeemed = list(
        session.scalars(
            select(LoyaltyReward.redeemed_at).where(
                LoyaltyReward.redeemed_at >= since,
                LoyaltyReward.redeemed_at < until,
                LoyaltyReward.card_id.in_(on_program),
            )
        )
    )
    new_by_day = Counter(_local_day(at) for at in new_rows)
    stamps_by_day: Counter[date] = Counter()
    for event in visits:
        stamps_by_day[_local_day(event.created_at)] += event.delta
    redeemed_by_day = Counter(_local_day(at) for at in redeemed if at is not None)
    daily = tuple(
        DayRow(
            date=first + timedelta(days=i),
            new_members=new_by_day.get(first + timedelta(days=i), 0),
            stamps=stamps_by_day.get(first + timedelta(days=i), 0),
            redemptions=redeemed_by_day.get(first + timedelta(days=i), 0),
        )
        for i in range(days)
    )

    receipts = int(
        session.scalar(
            select(func.count(func.distinct(Sale.lightspeed_receipt_id))).where(
                Sale.channel == SaleChannel.EPOS,
                Sale.voided.is_(False),
                Sale.sold_at >= since,
                Sale.sold_at < until,
            )
        )
        or 0
    )
    with_redemption = int(
        session.scalar(
            select(func.count(func.distinct(LoyaltyCard.member_id)))
            .join(LoyaltyReward, LoyaltyReward.card_id == LoyaltyCard.id)
            .join(LoyaltyMember, LoyaltyMember.id == LoyaltyCard.member_id)
            .where(
                live,
                LoyaltyReward.redeemed_at.is_not(None),
                LoyaltyCard.program_id == program.id,
            )
        )
        or 0
    )
    matured = now - RETURN_WINDOW
    delivered = int(
        session.scalar(
            select(func.count(LoyaltyCampaignDelivery.id)).where(
                LoyaltyCampaignDelivery.sent_at >= since,
                LoyaltyCampaignDelivery.sent_at <= matured,
            )
        )
        or 0
    )
    returned = int(
        session.scalar(
            select(func.count(LoyaltyCampaignDelivery.id)).where(
                LoyaltyCampaignDelivery.sent_at >= since,
                LoyaltyCampaignDelivery.sent_at <= matured,
                LoyaltyCampaignDelivery.returned_at.is_not(None),
            )
        )
        or 0
    )
    repeat = _repeat(session, since, until, program.id, visits, receipts)
    return LoyaltyStats(
        members_total=members_total,
        members_new_in_window=len(new_rows),
        opted_in_share=round(opted / members_total, 4) if members_total else 0.0,
        by_source=by_source,
        daily=daily,
        stamp_share_of_transactions=round(len(visits) / receipts, 4) if receipts else None,
        redemptions_per_week=round(len(redeemed) / (days / 7), 2),
        visits_per_member_per_month=(
            round(len(visits) / members_total / (days / 30), 3) if members_total else None
        ),
        members_with_redemption_share=(
            round(with_redemption / members_total, 4) if members_total else 0.0
        ),
        campaign_return_rate=round(returned / delivered, 4) if delivered else None,
        program_slug=program.slug,
        repeat_rate_members=repeat.members,
        repeat_rate_non_members=repeat.non_members,
        repeat_customers_members=repeat.n_members,
        repeat_customers_non_members=repeat.n_non_members,
        repeat_rate_members_by_scans=repeat.by_scans,
        receipts_with_customer_share=repeat.coverage,
        repeat_rate_reason=repeat.reason,
    )


@dataclass(frozen=True, slots=True)
class DailySummary:
    day: date
    new_members: int
    visits: int
    stamps: int
    redemptions: int
    rewards_issued: int
    members_total: int
    alerts: int


def daily_summary(session: Session, *, day: date | None = None) -> DailySummary:
    """One local day, for the 19:30 Telegram message and `cafeops loyalty summary`."""
    day = day or _local_day(now_utc())
    since = _local_midnight(day)
    until = _local_midnight(day + timedelta(days=1))
    visits = _visits(session, since, until, default_program(session).id)

    def count(stmt: Select[tuple[int]]) -> int:
        return int(session.scalar(stmt) or 0)

    return DailySummary(
        day=day,
        new_members=count(
            select(func.count(LoyaltyMember.id)).where(
                LoyaltyMember.created_at >= since, LoyaltyMember.created_at < until
            )
        ),
        visits=len(visits),
        stamps=sum(e.delta for e in visits),
        redemptions=count(
            select(func.count(LoyaltyReward.id)).where(
                LoyaltyReward.redeemed_at >= since, LoyaltyReward.redeemed_at < until
            )
        ),
        rewards_issued=count(
            select(func.count(LoyaltyReward.id)).where(
                LoyaltyReward.issued_at >= since,
                LoyaltyReward.issued_at < until,
                LoyaltyReward.voided_at.is_(None),
            )
        ),
        members_total=count(
            select(func.count(LoyaltyMember.id)).where(LoyaltyMember.deleted_at.is_(None))
        ),
        alerts=count(
            select(func.count(LoyaltyAudit.id)).where(
                LoyaltyAudit.is_alert.is_(True), LoyaltyAudit.at >= since, LoyaltyAudit.at < until
            )
        ),
    )
