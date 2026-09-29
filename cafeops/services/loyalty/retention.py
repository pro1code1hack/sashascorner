"""Erasure: "Delete my card", the admin delete, and the 24-month retention sweep.

SPEC privacy: data is first name, email or phone, optional birthday, stamp history;
"deleted 24 months after last activity"; "Delete my card ... deletes the member and voids
passes".

What erasure does, in one function so all three paths agree:

- **Personal data goes.** First name, email, phone, birthday, consent fields and `source`
  attribution are cleared; pending recovery codes are deleted (they are keyed to a person).
- **The card is voided and its unused rewards with it.** Its id and token stay, so the
  pass on the phone can fetch one last version saying the card is gone (a silent refresh
  is queued) instead of showing stamps that no longer exist.
- **The ledger stays.** Stamp events, redeemed rewards and campaign deliveries carry no
  personal data once the member row is blank, and "how many free drinks did we give in
  March" must not change because somebody left.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cafeops.clock import local_day_bounds, local_today
from cafeops.config import settings
from cafeops.db.models import LoyaltyCard, LoyaltyMember, LoyaltyOtp, LoyaltyReward
from cafeops.domain.loyalty import RETENTION_MONTHS, months_before
from cafeops.services.loyalty.common import audit, enqueue_wallet_update, now_utc, touch
from cafeops.services.loyalty.errors import LoyaltyError

__all__ = ["RetentionReport", "erase_member", "retention_cutoff", "run_retention"]


def erase_member(session: Session, member_id: int, *, why: str) -> None:
    member = session.get(LoyaltyMember, member_id)
    if member is None:
        raise LoyaltyError(404, "unknown_member", "There is no member with that id.")
    if member.deleted_at is not None:
        return
    now = now_utc()
    member.first_name = ""
    member.email = None
    member.phone = None
    member.birthday_day = None
    member.birthday_month = None
    member.birthday_set_at = None
    member.marketing_opt_in = False
    member.opt_in_at = None
    member.opt_in_source = None
    member.source = None
    # The till customer id links this person to their receipts: erased with the rest.
    # (Their receipts keep only a customer id and keyed hashes -- no contact.)
    member.lightspeed_customer_id = None
    member.lightspeed_linked_at = None
    member.lightspeed_link_source = None
    member.deleted_at = now
    session.execute(delete(LoyaltyOtp).where(LoyaltyOtp.member_id == member.id))
    for card in session.scalars(select(LoyaltyCard).where(LoyaltyCard.member_id == member.id)):
        if card.voided_at is None:
            card.voided_at = now
        for reward in session.scalars(
            select(LoyaltyReward).where(
                LoyaltyReward.card_id == card.id,
                LoyaltyReward.redeemed_at.is_(None),
                LoyaltyReward.voided_at.is_(None),
            )
        ):
            reward.voided_at = now
        card.reward_available = False
        touch(card, now)
        enqueue_wallet_update(session, card.id, None)
    # The audit row names the member id only: after this, that id is all there is.
    audit(session, "erased", f"member #{member.id} erased ({why})", member_id=member.id, at=now)
    session.flush()


def retention_cutoff(now: datetime) -> datetime:
    """Start of the local day 24 calendar months ago."""
    cutoff = months_before(local_today(settings.tz, now=now), RETENTION_MONTHS)
    return local_day_bounds(cutoff, tz=settings.tz)[0]


@dataclass(frozen=True, slots=True)
class RetentionReport:
    erased: int
    cutoff: datetime

    def summary(self) -> str:
        return (
            f"loyalty_retention: erased {self.erased} member(s) inactive since before "
            f"{self.cutoff.date().isoformat()}"
        )


def run_retention(session: Session, *, now: datetime | None = None) -> RetentionReport:
    now = now or now_utc()
    cutoff = retention_cutoff(now)
    ids = list(
        session.scalars(
            select(LoyaltyMember.id).where(
                LoyaltyMember.deleted_at.is_(None), LoyaltyMember.last_activity_at < cutoff
            )
        )
    )
    for member_id in ids:
        erase_member(
            session, member_id, why=f"retention: no activity for {RETENTION_MONTHS} months"
        )
    return RetentionReport(erased=len(ids), cutoff=cutoff)
