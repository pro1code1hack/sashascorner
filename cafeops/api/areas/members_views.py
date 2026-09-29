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
    CardFaceOut,
    CardRowOut,
    DayOut,
    DeviceAdminOut,
    DeviceCreatedOut,
    DevicesOut,
    EligibilityIn,
    EligibilityIO,
    EligibilityPreviewOut,
    EventOut,
    GiveRewardIn,
    HistoryEntryOut,
    HourOut,
    InsightsOut,
    JoinSourceOut,
    KpiOut,
    LightspeedLinkOut,
    LinkIn,
    MemberCreateIn,
    MemberDetailOut,
    MemberDetailRowOut,
    MemberPatchIn,
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
    ProgramSettingsOut,
    RegularOut,
    RewardOptionAdminOut,
    RewardRowOut,
    SegmentName,
    SendLinkOut,
    SendOut,
    SortName,
    SourceOut,
    SourceShareOut,
    StaffCreateIn,
    StaffListOut,
    StaffOut,
    StaffPatchIn,
    StatsOut,
    StickerIn,
    StickerOut,
    TargetOut,
    TargetsIn,
    TargetsOut,
    TemplateRefOut,
    WeekOut,
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
from cafeops.domain.loyalty import (
    PROMO_LIMIT_PER_MONTH,
    STICKER_KEYS,
    STICKER_NAMES,
    Eligibility,
    sticker_set,
)
from cafeops.services.loyalty import (
    admin,
    alerts,
    backoffice,
    campaigns,
    pos,
    programs,
    retention,
    staff_auth,
)
from cafeops.services.loyalty import insights as insights_service
from cafeops.services.loyalty import stats as stats_service
from cafeops.services.loyalty.common import DEFAULT_PROGRAM_SLUG, default_program, now_utc
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.export import member_export
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
        total=page.total,
        members=[MemberRowOut(**asdict(row)) for row in page.members],
        counts=page.counts or {},
    )


def detail_view(session: Session, member_id: int) -> MemberDetailOut:
    d = admin.member_detail(session, member_id)
    member, card = backoffice.main_card(session, member_id)
    extras = backoffice.member_extras(session, member, card, d.member.last_visit_at)
    return MemberDetailOut(
        member=MemberDetailRowOut(
            **asdict(d.member),
            birthday=d.birthday,
            opt_in_at=d.opt_in_at,
            opt_in_source=d.opt_in_source,
            referred_by=d.referred_by,
            terms_accepted_at=extras.terms_accepted_at,
            notes=extras.notes,
            visits_per_month=extras.visits_per_month,
        ),
        card=CardFaceOut(**asdict(backoffice.card_face(session, card))),
        history=[HistoryEntryOut(**asdict(h)) for h in backoffice.history(session, member, card)],
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
        cooldown_max_stamps=p.cooldown_max_stamps,
        cooldown_minutes=p.cooldown_minutes,
    )


def _managers_exist(session: Session) -> bool:
    """Is there anybody whose PIN could approve a rule change?"""
    return (
        session.scalar(
            select(func.count(StaffUser.id)).where(
                StaffUser.active.is_(True),
                StaffUser.role.in_([StaffRole.MANAGER, StaffRole.OWNER]),
            )
        )
        or 0
    ) > 0


def _approve_rules(session: Session, pin: str | None, ip: str) -> None:
    """A manager's PIN, once there is a manager. Before the first one exists the back
    office's own password is the only guard -- otherwise the rules could never be saved
    on a fresh install, which is exactly where the owner found themselves."""
    if pin or _managers_exist(session):
        staff_auth.verify_manager_pin(session, pin, limiter_key=f"ip:{ip}")


