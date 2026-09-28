"""The back office's Members area (CONTRACT §6): members, stats, programme, staff, devices,
campaigns, alerts. Behind the existing shared password (`ApiAuth`), on the ops domain.

Deliberately NOT under `/api/loyalty/*`: that prefix is forwarded from the public site, and
a customer list must never be one Caddy rule away from the internet.

Static paths are declared before `/{member_id}`: FastAPI matches in order, and "stats"
failing the int conversion of `member_id` would be a 422, not a fall-through.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request, status
from fastapi.responses import JSONResponse, Response

from cafeops.api.areas import members_views as views
from cafeops.api.areas.loyalty_edge import deliver_after_commit, kick_wallets
from cafeops.api.areas.members_schemas import (
    AdjustIn,
    AlertsOut,
    CampaignIn,
    CampaignOut,
    CampaignsOut,
    DeviceCreatedOut,
    DeviceCreateIn,
    DevicesOut,
    EligibilityIn,
    EligibilityPreviewOut,
    GiveRewardIn,
    InsightsOut,
    LinkIn,
    MemberCreateIn,
    MemberDetailOut,
    MemberPatchIn,
    MembersPageOut,
    MenuFacetsOut,
    PosCustomersAdminOut,
    ProgramCreateIn,
    ProgramEditIn,
    ProgramFullOut,
    ProgramIn,
    ProgramsAdminOut,
    ProgramSettingsOut,
    SegmentName,
    SendLinkOut,
    SendOut,
    SortName,
    StaffCreateIn,
    StaffListOut,
    StaffOut,
    StaffPatchIn,
    StampIn,
    StatsOut,
    StickerIn,
    TargetsIn,
    TargetsOut,
)
from cafeops.api.runtime import in_session
from cafeops.api.security import ApiAuth, client_ip

router = APIRouter(prefix="/api/members", tags=["members"], dependencies=[ApiAuth])


@router.get("", response_model=MembersPageOut, summary="Members, searchable, by segment.")
async def members(
    q: Annotated[str | None, Query(max_length=120)] = None,
    segment: SegmentName = "all",
    sort: SortName = "recent",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    program: Annotated[str | None, Query(max_length=40, description="Phase 3 slug")] = None,
) -> MembersPageOut:
    return await in_session(
        lambda s: views.members_view(
            s, q=q, segment=segment, sort=sort, limit=limit, offset=offset, program=program
        )
    )


@router.post(
    "",
    response_model=MemberDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member from the back office (terms confirmed by staff).",
)
async def member_create(body: MemberCreateIn, background: BackgroundTasks) -> MemberDetailOut:
    out = await in_session(lambda s: views.create_member_view(s, body))
    background.add_task(kick_wallets)
    return out


@router.get(
    "/insights",
    response_model=InsightsOut,
    summary="How the card is doing: this window against the one before.",
)
async def member_insights(days: Annotated[int, Query(ge=7, le=366)] = 90) -> InsightsOut:
    return await in_session(lambda s: views.insights_view(s, days))


@router.get("/stats", response_model=StatsOut, summary="The spec's 90-day success metrics.")
async def member_stats(
    days: Annotated[int, Query(ge=7, le=730)] = 90,
    program: Annotated[str | None, Query(max_length=40, description="Phase 3 slug")] = None,
) -> StatsOut:
    return await in_session(lambda s: views.stats_view(s, days, program))


@router.get(
    "/programs",
    response_model=ProgramsAdminOut,
    summary="Phase 3: every programme with its rules and reward catalogue.",
)
async def programs_list() -> ProgramsAdminOut:
    return await in_session(views.programs_admin_view)


@router.post(
    "/programs",
    response_model=ProgramFullOut,
    status_code=status.HTTP_201_CREATED,
    summary="Phase 3: a new programme (inactive unless active=true). Manager PIN.",
)
async def program_create(body: ProgramCreateIn, request: Request) -> ProgramFullOut:
    ip = client_ip(request)
    return await in_session(lambda s: views.program_create_view(s, body, ip))


@router.put(
    "/programs/{program_id}",
    response_model=ProgramFullOut,
    summary="Phase 3: edit any programme, its eligibility and catalogue. Manager PIN.",
)
async def program_edit(program_id: int, body: ProgramEditIn, request: Request) -> ProgramFullOut:
    ip = client_ip(request)
    return await in_session(lambda s: views.program_edit_view(s, program_id, body, ip))


@router.get(
    "/menu-facets",
    response_model=MenuFacetsOut,
    summary="Phase 3: menu categories and drink templates, for eligibility editing.",
)
async def menu_facets() -> MenuFacetsOut:
    return await in_session(views.menu_facets_view)


@router.post(
    "/eligibility-preview",
    response_model=EligibilityPreviewOut,
    summary="Phase 3: which menu items an eligibility rule matches. Writes nothing.",
)
async def eligibility_preview(body: EligibilityIn) -> EligibilityPreviewOut:
    return await in_session(lambda s: views.eligibility_preview_view(s, body))


@router.get(
    "/pos-customers",
    response_model=PosCustomersAdminOut,
    summary="Phase 3: till customers on recent receipts not linked to any member.",
)
async def pos_customers(days: Annotated[int, Query(ge=1, le=90)] = 14) -> PosCustomersAdminOut:
    return await in_session(lambda s: views.pos_customers_view(s, days))


@router.get("/program", response_model=ProgramSettingsOut, summary="The stamp card's settings.")
async def program() -> ProgramSettingsOut:
    return await in_session(views.program_view)


@router.put(
    "/program",
    response_model=ProgramSettingsOut,
    summary="Edit them. Manager PIN once a manager exists.",
)
async def program_update(body: ProgramIn, request: Request) -> ProgramSettingsOut:
    ip = client_ip(request)
    return await in_session(lambda s: views.program_update_view(s, body, ip))


@router.get("/staff", response_model=StaffListOut, summary="Staff who use the scanner.")
async def staff() -> StaffListOut:
    return await in_session(views.staff_list_view)


@router.post(
    "/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED, summary="Add staff."
)
async def staff_create(body: StaffCreateIn) -> StaffOut:
    return await in_session(lambda s: views.staff_create_view(s, body))


@router.patch("/staff/{user_id}", response_model=StaffOut, summary="Change name, role, PIN, ...")
async def staff_patch(user_id: int, body: StaffPatchIn) -> StaffOut:
    return await in_session(lambda s: views.staff_patch_view(s, user_id, body))


@router.get("/devices", response_model=DevicesOut, summary="Registered scanner devices.")
async def devices() -> DevicesOut:
    return await in_session(views.devices_view)


@router.post(
    "/devices",
    response_model=DeviceCreatedOut,
    status_code=status.HTTP_201_CREATED,
    summary="A new device slot and its 6-digit pairing code (15 minutes).",
)
async def device_create(body: DeviceCreateIn) -> DeviceCreatedOut:
    return await in_session(lambda s: views.device_create_view(s, body.name))


@router.delete(
    "/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke a device and end its sessions.",
)
async def device_revoke(device_id: int) -> Response:
    await in_session(lambda s: views.device_revoke_view(s, device_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/campaigns", response_model=CampaignsOut, summary="Campaigns, newest first.")
async def campaigns() -> CampaignsOut:
    return await in_session(views.campaigns_view)


@router.post(
    "/campaigns",
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a campaign (sent now by /send, or at scheduled_at by the job).",
)
async def campaign_create(body: CampaignIn, background: BackgroundTasks) -> CampaignOut:
    out, outgoing = await in_session(lambda s: views.campaign_create_view(s, body))
    if body.send_now:
        background.add_task(kick_wallets)
    if outgoing:
        background.add_task(deliver_after_commit, outgoing)
    return out


@router.post(
    "/campaigns/{campaign_id}/cancel",
    response_model=CampaignOut,
    summary="Cancel a campaign that has not gone out.",
)
async def campaign_cancel(campaign_id: int) -> CampaignOut:
    return await in_session(lambda s: views.campaign_cancel_view(s, campaign_id))


@router.post(
    "/campaigns/{campaign_id}/send",
    response_model=SendOut,
    summary="Send to opted-in members; skips anyone at 2 promos this month.",
)
async def campaign_send(campaign_id: int, background: BackgroundTasks) -> SendOut:
    out, outgoing = await in_session(lambda s: views.campaign_send_view(s, campaign_id))
    background.add_task(kick_wallets)
    if outgoing:
        background.add_task(deliver_after_commit, outgoing)
    return out


@router.get("/alerts", response_model=AlertsOut, summary="Recent fraud-pattern alerts.")
async def alerts() -> AlertsOut:
    return await in_session(views.alerts_view)


@router.get(
    "/targets",
    response_model=TargetsOut,
    summary="The owner's 90-day targets, each with the figure it is measured against.",
)
async def targets(
    program: Annotated[str | None, Query(max_length=40)] = None,
    days: Annotated[int, Query(ge=7, le=730)] = 90,
) -> TargetsOut:
    return await in_session(lambda s: views.targets_view(s, program, days))


@router.put(
    "/targets",
    response_model=TargetsOut,
    summary="Set or clear targets ({metric: number|null}); unnamed metrics are kept.",
)
async def targets_update(body: TargetsIn) -> TargetsOut:
    return await in_session(lambda s: views.targets_update_view(s, body))


@router.get("/{member_id}", response_model=MemberDetailOut, summary="One member and history.")
async def member_detail(member_id: int) -> MemberDetailOut:
    return await in_session(lambda s: views.detail_view(s, member_id))


@router.get(
    "/{member_id}/export",
    summary="Everything held about one member, as a JSON download (subject access request).",
    response_class=JSONResponse,
)
async def member_export(member_id: int) -> JSONResponse:
    data = await in_session(lambda s: views.export_view(s, member_id))
    return JSONResponse(
        data,
        headers={
            "Content-Disposition": f'attachment; filename="member-{member_id}-data.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/{member_id}/adjust",
    response_model=MemberDetailOut,
    summary="Manager correction with a reason (MANUAL_FIX).",
)
async def member_adjust(
    member_id: int, body: AdjustIn, request: Request, background: BackgroundTasks
) -> MemberDetailOut:
    ip = client_ip(request)
    out = await in_session(
        lambda s: views.adjust_view(
            s,
            member_id,
            delta=body.delta,
            reason=body.reason,
            manager_pin=body.manager_pin,
            ip=ip,
            program=body.program,
        )
    )
    background.add_task(kick_wallets)
    return out


@router.patch(
    "/{member_id}",
    response_model=MemberDetailOut,
    summary="Edit contact, birthday, consent (source: back office) and notes.",
)
async def member_patch(
    member_id: int, body: MemberPatchIn, background: BackgroundTasks
) -> MemberDetailOut:
    out = await in_session(lambda s: views.patch_member_view(s, member_id, body))
    background.add_task(kick_wallets)
    return out


@router.post("/{member_id}/stamp", response_model=MemberDetailOut, summary="One stamp.")
async def member_stamp(
    member_id: int, body: StampIn, background: BackgroundTasks
) -> MemberDetailOut:
    out = await in_session(lambda s: views.stamp_view(s, member_id, body.sticker))
    background.add_task(kick_wallets)
    return out


@router.post(
    "/{member_id}/give-reward", response_model=MemberDetailOut, summary="Give the free drink."
)
async def member_give_reward(
    member_id: int, body: GiveRewardIn, background: BackgroundTasks
) -> MemberDetailOut:
    out = await in_session(lambda s: views.give_reward_view(s, member_id, body))
    background.add_task(kick_wallets)
    return out


@router.post(
    "/{member_id}/undo-last",
    response_model=MemberDetailOut,
    summary="Reverse the newest stamp, correction or free drink on the card.",
)
async def member_undo_last(member_id: int, background: BackgroundTasks) -> MemberDetailOut:
    out = await in_session(lambda s: views.undo_last_view(s, member_id))
    background.add_task(kick_wallets)
    return out


@router.put(
    "/{member_id}/stickers",
    response_model=MemberDetailOut,
    summary="Change the sticker in one filled slot (cosmetic; the ledger is untouched).",
)
async def member_sticker(
    member_id: int, body: StickerIn, background: BackgroundTasks
) -> MemberDetailOut:
    out = await in_session(lambda s: views.sticker_view(s, member_id, body))
    background.add_task(kick_wallets)
    return out


@router.post(
    "/{member_id}/send-link",
    response_model=SendLinkOut,
    summary="Send the member their web-card link (email or text when configured).",
)
async def member_send_link(member_id: int, background: BackgroundTasks) -> SendLinkOut:
    out, outgoing = await in_session(lambda s: views.send_link_view(s, member_id))
    if outgoing:
        background.add_task(deliver_after_commit, outgoing)
    return out


@router.put(
    "/{member_id}/lightspeed",
    response_model=MemberDetailOut,
    summary="Phase 3: link to a Lightspeed customer (by id or receipt number).",
)
async def member_link(member_id: int, body: LinkIn, background: BackgroundTasks) -> MemberDetailOut:
    out = await in_session(lambda s: views.link_view(s, member_id, body))
    background.add_task(kick_wallets)
    return out


@router.delete(
    "/{member_id}/lightspeed",
    response_model=MemberDetailOut,
    summary="Phase 3: forget the Lightspeed link (stamps already given stay).",
)
async def member_unlink(member_id: int) -> MemberDetailOut:
    return await in_session(lambda s: views.unlink_view(s, member_id))


@router.delete(
    "/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Erase a member (same as the customer's own delete).",
)
async def member_delete(member_id: int, background: BackgroundTasks) -> Response:
    await in_session(lambda s: views.delete_view(s, member_id))
    background.add_task(kick_wallets)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
