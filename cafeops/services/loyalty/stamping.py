"""Stamps: at the till, from a paper card, by a manager, and undone (SPEC flows 2 and 5).

Every change to a card's count is a `loyalty_stamp_event` row, and `_apply` is the only
function that writes one. It does the whole bookkeeping in one place: the event, the
carry-over arithmetic (`domain.loyalty.apply_stamps`), a STAMP_CARD reward per full card
(linked to the event that filled it, so an undo can find it), `reward_available`,
`updated_at`, and the wallet outbox row.

The rules around it:

- **+1 by default, up to `max_stamps_per_scan` (3) for a group order.**
- **Cooldown:** more than 3 purchase stamps on one card within 10 minutes needs a manager
  PIN. Undone stamps do not count towards it.
- **Undo within 2 minutes**, from the same device or by any manager. It is a new UNDO row
  with the opposite delta. If the stamp completed a card whose reward has not been given,
  the reward is voided and the 8 stamps come back; if it HAS been given, the undo is
  refused and says to undo the redemption first -- otherwise the card would go negative
  and the free drink would have come from nowhere.
- **Later corrections** are `adjust`: manager PIN and a written reason, recorded as
  MANUAL_FIX. Nothing is ever edited in place.
- **Paper cards** convert once (1-7 stickers), as PAPER_MIGRATION.
- **Referral:** the first purchase stamp on a referred member's card credits the referrer
  with `referral_stamps` stamps (reason REFERRAL), once per referred member.
- **Fraud alert:** a staff member giving >20 stamps in an hour raises an alert.

Phase 3: a POINTS card is stamped by **spend** (`add_spend`): staff type what the customer
paid and the card gets `spend * points_per_pound // 100` points as one PURCHASE event.
`stamp` refuses a points card and `add_spend` a stamp card, so neither kind can be fed
the other's unit. A spend over `MAX_POINTS_SPEND_PENCE` needs a manager's PIN.
`apply_units` is the entry the Lightspeed auto-stamper (`pos`) uses: the same `_apply`,
no staff, no device, a note naming the receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.clock import local_day_bounds, local_today
from cafeops.config import settings
from cafeops.db.models import (
    LoyaltyCard,
    LoyaltyMember,
    LoyaltyProgram,
    LoyaltyReward,
    LoyaltyStampEvent,
    ProgramKind,
    RewardKind,
    StaffUser,
    StampReason,
)
from cafeops.domain.loyalty import (
    COOLDOWN_MAX_STAMPS,
    COOLDOWN_MINUTES,
    MAX_POINTS_SPEND_PENCE,
    UNDO_SECONDS,
    add_stickers,
    apply_stamps,
    fit_stickers,
    months_before,
    points_for_spend,
    sticker_set,
)
from cafeops.domain.units import pounds
from cafeops.services.loyalty.alerts import check_stamp_rate
from cafeops.services.loyalty.common import (
    audit,
    enqueue_wallet_update,
    load_card,
    now_utc,
    refresh_reward_available,
    touch,
)
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.redeem import undo_redemption
from cafeops.services.loyalty.scan import (
    ScanView,
    is_undone,
    net_purchase_stamps_since,
    scan_view,
)
from cafeops.services.loyalty.staff_auth import StaffActor, verify_manager_pin

__all__ = [
    "PAPER_MAX",
    "StampResult",
    "add_spend",
    "adjust",
    "apply_units",
    "credit_referrer",
    "expire_stamps",
    "migrate",
    "reverse_event",
    "reverse_with_referral",
    "stamp",
    "undo",
    "welcome_stamp",
]

#: SPEC flow 5: "enter stickers (1-7)". Eight would be a full card, which is a free drink
#: on paper -- give it on paper.
PAPER_MAX = 7
#: A manual correction bigger than this is almost certainly a typo (80 for 8).
_ADJUST_MAX = 24


@dataclass(frozen=True, slots=True)
class StampResult:
    card: ScanView
    event_id: int
    undo_until: datetime
    reward_issued: bool
    #: Alerts raised by this stamp, to be sent after commit.
    alert_ids: tuple[int, ...] = ()


def _live_card(session: Session, card_id: str) -> LoyaltyCard:
    card = load_card(session, card_id)
    if card.voided_at is not None:
        raise LoyaltyError(409, "card_voided", "This card has been deleted.")
    return card


def _apply(
    session: Session,
    card: LoyaltyCard,
    delta: int,
    reason: StampReason,
    *,
    now: datetime,
    note: str | None = None,
    staff_user_id: int | None = None,
    device_id: int | None = None,
    source_event_id: int | None = None,
    customer_activity: bool = True,
    sticker: str | None = None,
) -> tuple[LoyaltyStampEvent, list[LoyaltyReward]]:
    program = card.program
    try:
        outcome = apply_stamps(card.stamps_current, delta, program.stamps_required)
    except ValueError as exc:
        raise LoyaltyError(409, "would_go_negative", str(exc)) from None
    placed: str | None = None
    if program.kind is ProgramKind.STAMPS:
        # BACKOFFICE-V2 §2: the card keeps one sticker per filled slot, written here and
        # only here (with `_reverse`), so the list and the count move together.
        chosen = sticker_set(program.stickers)
        slots, placed = add_stickers(
            fit_stickers(card.stickers, card.stamps_current, chosen),
            delta,
            program.stamps_required,
            chosen,
            outcome.stamps_after,
            first=sticker,
        )
        card.stickers = slots
    event = LoyaltyStampEvent(
        card_id=card.id,
        delta=delta,
        reason=reason,
        note=note[:400] if note else None,
        staff_user_id=staff_user_id,
        device_id=device_id,
        source_event_id=source_event_id,
        sticker=placed,
        created_at=now,
    )
    session.add(event)
    session.flush()
    card.stamps_current = outcome.stamps_after
    card.cycles_completed += outcome.rewards_issued
    rewards: list[LoyaltyReward] = []
    for _ in range(outcome.rewards_issued):
        reward = LoyaltyReward(
            card_id=card.id,
            kind=RewardKind.STAMP_CARD,
            issued_at=now,
            expires_at=None,
            stamp_event_id=event.id,
        )
        session.add(reward)
        rewards.append(reward)
    refresh_reward_available(session, card, now)
    touch(card, now)
    if customer_activity:
        card.member.last_activity_at = now
    if rewards:
        message: str | None = (
            "Your free drink is ready"
            if program.reward_ready_label == "Free drink ready"
            else f"{program.name}: {program.reward_ready_label.lower()}"
        )
    elif delta > 0:
        n = card.stamps_current
        unit = "point" if program.kind is ProgramKind.POINTS else "stamp"
        message = f"You've got {n} {unit}{'' if n == 1 else 's'}"
        if program.slug != "stamp":
            message = f"{program.name}: {message[0].lower()}{message[1:]}"
    else:
        message = None
    enqueue_wallet_update(session, card.id, message)
    return event, rewards


def _credit_referrer(
    session: Session, card: LoyaltyCard, first_stamp: LoyaltyStampEvent, now: datetime
) -> None:
    """The referred member's FIRST purchase stamp pays the referrer, once."""
    member = card.member
    program = card.program
    if (
        member.referred_by_member_id is None
        or member.referral_rewarded_at is not None
        or program.referral_stamps <= 0
    ):
        return
    undone = select(LoyaltyStampEvent.undoes_event_id).where(
        LoyaltyStampEvent.card_id == card.id, LoyaltyStampEvent.reason == StampReason.UNDO
    )
    purchases = session.scalar(
        select(func.count(LoyaltyStampEvent.id)).where(
            LoyaltyStampEvent.card_id == card.id,
            LoyaltyStampEvent.reason == StampReason.PURCHASE,
            LoyaltyStampEvent.id.not_in(undone),
        )
    )
    if (purchases or 0) != 1:
        return
    referrer = session.get(LoyaltyMember, member.referred_by_member_id)
    if referrer is None or referrer.deleted_at is not None:
        return
    referrer_card = session.scalar(
        select(LoyaltyCard).where(
            LoyaltyCard.member_id == referrer.id,
            LoyaltyCard.program_id == program.id,
            LoyaltyCard.voided_at.is_(None),
        )
    )
    if referrer_card is None:
        return
    member.referral_rewarded_at = now
    _apply(
        session,
        referrer_card,
        program.referral_stamps,
        StampReason.REFERRAL,
        now=now,
        note=f"referral: member #{member.id} made their first visit",
        source_event_id=first_stamp.id,
        # Being credited is not the referrer's own visit; it must not reset the 24-month
        # retention clock on their behalf.
        customer_activity=False,
    )


