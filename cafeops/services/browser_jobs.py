"""Browser ordering jobs: the queue's front door, and what the API shows about it.

docs/agents/BROWSER-ORDERING.md §4 step 1 and §7. Everything that is NOT running a
browser lives here: queueing a STAGE_BASKET or CHECK_SESSION job, cancelling one,
listing them, the per-supplier integration status the Integrations screen renders,
and moving a sign-in (an encrypted Playwright storage state) in and out of
`supplier_session`. The worker (`cafeops/agent/browser/worker.py`) is the only
consumer of the queue and the only thing that opens a browser.

Refusals are `BrowserJobRefused` with the reason as the message (HTTP 409 at the
API). A job is refused rather than queued-and-failed when it cannot possibly run:
the worker is off, the supplier has no portal adapter, the session is not
CONNECTED, the order is not DRAFT/CONFIRMED, or a job for that order is already
active. Nothing here changes a purchase order (invariant 1: "Mark sent" stays a
person's click, and staging a basket does not send anything).

Services flush; callers commit (the convention in `agent_proposals.py`).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from cafeops.config import settings
from cafeops.db.models import (
    BrowserJob,
    BrowserJobKind,
    BrowserJobStatus,
    BrowserJobStep,
    Ingredient,
    MediaAsset,
    POLine,
    POStatus,
    PurchaseOrder,
    Supplier,
    SupplierProduct,
    SupplierSession,
    SupplierSessionStatus,
)
from cafeops.integrations.suppliers import portals as _portals  # noqa: F401 -- registers adapters
from cafeops.integrations.suppliers.portals.base import SupplierPortal, portal_for_supplier
from cafeops.services.media_store import media_path

__all__ = [
    "BrowserJobRefused",
    "IntegrationStatus",
    "PortalInfo",
    "SessionInfo",
    "cancel_job",
    "enqueue_session_check",
    "enqueue_stage_basket",
    "forget_session",
    "get_job",
    "import_session_state",
    "integration_status",
    "integrations_overview",
    "list_jobs",
    "set_auto_stage",
    "step_screenshot",
]

_STAGEABLE = (POStatus.DRAFT, POStatus.CONFIRMED)
_ACTIVE = (BrowserJobStatus.QUEUED, BrowserJobStatus.RUNNING)


class BrowserJobRefused(ValueError):
    """The job cannot be queued; the message is the reason (HTTP 409)."""


@dataclass(frozen=True, slots=True)
class PortalInfo:
    slug: str
    label: str
    start_url: str


@dataclass(frozen=True, slots=True)
class SessionInfo:
    status: SupplierSessionStatus
    account_label: str | None
    connected_at: datetime | None
    connected_by: str | None
    last_ok_at: datetime | None
    last_checked_at: datetime | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    supplier_id: int
    supplier_name: str
    portal: PortalInfo | None
    session: SessionInfo
    auto_stage: bool
    worker_enabled: bool
    last_job: BrowserJob | None
    can_stage: bool
    cannot_stage_reason: str | None


def _now() -> datetime:
    return datetime.now(UTC)


# ==========================================================================
# Reads
# ==========================================================================


def _session_row(session: Session, supplier_id: int) -> SupplierSession | None:
    return session.scalar(select(SupplierSession).where(SupplierSession.supplier_id == supplier_id))


def _session_info(row: SupplierSession | None) -> SessionInfo:
    if row is None:
        return SessionInfo(SupplierSessionStatus.NOT_CONNECTED, None, None, None, None, None, None)
    return SessionInfo(
        status=row.status,
        account_label=row.account_label,
        connected_at=row.connected_at,
        connected_by=row.connected_by,
        last_ok_at=row.last_ok_at,
        last_checked_at=row.last_checked_at,
        last_error=row.last_error,
    )


def _last_job(session: Session, supplier_id: int) -> BrowserJob | None:
    return session.scalar(
        select(BrowserJob)
        .where(BrowserJob.supplier_id == supplier_id)
        .order_by(BrowserJob.created_at.desc(), BrowserJob.id.desc())
        .limit(1)
    )


def _portal_info(portal: SupplierPortal | None) -> PortalInfo | None:
    if portal is None:
        return None
    return PortalInfo(slug=portal.slug, label=portal.label, start_url=portal.policy.start_url)


def _status_for(session: Session, supplier: Supplier) -> IntegrationStatus:
    portal = portal_for_supplier(supplier)
    row = _session_row(session, supplier.id)
    info = _session_info(row)
    config = supplier.channel_config if isinstance(supplier.channel_config, dict) else {}
    reason: str | None = None
    if not settings.browser_worker_enabled:
        reason = "the browser worker is off (CAFEOPS_BROWSER_WORKER_ENABLED)"
    elif portal is None:
        reason = "no portal adapter for this supplier"
    elif info.status is not SupplierSessionStatus.CONNECTED:
        reason = f"sign-in is {info.status.value.lower().replace('_', ' ')}; connect it first"
    return IntegrationStatus(
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        portal=_portal_info(portal),
        session=info,
        auto_stage=bool(config.get("auto_stage")),
        worker_enabled=settings.browser_worker_enabled,
        last_job=_last_job(session, supplier.id),
        can_stage=reason is None,
        cannot_stage_reason=reason,
    )


def integrations_overview(session: Session) -> list[IntegrationStatus]:
    """Every non-archived supplier, the ones with a portal adapter first."""
    suppliers = list(
        session.scalars(
            select(Supplier).where(Supplier.archived_at.is_(None)).order_by(Supplier.name)
        )
    )
    statuses = [_status_for(session, s) for s in suppliers]
    statuses.sort(key=lambda s: (s.portal is None, s.supplier_name.lower()))
    return statuses


def integration_status(session: Session, *, supplier_id: int) -> IntegrationStatus:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise LookupError(f"supplier {supplier_id} not found")
    return _status_for(session, supplier)


def list_jobs(
    session: Session,
    *,
    status: BrowserJobStatus | None = None,
    supplier_id: int | None = None,
    po_id: int | None = None,
    limit: int = 30,
    before: datetime | None = None,
) -> tuple[list[BrowserJob], bool]:
    """Newest first, with a has_more flag for the page after `before`."""
    limit = max(1, min(int(limit), 200))
    stmt = select(BrowserJob)
    if status is not None:
        stmt = stmt.where(BrowserJob.status == status)
    if supplier_id is not None:
        stmt = stmt.where(BrowserJob.supplier_id == supplier_id)
    if po_id is not None:
        stmt = stmt.where(BrowserJob.purchase_order_id == po_id)
    if before is not None:
        stmt = stmt.where(BrowserJob.created_at < before)
    stmt = stmt.order_by(BrowserJob.created_at.desc(), BrowserJob.id.desc()).limit(limit + 1)
    rows = list(session.scalars(stmt))
    return rows[:limit], len(rows) > limit


def get_job(session: Session, *, job_id: int) -> BrowserJob:
    job = session.scalar(
        select(BrowserJob).where(BrowserJob.id == job_id).options(selectinload(BrowserJob.steps))
    )
    if job is None:
        raise LookupError(f"browser job {job_id} not found")
    return job


def step_screenshot(session: Session, *, job_id: int, seq: int) -> tuple[bytes, str] | None:
    """(bytes, content type) of a step's screenshot, or None when there is none."""
    step = session.scalar(
        select(BrowserJobStep).where(BrowserJobStep.job_id == job_id, BrowserJobStep.seq == seq)
    )
    if step is None or step.screenshot_asset_id is None:
        return None
    asset = session.get(MediaAsset, step.screenshot_asset_id)
    if asset is None:
        return None
    path = media_path(asset.filename)
    if not path.exists():
        return None
    return path.read_bytes(), asset.content_type


