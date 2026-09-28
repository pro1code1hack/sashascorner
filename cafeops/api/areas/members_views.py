"""Sync assembly for the Members screen. Converts service dataclasses; decides nothing."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.api.areas.members_schemas import (
    AlertOut,
    AlertsOut,
    CampaignIn,
    CampaignOut,
    CampaignsOut,
    CardRowOut,
    DayOut,
    DeviceAdminOut,
    DeviceCreatedOut,
    DevicesOut,
    EligibilityIn,
    EligibilityIO,
    EligibilityPreviewOut,
    EventOut,
    LightspeedLinkOut,
    LinkIn,
    MemberDetailOut,
    MemberDetailRowOut,
    MemberRowOut,
    MembersPageOut,
    MenuFacetsOut,
    PosCustomerAdminOut,
    PosCustomersAdminOut,
    PosReceiptOut,
    ProgramAdminOut,
    ProgramCreateIn,
    ProgramEditIn,
    ProgramFullOut,
    ProgramIn,
    ProgramsAdminOut,
    RewardOptionAdminOut,
    RewardRowOut,
    SegmentName,
    SendOut,
    SortName,
    SourceOut,
    StaffCreateIn,
    StaffListOut,
    StaffOut,
    StaffPatchIn,
    StatsOut,
    TemplateRefOut,
)
from cafeops.config import settings
from cafeops.db.models import (
    CampaignSegment,
    DrinkTemplate,
    LoyaltyCampaign,
    LoyaltyCampaignDelivery,
    LoyaltyCard,
    LoyaltyMember,
    LoyaltyProgram,
    LoyaltyStampEvent,
    MenuItem,
    ProgramKind,
    StaffRole,
    StaffUser,
)
from cafeops.domain.loyalty import PROMO_LIMIT_PER_MONTH, Eligibility
from cafeops.services.loyalty import (
    admin,
    alerts,
    campaigns,
    pos,
    programs,
    retention,
    staff_auth,
)
from cafeops.services.loyalty.common import DEFAULT_PROGRAM_SLUG, default_program, now_utc
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.messaging import Outgoing
from cafeops.services.loyalty.stats import stats


def members_view(
    session: Session,
    *,
    q: str | None,
    segment: SegmentName,
    sort: SortName,
    limit: int,
    offset: int,
    program: str | None = None,
) -> MembersPageOut:
    page = admin.members_page(
        session,
        q=q,
        segment=segment,
        sort=sort,
        limit=limit,
        offset=offset,
        program_slug=program,
    )
    return MembersPageOut(
        total=page.total, members=[MemberRowOut(**asdict(row)) for row in page.members]
    )


def detail_view(session: Session, member_id: int) -> MemberDetailOut:
    d = admin.member_detail(session, member_id)
    return MemberDetailOut(
        member=MemberDetailRowOut(
            **asdict(d.member),
            birthday=d.birthday,
            opt_in_at=d.opt_in_at,
            opt_in_source=d.opt_in_source,
            referred_by=d.referred_by,
        ),
        events=[EventOut(**asdict(e)) for e in d.events],
        rewards=[RewardRowOut(**asdict(r)) for r in d.rewards],
        cards=[CardRowOut(**asdict(c)) for c in d.cards],
        lightspeed=(
            LightspeedLinkOut(
                customer_id=d.lightspeed.customer_id,
                linked_at=d.lightspeed.linked_at,
                source=d.lightspeed.source,
                receipts=[
                    PosReceiptOut(
                        receipt_id=r.receipt_id,
                        closed_at=r.closed_at,
                        total_pence=r.total_pence,
                        awards=list(r.awards),
                    )
                    for r in d.lightspeed.receipts
                ],
            )
            if d.lightspeed
            else None
        ),
        auto_stamp=settings.loyalty_auto_stamp,
    )


def adjust_view(
    session: Session,
    member_id: int,
    *,
    delta: int,
    reason: str,
    manager_pin: str,
    ip: str,
    program: str | None = None,
) -> MemberDetailOut:
    admin.adjust_member(
        session,
        member_id,
        delta=delta,
        reason=reason,
        manager_pin=manager_pin,
        client_ip=ip,
        program_slug=program,
    )
    return detail_view(session, member_id)


def _member(session: Session, member_id: int) -> LoyaltyMember:
    member = session.get(LoyaltyMember, member_id)
    if member is None:
        raise LoyaltyError(404, "unknown_member", "There is no member with that id.")
    return member


def link_view(session: Session, member_id: int, body: LinkIn) -> MemberDetailOut:
    member = _member(session, member_id)
    pos.link_customer(
        session,
        member,
        customer_id=body.customer_id,
        receipt_id=body.receipt_id,
        source="back_office",
    )
    pos.reconcile(session, member_ids=[member.id])
    return detail_view(session, member_id)


def unlink_view(session: Session, member_id: int) -> MemberDetailOut:
    pos.unlink_customer(session, _member(session, member_id), source="back_office")
    return detail_view(session, member_id)


def pos_customers_view(session: Session, days: int) -> PosCustomersAdminOut:
    return PosCustomersAdminOut(
        customers=[
            PosCustomerAdminOut(
                customer_id=c.customer_id,
                label=c.label,
                last_receipt_id=c.last_receipt_id,
                last_at=c.last_at,
                receipts=c.receipts,
            )
            for c in pos.recent_customers(session, since=now_utc() - timedelta(days=days), limit=50)
        ]
    )


# --- phase 3: programmes -------------------------------------------------------------


def _eligibility_io(raw: dict[str, Any] | None) -> EligibilityIO:
    rule = Eligibility.parse(raw)
    return EligibilityIO(
        scope="all" if rule.scope == "all" else "drinks",
        categories=list(rule.categories),
        keywords=list(rule.keywords),
        template_ids=list(rule.template_ids),
    )


def _program_full(session: Session, p: LoyaltyProgram) -> ProgramFullOut:
    base = _program_out(p)
    cards = int(
        session.scalar(
            select(func.count(LoyaltyCard.id)).where(
                LoyaltyCard.program_id == p.id, LoyaltyCard.voided_at.is_(None)
            )
        )
        or 0
    )
    earned = (
        session.scalar(
            select(func.count(LoyaltyStampEvent.id)).where(
                LoyaltyStampEvent.card_id.in_(
                    select(LoyaltyCard.id).where(LoyaltyCard.program_id == p.id)
                )
            )
        )
        or 0
    )
    return ProgramFullOut(
        **base.model_dump(),
        kind=p.kind.value,
        points_per_pound=p.points_per_pound,
        eligibility=_eligibility_io(p.eligibility),
        earns=programs.program_rule(p).describe(),
        description=p.description,
        reward_ready_label=p.reward_ready_label,
        sort_order=p.sort_order,
        is_default=p.slug == DEFAULT_PROGRAM_SLUG,
        reward_options=[
            RewardOptionAdminOut(
                id=o.id,
                name=o.name,
                description=o.description,
                eligibility=_eligibility_io(o.eligibility),
                covers=programs.option_rule(o).describe(),
                max_price_pence=o.max_price_pence,
                active=o.active,
            )
            for o in programs.reward_options(session, p.id, active_only=False)
        ],
        cards=cards,
        kind_editable=earned == 0,
    )


def programs_admin_view(session: Session) -> ProgramsAdminOut:
    return ProgramsAdminOut(
        programs=[_program_full(session, p) for p in programs.programs(session)],
        auto_stamp=settings.loyalty_auto_stamp,
    )


def _change(body: ProgramEditIn) -> programs.ProgramChange:
    sent = body.model_fields_set
    return programs.ProgramChange(
        name=body.name,
        stamps_required=body.stamps_required,
        max_stamps_per_scan=body.max_stamps_per_scan,
        reward_text=body.reward_text,
        reward_max_price_pence=body.reward_max_price_pence,
        clear_price_cap="reward_max_price_pence" in sent and body.reward_max_price_pence is None,
        birthday_reward=body.birthday_reward,
        referral_stamps=body.referral_stamps,
        active=body.active,
        kind=ProgramKind[body.kind] if body.kind else None,
        points_per_pound=body.points_per_pound,
        eligibility=body.eligibility.model_dump() if body.eligibility else None,
        clear_eligibility="eligibility" in sent and body.eligibility is None,
        description=body.description,
        reward_ready_label=body.reward_ready_label,
        sort_order=body.sort_order,
        reward_options=(
            [
                programs.OptionChange(
                    id=o.id,
                    name=o.name,
                    description=o.description,
                    eligibility=o.eligibility.model_dump() if o.eligibility else None,
                    max_price_pence=o.max_price_pence,
                    active=o.active,
                )
                for o in body.reward_options
            ]
            if body.reward_options is not None
            else None
        ),
    )


def program_edit_view(
    session: Session, program_id: int, body: ProgramEditIn, ip: str
) -> ProgramFullOut:
    staff_auth.verify_manager_pin(session, body.manager_pin, limiter_key=f"ip:{ip}")
    program = programs.program_by_id(session, program_id)
    programs.apply_change(session, program, _change(body))
    return _program_full(session, program)


def program_create_view(session: Session, body: ProgramCreateIn, ip: str) -> ProgramFullOut:
    staff_auth.verify_manager_pin(session, body.manager_pin, limiter_key=f"ip:{ip}")
    program = programs.create_program(session, slug=body.slug, change=_change(body))
    return _program_full(session, program)


def menu_facets_view(session: Session) -> MenuFacetsOut:
    categories = sorted(
        {
            c.strip()
            for c in session.scalars(
                select(MenuItem.category).where(
                    MenuItem.active.is_(True), MenuItem.category.is_not(None)
                )
            )
            if c and c.strip()
        }
    )
    templates = [
        TemplateRefOut(id=t.id, name=t.name)
        for t in session.scalars(select(DrinkTemplate).order_by(DrinkTemplate.name))
    ]
    return MenuFacetsOut(categories=categories, templates=templates)


def eligibility_preview_view(session: Session, body: EligibilityIn) -> EligibilityPreviewOut:
    rule = Eligibility.parse(body.model_dump())
    items = programs.eligible_menu(session, rule)
    names = list(dict.fromkeys(i.name.split(" (")[0] for i in items))
    return EligibilityPreviewOut(count=len(items), sample=names[:12], covers=rule.describe())


def delete_view(session: Session, member_id: int) -> None:
    retention.erase_member(session, member_id, why="deleted from the back office")


def stats_view(session: Session, days: int, program: str | None = None) -> StatsOut:
    s = stats(session, days=days, program_slug=program)
    return StatsOut(
        members_total=s.members_total,
        members_new_in_window=s.members_new_in_window,
        opted_in_share=s.opted_in_share,
        by_source=[SourceOut(source=r.source, members=r.members) for r in s.by_source],
        daily=[DayOut(**asdict(d)) for d in s.daily],
        stamp_share_of_transactions=s.stamp_share_of_transactions,
        redemptions_per_week=s.redemptions_per_week,
        visits_per_member_per_month=s.visits_per_member_per_month,
        members_with_redemption_share=s.members_with_redemption_share,
        campaign_return_rate=s.campaign_return_rate,
        program_slug=s.program_slug,
        repeat_rate_members=s.repeat_rate_members,
        repeat_rate_non_members=s.repeat_rate_non_members,
        repeat_customers_members=s.repeat_customers_members,
        repeat_customers_non_members=s.repeat_customers_non_members,
        repeat_rate_members_by_scans=s.repeat_rate_members_by_scans,
        receipts_with_customer_share=s.receipts_with_customer_share,
        repeat_rate_reason=s.repeat_rate_reason,
    )


def _program_out(p: LoyaltyProgram) -> ProgramAdminOut:
    return ProgramAdminOut(
        id=p.id,
        slug=p.slug,
        name=p.name,
        stamps_required=p.stamps_required,
        max_stamps_per_scan=p.max_stamps_per_scan,
        reward_text=p.reward_text,
        reward_max_price_pence=p.reward_max_price_pence,
        birthday_reward=p.birthday_reward,
        referral_stamps=p.referral_stamps,
        active=p.active,
    )


def program_view(session: Session) -> ProgramAdminOut:
    return _program_out(default_program(session))


def program_update_view(session: Session, body: ProgramIn, ip: str) -> ProgramAdminOut:
    sent = body.model_fields_set
    change = admin.ProgramUpdate(
        name=body.name,
        stamps_required=body.stamps_required,
        max_stamps_per_scan=body.max_stamps_per_scan,
        reward_text=body.reward_text,
        reward_max_price_pence=body.reward_max_price_pence,
        clear_price_cap="reward_max_price_pence" in sent and body.reward_max_price_pence is None,
        birthday_reward=body.birthday_reward,
        referral_stamps=body.referral_stamps,
        active=body.active,
    )
    program = admin.update_program(session, change, manager_pin=body.manager_pin, client_ip=ip)
    return _program_out(program)


def _staff_out(u: StaffUser) -> StaffOut:
    return StaffOut(
        id=u.id, name=u.name, role=u.role.value.lower(), active=u.active, telegram_id=u.telegram_id
    )


def staff_list_view(session: Session) -> StaffListOut:
    return StaffListOut(users=[_staff_out(u) for u in staff_auth.list_staff(session)])


def staff_create_view(session: Session, body: StaffCreateIn) -> StaffOut:
    user = staff_auth.create_staff_user(
        session,
        name=body.name,
        role=StaffRole[body.role.upper()],
        pin=body.pin,
        telegram_id=body.telegram_id,
    )
    return _staff_out(user)


def staff_patch_view(session: Session, user_id: int, body: StaffPatchIn) -> StaffOut:
    sent = body.model_fields_set
    user = staff_auth.update_staff_user(
        session,
        user_id,
        name=body.name,
        role=StaffRole[body.role.upper()] if body.role else None,
        pin=body.pin,
        active=body.active,
        telegram_id=body.telegram_id,
        clear_telegram="telegram_id" in sent and body.telegram_id is None,
    )
    return _staff_out(user)


def devices_view(session: Session) -> DevicesOut:
    return DevicesOut(
        devices=[
            DeviceAdminOut(
                id=d.id,
                name=d.name,
                registered_at=d.registered_at,
                last_seen_at=d.last_seen_at,
                revoked_at=d.revoked_at,
            )
            for d in staff_auth.list_devices(session)
        ]
    )


def device_create_view(session: Session, name: str) -> DeviceCreatedOut:
    new = staff_auth.create_device(session, name=name)
    return DeviceCreatedOut(
        device_id=new.device_id, pairing_code=new.pairing_code, expires_at=new.expires_at
    )


def device_revoke_view(session: Session, device_id: int) -> None:
    staff_auth.revoke_device(session, device_id)


def _campaign_out(session: Session, c: LoyaltyCampaign) -> CampaignOut:
    returned = session.scalar(
        select(func.count(LoyaltyCampaignDelivery.id)).where(
            LoyaltyCampaignDelivery.campaign_id == c.id,
            LoyaltyCampaignDelivery.returned_at.is_not(None),
        )
    )
    return CampaignOut(
        id=c.id,
        title=c.title,
        message=c.message,
        segment=c.segment.value,
        is_promo=c.is_promo,
        scheduled_at=c.scheduled_at,
        sent_at=c.sent_at,
        recipients=c.recipients,
        returned=int(returned or 0),
        audience=(
            len(campaigns.segment_members(session, c.segment, now=now_utc()))
            if c.sent_at is None
            else None
        ),
        created_by=c.created_by,
        created_at=c.created_at,
    )


def campaigns_view(session: Session) -> CampaignsOut:
    return CampaignsOut(
        campaigns=[_campaign_out(session, c) for c in campaigns.list_campaigns(session)],
        promo_limit_per_month=PROMO_LIMIT_PER_MONTH,
    )


def campaign_create_view(session: Session, body: CampaignIn) -> CampaignOut:
    row = campaigns.create_campaign(
        session,
        title=body.title,
        message=body.message,
        segment=CampaignSegment[body.segment.upper()],
        scheduled_at=body.scheduled_at,
        is_promo=body.is_promo,
        created_by=body.created_by or "back office",
    )
    return _campaign_out(session, row)


def campaign_send_view(session: Session, campaign_id: int) -> tuple[SendOut, list[Outgoing]]:
    result = campaigns.send_campaign(session, campaign_id)
    return (
        SendOut(recipients=result.recipients, skipped_over_limit=result.skipped_over_limit),
        list(result.outgoing),
    )


def alerts_view(session: Session) -> AlertsOut:
    return AlertsOut(
        alerts=[
            AlertOut(at=a.at, kind=a.kind, detail=a.detail) for a in alerts.list_alerts(session)
        ]
    )