def stamp(
    session: Session,
    actor: StaffActor,
    *,
    card_id: str,
    delta: int,
    manager_pin: str | None = None,
) -> StampResult:
    now = now_utc()
    card = _live_card(session, card_id)
    program = card.program
    if program.kind is ProgramKind.POINTS:
        raise LoyaltyError(
            409, "points_card", f"{program.name} is a points card: enter what they spent."
        )
    if not 1 <= delta <= program.max_stamps_per_scan:
        raise LoyaltyError(
            422,
            "bad_delta",
            f"A scan adds 1 to {program.max_stamps_per_scan} stamps.",
        )
    # The programme's own cooldown (Rewards > Programme); defaults to the SPEC's 3 in 10.
    window = program.cooldown_minutes or COOLDOWN_MINUTES
    limit = program.cooldown_max_stamps or COOLDOWN_MAX_STAMPS
    recent = net_purchase_stamps_since(session, card.id, now - timedelta(minutes=window))
    approver: StaffUser | None = None
    if recent + delta > limit:
        if not manager_pin:
            raise LoyaltyError(
                409,
                "manager_pin_required",
                f"This card has had {recent} stamp{'' if recent == 1 else 's'} in the last "
                f"{window} minutes; more than {limit} needs a "
                "manager's PIN.",
            )
        approver = verify_manager_pin(session, manager_pin, limiter_key=actor.limiter_key)

    note = f"cooldown override approved by {approver.name}" if approver else None
    event, rewards = _apply(
        session,
        card,
        delta,
        StampReason.PURCHASE,
        now=now,
        note=note,
        staff_user_id=actor.user_id,
        device_id=actor.device_id,
    )
    if approver is not None:
        audit(
            session,
            "cooldown_override",
            f"{approver.name} approved +{delta} after {recent} in {window} min "
            f"(stamped by {actor.name} on {actor.device_name})",
            staff_user_id=approver.id,
            device_id=actor.device_id,
            card_id=card.id,
            member_id=card.member_id,
            at=now,
        )
    _credit_referrer(session, card, event, now)
    alert_id = check_stamp_rate(session, actor, now)
    return StampResult(
        card=scan_view(session, card, now=now),
        event_id=event.id,
        undo_until=now + timedelta(seconds=UNDO_SECONDS),
        reward_issued=bool(rewards),
        alert_ids=(alert_id,) if alert_id is not None else (),
    )