# ==========================================================================
# Queueing
# ==========================================================================


def _active_job(
    session: Session, *, kind: BrowserJobKind, po_id: int | None = None, supplier_id: int | None
) -> BrowserJob | None:
    stmt = select(BrowserJob).where(BrowserJob.kind == kind, BrowserJob.status.in_(list(_ACTIVE)))
    if po_id is not None:
        stmt = stmt.where(BrowserJob.purchase_order_id == po_id)
    if supplier_id is not None:
        stmt = stmt.where(BrowserJob.supplier_id == supplier_id)
    return session.scalar(stmt.order_by(BrowserJob.id).limit(1))


def _require_worker() -> None:
    if not settings.browser_worker_enabled:
        raise BrowserJobRefused(
            "the browser worker is off: set CAFEOPS_BROWSER_WORKER_ENABLED=true and run "
            "`cafeops browser-worker`"
        )


def _require_portal(supplier: Supplier) -> SupplierPortal:
    portal = portal_for_supplier(supplier)
    if portal is None:
        raise BrowserJobRefused(
            f"{supplier.name} has no portal adapter; set channel_config.portal to one of the "
            "registered slugs or add an adapter under integrations/suppliers/portals/"
        )
    return portal


def _order_lines(session: Session, po_id: int) -> list[dict[str, Any]]:
    stmt = (
        select(
            POLine.id.label("po_line_id"),
            Ingredient.name.label("ingredient_name"),
            SupplierProduct.sku.label("sku"),
            SupplierProduct.product_url.label("product_url"),
            POLine.final_packs.label("final_packs"),
            POLine.suggested_packs.label("suggested_packs"),
            POLine.unit_price_pence.label("unit_price_pence"),
        )
        .join(Ingredient, Ingredient.id == POLine.ingredient_id)
        .join(SupplierProduct, SupplierProduct.id == POLine.supplier_product_id)
        .where(POLine.po_id == po_id)
        .order_by(POLine.id)
    )
    lines: list[dict[str, Any]] = []
    for row in session.execute(stmt):
        # `final_packs` is the human's number and starts equal to the suggestion; a
        # line a person zeroed is not staged.
        packs = int(row.final_packs)
        if packs <= 0:
            continue
        lines.append(
            {
                "po_line_id": int(row.po_line_id),
                "ingredient_name": str(row.ingredient_name),
                "sku": str(row.sku or ""),
                "product_url": row.product_url,
                "packs": packs,
                "unit_price_pence": int(row.unit_price_pence),
            }
        )
    return lines


