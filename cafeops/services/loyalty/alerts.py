"""Fraud-pattern alerts: stored first, then sent (CONTRACT §7).

SPEC: "Telegram alert on unusual patterns (e.g. one staff member >20 stamps an hour)".
An alert is a `loyalty_audit` row with `is_alert = True`, written in the same transaction
as the stamp that tripped it, so `/api/members/alerts` lists it even when Telegram is not
configured. Sending is separate and after commit (`pending` / `mark_notified`): the
stamp route kicks it, and the 5-minute loyalty job retries anything still unsent.

Once per staff member per hour: the 21st stamp and the 40th in the same hour are the same
story, and a notifier that repeats itself is a notifier that gets muted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    LoyaltyAudit,
    LoyaltyCard,
    LoyaltyProgram,
    LoyaltyStampEvent,
    ProgramKind,
    StampReason,
)
from cafeops.domain.loyalty import HOURLY_ALERT_THRESHOLD, over_hourly_threshold
from cafeops.services.loyalty.common import audit, now_utc
from cafeops.services.loyalty.staff_auth import StaffActor

__all__ = [
    "AlertView",
    "check_stamp_rate",
    "list_alerts",
    "mark_notified",
    "pending",
]

KIND_STAMP_RATE = "stamp_rate"


@dataclass(frozen=True, slots=True)
class AlertView:
    id: int
    at: datetime
    kind: str
    detail: str


def check_stamp_rate(session: Session, actor: StaffActor, now: datetime) -> int | None:
    """After a stamp: alert if this person gave >20 stamps in the last hour. Returns its id."""
    session.flush()
    hour_ago = now - timedelta(hours=1)
    # Stamp cards only: a points card's "stamps" are points, and one £30 order would
    # read as 300 stamps.
    given = session.scalar(
        select(func.coalesce(func.sum(LoyaltyStampEvent.delta), 0))
        .join(LoyaltyCard, LoyaltyCard.id == LoyaltyStampEvent.card_id)
        .join(LoyaltyProgram, LoyaltyProgram.id == LoyaltyCard.program_id)
        .where(
            LoyaltyProgram.kind == ProgramKind.STAMPS,
            LoyaltyStampEvent.staff_user_id == actor.user_id,
            LoyaltyStampEvent.reason == StampReason.PURCHASE,
            LoyaltyStampEvent.created_at > hour_ago,
        )
    )
    total = int(given or 0)
    if not over_hourly_threshold(total):
        return None
    already = session.scalar(
        select(LoyaltyAudit.id).where(
            LoyaltyAudit.kind == KIND_STAMP_RATE,
            LoyaltyAudit.staff_user_id == actor.user_id,
            LoyaltyAudit.at > hour_ago,
        )
    )
    if already is not None:
        return None
    row = audit(
        session,
        KIND_STAMP_RATE,
        f"{actor.name} gave {total} stamps in the last hour on {actor.device_name} "
        f"(alert above {HOURLY_ALERT_THRESHOLD})",
        is_alert=True,
        staff_user_id=actor.user_id,
        device_id=actor.device_id,
        at=now,
    )
    session.flush()
    return row.id


def list_alerts(session: Session, *, limit: int = 100) -> list[AlertView]:
    rows = session.scalars(
        select(LoyaltyAudit)
        .where(LoyaltyAudit.is_alert.is_(True))
        .order_by(LoyaltyAudit.at.desc())
        .limit(limit)
    )
    return [AlertView(id=r.id, at=r.at, kind=r.kind, detail=r.detail) for r in rows]


def pending(session: Session) -> list[AlertView]:
    rows = session.scalars(
        select(LoyaltyAudit)
        .where(LoyaltyAudit.is_alert.is_(True), LoyaltyAudit.notified_at.is_(None))
        .order_by(LoyaltyAudit.at)
    )
    return [AlertView(id=r.id, at=r.at, kind=r.kind, detail=r.detail) for r in rows]


def mark_notified(session: Session, ids: list[int]) -> None:
    now = now_utc()
    for row in session.scalars(select(LoyaltyAudit).where(LoyaltyAudit.id.in_(ids))):
        row.notified_at = now
