"""Loyalty > Insights (docs/loyalty/BACKOFFICE-V2.md §3): how the card is doing.

The main stamp card only, over the last `days` against the `days` before. Definitions,
chosen to match what the screen says under each figure:

- **Members**: live members holding the main card now, against those who had joined by
  the start of the window (erased members are gone from both).
- **Active members** ("came in and stamped"): distinct members with a purchase stamp in
  the window, undone stamps excluded.
- **Visits per active member per month**: distinct (member, local day) purchase visits
  / active members / (days / 30).
- **Stamps given**: net purchase stamps -- PURCHASE and PAPER_MIGRATION deltas not undone.
  Welcome, referral and corrections are not "given" at the counter.
- **Free drinks**: rewards redeemed in the window (voided ones excluded).
- **Came back**: of the members who had joined before the window started, the share who
  stamped in it. Null when nobody had joined before.
- **Weekly** buckets are Monday-started local weeks from the one holding the window's
  first day up to this week; **hours** are local, 7:00 to 20:00.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    LoyaltyAudit,
    LoyaltyCard,
    LoyaltyMember,
    LoyaltyReward,
    LoyaltyStampEvent,
    StampReason,
)
from cafeops.services.loyalty.backoffice import source_label
from cafeops.services.loyalty.common import default_program, now_utc

__all__ = ["Insights", "Kpi", "insights"]

_GIVEN = (StampReason.PURCHASE, StampReason.PAPER_MIGRATION)
_HOURS = range(7, 21)


@dataclass(frozen=True, slots=True)
class Kpi:
    value: float | None
    previous: float | None


@dataclass(frozen=True, slots=True)
class Week:
    week_start: date
    new_members: int
    stamps: int
    free_drinks: int


@dataclass(frozen=True, slots=True)
class SourceShare:
    source: str | None
    label: str
    members: int
    share: float


@dataclass(frozen=True, slots=True)
class Regular:
    member_id: int
    first_name: str
    stamps: int


@dataclass(frozen=True, slots=True)
class Alert:
    at: datetime
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class Insights:
    days: int
    members: Kpi
    joined_in_window: int
    active_members: Kpi
    visits_per_active_member_per_month: float | None
    stamps: Kpi
    stamps_per_week: float
    free_drinks: Kpi
    came_back: Kpi
    opted_in_share: float | None
    opted_in_count: int
    weekly: tuple[Week, ...]
    by_source: tuple[SourceShare, ...]
    hours: tuple[tuple[int, int], ...]
    regulars: tuple[Regular, ...]
    alerts: tuple[Alert, ...]


def _local(at: datetime) -> datetime:
    return at.astimezone(settings.tz)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def insights(session: Session, *, days: int, now: datetime | None = None) -> Insights:
    now = now or now_utc()
    start = now - timedelta(days=days)
    prev_start = start - timedelta(days=days)
    program = default_program(session)

    members = list(
        session.execute(
            select(LoyaltyMember, LoyaltyCard)
            .join(LoyaltyCard, LoyaltyCard.member_id == LoyaltyMember.id)
            .where(
                LoyaltyCard.program_id == program.id,
                LoyaltyCard.voided_at.is_(None),
                LoyaltyMember.deleted_at.is_(None),
            )
        ).all()
    )
    card_member = {c.id: m.id for m, c in members}
    name_of = {m.id: m.first_name for m, _ in members}
    joined_at = {m.id: m.created_at for m, _ in members}
    card_ids = list(card_member)

    undone = select(LoyaltyStampEvent.undoes_event_id).where(
        LoyaltyStampEvent.undoes_event_id.is_not(None)
    )
    events = (
        list(
            session.execute(
                select(
                    LoyaltyStampEvent.card_id,
                    LoyaltyStampEvent.delta,
                    LoyaltyStampEvent.created_at,
                    LoyaltyStampEvent.reason,
                ).where(
                    LoyaltyStampEvent.card_id.in_(card_ids),
                    LoyaltyStampEvent.reason.in_(_GIVEN),
                    LoyaltyStampEvent.id.not_in(undone),
                    LoyaltyStampEvent.created_at >= prev_start,
                )
            ).all()
        )
        if card_ids
        else []
    )
    redemptions = (
        list(
            session.execute(
                select(LoyaltyReward.card_id, LoyaltyReward.redeemed_at).where(
                    LoyaltyReward.card_id.in_(card_ids),
                    LoyaltyReward.redeemed_at.is_not(None),
                    LoyaltyReward.voided_at.is_(None),
                    LoyaltyReward.redeemed_at >= prev_start,
                )
            ).all()
        )
        if card_ids
        else []
    )

    def window(lo: datetime, hi: datetime) -> tuple[int, set[int], set[tuple[int, date]], int]:
        stamps = 0
        active: set[int] = set()
        visits: set[tuple[int, date]] = set()
        for card_id, delta, at, reason in events:
            if lo <= at < hi:
                stamps += delta
                mid = card_member[card_id]
                if reason is StampReason.PURCHASE:
                    active.add(mid)
                    visits.add((mid, _local(at).date()))
        drinks = sum(1 for _, at in redemptions if at is not None and lo <= at < hi)
        return stamps, active, visits, drinks

    end = now + timedelta(seconds=1)
    stamps, active, visits, drinks = window(start, end)
    p_stamps, p_active, _, p_drinks = window(prev_start, start)

    total = len(members)
    before_start = sum(1 for m, _ in members if m.created_at < start)
    before_prev = sum(1 for m, _ in members if m.created_at < prev_start)

    def came_back(lo: datetime, active_ids: set[int], older: int) -> float | None:
        if older == 0:
            return None
        back = sum(1 for mid in active_ids if joined_at[mid] < lo)
        return back / older

    opted = sum(1 for m, _ in members if m.marketing_opt_in)

    # Weekly buckets, Monday-started local weeks.
    first = _monday(_local(start).date())
    this = _monday(_local(now).date())
    weeks: list[date] = []
    d = first
    while d <= this:
        weeks.append(d)
        d += timedelta(days=7)
    new_w: Counter[date] = Counter()
    stamps_w: Counter[date] = Counter()
    drinks_w: Counter[date] = Counter()
    for m, _ in members:
        if m.created_at >= start:
            new_w[_monday(_local(m.created_at).date())] += 1
    hours: Counter[int] = Counter()
    per_member: defaultdict[int, int] = defaultdict(int)
    for card_id, delta, at, _reason in events:
        if at >= start:
            local = _local(at)
            stamps_w[_monday(local.date())] += delta
            hours[local.hour] += delta
            per_member[card_member[card_id]] += delta
    for _, at in redemptions:
        if at is not None and at >= start:
            drinks_w[_monday(_local(at).date())] += 1

    # Untagged joins came through the website itself: one row with a `website` tag.
    sources: Counter[str | None] = Counter(
        None if m.source in (None, "", "website") else m.source for m, _ in members
    )
    by_source = tuple(
        SourceShare(
            source=src,
            label=source_label(src),
            members=n,
            share=n / total if total else 0.0,
        )
        for src, n in sorted(sources.items(), key=lambda kv: (-kv[1], kv[0] or ""))
    )
    regulars = tuple(
        Regular(member_id=mid, first_name=name_of[mid], stamps=n)
        for mid, n in sorted(per_member.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        if n > 0
    )
    alerts = tuple(
        Alert(at=a.at, kind=a.kind, detail=a.detail)
        for a in session.scalars(
            select(LoyaltyAudit)
            .where(LoyaltyAudit.is_alert.is_(True), LoyaltyAudit.at >= now - timedelta(days=30))
            .order_by(LoyaltyAudit.at.desc())
            .limit(20)
        )
    )
    return Insights(
        days=days,
        members=Kpi(total, before_start),
        joined_in_window=total - before_start,
        active_members=Kpi(len(active), len(p_active)),
        visits_per_active_member_per_month=(
            round(len(visits) / len(active) / (days / 30), 1) if active else None
        ),
        stamps=Kpi(stamps, p_stamps),
        stamps_per_week=round(stamps / (days / 7), 1),
        free_drinks=Kpi(drinks, p_drinks),
        came_back=Kpi(
            came_back(start, active, before_start), came_back(prev_start, p_active, before_prev)
        ),
        opted_in_share=opted / total if total else None,
        opted_in_count=opted,
        weekly=tuple(Week(w, new_w[w], stamps_w[w], drinks_w[w]) for w in weeks),
        by_source=by_source,
        hours=tuple((h, hours[h]) for h in _HOURS),
        regulars=regulars,
        alerts=alerts,
    )