def enqueue_stage_basket(
    session: Session, *, po_id: int, requested_by: str, via: str
) -> BrowserJob:
    """Queue a STAGE_BASKET job for an order, with its lines snapshotted in `params`."""
    _require_worker()
    if not requested_by.strip():
        raise BrowserJobRefused("requested_by is required: every job has a requester")
    po = session.get(PurchaseOrder, po_id)
    if po is None:
        raise BrowserJobRefused(f"purchase order {po_id} does not exist")
    if po.status not in _STAGEABLE:
        raise BrowserJobRefused(
            f"order {po_id} is {po.status.value.lower().replace('_', ' ')}; only a draft or "
            "confirmed order can be staged"
        )
    supplier = po.supplier
    portal = _require_portal(supplier)
    row = _session_row(session, supplier.id)
    if row is None or row.status is not SupplierSessionStatus.CONNECTED:
        state = row.status.value.lower().replace("_", " ") if row else "not connected"
        raise BrowserJobRefused(
            f"{supplier.name}'s sign-in is {state}; connect it "
            "(`cafeops portal connect`) before staging a basket"
        )
    active = _active_job(session, kind=BrowserJobKind.STAGE_BASKET, po_id=po_id, supplier_id=None)
    if active is not None:
        raise BrowserJobRefused(
            f"job {active.id} is already {active.status.value.lower()} for order {po_id}"
        )
    lines = _order_lines(session, po_id)
    if not lines:
        raise BrowserJobRefused(f"order {po_id} has no lines with a quantity to stage")
    job = BrowserJob(
        created_at=_now(),
        kind=BrowserJobKind.STAGE_BASKET,
        status=BrowserJobStatus.QUEUED,
        supplier_id=supplier.id,
        purchase_order_id=po_id,
        requested_by=requested_by.strip()[:120],
        requested_via=via.strip()[:20] or "web",
        params={
            "lines": lines,
            "supplier_name": supplier.name,
            "po_status": po.status.value,
            "portal": portal.slug,
        },
        model=settings.browser_model,
    )
    session.add(job)
    session.flush()
    return job


def enqueue_session_check(
    session: Session, *, supplier_id: int, requested_by: str, via: str
) -> BrowserJob:
    """Queue a CHECK_SESSION job: open the stored profile and report signed-in or not."""
    _require_worker()
    if not requested_by.strip():
        raise BrowserJobRefused("requested_by is required: every job has a requester")
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise BrowserJobRefused(f"supplier {supplier_id} does not exist")
    portal = _require_portal(supplier)
    row = _session_row(session, supplier_id)
    if row is None or row.status is SupplierSessionStatus.NOT_CONNECTED:
        raise BrowserJobRefused(
            f"{supplier.name} has never been connected; there is no sign-in to check"
        )
    active = _active_job(
        session, kind=BrowserJobKind.CHECK_SESSION, po_id=None, supplier_id=supplier_id
    )
    if active is not None:
        raise BrowserJobRefused(
            f"job {active.id} is already {active.status.value.lower()} for {supplier.name}"
        )
    job = BrowserJob(
        created_at=_now(),
        kind=BrowserJobKind.CHECK_SESSION,
        status=BrowserJobStatus.QUEUED,
        supplier_id=supplier_id,
        requested_by=requested_by.strip()[:120],
        requested_via=via.strip()[:20] or "web",
        params={"supplier_name": supplier.name, "portal": portal.slug},
        model=settings.browser_model,
    )
    session.add(job)
    session.flush()
    return job