def add_spend(
    session: Session,
    actor: StaffActor,
    *,
    card_id: str,
    spend_pence: int,
    manager_pin: str | None = None,
) -> StampResult:
    """Points for a spend typed at the till (POINTS programmes only)."""
    now = now_utc()
    card = _live_card(session, card_id)
    program = card.program
    if program.kind is not ProgramKind.POINTS or program.points_per_pound is None:
        raise LoyaltyError(409, "stamp_card", f"{program.name} takes stamps, not a spend.")
    if spend_pence <= 0:
        raise LoyaltyError(422, "bad_spend", "Enter what the customer paid, in pounds and pence.")
    points = points_for_spend(spend_pence, program.points_per_pound)
    if points <= 0:
        raise LoyaltyError(
            422,
            "bad_spend",
            f"{pounds(spend_pence)} earns no points at {program.points_per_pound} a pound.",
        )
    approver: StaffUser | None = None
    if spend_pence > MAX_POINTS_SPEND_PENCE:
        if not manager_pin:
            raise LoyaltyError(
                409,
                "manager_pin_required",
                f"{pounds(spend_pence)} is more than {pounds(MAX_POINTS_SPEND_PENCE)}; "
                "a manager's PIN confirms it is not a typo.",
            )
        approver = verify_manager_pin(session, manager_pin, limiter_key=actor.limiter_key)
    note = f"spend {pounds(spend_pence)}"
    if approver is not None:
        note += f", approved by {approver.name}"
    event, rewards = _apply(
        session,
        card,
        points,
        StampReason.PURCHASE,
        now=now,
        note=note,
        staff_user_id=actor.user_id,
        device_id=actor.device_id,
    )
    _credit_referrer(session, card, event, now)
    return StampResult(
        card=scan_view(session, card, now=now),
        event_id=event.id,
        undo_until=now + timedelta(seconds=UNDO_SECONDS),
        reward_issued=bool(rewards),
    )


