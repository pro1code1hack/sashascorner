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

### Tier 0 lives here (docs/agents/BROWSER-ORDERING.md §10)

When the supplier's adapter can build the basket as a URL (`portal.cart_link`:
Amazon's add-to-cart form, Shopify's cart permalink) and the link covers every line,
there is nothing for a browser to do: `enqueue_stage_basket` writes the job already
SUCCEEDED, with its one step, its snapshot and its SUPPLIER_BASKET proposal, and the
person opens the link in their own signed-in browser. No worker flag, no stored
session, no bot detection. A link that covers only some lines is stored in
`params["cart_link"]` and the queued job opens it first, then adds the rest.

Services flush; callers commit (the convention in `agent_proposals.py`).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session, selectinload

from cafeops.agent.browser.report import (
    CART_LINK_WARNING,
    lines_from_params,
    summary,
    write_audit,
)
from cafeops.clock import utcnow
from cafeops.config import settings
from cafeops.db.models import (
    BrowserJob,
    BrowserJobKind,
    BrowserJobStatus,
    BrowserJobStep,
    BrowserStepOutcome,
    BrowserStepSource,
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
from cafeops.domain.types import AgentToolOutcome
from cafeops.integrations.suppliers import portals as _portals  # noqa: F401 -- registers adapters
from cafeops.integrations.suppliers.portals.base import (
    BasketSnapshot,
    CartLinkCapable,
    CartLinkPlan,
    QuickOrderCapable,
    SupplierPortal,
    portal_for_supplier,
)
from cafeops.services.actor import require_actor
from cafeops.services.media_store import media_path

__all__ = [
    "BasketJobNotice",
    "BrowserJobRefused",
    "IntegrationStatus",
    "PortalInfo",
    "SessionInfo",
    "basket_job_notice",
    "cancel_job",
    "enqueue_session_check",
    "enqueue_stage_basket",
    "forget_session",
    "get_job",
    "import_session_state",
    "integration_status",
    "integrations_overview",
    "list_jobs",
    "portal_tiers",
    "set_auto_stage",
    "step_screenshot",
]

log = logging.getLogger("cafeops.browser.jobs")

_STAGEABLE = (POStatus.DRAFT, POStatus.CONFIRMED)
_ACTIVE = (BrowserJobStatus.QUEUED, BrowserJobStatus.RUNNING)


class BrowserJobRefused(ValueError):
    """The job cannot be queued; the message is the reason (HTTP 409)."""


def portal_tiers(portal: SupplierPortal | None) -> list[str]:
    """Which rungs of the ladder this adapter offers, best first: "cart_link" when it
    can build the basket as a URL, "quick_order" when it drives a product-code pad,
    and always "browser" (the per-line scripted/model path every adapter has)."""
    if portal is None:
        return []
    tiers: list[str] = []
    if isinstance(portal, CartLinkCapable):
        tiers.append("cart_link")
    if isinstance(portal, QuickOrderCapable):
        tiers.append("quick_order")
    tiers.append("browser")
    return tiers


@dataclass(frozen=True, slots=True)
class PortalInfo:
    slug: str
    label: str
    start_url: str
    tiers: tuple[str, ...] = ("browser",)
    #: The site refuses headless browsers; the worker needs a display for it.
    prefers_headed: bool = False


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
    #: `portal_tiers(portal)`; empty when there is no adapter.
    tiers: tuple[str, ...] = ()


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
    return PortalInfo(
        slug=portal.slug,
        label=portal.label,
        start_url=portal.policy.start_url,
        tiers=tuple(portal_tiers(portal)),
        prefers_headed=bool(getattr(portal, "prefers_headed", False)),
    )


def _status_for(session: Session, supplier: Supplier) -> IntegrationStatus:
    portal = portal_for_supplier(supplier)
    row = _session_row(session, supplier.id)
    info = _session_info(row)
    config = supplier.channel_config if isinstance(supplier.channel_config, dict) else {}
    tiers = portal_tiers(portal)
    reason: str | None = None
    if portal is None:
        reason = "no portal adapter for this supplier"
    elif "cart_link" in tiers:
        # Tier 0 needs neither the worker nor a stored sign-in: the link opens in the
        # owner's own browser. (A link that covers only part of an order still needs
        # the worker; `enqueue_stage_basket` says so for that order.)
        reason = None
    elif not settings.browser_worker_enabled:
        reason = "the browser worker is off (CAFEOPS_BROWSER_WORKER_ENABLED)"
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
        tiers=tuple(tiers),
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


def _cart_link_plan(
    portal: SupplierPortal, params: dict[str, Any], supplier_name: str
) -> CartLinkPlan | None:
    """Ask the adapter for a cart link. An adapter that raises here loses only its
    tier 0: the job falls through to the browser rather than refusing to queue."""
    if not isinstance(portal, CartLinkCapable):
        return None
    try:
        plan = portal.cart_link(lines_from_params(params))
    except Exception:
        log.warning("cart_link failed for %s; falling through", supplier_name, exc_info=True)
        return None
    if plan is None or not isinstance(plan, CartLinkPlan) or not plan.url or not plan.covered:
        return None
    return plan


def _stage_by_cart_link(
    session: Session,
    *,
    po: PurchaseOrder,
    supplier: Supplier,
    portal: SupplierPortal,
    plan: CartLinkPlan,
    params: dict[str, Any],
    requested_by: str,
    via: str,
) -> BrowserJob:
    """Tier 0, done here and now: the job row already SUCCEEDED, one SCRIPT step, the
    snapshot with every line `added` by the link, and the proposal whose accept opens
    the link. Prices are None (unseen), never zero: the basket page shows them."""
    now = utcnow()
    job = BrowserJob(
        created_at=now,
        kind=BrowserJobKind.STAGE_BASKET,
        status=BrowserJobStatus.SUCCEEDED,
        supplier_id=supplier.id,
        purchase_order_id=po.id,
        requested_by=requested_by,
        requested_via=via,
        params=params,
        model=None,
        started_at=now,
        finished_at=now,
        heartbeat_at=now,
        worker_id="inline",
        steps_total=1,
    )
    session.add(job)
    session.flush()
    session.add(
        BrowserJobStep(
            job_id=job.id,
            seq=1,
            at=now,
            source=BrowserStepSource.SCRIPT,
            member="script:cart_link",
            input={
                "covered": list(plan.covered),
                "refs": {str(k): v for k, v in plan.refs.items()},
            },
            outcome=BrowserStepOutcome.OK,
            output=(plan.label or f"cart link, {len(plan.covered)} item(s)")[:1000],
            url=plan.url[:2000],
            duration_ms=0,
        )
    )
    lines = tuple(
        replace(
            line,
            status="added",
            added_by="cart_link",
            packs_in_basket=line.packs_wanted,
            note=plan.refs.get(line.po_line_id, ""),
        )
        for line in lines_from_params(params)
    )
    snapshot = BasketSnapshot(
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        portal_slug=portal.slug,
        po_id=po.id,
        basket_url=plan.url,
        lines=lines,
        subtotal_seen_pence=None,
        total_expected_pence=sum(
            line.packs_wanted * line.unit_price_expected_pence for line in lines
        ),
        captured_at=now,
        warnings=(CART_LINK_WARNING,),
    )
    result = snapshot.as_dict()
    result["tier"] = "cart_link"
    result["tiers_used"] = ["cart_link"]
    job.result = result
    write_audit(
        session,
        job,
        supplier=supplier,
        snapshot=snapshot,
        outcome=AgentToolOutcome.AWAITING_HUMAN,
        summary_text=summary(snapshot, supplier, tier="cart_link"),
        tier="cart_link",
    )
    session.flush()
    return job


def enqueue_stage_basket(
    session: Session, *, po_id: int, requested_by: str, via: str
) -> BrowserJob:
    """Stage an order's basket: by cart link right now when the adapter can build one
    that covers every line (tier 0), else a queued STAGE_BASKET job with the lines
    snapshotted in `params` (and a partial link, when there is one, for the job to
    open first)."""
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
    active = _active_job(session, kind=BrowserJobKind.STAGE_BASKET, po_id=po_id, supplier_id=None)
    if active is not None:
        raise BrowserJobRefused(
            f"job {active.id} is already {active.status.value.lower()} for order {po_id}"
        )
    lines = _order_lines(session, po_id)
    if not lines:
        raise BrowserJobRefused(f"order {po_id} has no lines with a quantity to stage")
    params: dict[str, Any] = {
        "lines": lines,
        "supplier_name": supplier.name,
        "po_status": po.status.value,
        "portal": portal.slug,
    }
    who = requested_by.strip()[:120]
    via = via.strip()[:20] or "web"

    # --- tier 0: a cart link needs no worker and no stored sign-in ------------
    plan = _cart_link_plan(portal, params, supplier.name)
    if plan is not None:
        if plan.complete:
            return _stage_by_cart_link(
                session,
                po=po,
                supplier=supplier,
                portal=portal,
                plan=plan,
                params=params,
                requested_by=who,
                via=via,
            )
        params["cart_link"] = {
            "url": plan.url,
            "covered": list(plan.covered),
            "uncovered": {str(k): v for k, v in plan.uncovered.items()},
            "label": plan.label,
        }

    # --- the browser tiers: the worker and a sign-in ---------------------------
    _require_worker()
    row = _session_row(session, supplier.id)
    if row is None or row.status is not SupplierSessionStatus.CONNECTED:
        state = row.status.value.lower().replace("_", " ") if row else "not connected"
        raise BrowserJobRefused(
            f"{supplier.name}'s sign-in is {state}; connect it "
            "(`cafeops portal connect`) before staging a basket"
        )
    job = BrowserJob(
        created_at=utcnow(),
        kind=BrowserJobKind.STAGE_BASKET,
        status=BrowserJobStatus.QUEUED,
        supplier_id=supplier.id,
        purchase_order_id=po_id,
        requested_by=who,
        requested_via=via,
        params=params,
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
        created_at=utcnow(),
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
    now = utcnow()
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
    now = utcnow()
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


_DIRS_KEY = "browser_profile_dirs_to_remove"


def _remove_after_commit(session: Session, target: Path) -> None:
    """Delete `target` when `session` commits; forget it if the session rolls back."""
    pending: list[Path] = session.info.setdefault(_DIRS_KEY, [])
    pending.append(target)
    if not session.info.get(f"{_DIRS_KEY}_hooked"):
        session.info[f"{_DIRS_KEY}_hooked"] = True
        event.listen(session, "after_commit", _remove_pending_dirs)
        event.listen(session, "after_rollback", _drop_pending_dirs)


def _remove_pending_dirs(session: Session) -> None:
    for target in session.info.pop(_DIRS_KEY, []):
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


def _drop_pending_dirs(session: Session) -> None:
    session.info.pop(_DIRS_KEY, None)


def forget_session(session: Session, *, supplier_id: int, by: str) -> SupplierSession:
    """Drop the sign-in: blob cleared, profile directory removed, NOT_CONNECTED.

    The directory goes only once this transaction COMMITS (`_remove_after_commit`):
    deleted first, a rollback would leave a row saying CONNECTED with the profile it
    points at gone. `by` is required -- both callers (the CLI's `--by`, the API's
    `ByIn.by`) always have a name.
    """
    from cafeops.agent.browser.session import browser_data_dir

    who = require_actor(by, error=BrowserJobRefused)
    row = _session_row(session, supplier_id)
    if row is None:
        supplier = session.get(Supplier, supplier_id)
        if supplier is None:
            raise LookupError(f"supplier {supplier_id} not found")
        row = SupplierSession(supplier_id=supplier_id)
        session.add(row)
    if row.profile_dir:
        _remove_after_commit(session, browser_data_dir() / row.profile_dir)
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


# ==========================================================================
# Telling a person a basket job has finished
# ==========================================================================


@dataclass(frozen=True, slots=True)
class BasketJobNotice:
    """What the owner is told when a STAGE_BASKET job stops. Data only; the bot words it.

    Nothing here says "ordered": a SUCCEEDED job is a filled basket that a person must
    still check out (invariant 1), and the order stays CONFIRMED until they mark it sent.
    """

    job_id: int
    po_id: int | None
    supplier_name: str
    #: SUCCEEDED (basket ready), NEEDS_HUMAN (stopped for a person) or FAILED.
    status: BrowserJobStatus
    basket_url: str | None
    lines_wanted: int
    #: Lines whose basket quantity is missing or below what the order wants.
    lines_short: int
    subtotal_seen_pence: int | None
    total_expected_pence: int | None
    warnings: int
    #: `needs_human_reason` or `error`, verbatim (English, operator-facing).
    reason: str | None


def basket_job_notice(session: Session, *, job_id: int) -> BasketJobNotice | None:
    """The notice for a finished STAGE_BASKET job, or None when there is nothing to say
    (another kind of job, still running, or cancelled by the person who would be told)."""
    job = session.get(BrowserJob, job_id)
    if job is None or job.kind is not BrowserJobKind.STAGE_BASKET:
        return None
    if job.status not in (
        BrowserJobStatus.SUCCEEDED,
        BrowserJobStatus.NEEDS_HUMAN,
        BrowserJobStatus.FAILED,
    ):
        return None
    result = job.result if isinstance(job.result, dict) else {}
    lines = [row for row in result.get("lines") or [] if isinstance(row, dict)]
    short = 0
    for row in lines:
        wanted = row.get("packs_wanted") or 0
        have = row.get("packs_in_basket")
        if have is None or have < wanted:
            short += 1
    supplier = session.get(Supplier, job.supplier_id)

    def _int(key: str) -> int | None:
        value = result.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    url = result.get("basket_url")
    return BasketJobNotice(
        job_id=job.id,
        po_id=job.purchase_order_id,
        supplier_name=supplier.name if supplier is not None else f"supplier {job.supplier_id}",
        status=job.status,
        basket_url=str(url) if url else None,
        lines_wanted=len(lines) or len(job.params.get("lines") or []),
        lines_short=short,
        subtotal_seen_pence=_int("subtotal_seen_pence"),
        total_expected_pence=_int("total_expected_pence"),
        warnings=len(result.get("warnings") or []),
        reason=job.needs_human_reason or job.error,
    )
