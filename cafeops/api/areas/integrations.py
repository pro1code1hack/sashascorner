"""Routes for supplier portal integrations and the browser job queue.

docs/agents/BROWSER-ORDERING.md §7. All behind `ApiAuth`. Every write here queues,
cancels or records; none of them opens a browser (that is the `cafeops browser-worker`
process alone) and none of them touches a purchase order's status: a staged basket ends
at a SUPPLIER_BASKET proposal, and "Mark sent" stays a person's click on the order page
(invariant 1). A refusal from the service is a 409 whose `detail` is the reason in
plain words, so the screen can show it as-is.

`POST /api/orders/{po_id}/stage-basket` lives here rather than in `stock.py` so the
orders area does not import the browser service; it is still an orders route.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from cafeops.api.areas import integrations_views as views
from cafeops.api.areas.integrations_schemas import (
    AutoStageIn,
    BrowserJobDetailOut,
    BrowserJobOut,
    BrowserJobsOut,
    ByIn,
    IntegrationOut,
    IntegrationsOut,
    RequestedByIn,
    SessionImportIn,
)
from cafeops.api.params import parse_before
from cafeops.api.runtime import in_session
from cafeops.api.security import ApiAuth

router = APIRouter(dependencies=[ApiAuth])


# --------------------------------------------------------------------------
# integrations
# --------------------------------------------------------------------------


@router.get(
    "/api/integrations",
    response_model=IntegrationsOut,
    tags=["integrations"],
    summary="Every supplier: its portal adapter, stored sign-in, last job and whether a "
    "basket can be staged now.",
)
async def integrations() -> IntegrationsOut:
    return await in_session(views.integrations_view)


@router.post(
    "/api/integrations/{supplier_id}/session",
    response_model=IntegrationOut,
    tags=["integrations"],
    summary="Import a sign-in made elsewhere (a Playwright storage state, plain or "
    "Fernet-encrypted). Never a password.",
)
async def import_session(supplier_id: int, body: SessionImportIn) -> IntegrationOut:
    return await in_session(
        lambda session: views.session_import_view(session, supplier_id=supplier_id, body=body)
    )


@router.delete(
    "/api/integrations/{supplier_id}/session",
    response_model=IntegrationOut,
    tags=["integrations"],
    summary="Forget the stored sign-in for this supplier.",
)
async def forget_session(supplier_id: int, body: ByIn) -> IntegrationOut:
    return await in_session(
        lambda session: views.session_forget_view(session, supplier_id=supplier_id, by=body.by)
    )


@router.post(
    "/api/integrations/{supplier_id}/check",
    response_model=BrowserJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["integrations"],
    summary="Queue a CHECK_SESSION job: is the stored profile still signed in?",
)
async def check_session(supplier_id: int, body: RequestedByIn) -> BrowserJobOut:
    return await in_session(
        lambda session: views.check_session_view(
            session, supplier_id=supplier_id, requested_by=body.requested_by
        )
    )


@router.post(
    "/api/integrations/{supplier_id}/auto-stage",
    response_model=IntegrationOut,
    tags=["integrations"],
    summary="Let the scheduler queue a basket for this supplier after each pre-delivery run.",
)
async def set_auto_stage(supplier_id: int, body: AutoStageIn) -> IntegrationOut:
    return await in_session(
        lambda session: views.auto_stage_view(
            session, supplier_id=supplier_id, enabled=body.enabled, by=body.by
        )
    )


# --------------------------------------------------------------------------
# orders -> jobs
# --------------------------------------------------------------------------


@router.post(
    "/api/orders/{po_id}/stage-basket",
    response_model=BrowserJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["orders", "integrations"],
    summary="Queue a STAGE_BASKET job for a DRAFT/CONFIRMED order. 409 with the reason "
    "when it cannot be staged. The order's status is not touched.",
)
async def stage_basket(po_id: int, body: RequestedByIn) -> BrowserJobOut:
    return await in_session(
        lambda session: views.stage_basket_view(
            session, po_id=po_id, requested_by=body.requested_by
        )
    )


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


@router.get(
    "/api/browser-jobs",
    response_model=BrowserJobsOut,
    tags=["integrations"],
    summary="Browser jobs, newest first.",
)
async def browser_jobs(
    status_: Annotated[
        str | None,
        Query(
            alias="status",
            max_length=20,
            description="QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED | NEEDS_HUMAN",
        ),
    ] = None,
    supplier_id: Annotated[int | None, Query()] = None,
    po_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    before: Annotated[str | None, Query(description="ISO timestamp; rows older than it.")] = None,
) -> BrowserJobsOut:
    cutoff = parse_before(before)
    return await in_session(
        lambda session: views.jobs_view(
            session,
            status_=status_,
            supplier_id=supplier_id,
            po_id=po_id,
            limit=limit,
            before=cutoff,
        )
    )


@router.get(
    "/api/browser-jobs/{job_id}",
    response_model=BrowserJobDetailOut,
    tags=["integrations"],
    summary="One job with every step it took and the basket snapshot it ended on.",
)
async def browser_job(job_id: int) -> BrowserJobDetailOut:
    return await in_session(lambda session: views.job_detail_view(session, job_id=job_id))


@router.post(
    "/api/browser-jobs/{job_id}/cancel",
    response_model=BrowserJobOut,
    tags=["integrations"],
    summary="Cancel a queued job, or ask a running one to stop. 409 once it is finished.",
)
async def cancel_browser_job(job_id: int, body: ByIn) -> BrowserJobOut:
    return await in_session(
        lambda session: views.cancel_job_view(session, job_id=job_id, by=body.by)
    )


@router.get(
    "/api/browser-jobs/{job_id}/steps/{seq}/screenshot",
    tags=["integrations"],
    summary="The screenshot a step took, as PNG. 404 when the step took none.",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def step_screenshot(job_id: int, seq: int) -> Response:
    data, media_type = await in_session(
        lambda session: views.screenshot_view(session, job_id=job_id, seq=seq)
    )
    return Response(content=data, media_type=media_type or "image/png")