def apply_units(
    session: Session, card: LoyaltyCard, units: int, *, note: str, now: datetime
) -> LoyaltyStampEvent:
    """Stamps or points from a till receipt (`services/loyalty/pos.py`): no staff, no device."""
    event, _ = _apply(session, card, units, StampReason.PURCHASE, now=now, note=note)
    _credit_referrer(session, card, event, now)
    return event


def reverse_event(session: Session, event: LoyaltyStampEvent, *, note: str) -> LoyaltyStampEvent:
    """UNDO an event with no time limit, for a till void/refund. Raises `LoyaltyError`
    (`reward_redeemed`, `would_go_negative`) when it cannot, changing nothing."""
    return _reverse(session, event, now=now_utc(), staff_user_id=None, device_id=None, note=note)


def welcome_stamp(session: Session, card: LoyaltyCard, *, now: datetime) -> bool:
    """The programme's "first stamp is on us" for a card just made. True if given.

    WELCOME, not PURCHASE: it is not a visit, so statistics, the cooldown and the
    referral's "first purchase" rule all leave it out.
    """
    program = card.program
    if not program.welcome_stamp or program.kind is not ProgramKind.STAMPS:
        return False
    _apply(
        session,
        card,
        1,
        StampReason.WELCOME,
        now=now,
        note="welcome stamp",
        customer_activity=False,
    )
    return True


def migrate(session: Session, actor: StaffActor, *, card_id: str, paper_stamps: int) -> StampResult:
    now = now_utc()
    card = _live_card(session, card_id)
    if card.program.kind is ProgramKind.POINTS:
        raise LoyaltyError(409, "points_card", "A paper card converts onto the stamp card.")
    if not 1 <= paper_stamps <= PAPER_MAX:
        raise LoyaltyError(
            422, "bad_paper_stamps", f"A paper card converts with 1 to {PAPER_MAX} stickers."
        )
    for earlier in session.scalars(
        select(LoyaltyStampEvent.id).where(
            LoyaltyStampEvent.card_id == card.id,
            LoyaltyStampEvent.reason == StampReason.PAPER_MIGRATION,
        )
    ):
        if not is_undone(session, earlier):
            raise LoyaltyError(
                409, "already_migrated", "A paper card has already been added to this card."
            )
    event, rewards = _apply(
        session,
        card,
        paper_stamps,
        StampReason.PAPER_MIGRATION,
        now=now,
        note=f"paper card, {paper_stamps} sticker{'' if paper_stamps == 1 else 's'}",
        staff_user_id=actor.user_id,
        device_id=actor.device_id,
    )
    return StampResult(
        card=scan_view(session, card, now=now),
        event_id=event.id,
        undo_until=now + timedelta(seconds=UNDO_SECONDS),
        reward_issued=bool(rewards),
    )


