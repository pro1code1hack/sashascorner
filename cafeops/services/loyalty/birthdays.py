"""The birthday drink (SPEC user flow 4), issued by the 06:00 job.

"From 7 days before to 7 days after the birthday the pass carries a free-drink voucher.
Separate reward, doesn't touch stamps." Once a year; a birthday entered or changed fewer
than 30 days before the date does not qualify that year.

The job issues on ANY day inside the window, not only on day -7: a server that was down on
day -7 must still issue on day -6. Once-a-year is the unique key
`(card_id, kind, birthday_year)`, so a second run on the same day is a no-op.

The same job expires what lapsed: a birthday reward past its date stops being "available",
so the card's cached flag and the pass must be refreshed -- nothing else would notice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.clock import local_day_bounds
from cafeops.config import settings
from cafeops.db.models import LoyaltyCard, LoyaltyMember, LoyaltyReward, RewardKind
from cafeops.domain.loyalty import birthday_decision
from cafeops.services.loyalty.common import (
    enqueue_wallet_update,
    now_utc,
    refresh_reward_available,
    touch,
)

__all__ = ["BIRTHDAY_MESSAGE", "BirthdayReport", "run_birthdays"]

BIRTHDAY_MESSAGE = "Happy birthday — a drink on us this week"


@dataclass(frozen=True, slots=True)
class BirthdayReport:
    issued: int
    expired_refreshed: int
    not_qualified: int

    def summary(self) -> str:
        return (
            f"loyalty_birthdays: issued {self.issued}, refreshed {self.expired_refreshed} "
            f"card(s) whose reward lapsed, {self.not_qualified} in-window but under the "
            "30-day rule"
        )


def _end_of_local_day(day: date) -> datetime:
    """The instant a reward valid "until day X" stops: midnight after X, local time."""
    return local_day_bounds(day, tz=settings.tz)[1]


def run_birthdays(session: Session, *, now: datetime | None = None) -> BirthdayReport:
    now = now or now_utc()
    today = now.astimezone(settings.tz).date()
    issued = not_qualified = 0

    rows = session.execute(
        select(LoyaltyMember, LoyaltyCard)
        .join(LoyaltyCard, LoyaltyCard.member_id == LoyaltyMember.id)
        .where(
            LoyaltyMember.deleted_at.is_(None),
            LoyaltyMember.birthday_day.is_not(None),
            LoyaltyCard.voided_at.is_(None),
        )
    ).all()
    for member, card in rows:
        if not card.program.birthday_reward or not card.program.active:
            continue
        assert member.birthday_day is not None and member.birthday_month is not None
        set_on = (
            member.birthday_set_at.astimezone(settings.tz).date()
            if member.birthday_set_at
            else None
        )
        decision = birthday_decision(
            member.birthday_day, member.birthday_month, today=today, birthday_set_on=set_on
        )
        if not decision.issue:
            if decision.window_opens <= today <= decision.expires_on:
                not_qualified += 1
            continue
        exists = session.scalar(
            select(LoyaltyReward.id).where(
                LoyaltyReward.card_id == card.id,
                LoyaltyReward.kind == RewardKind.BIRTHDAY,
                LoyaltyReward.birthday_year == decision.birthday_year,
            )
        )
        if exists is not None:
            continue
        session.add(
            LoyaltyReward(
                card_id=card.id,
                kind=RewardKind.BIRTHDAY,
                issued_at=now,
                expires_at=_end_of_local_day(decision.expires_on),
                birthday_year=decision.birthday_year,
            )
        )
        refresh_reward_available(session, card, now)
        touch(card, now)
        enqueue_wallet_update(session, card.id, BIRTHDAY_MESSAGE)
        issued += 1

    # Lapsed rewards: the flag says "available" but nothing is any more.
    refreshed = 0
    for card in session.scalars(
        select(LoyaltyCard).where(
            LoyaltyCard.reward_available.is_(True), LoyaltyCard.voided_at.is_(None)
        )
    ):
        if refresh_reward_available(session, card, now):
            touch(card, now)
            enqueue_wallet_update(session, card.id, None)
            refreshed += 1
    return BirthdayReport(issued=issued, expired_refreshed=refreshed, not_qualified=not_qualified)
