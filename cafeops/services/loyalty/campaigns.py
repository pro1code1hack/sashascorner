"""Push campaigns ("new autumn menu") within PECR's limits (SPEC phase 2, CONTRACT §6).

The rules, and why each is a hard stop rather than a warning:

- **Promotions go to opted-in members only**, whatever the segment. The card itself is
  contract; a message about the autumn menu is marketing, and marketing needs the
  separate, unticked consent.
- **Notices** (`is_promo = false`, e.g. "closed Monday for the bank holiday") go to
  everyone with a live card in the segment -- the owner's design, 2026-09-28
  (BACKOFFICE-V2 §3): a holiday closure is a service message about the card's café, not
  marketing. They land on the wallet pass only; an email is sent only to members who
  opted in, so nobody who said no gets mail from us. Keep notices to that kind of
  message -- a notice with an offer in it is a promotion.
- **At most 2 promos per member per calendar month** (SPEC: "<=2 promos/month"), counted
  per member at send time from `loyalty_campaign_delivery`. Members over the limit are
  skipped and counted, not queued for later: a promo that arrives next month is a
  different promo.
- **Unsubscribe on every marketing message.** Wallet messages carry no link (the pass back
  has the privacy page and the web card has the switch); emails, sent when SMTP is
  configured and the member gave an email, end with a one-click unsubscribe link and the
  RFC 8058 header.

"Returned" is a purchase stamp within 7 days of the send; `mark_returns` fills it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.clock import local_day_bounds, local_today
from cafeops.config import settings
from cafeops.db.models import (
    CampaignSegment,
    LoyaltyCampaign,
    LoyaltyCampaignDelivery,
    LoyaltyCard,
    LoyaltyMember,
    LoyaltyStampEvent,
    StampReason,
)
from cafeops.domain.loyalty import PROMO_LIMIT_PER_MONTH
from cafeops.services.loyalty.common import default_program, enqueue_wallet_update, now_utc
from cafeops.services.loyalty.consent import unsubscribe_url
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.messaging import Outgoing

__all__ = [
    "RETURN_WINDOW",
    "SendResult",
    "cancel_campaign",
    "create_campaign",
    "due_campaigns",
    "list_campaigns",
    "mark_returns",
    "promos_this_month",
    "segment_members",
    "send_campaign",
]

RETURN_WINDOW = timedelta(days=7)
_LAPSED = timedelta(days=30)
_NEW = timedelta(days=30)


def _title_from(message: str) -> str:
    """A title nobody typed: the message's first words, 40 characters at most."""
    words = message.split()
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > 40:
            break
        out = f"{out} {w}".strip()
    return (out or message[:40]).rstrip(".,;:!?") or "Message"


def create_campaign(
    session: Session,
    *,
    title: str | None,
    message: str,
    segment: CampaignSegment,
    scheduled_at: datetime | None,
    is_promo: bool,
    created_by: str,
) -> LoyaltyCampaign:
    message = " ".join(message.split())
    if not message:
        raise LoyaltyError(422, "message_required", "Write the message members will see.")
    title = " ".join((title or "").split()) or _title_from(message)
    if len(message) > 200:
        raise LoyaltyError(422, "message_too_long", "Keep the message to 200 characters.")
    if scheduled_at is not None and scheduled_at.tzinfo is None:
        raise LoyaltyError(422, "bad_time", "Give the scheduled time with a timezone.")
    row = LoyaltyCampaign(
        title=title[:120],
        message=message,
        segment=segment,
        is_promo=is_promo,
        scheduled_at=scheduled_at.astimezone(UTC) if scheduled_at else None,
        created_by=(created_by.strip() or "back office")[:120],
        created_at=now_utc(),
    )
    session.add(row)
    session.flush()
    return row


def list_campaigns(session: Session) -> list[LoyaltyCampaign]:
    return list(
        session.scalars(select(LoyaltyCampaign).order_by(LoyaltyCampaign.created_at.desc()))
    )


def segment_members(
    session: Session, segment: CampaignSegment, *, now: datetime, promo: bool = True
) -> list[tuple[LoyaltyMember, LoyaltyCard]]:
    """Live members with a live card, narrowed by the segment; opted-in only for a promo."""
    program = default_program(session)
    stmt = (
        select(LoyaltyMember, LoyaltyCard)
        .join(LoyaltyCard, LoyaltyCard.member_id == LoyaltyMember.id)
        .where(
            LoyaltyCard.program_id == program.id,
            LoyaltyCard.voided_at.is_(None),
            LoyaltyMember.deleted_at.is_(None),
        )
    )
    if promo:
        stmt = stmt.where(LoyaltyMember.marketing_opt_in.is_(True))
    if segment is CampaignSegment.LAPSED_30:
        stmt = stmt.where(LoyaltyMember.last_activity_at < now - _LAPSED)
    elif segment is CampaignSegment.REWARD_READY:
        stmt = stmt.where(LoyaltyCard.reward_available.is_(True))
    elif segment is CampaignSegment.NEW_30:
        stmt = stmt.where(LoyaltyMember.created_at >= now - _NEW)
    elif segment is CampaignSegment.BIRTHDAY:
        stmt = stmt.where(LoyaltyMember.birthday_month == now.astimezone(settings.tz).month)
    return [(m, c) for m, c in session.execute(stmt.order_by(LoyaltyMember.id)).all()]


def _month_start(now: datetime) -> datetime:
    first = local_today(settings.tz, now=now).replace(day=1)
    return local_day_bounds(first, tz=settings.tz)[0]


