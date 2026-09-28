"""A member's own data, as one JSON document (UK GDPR Art. 15, "right of access").

Rewards > Members > a member > "Download their data". Everything the café holds about
that person: who they are, their consent, every card, every stamp and reward, the
messages they were sent, the till receipts linked to them and the audit trail that
names them.

Left out on purpose: the card's `auth_token` and `qr_secret`. They are credentials, not
personal data about the member, and a file that gets emailed around must not be enough
to open or stamp the card. Staff are named (the member may ask who stamped them), staff
PINs and device tokens are not. Nothing here writes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    LoyaltyAudit,
    LoyaltyCampaign,
    LoyaltyCampaignDelivery,
    LoyaltyCard,
    LoyaltyMember,
    LoyaltyPosAward,
    LoyaltyPosReceipt,
    LoyaltyProgram,
    LoyaltyReward,
    LoyaltyRewardOption,
    LoyaltyStampEvent,
    MenuItem,
    StaffUser,
)
from cafeops.services.loyalty.common import now_utc
from cafeops.services.loyalty.errors import LoyaltyError

__all__ = ["member_export"]


def _iso(at: datetime | None) -> str | None:
    return None if at is None else at.isoformat()


def member_export(session: Session, member_id: int) -> dict[str, Any]:
    member = session.get(LoyaltyMember, member_id)
    if member is None:
        raise LoyaltyError(404, "unknown_member", "There is no member with that id.")
    staff = {u.id: u.name for u in session.scalars(select(StaffUser))}
    cards = list(
        session.scalars(
            select(LoyaltyCard)
            .where(LoyaltyCard.member_id == member.id)
            .order_by(LoyaltyCard.created_at)
        )
    )
    card_ids = [c.id for c in cards]
    programs = {p.id: p for p in session.scalars(select(LoyaltyProgram))}
    slug_of = {c.id: programs[c.program_id].slug for c in cards}

    events = (
        list(
            session.scalars(
                select(LoyaltyStampEvent)
                .where(LoyaltyStampEvent.card_id.in_(card_ids))
                .order_by(LoyaltyStampEvent.created_at, LoyaltyStampEvent.id)
            )
        )
        if card_ids
        else []
    )
    rewards = (
        list(
            session.scalars(
                select(LoyaltyReward)
                .where(LoyaltyReward.card_id.in_(card_ids))
                .order_by(LoyaltyReward.issued_at, LoyaltyReward.id)
            )
        )
        if card_ids
        else []
    )
    item_ids = {r.redeemed_menu_item_id for r in rewards if r.redeemed_menu_item_id}
    items = (
        {i.id: i.name for i in session.scalars(select(MenuItem).where(MenuItem.id.in_(item_ids)))}
        if item_ids
        else {}
    )
    option_ids = {r.reward_option_id for r in rewards if r.reward_option_id}
    options = (
        {
            o.id: o.name
            for o in session.scalars(
                select(LoyaltyRewardOption).where(LoyaltyRewardOption.id.in_(option_ids))
            )
        }
        if option_ids
        else {}
    )
    deliveries = session.execute(
        select(LoyaltyCampaignDelivery, LoyaltyCampaign)
        .join(LoyaltyCampaign, LoyaltyCampaign.id == LoyaltyCampaignDelivery.campaign_id)
        .where(LoyaltyCampaignDelivery.member_id == member.id)
        .order_by(LoyaltyCampaignDelivery.sent_at)
    ).all()
    audit = list(
        session.scalars(
            select(LoyaltyAudit)
            .where(
                (LoyaltyAudit.member_id == member.id)
                | (LoyaltyAudit.card_id.in_(card_ids) if card_ids else False)
            )
            .order_by(LoyaltyAudit.at, LoyaltyAudit.id)
        )
    )
    receipts: list[dict[str, Any]] = []
    if member.lightspeed_customer_id:
        for r in session.scalars(
            select(LoyaltyPosReceipt)
            .where(LoyaltyPosReceipt.lightspeed_customer_id == member.lightspeed_customer_id)
            .order_by(LoyaltyPosReceipt.closed_at)
        ):
            awards = (
                list(
                    session.scalars(
                        select(LoyaltyPosAward).where(
                            LoyaltyPosAward.lightspeed_receipt_id == r.lightspeed_receipt_id,
                            LoyaltyPosAward.card_id.in_(card_ids),
                        )
                    )
                )
                if card_ids
                else []
            )
            receipts.append(
                {
                    "receipt_id": r.lightspeed_receipt_id,
                    "closed_at": _iso(r.closed_at),
                    "total_pence": r.total_pence,
                    "earned": [
                        {
                            "program": slug_of.get(a.card_id),
                            "units": a.units,
                            "status": a.status.value,
                            "detail": a.detail,
                        }
                        for a in awards
                    ],
                }
            )

    birthday = (
        f"{member.birthday_day:02d}-{member.birthday_month:02d}"
        if member.birthday_day and member.birthday_month
        else None
    )
    return {
        "about": (
            "Everything Sasha's Corner Rewards holds about this member, exported from the "
            "back office for a subject access request. Card access tokens are left out: "
            "they are credentials, not personal data."
        ),
        "exported_at": _iso(now_utc()),
        "member": {
            "member_id": member.id,
            "first_name": member.first_name,
            "email": member.email,
            "phone": member.phone,
            "birthday_dd_mm": birthday,
            "birthday_set_at": _iso(member.birthday_set_at),
            "joined_at": _iso(member.created_at),
            "terms_accepted_at": _iso(member.terms_accepted_at),
            "last_activity_at": _iso(member.last_activity_at),
            "joined_from": member.source,
            "referred_by_member_id": member.referred_by_member_id,
            "erased_at": _iso(member.deleted_at),
        },
        "consent": {
            "marketing_opt_in": member.marketing_opt_in,
            "opt_in_at": _iso(member.opt_in_at),
            "opt_in_source": member.opt_in_source,
        },
        "lightspeed_link": (
            {
                "customer_id": member.lightspeed_customer_id,
                "linked_at": _iso(member.lightspeed_linked_at),
                "how": member.lightspeed_link_source,
            }
            if member.lightspeed_customer_id
            else None
        ),
        "cards": [
            {
                "card_id": c.id,
                "program": programs[c.program_id].slug,
                "program_name": programs[c.program_id].name,
                "stamps_current": c.stamps_current,
                "cycles_completed": c.cycles_completed,
                "created_at": _iso(c.created_at),
                "web_card_first_opened_at": _iso(c.web_seen_at),
                "voided_at": _iso(c.voided_at),
            }
            for c in cards
        ],
        "stamp_history": [
            {
                "id": e.id,
                "program": slug_of.get(e.card_id),
                "at": _iso(e.created_at),
                "change": e.delta,
                "reason": e.reason.value,
                "note": e.note,
                "staff": staff.get(e.staff_user_id) if e.staff_user_id else None,
                "undoes_event_id": e.undoes_event_id,
            }
            for e in events
        ],
        "rewards": [
            {
                "id": r.id,
                "program": slug_of.get(r.card_id),
                "kind": r.kind.value,
                "issued_at": _iso(r.issued_at),
                "expires_at": _iso(r.expires_at),
                "redeemed_at": _iso(r.redeemed_at),
                "redeemed_item": items.get(r.redeemed_menu_item_id)
                if r.redeemed_menu_item_id
                else None,
                "taken_as": options.get(r.reward_option_id) if r.reward_option_id else None,
                "staff": staff.get(r.staff_user_id) if r.staff_user_id else None,
                "voided_at": _iso(r.voided_at),
            }
            for r in rewards
        ],
        "messages_sent": [
            {
                "campaign": c.title,
                "message": c.message,
                "sent_at": _iso(d.sent_at),
                "came_back_at": _iso(d.returned_at),
            }
            for d, c in deliveries
        ],
        "till_receipts": receipts,
        "audit": [{"at": _iso(a.at), "kind": a.kind, "detail": a.detail} for a in audit],
    }