def programs_admin_view(session: Session) -> ProgramsAdminOut:
    return ProgramsAdminOut(
        programs=[_program_full(session, p) for p in programs.programs(session)],
        auto_stamp=settings.loyalty_auto_stamp,
        pin_required=_managers_exist(session),
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
        cooldown_max_stamps=body.cooldown_max_stamps,
        cooldown_minutes=body.cooldown_minutes,
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
    _approve_rules(session, body.manager_pin, ip)
    program = programs.program_by_id(session, program_id)
    programs.apply_change(session, program, _change(body))
    return _program_full(session, program)


def program_create_view(session: Session, body: ProgramCreateIn, ip: str) -> ProgramFullOut:
    _approve_rules(session, body.manager_pin, ip)
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


def _program_settings(session: Session, p: LoyaltyProgram) -> ProgramSettingsOut:
    cards = int(
        session.scalar(
            select(func.count(LoyaltyCard.id)).where(
                LoyaltyCard.program_id == p.id, LoyaltyCard.voided_at.is_(None)
            )
        )
        or 0
    )
    sources = session.execute(
        select(LoyaltyMember.source, func.count(LoyaltyMember.id))
        .join(LoyaltyCard, LoyaltyCard.member_id == LoyaltyMember.id)
        .where(
            LoyaltyCard.program_id == p.id,
            LoyaltyMember.deleted_at.is_(None),
            LoyaltyMember.source.is_not(None),
        )
        .group_by(LoyaltyMember.source)
        .order_by(func.count(LoyaltyMember.id).desc(), LoyaltyMember.source)
    ).all()
    return ProgramSettingsOut(
        **_program_out(p).model_dump(),
        welcome_stamp=p.welcome_stamp,
        stamps_expire=p.stamps_expire_months is not None,
        stickers=list(sticker_set(p.stickers)),
        sticker_catalogue=[
            StickerOut(key=k, name=STICKER_NAMES[k], url=f"/stickers/slot-{i}.svg")
            for i, k in enumerate(STICKER_KEYS, start=1)
        ],
        # The public join page (site/web/src/pages/rewards.astro) reads `?src=`.
        join_url=f"{settings.loyalty_public_url.rstrip('/')}/rewards",
        join_sources=[JoinSourceOut(source=str(src), members=int(n)) for src, n in sources],
        cards=cards,
        pin_required=_managers_exist(session),
    )


def program_view(session: Session) -> ProgramSettingsOut:
    return _program_settings(session, default_program(session))


def program_update_view(session: Session, body: ProgramIn, ip: str) -> ProgramSettingsOut:
    _approve_rules(session, body.manager_pin, ip)
    program = default_program(session)
    sent = body.model_fields_set
    programs.apply_change(
        session,
        program,
        programs.ProgramChange(
            name=body.name,
            stamps_required=body.stamps_required,
            max_stamps_per_scan=body.max_stamps_per_scan,
            reward_text=body.reward_text,
            reward_max_price_pence=body.reward_max_price_pence,
            clear_price_cap="reward_max_price_pence" in sent
            and body.reward_max_price_pence is None,
            birthday_reward=body.birthday_reward,
            referral_stamps=body.referral_stamps,
            active=body.active,
            welcome_stamp=body.welcome_stamp,
            stamps_expire=body.stamps_expire,
            stickers=body.stickers,
        ),
    )
    return _program_settings(session, program)


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
    now = now_utc()
    returned = int(
        session.scalar(
            select(func.count(LoyaltyCampaignDelivery.id)).where(
                LoyaltyCampaignDelivery.campaign_id == c.id,
                LoyaltyCampaignDelivery.returned_at.is_not(None),
            )
        )
        or 0
    )
    status = (
        "cancelled"
        if c.cancelled_at is not None
        else "sent"
        if c.sent_at is not None
        else "scheduled"
        if c.scheduled_at is not None
        else "draft"
    )
    rate: float | None = None
    if c.sent_at is not None and c.recipients:
        if returned or now - c.sent_at >= campaigns.RETURN_WINDOW:
            rate = returned / c.recipients
    return CampaignOut(
        id=c.id,
        title=c.title,
        message=c.message,
        segment=c.segment.value,
        is_promo=c.is_promo,
        scheduled_at=c.scheduled_at,
        sent_at=c.sent_at,
        recipients=c.recipients,
        returned=returned,
        audience=(
            len(campaigns.segment_members(session, c.segment, now=now, promo=c.is_promo))
            if c.sent_at is None and c.cancelled_at is None
            else None
        ),
        created_by=c.created_by,
        created_at=c.created_at,
        status=status,
        cancelled_at=c.cancelled_at,
        return_rate=rate,
    )


def campaigns_view(session: Session) -> CampaignsOut:
    now = now_utc()
    return CampaignsOut(
        campaigns=[_campaign_out(session, c) for c in campaigns.list_campaigns(session)],
        promo_limit_per_month=PROMO_LIMIT_PER_MONTH,
        promos_this_month=campaigns.promos_this_month(session, now=now),
        audiences={
            seg.value: len(campaigns.segment_members(session, seg, now=now, promo=True))
            for seg in CampaignSegment
        },
        everyone_with_card=len(
            campaigns.segment_members(session, CampaignSegment.ALL_OPTED_IN, now=now, promo=False)
        ),
    )


def campaign_create_view(session: Session, body: CampaignIn) -> tuple[CampaignOut, list[Outgoing]]:
    row = campaigns.create_campaign(
        session,
        title=body.title,
        message=body.message,
        segment=CampaignSegment[body.segment.upper()],
        scheduled_at=None if body.send_now else body.scheduled_at,
        is_promo=body.is_promo,
        created_by=body.created_by or "back office",
    )
    outgoing: list[Outgoing] = []
    if body.send_now:
        outgoing = list(campaigns.send_campaign(session, row.id).outgoing)
    return _campaign_out(session, row), outgoing


def campaign_cancel_view(session: Session, campaign_id: int) -> CampaignOut:
    return _campaign_out(session, campaigns.cancel_campaign(session, campaign_id))


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


# --- 90-day targets --------------------------------------------------------------


def _program_for(session: Session, slug: str | None) -> LoyaltyProgram:
    return programs.program_by_slug(session, slug) if slug else default_program(session)


def targets_view(session: Session, program: str | None, days: int = 90) -> TargetsOut:
    p = _program_for(session, program)
    s = stats(session, days=days, program_slug=p.slug)
    return TargetsOut(
        program_slug=p.slug,
        days=days,
        targets=[
            TargetOut(
                metric=r.metric.key,
                label=r.metric.label,
                unit=r.metric.unit,
                target=r.target,
                actual=r.actual,
                progress=r.progress,
                met=r.met,
            )
            for r in stats_service.target_rows(s, p.targets)
        ],
    )


def targets_update_view(session: Session, body: TargetsIn) -> TargetsOut:
    p = _program_for(session, body.program)
    programs.set_targets(session, p, body.targets)
    return targets_view(session, p.slug)


# --- subject access export -------------------------------------------------------


def export_view(session: Session, member_id: int) -> dict[str, Any]:
    return member_export(session, member_id)


# --- loyalty card v2: one member's card (BACKOFFICE-V2 §3) ---------------------------


def create_member_view(session: Session, body: MemberCreateIn) -> MemberDetailOut:
    member_id = backoffice.create_member(
        session,
        first_name=body.first_name,
        email=body.email,
        phone=body.phone,
        birthday=body.birthday,
        marketing_opt_in=body.marketing_opt_in,
        source=body.source,
    )
    return detail_view(session, member_id)


def patch_member_view(session: Session, member_id: int, body: MemberPatchIn) -> MemberDetailOut:
    backoffice.patch_member(
        session,
        member_id,
        backoffice.MemberPatch(
            sent=frozenset(body.model_fields_set),
            first_name=body.first_name,
            email=body.email,
            phone=body.phone,
            birthday=body.birthday,
            marketing_opt_in=body.marketing_opt_in,
            notes=body.notes,
        ),
    )
    return detail_view(session, member_id)


def stamp_view(session: Session, member_id: int, sticker: str | None) -> MemberDetailOut:
    backoffice.add_stamp(session, member_id, sticker=sticker)
    return detail_view(session, member_id)


def give_reward_view(session: Session, member_id: int, body: GiveRewardIn) -> MemberDetailOut:
    backoffice.give_reward(session, member_id, reward_id=body.reward_id)
    return detail_view(session, member_id)


def undo_last_view(session: Session, member_id: int) -> MemberDetailOut:
    backoffice.undo_last(session, member_id)
    return detail_view(session, member_id)


def sticker_view(session: Session, member_id: int, body: StickerIn) -> MemberDetailOut:
    backoffice.set_sticker(session, member_id, slot=body.slot, sticker=body.sticker)
    return detail_view(session, member_id)


def send_link_view(session: Session, member_id: int) -> tuple[SendLinkOut, list[Outgoing]]:
    r = backoffice.send_link(session, member_id)
    out = SendLinkOut(
        delivery="email" if r.delivery == "email" else "sms" if r.delivery == "sms" else "none",
        to=r.to,
        url=r.url,
        message=r.message,
    )
    return out, [r.outgoing] if r.outgoing is not None else []


def insights_view(session: Session, days: int) -> InsightsOut:
    i = insights_service.insights(session, days=days)
    return InsightsOut(
        days=i.days,
        members=KpiOut(value=i.members.value, previous=i.members.previous),
        joined_in_window=i.joined_in_window,
        active_members=KpiOut(value=i.active_members.value, previous=i.active_members.previous),
        visits_per_active_member_per_month=i.visits_per_active_member_per_month,
        stamps=KpiOut(value=i.stamps.value, previous=i.stamps.previous),
        stamps_per_week=i.stamps_per_week,
        free_drinks=KpiOut(value=i.free_drinks.value, previous=i.free_drinks.previous),
        came_back=KpiOut(value=i.came_back.value, previous=i.came_back.previous),
        opted_in_share=i.opted_in_share,
        opted_in_count=i.opted_in_count,
        weekly=[WeekOut(**asdict(w)) for w in i.weekly],
        by_source=[SourceShareOut(**asdict(b)) for b in i.by_source],
        hours=[HourOut(hour=h, stamps=n) for h, n in i.hours],
        regulars=[RegularOut(**asdict(r)) for r in i.regulars],
        alerts=[AlertOut(at=a.at, kind=a.kind, detail=a.detail) for a in i.alerts],
    )
