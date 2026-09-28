"""Sync assembly for the scanner API. Each function resolves the staff member itself.

The actor is resolved INSIDE the same unit of work as the action (not in a dependency with
its own session), so "this device is registered and this session is live" and "record
the stamp" are one transaction: a device revoked between the two cannot slip one in.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from cafeops.api.areas.loyalty_schemas import RewardOut
from cafeops.api.areas.staff_schemas import (
    CardOut,
    DeviceOut,
    DrinkOut,
    DrinksOut,
    LastEventOut,
    LoginOut,
    LookupMemberOut,
    LookupOut,
    MeOut,
    PosCustomerOut,
    PosCustomersOut,
    ProgramRefOut,
    RecoveryLinkOut,
    RedeemOut,
    RewardOptionOut,
    ScanResult,
    StaffUserOut,
    StampOut,
)
from cafeops.db.models import LoyaltyCard, LoyaltyReward, LoyaltyRewardOption, StaffRole
from cafeops.services.loyalty import pos, programs, stamping
from cafeops.services.loyalty import redeem as redeem_service
from cafeops.services.loyalty.common import audit, load_card, now_utc
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.join import add_program_card
from cafeops.services.loyalty.recovery import lookup_members, recovery_link
from cafeops.services.loyalty.scan import ScanView, resolve_payload, scan_view
from cafeops.services.loyalty.staff_auth import (
    StaffActor,
    login,
    logout,
    resolve_staff,
)


def role_name(role: StaffRole) -> str:
    return role.value.lower()


def scan_result(view: ScanView) -> ScanResult:
    last = view.last_event
    return ScanResult(
        program_slug=view.program_slug,
        program_name=view.program_name,
        program_kind=view.program_kind,
        points_per_pound=view.points_per_pound,
        reward_ready_label=view.reward_ready_label,
        max_stamps_per_scan=view.max_stamps_per_scan,
        reward_options=[
            RewardOptionOut(
                id=o.id,
                name=o.name,
                description=o.description,
                max_price_pence=o.max_price_pence,
                covers=o.covers,
            )
            for o in view.reward_options
        ],
        other_cards=[scan_result(other) for other in view.other_cards],
        joinable=[ProgramRefOut(slug=p.slug, name=p.name, kind=p.kind) for p in view.joinable],
        lightspeed_linked=view.lightspeed_linked,
        card_id=view.card_id,
        first_name=view.first_name,
        member_since=view.member_since,
        stamps_current=view.stamps_current,
        stamps_required=view.stamps_required,
        rewards=[
            RewardOut(id=r.id, kind=r.kind, label=r.label, expires_at=r.expires_at)
            for r in view.rewards
        ],
        stamps_last_10_min=view.stamps_last_10_min,
        last_event=(
            LastEventOut(
                id=last.id,
                delta=last.delta,
                reason=last.reason,
                at=last.at,
                staff_name=last.staff_name,
            )
            if last
            else None
        ),
        voided=view.voided,
    )


def actor(session: Session, device: str | None, bearer: str | None) -> StaffActor:
    return resolve_staff(session, device_token=device, session_token=bearer)


def login_view(session: Session, device: str | None, pin: str) -> LoginOut:
    issued = login(session, device_token=device, pin=pin)
    a = issued.actor
    return LoginOut(
        session_token=issued.token,
        expires_at=issued.expires_at,
        user=StaffUserOut(id=a.user_id, name=a.name, role=role_name(a.role)),
    )


def logout_view(session: Session, device: str | None, bearer: str | None) -> None:
    logout(session, actor(session, device, bearer))


def me_view(session: Session, device: str | None, bearer: str | None) -> MeOut:
    a = actor(session, device, bearer)
    return MeOut(
        user=StaffUserOut(id=a.user_id, name=a.name, role=role_name(a.role)),
        device=DeviceOut(id=a.device_id, name=a.device_name),
        expires_at=a.expires_at,
    )


def scan_view_(
    session: Session, device: str | None, bearer: str | None, payload: str
) -> ScanResult:
    actor(session, device, bearer)
    card = resolve_payload(session, payload)
    return scan_result(scan_view(session, card))


def stamp_view(
    session: Session,
    device: str | None,
    bearer: str | None,
    *,
    card_id: str,
    delta: int,
    manager_pin: str | None,
) -> tuple[StampOut, tuple[int, ...]]:
    result = stamping.stamp(
        session,
        actor(session, device, bearer),
        card_id=card_id,
        delta=delta,
        manager_pin=manager_pin,
    )
    out = StampOut(
        card=scan_result(result.card),
        event_id=result.event_id,
        undo_until=result.undo_until,
        reward_issued=result.reward_issued,
    )
    return out, result.alert_ids


def migrate_view(
    session: Session, device: str | None, bearer: str | None, *, card_id: str, paper_stamps: int
) -> StampOut:
    result = stamping.migrate(
        session, actor(session, device, bearer), card_id=card_id, paper_stamps=paper_stamps
    )
    return StampOut(
        card=scan_result(result.card),
        event_id=result.event_id,
        undo_until=result.undo_until,
        reward_issued=result.reward_issued,
    )


def redeem_view(
    session: Session,
    device: str | None,
    bearer: str | None,
    *,
    reward_id: int,
    menu_item_id: int | None,
    option_id: int | None = None,
) -> RedeemOut:
    result = redeem_service.redeem(
        session,
        actor(session, device, bearer),
        reward_id=reward_id,
        menu_item_id=menu_item_id,
        option_id=option_id,
    )
    return RedeemOut(
        card=scan_result(result.card), reward_id=result.reward_id, undo_until=result.undo_until
    )


def undo_view(
    session: Session,
    device: str | None,
    bearer: str | None,
    *,
    event_id: int | None,
    reward_id: int | None,
) -> CardOut:
    view = stamping.undo(
        session, actor(session, device, bearer), event_id=event_id, reward_id=reward_id
    )
    return CardOut(card=scan_result(view))


def drinks_view(
    session: Session,
    device: str | None,
    bearer: str | None,
    *,
    reward_id: int | None = None,
    option_id: int | None = None,
) -> DrinksOut:
    """Phase 1: the drinks. Phase 3: what a given reward (or catalogue entry) covers."""
    actor(session, device, bearer)
    cap: int | None = None
    covers: str | None = None
    items = None
    if option_id is not None:
        option = session.get(LoyaltyRewardOption, option_id)
        if option is None or not option.active:
            raise LoyaltyError(404, "unknown_option", "That reward is not on the list any more.")
        rule = programs.option_rule(option)
        cap = option.max_price_pence
        if cap is None:
            cap = option_program_cap(session, option)
        covers = rule.describe()
        items = programs.eligible_menu(session, rule)
    elif reward_id is not None:
        reward = session.get(LoyaltyReward, reward_id)
        if reward is None:
            raise LoyaltyError(404, "unknown_reward", "There is no reward with that number.")
        card = session.get(LoyaltyCard, reward.card_id)
        assert card is not None
        rule = programs.program_rule(card.program)
        cap = card.program.reward_max_price_pence
        covers = rule.describe()
        if not rule.is_default:
            items = programs.eligible_menu(session, rule)
    if items is None:
        items = redeem_service.list_drinks(session)
    return DrinksOut(
        items=[
            DrinkOut(
                menu_item_id=d.menu_item_id,
                name=d.name,
                category=d.category,
                price_pence=d.price_pence,
            )
            for d in items
        ],
        max_price_pence=cap,
        covers=covers,
    )


def option_program_cap(session: Session, option: LoyaltyRewardOption) -> int | None:
    program = programs.program_by_id(session, option.program_id)
    return program.reward_max_price_pence


def spend_view(
    session: Session,
    device: str | None,
    bearer: str | None,
    *,
    card_id: str,
    spend_pence: int,
    manager_pin: str | None,
) -> StampOut:
    result = stamping.add_spend(
        session,
        actor(session, device, bearer),
        card_id=card_id,
        spend_pence=spend_pence,
        manager_pin=manager_pin,
    )
    return StampOut(
        card=scan_result(result.card),
        event_id=result.event_id,
        undo_until=result.undo_until,
        reward_issued=result.reward_issued,
    )


def add_card_view(
    session: Session, device: str | None, bearer: str | None, *, card_id: str, program: str
) -> CardOut:
    a = actor(session, device, bearer)
    card = load_card(session, card_id)
    new = add_program_card(session, card.member, program, source=f"scanner:{a.name}")
    return CardOut(card=scan_result(scan_view(session, new)))


def link_pos_view(
    session: Session,
    device: str | None,
    bearer: str | None,
    *,
    card_id: str,
    customer_id: str | None,
    receipt_id: str | None,
) -> CardOut:
    a = actor(session, device, bearer)
    card = load_card(session, card_id)
    pos.link_customer(
        session,
        card.member,
        customer_id=customer_id,
        receipt_id=receipt_id,
        source="staff",
    )
    audit(
        session,
        "pos_link_staff",
        f"{a.name} linked the till customer on {a.device_name}",
        staff_user_id=a.user_id,
        device_id=a.device_id,
        card_id=card.id,
        member_id=card.member_id,
    )
    # Receipts already synced for that customer earn now, not at the next sync.
    pos.reconcile(session, member_ids=[card.member_id])
    return CardOut(card=scan_result(scan_view(session, card)))


def pos_customers_view(session: Session, device: str | None, bearer: str | None) -> PosCustomersOut:
    actor(session, device, bearer)
    since = now_utc() - timedelta(days=3)
    return PosCustomersOut(
        customers=[
            PosCustomerOut(
                customer_id=c.customer_id,
                label=c.label,
                last_receipt_id=c.last_receipt_id,
                last_at=c.last_at,
                receipts=c.receipts,
            )
            for c in pos.recent_customers(session, since=since)
        ]
    )


def lookup_view(session: Session, device: str | None, bearer: str | None, q: str) -> LookupOut:
    actor(session, device, bearer)
    return LookupOut(
        members=[
            LookupMemberOut(
                card_id=h.card_id, first_name=h.first_name, contact_masked=h.contact_masked
            )
            for h in lookup_members(session, q)
        ]
    )


def recovery_link_view(
    session: Session, device: str | None, bearer: str | None, card_id: str
) -> RecoveryLinkOut:
    return RecoveryLinkOut(url=recovery_link(session, actor(session, device, bearer), card_id))
