"""Marketing consent: given, withdrawn, and the one-click unsubscribe.

PECR / UK GDPR (SPEC privacy): marketing is by consent, separate from the card, recorded
with when and where. Opting out clears `opt_in_at`/`opt_in_source` on the member (so no
reader can mistake an old consent for a live one) and leaves a `consent` audit row with
the time and route -- that row is the evidence if anyone later asks.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import LoyaltyCard, LoyaltyMember
from cafeops.domain.loyalty import unsubscribe_signature, verify_unsubscribe
from cafeops.services.loyalty.common import audit, enqueue_wallet_update, now_utc, touch

__all__ = ["set_marketing_opt_in", "unsubscribe", "unsubscribe_url"]


def set_marketing_opt_in(session: Session, card: LoyaltyCard, *, opt_in: bool, source: str) -> None:
    member = card.member
    if member.deleted_at is not None or member.marketing_opt_in == opt_in:
        return
    now = now_utc()
    member.marketing_opt_in = opt_in
    member.opt_in_at = now if opt_in else None
    member.opt_in_source = source[:60] if opt_in else None
    audit(
        session,
        "consent",
        f"marketing {'opt-in' if opt_in else 'opt-out'} via {source}",
        card_id=card.id,
        member_id=member.id,
        at=now,
    )
    touch(card, now)
    enqueue_wallet_update(session, card.id, None)


def unsubscribe_url(member_id: int) -> str:
    sig = unsubscribe_signature(member_id, settings.loyalty_key)
    base = settings.loyalty_public_url.rstrip("/")
    return f"{base}/api/loyalty/unsubscribe?m={member_id}&s={sig}"


def unsubscribe(session: Session, member_id: int, signature: str) -> bool:
    """Opt out from a signed link. True when the link is genuine (even if already out)."""
    if not verify_unsubscribe(member_id, signature, settings.loyalty_key):
        return False
    member = session.get(LoyaltyMember, member_id)
    if member is None or member.deleted_at is not None:
        return True
    if member.marketing_opt_in:
        now = now_utc()
        member.marketing_opt_in = False
        member.opt_in_at = None
        member.opt_in_source = None
        audit(
            session,
            "consent",
            "marketing opt-out via unsubscribe link",
            member_id=member.id,
            at=now,
        )
    return True