@dataclass(frozen=True, slots=True)
class SendResult:
    campaign_id: int
    recipients: int
    skipped_over_limit: int
    #: Emails to send after commit (SMTP configured and the member gave an email).
    outgoing: tuple[Outgoing, ...]


def send_campaign(session: Session, campaign_id: int, *, now: datetime | None = None) -> SendResult:
    now = now or now_utc()
    campaign = session.get(LoyaltyCampaign, campaign_id)
    if campaign is None:
        raise LoyaltyError(404, "unknown_campaign", "There is no campaign with that id.")
    if campaign.sent_at is not None:
        raise LoyaltyError(409, "already_sent", "That campaign has already been sent.")
    if campaign.cancelled_at is not None:
        raise LoyaltyError(409, "cancelled", "That campaign was cancelled.")
    month_start = _month_start(now)
    recipients = skipped = 0
    outgoing: list[Outgoing] = []
    for member, card in segment_members(
        session, campaign.segment, now=now, promo=campaign.is_promo
    ):
        if campaign.is_promo:
            promos = session.scalar(
                select(func.count(LoyaltyCampaignDelivery.id)).where(
                    LoyaltyCampaignDelivery.member_id == member.id,
                    LoyaltyCampaignDelivery.is_promo.is_(True),
                    LoyaltyCampaignDelivery.sent_at >= month_start,
                )
            )
            if (promos or 0) >= PROMO_LIMIT_PER_MONTH:
                skipped += 1
                continue
        session.add(
            LoyaltyCampaignDelivery(
                campaign_id=campaign.id,
                member_id=member.id,
                is_promo=campaign.is_promo,
                sent_at=now,
            )
        )
        enqueue_wallet_update(session, card.id, campaign.message)
        # Email only with consent, notices included (see the module docstring).
        if settings.smtp_configured and member.email and member.marketing_opt_in:
            link = unsubscribe_url(member.id)
            outgoing.append(
                Outgoing(
                    channel="email",
                    to=member.email,
                    subject=campaign.title,
                    body=(
                        f"Hi {member.first_name},\n\n{campaign.message}\n\n"
                        "Sasha's Corner, 23 Commercial Street, Dundee\n\n"
                        f"You get this because you asked for news from us. "
                        f"Unsubscribe: {link}\n"
                    ),
                    unsubscribe_url=link,
                )
            )
        recipients += 1
    campaign.sent_at = now
    campaign.recipients = recipients
    session.flush()
    return SendResult(
        campaign_id=campaign.id,
        recipients=recipients,
        skipped_over_limit=skipped,
        outgoing=tuple(outgoing),
    )


def due_campaigns(session: Session, *, now: datetime) -> list[int]:
    return list(
        session.scalars(
            select(LoyaltyCampaign.id).where(
                LoyaltyCampaign.sent_at.is_(None),
                LoyaltyCampaign.cancelled_at.is_(None),
                LoyaltyCampaign.scheduled_at.is_not(None),
                LoyaltyCampaign.scheduled_at <= now,
            )
        )
    )


def cancel_campaign(session: Session, campaign_id: int) -> LoyaltyCampaign:
    """Stop a campaign that has not gone out. Kept (with `cancelled_at`) as a record."""
    campaign = session.get(LoyaltyCampaign, campaign_id)
    if campaign is None:
        raise LoyaltyError(404, "unknown_campaign", "There is no campaign with that id.")
    if campaign.sent_at is not None:
        raise LoyaltyError(409, "already_sent", "That campaign has already gone out.")
    if campaign.cancelled_at is None:
        campaign.cancelled_at = now_utc()
    session.flush()
    return campaign


def promos_this_month(session: Session, *, now: datetime | None = None) -> int:
    """Promotions sent this calendar month, or scheduled to go out in it (not cancelled)."""
    now = now or now_utc()
    first = local_today(settings.tz, now=now).replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    start, end = local_day_bounds(first, last, tz=settings.tz)
    rows = session.scalars(
        select(LoyaltyCampaign).where(
            LoyaltyCampaign.is_promo.is_(True), LoyaltyCampaign.cancelled_at.is_(None)
        )
    )
    n = 0
    for c in rows:
        at = c.sent_at or c.scheduled_at
        if at is not None and start <= at < end:
            n += 1
    return n


def mark_returns(session: Session, *, now: datetime | None = None) -> int:
    """Set `returned_at` for deliveries followed by a purchase stamp within 7 days."""
    now = now or now_utc()
    marked = 0
    open_rows = session.scalars(
        select(LoyaltyCampaignDelivery).where(
            LoyaltyCampaignDelivery.returned_at.is_(None),
            LoyaltyCampaignDelivery.sent_at > now - RETURN_WINDOW - timedelta(days=1),
        )
    )
    for delivery in open_rows:
        stamp_at = session.scalar(
            select(func.min(LoyaltyStampEvent.created_at))
            .join(LoyaltyCard, LoyaltyCard.id == LoyaltyStampEvent.card_id)
            .where(
                LoyaltyCard.member_id == delivery.member_id,
                LoyaltyStampEvent.reason == StampReason.PURCHASE,
                LoyaltyStampEvent.created_at > delivery.sent_at,
                LoyaltyStampEvent.created_at <= delivery.sent_at + RETURN_WINDOW,
            )
        )
        if stamp_at is not None:
            delivery.returned_at = stamp_at
            marked += 1
    return marked