def adjust(
    session: Session, *, card_id: str, delta: int, reason: str, manager: StaffUser
) -> LoyaltyStampEvent:
    """A manager's later correction, with a written reason (SPEC: "manager-only")."""
    now = now_utc()
    card = _live_card(session, card_id)
    reason = " ".join(reason.split())
    if len(reason) < 5:
        raise LoyaltyError(422, "reason_required", "Say why, in a few words (5+ characters).")
    if delta == 0 or abs(delta) > _ADJUST_MAX:
        raise LoyaltyError(
            422, "bad_delta", f"A correction changes the count by 1 to {_ADJUST_MAX} stamps."
        )
    event, _ = _apply(
        session,
        card,
        delta,
        StampReason.MANUAL_FIX,
        now=now,
        note=f"{reason} (by {manager.name})",
        staff_user_id=manager.id,
        customer_activity=False,
    )
    return event


def _reverse(
    session: Session,
    event: LoyaltyStampEvent,
    *,
    now: datetime,
    staff_user_id: int | None,
    device_id: int | None,
    note: str,
) -> LoyaltyStampEvent:
    """Write the UNDO row for `event`, voiding the reward it issued if still unredeemed."""
    card = session.get(LoyaltyCard, event.card_id)
    assert card is not None
    required = card.program.stamps_required
    issued = list(
        session.scalars(
            select(LoyaltyReward).where(
                LoyaltyReward.stamp_event_id == event.id, LoyaltyReward.voided_at.is_(None)
            )
        )
    )
    # Every check before any change: the referral path catches a refusal and carries on,
    # so a half-applied reversal must be impossible.
    if any(reward.redeemed_at is not None for reward in issued):
        raise LoyaltyError(
            409,
            "reward_redeemed",
            "The free drink this stamp completed has already been given. "
            "Undo the free drink first.",
        )
    after = card.stamps_current + required * len(issued) - event.delta
    if after < 0:
        raise LoyaltyError(409, "would_go_negative", "Undoing this would leave the card negative.")
    for reward in issued:
        reward.voided_at = now
    card.cycles_completed -= len(issued)
    undo_row = LoyaltyStampEvent(
        card_id=card.id,
        delta=-event.delta,
        reason=StampReason.UNDO,
        note=note[:400],
        staff_user_id=staff_user_id,
        device_id=device_id,
        undoes_event_id=event.id,
        created_at=now,
    )
    session.add(undo_row)
    if card.program.kind is ProgramKind.STAMPS:
        card.stickers = fit_stickers(card.stickers, after, sticker_set(card.program.stickers))
    card.stamps_current = after
    refresh_reward_available(session, card, now)
    touch(card, now)
    enqueue_wallet_update(session, card.id, None)
    session.flush()
    return undo_row


_UNDOABLE = (StampReason.PURCHASE, StampReason.PAPER_MIGRATION)


def undo(
    session: Session,
    actor: StaffActor,
    *,
    event_id: int | None = None,
    reward_id: int | None = None,
) -> ScanView:
    if (event_id is None) == (reward_id is None):
        raise LoyaltyError(422, "bad_request", "Undo one thing: a stamp or a free drink.")
    if reward_id is not None:
        return undo_redemption(session, actor, reward_id)
    assert event_id is not None
    now = now_utc()
    event = session.get(LoyaltyStampEvent, event_id)
    if event is None or event.reason not in _UNDOABLE:
        raise LoyaltyError(404, "unknown_event", "There is no stamp with that number to undo.")
    if is_undone(session, event.id):
        raise LoyaltyError(409, "already_undone", "That stamp has already been undone.")
    if now - event.created_at > timedelta(seconds=UNDO_SECONDS):
        raise LoyaltyError(
            409,
            "undo_expired",
            "Undo only works for two minutes. A manager can correct it from the back office.",
        )
    if event.device_id != actor.device_id and not actor.is_manager:
        raise LoyaltyError(
            403, "not_allowed", "Only the till that stamped it, or a manager, can undo this."
        )
    card = _live_card(session, event.card_id)
    reverse_with_referral(
        session,
        card,
        event,
        now=now,
        staff_user_id=actor.user_id,
        device_id=actor.device_id,
        note=f"undone by {actor.name}",
    )
    return scan_view(session, card, now=now)