def cancel_job(session: Session, *, job_id: int, by: str) -> BrowserJob:
    """QUEUED: cancelled now. RUNNING: asked to stop at its next step. Else refused."""
    job = session.get(BrowserJob, job_id)
    if job is None:
        raise LookupError(f"browser job {job_id} not found")
    who = by.strip()[:120] or "unknown"
    now = _now()
    if job.status is BrowserJobStatus.QUEUED:
        job.status = BrowserJobStatus.CANCELLED
        job.cancel_requested_at = now
        job.cancel_requested_by = who
        job.finished_at = now
        job.error = f"cancelled by {who} before it started"
    elif job.status is BrowserJobStatus.RUNNING:
        if job.cancel_requested_at is None:
            job.cancel_requested_at = now
            job.cancel_requested_by = who
    else:
        raise BrowserJobRefused(f"job {job_id} is already {job.status.value.lower()}")
    session.flush()
    return job


# ==========================================================================
# Sessions (sign-ins)
# ==========================================================================


def import_session_state(
    session: Session,
    *,
    supplier_id: int,
    storage_state: dict[str, Any] | None,
    storage_state_enc: bytes | None,
    account_label: str | None,
    connected_by: str,
) -> SupplierSession:
    """Record a sign-in made elsewhere: a plain storage state (encrypted here) or an
    already-encrypted blob (from `cafeops portal export-session`). CONNECTED."""
    from cafeops.agent.browser.session import decrypt_state, encrypt_state

    if not connected_by.strip():
        raise BrowserJobRefused("connected_by is required: a sign-in is somebody's")
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise BrowserJobRefused(f"supplier {supplier_id} does not exist")
    _require_portal(supplier)
    blob: bytes | None
    if storage_state is not None:
        blob = encrypt_state(storage_state)
    elif storage_state_enc:
        blob = bytes(storage_state_enc)
        if settings.browser_session_key:
            decrypt_state(blob)  # refuse a blob this key cannot open
    else:
        blob = None
    row = _session_row(session, supplier_id)
    if row is None:
        row = SupplierSession(supplier_id=supplier_id)
        session.add(row)
    now = _now()
    row.status = SupplierSessionStatus.CONNECTED
    row.connected_at = now
    row.connected_by = connected_by.strip()[:120]
    row.account_label = (account_label or "").strip()[:200] or row.account_label
    if blob is not None:
        row.storage_state_enc = blob
    row.last_error = None
    row.last_ok_at = now
    row.last_checked_at = now
    session.flush()
    return row


def forget_session(session: Session, *, supplier_id: int, by: str) -> SupplierSession:
    """Drop the sign-in: blob cleared, profile directory removed, NOT_CONNECTED."""
    from cafeops.agent.browser.session import browser_data_dir

    row = _session_row(session, supplier_id)
    if row is None:
        supplier = session.get(Supplier, supplier_id)
        if supplier is None:
            raise LookupError(f"supplier {supplier_id} not found")
        row = SupplierSession(supplier_id=supplier_id)
        session.add(row)
    who = by.strip()[:120] or "unknown"
    if row.profile_dir:
        target = browser_data_dir() / row.profile_dir
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    row.status = SupplierSessionStatus.NOT_CONNECTED
    row.storage_state_enc = None
    row.account_label = None
    row.connected_at = None
    row.connected_by = None
    row.profile_dir = None
    row.last_error = f"forgotten by {who}"
    session.flush()
    return row


def set_auto_stage(
    session: Session, *, supplier_id: int, enabled: bool, by: str
) -> IntegrationStatus:
    """Toggle `channel_config.auto_stage`; the scheduler queues a job after each
    pre-delivery run for suppliers with it on (and a CONNECTED session)."""
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise LookupError(f"supplier {supplier_id} not found")
    if enabled:
        _require_portal(supplier)
    config = dict(supplier.channel_config) if isinstance(supplier.channel_config, dict) else {}
    config["auto_stage"] = bool(enabled)
    config["auto_stage_by"] = by.strip()[:120] or "unknown"
    if not config.get("portal"):
        portal = portal_for_supplier(supplier)
        if portal is not None:
            config["portal"] = portal.slug
    supplier.channel_config = config  # a new dict, so SQLAlchemy sees the change
    session.flush()
    return _status_for(session, supplier)
