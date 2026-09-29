"""Views for supplier portal integrations and browser jobs: session in, response out.

Same rule as the other areas: build the Pydantic model inside the worker thread,
because the session closes when the view returns (api/runtime.py). No rule about who
may stage what lives here -- `services/browser_jobs.py` owns every refusal and this
module only translates them: `BrowserJobRefused` -> 409 with the service's own reason
as `detail`, `LookupError` -> 404. A refusal writes nothing, so raising through
`session_scope` (which rolls back) loses nothing.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, cast

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.api.areas.integrations_schemas import (
    BrowserJobDetailOut,
    BrowserJobOut,
    BrowserJobsOut,
    BrowserStepOut,
    IntegrationOut,
    IntegrationsOut,
    PortalOut,
    SessionImportIn,
    SessionOut,
)
from cafeops.config import settings
from cafeops.db.models import BrowserJob, BrowserJobKind, BrowserJobStep, Supplier
from cafeops.services import browser_jobs
from cafeops.services.browser_jobs import BrowserJobRefused, IntegrationStatus

# --------------------------------------------------------------------------
# mapping
# --------------------------------------------------------------------------


def _pounds(pence: int) -> str:
    """Integer pence -> "£48.20". Text only; every numeric field stays pence."""
    sign = "-" if pence < 0 else ""
    pence = abs(pence)
    return f"{sign}£{pence // 100}.{pence % 100:02d}"


def result_summary(job: BrowserJob) -> str | None:
    """One sentence from `job.result`, so a list row can say what happened without
    the frontend re-deriving it from the snapshot. None when there is no result yet."""
    result = job.result
    if not isinstance(result, dict):
        return None
    if job.kind is BrowserJobKind.CHECK_SESSION:
        if not result.get("signed_in"):
            return "not signed in"
        label = result.get("account_label")
        return f"signed in as {label}" if label else "signed in"
    if job.kind is BrowserJobKind.STAGE_BASKET:
        lines = result.get("lines")
        lines = lines if isinstance(lines, list) else []
        in_basket = sum(
            1
            for line in lines
            if isinstance(line, dict) and line.get("status") in ("added", "already")
        )
        parts = [f"{in_basket} of {len(lines)} line{'' if len(lines) == 1 else 's'} in basket"]
        seen = result.get("subtotal_seen_pence")
        expected = result.get("total_expected_pence")
        if isinstance(seen, int) and not isinstance(seen, bool):
            money = f"{_pounds(seen)} seen"
            if isinstance(expected, int) and not isinstance(expected, bool):
                money += f" (expected {_pounds(expected)})"
            parts.append(money)
        elif isinstance(expected, int) and not isinstance(expected, bool):
            parts.append(f"subtotal not read (expected {_pounds(expected)})")
        return ", ".join(parts)
    return None


def _supplier_names(session: Session, jobs: list[BrowserJob]) -> dict[int, str]:
    """`browser_job` carries only `supplier_id`; one query names them all."""
    ids = {job.supplier_id for job in jobs}
    if not ids:
        return {}
    rows = session.execute(select(Supplier.id, Supplier.name).where(Supplier.id.in_(ids))).all()
    return {int(sid): str(name) for sid, name in rows}


def job_out(job: BrowserJob, *, supplier_name: str | None) -> BrowserJobOut:
    return BrowserJobOut(
        id=job.id,
        kind=cast(Any, job.kind.value),
        status=cast(Any, job.status.value),
        supplier_id=job.supplier_id,
        supplier_name=supplier_name or f"supplier {job.supplier_id}",
        purchase_order_id=job.purchase_order_id,
        requested_by=job.requested_by,
        requested_via=job.requested_via,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        heartbeat_at=job.heartbeat_at,
        error=job.error,
        needs_human_reason=job.needs_human_reason,
        model=job.model,
        steps_total=job.steps_total,
        model_calls=job.model_calls,
        input_tokens=job.input_tokens,
        output_tokens=job.output_tokens,
        proposal_id=job.proposal_id,
        run_id=job.run_id,
        result_summary=result_summary(job),
    )


def _step_out(job_id: int, step: BrowserJobStep) -> BrowserStepOut:
    has_shot = step.screenshot_asset_id is not None
    return BrowserStepOut(
        seq=step.seq,
        at=step.at,
        source=cast(Any, step.source.value),
        member=step.member,
        input=dict(step.input or {}),
        outcome=cast(Any, step.outcome.value),
        output=step.output,
        refusal_reason=step.refusal_reason,
        url=step.url,
        duration_ms=step.duration_ms,
        has_screenshot=has_shot,
        screenshot_url=(
            f"/api/browser-jobs/{job_id}/steps/{step.seq}/screenshot" if has_shot else None
        ),
    )


def job_detail_out(job: BrowserJob, *, supplier_name: str | None) -> BrowserJobDetailOut:
    base = job_out(job, supplier_name=supplier_name)
    return BrowserJobDetailOut(
        **base.model_dump(),
        steps=tuple(_step_out(job.id, step) for step in job.steps),
        snapshot=dict(job.result) if isinstance(job.result, dict) else None,
    )


def _portal_out(portal: Any) -> PortalOut | None:
    """Accepts the service's flat `PortalInfo` and a registered adapter, whose start
    URL sits on its `PortalPolicy`."""
    if portal is None:
        return None
    start_url = getattr(portal, "start_url", None)
    if start_url is None:
        start_url = portal.policy.start_url
    return PortalOut(slug=portal.slug, label=portal.label, start_url=str(start_url))


def integration_out(status_: IntegrationStatus) -> IntegrationOut:
    s = status_.session
    return IntegrationOut(
        supplier_id=status_.supplier_id,
        supplier_name=status_.supplier_name,
        portal=_portal_out(status_.portal),
        session=SessionOut(
            status=cast(Any, s.status.value),
            account_label=s.account_label,
            connected_at=s.connected_at,
            connected_by=s.connected_by,
            last_ok_at=s.last_ok_at,
            last_checked_at=s.last_checked_at,
            last_error=s.last_error,
        ),
        auto_stage=status_.auto_stage,
        can_stage=status_.can_stage,
        cannot_stage_reason=status_.cannot_stage_reason,
        last_job=(
            None
            if status_.last_job is None
            else job_out(status_.last_job, supplier_name=status_.supplier_name)
        ),
    )


def _registered_portals() -> tuple[PortalOut, ...]:
    """Every adapter the portals package registers on import. An empty tuple is a
    legitimate answer while the adapters are still being written."""
    from cafeops.integrations.suppliers.portals import REGISTRY

    out: list[PortalOut] = []
    for portal in REGISTRY.all():
        made = _portal_out(portal)
        if made is not None:
            out.append(made)
    return tuple(out)


def _refused(exc: BrowserJobRefused) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# --------------------------------------------------------------------------
# integrations
# --------------------------------------------------------------------------


def integrations_view(session: Session) -> IntegrationsOut:
    rows = browser_jobs.integrations_overview(session)
    return IntegrationsOut(
        suppliers=tuple(integration_out(row) for row in rows),
        worker_enabled=bool(settings.browser_worker_enabled),
        portals=_registered_portals(),
    )


def session_import_view(
    session: Session, *, supplier_id: int, body: SessionImportIn
) -> IntegrationOut:
    if (body.storage_state is None) == (body.storage_state_enc_b64 is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send exactly one of storage_state or storage_state_enc_b64.",
        )
    enc: bytes | None = None
    if body.storage_state_enc_b64 is not None:
        try:
            enc = base64.b64decode(body.storage_state_enc_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"storage_state_enc_b64 is not valid base64: {exc}",
            ) from exc
    try:
        browser_jobs.import_session_state(
            session,
            supplier_id=supplier_id,
            storage_state=body.storage_state,
            storage_state_enc=enc,
            account_label=body.account_label,
            connected_by=body.connected_by,
        )
    except BrowserJobRefused as exc:
        raise _refused(exc) from exc
    session.flush()
    return integration_out(browser_jobs.integration_status(session, supplier_id=supplier_id))


def session_forget_view(session: Session, *, supplier_id: int, by: str) -> IntegrationOut:
    try:
        browser_jobs.forget_session(session, supplier_id=supplier_id, by=by)
    except BrowserJobRefused as exc:
        raise _refused(exc) from exc
    session.flush()
    return integration_out(browser_jobs.integration_status(session, supplier_id=supplier_id))


def check_session_view(session: Session, *, supplier_id: int, requested_by: str) -> BrowserJobOut:
    try:
        job = browser_jobs.enqueue_session_check(
            session, supplier_id=supplier_id, requested_by=requested_by, via="web"
        )
    except BrowserJobRefused as exc:
        raise _refused(exc) from exc
    session.flush()
    return job_out(job, supplier_name=_supplier_names(session, [job]).get(job.supplier_id))


def auto_stage_view(
    session: Session, *, supplier_id: int, enabled: bool, by: str
) -> IntegrationOut:
    try:
        status_ = browser_jobs.set_auto_stage(
            session, supplier_id=supplier_id, enabled=enabled, by=by
        )
    except BrowserJobRefused as exc:
        raise _refused(exc) from exc
    session.flush()
    return integration_out(status_)


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


def stage_basket_view(session: Session, *, po_id: int, requested_by: str) -> BrowserJobOut:
    try:
        job = browser_jobs.enqueue_stage_basket(
            session, po_id=po_id, requested_by=requested_by, via="web"
        )
    except BrowserJobRefused as exc:
        raise _refused(exc) from exc
    session.flush()
    return job_out(job, supplier_name=_supplier_names(session, [job]).get(job.supplier_id))


def jobs_view(
    session: Session,
    *,
    status_: str | None,
    supplier_id: int | None,
    po_id: int | None,
    limit: int,
    before: Any,
) -> BrowserJobsOut:
    jobs, has_more = browser_jobs.list_jobs(
        session, status=status_, supplier_id=supplier_id, po_id=po_id, limit=limit, before=before
    )
    names = _supplier_names(session, list(jobs))
    return BrowserJobsOut(
        jobs=tuple(job_out(job, supplier_name=names.get(job.supplier_id)) for job in jobs),
        has_more=has_more,
    )


def job_detail_view(session: Session, *, job_id: int) -> BrowserJobDetailOut:
    job = browser_jobs.get_job(session, job_id=job_id)
    return job_detail_out(job, supplier_name=_supplier_names(session, [job]).get(job.supplier_id))


def cancel_job_view(session: Session, *, job_id: int, by: str) -> BrowserJobOut:
    try:
        job = browser_jobs.cancel_job(session, job_id=job_id, by=by)
    except BrowserJobRefused as exc:
        raise _refused(exc) from exc
    session.flush()
    return job_out(job, supplier_name=_supplier_names(session, [job]).get(job.supplier_id))


def screenshot_view(session: Session, *, job_id: int, seq: int) -> tuple[bytes, str]:
    found = browser_jobs.step_screenshot(session, job_id=job_id, seq=seq)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"job {job_id} step {seq} has no screenshot",
        )
    return found