def reverse_with_referral(
    session: Session,
    card: LoyaltyCard,
    event: LoyaltyStampEvent,
    *,
    now: datetime,
    staff_user_id: int | None,
    device_id: int | None,
    note: str,
) -> None:
    """Undo `event` (the checks are the caller's) and the referral it triggered, if any."""
    _reverse(session, event, now=now, staff_user_id=staff_user_id, device_id=device_id, note=note)
    # A referral this stamp triggered goes with it, and the referred member can earn it
    # again on their real first visit.
    referral = session.scalar(
        select(LoyaltyStampEvent).where(
            LoyaltyStampEvent.source_event_id == event.id,
            LoyaltyStampEvent.reason == StampReason.REFERRAL,
        )
    )
    if referral is not None and not is_undone(session, referral.id):
        try:
            _reverse(
                session,
                referral,
                now=now,
                staff_user_id=staff_user_id,
                device_id=device_id,
                note=f"referral reversed: its stamp #{event.id} was undone",
            )
            card.member.referral_rewarded_at = None
        except LoyaltyError:
            # The referrer already spent the reward it completed. Leave it: taking a free
            # drink back from somebody who did nothing wrong is worse than one stamp.
            audit(
                session,
                "referral_kept",
                f"stamp #{event.id} undone but the referral reward was already given",
                card_id=referral.card_id,
                at=now,
            )


def credit_referrer(
    session: Session, card: LoyaltyCard, first_stamp: LoyaltyStampEvent, now: datetime
) -> None:
    """The referral rule, for stamps given outside the scanner (the back office)."""
    _credit_referrer(session, card, first_stamp, now)


def expire_stamps(session: Session, *, now: datetime | None = None) -> int:
    """Reset cards untouched for the programme's `stamps_expire_months` (BACKOFFICE-V2).

    A MANUAL_FIX taking the card to zero, with the reason in the note -- the ledger says
    exactly what happened and a manager can undo it. Rewards already issued are kept:
    the rule is about stamps, and a ready drink "doesn't expire while the card is active".
    Returns the number of cards reset. Idempotent: a reset card holds no stamps.
    """
    now = now or now_utc()
    reset = 0
    for program in session.scalars(
        select(LoyaltyProgram).where(
            LoyaltyProgram.stamps_expire_months.is_not(None),
            LoyaltyProgram.kind == ProgramKind.STAMPS,
        )
    ):
        months = int(program.stamps_expire_months or 0)
        if months < 1:
            continue
        cutoff_day = months_before(local_today(settings.tz, now=now), months)
        cutoff = local_day_bounds(cutoff_day, tz=settings.tz)[0]
        stale = session.scalars(
            select(LoyaltyCard)
            .join(LoyaltyMember, LoyaltyMember.id == LoyaltyCard.member_id)
            .where(
                LoyaltyCard.program_id == program.id,
                LoyaltyCard.voided_at.is_(None),
                LoyaltyCard.stamps_current > 0,
                LoyaltyMember.deleted_at.is_(None),
                LoyaltyMember.last_activity_at < cutoff,
            )
        )
        for card in list(stale):
            _apply(
                session,
                card,
                -card.stamps_current,
                StampReason.MANUAL_FIX,
                now=now,
                note=f"stamps expired: no visit in {months} months",
                customer_activity=False,
            )
            reset += 1
    return reset
