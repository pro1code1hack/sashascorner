"""One browser job, end to end: scripted steps, model fallback, snapshot, audit.

docs/agents/BROWSER-ORDERING.md §4 steps 3-6. `run_stage_basket` takes a claimed
RUNNING `browser_job` and leaves it SUCCEEDED (a basket was staged, possibly with
warnings), NEEDS_HUMAN (the portal wants a sign-in, a code or a CAPTCHA), CANCELLED
(a person asked) or FAILED (nothing could be read at all; an escaped exception is the
worker's FAILED). The purchase order is never touched.

### Where the audit rows are written, and why on this session

The narration agent writes `agent_action_log` and `agent_proposal` through
`agent/runner.py`'s audit engine, whose connection may INSERT into exactly those two
tables and nothing else -- the device that makes the model's writes unrepresentable.
This module does not run inside that engine, and deliberately so: the worker is a
*service process* that must also write `browser_job`, `browser_job_step` and
`media_asset`, which the audit engine would refuse. What the engine protects against
-- a model choosing to write -- cannot happen here, because the model never holds a
session: the only thing it can do is ask for browser toolset members, and those go
through `PolicyGuard` and the executor. So the two audit rows are written on the
worker's ordinary session with the same helpers the engine uses
(`SqlAgentLogRepository.log`, `insert_proposal`), INSERT only, in the same commit as
the job's result, so a proposal never exists without the job and log row that made
it. Invariant 10 holds by construction: nothing here imports a stock, order or
composition service.

Every `StepRecord` becomes a `browser_job_step` row as it happens (with its screenshot
in `media_asset`), the job's heartbeat is touched on every step, and a cancel request
is honoured at the next step boundary.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.agent.browser.loop import basket_line_task, read_basket_task, run_model_task
from cafeops.agent.browser.policy import PolicyGuard
from cafeops.agent.browser.session import check_signed_in, open_supplier_browser
from cafeops.agent.browser.types import BrowserExecutor, Budget, StepRecord
from cafeops.config import settings
from cafeops.db.models import (
    AgentActionLog,
    BrowserJob,
    BrowserJobStatus,
    BrowserJobStep,
    BrowserStepOutcome,
    BrowserStepSource,
    Supplier,
    SupplierSession,
    SupplierSessionStatus,
)
from cafeops.db.repositories.agent_log import SqlAgentLogRepository
from cafeops.domain.types import AgentProposal, AgentToolOutcome
from cafeops.integrations.suppliers import portals as _portals  # noqa: F401 -- registers adapters
from cafeops.integrations.suppliers.portals.base import (
    REGISTRY,
    BasketLine,
    BasketSnapshot,
    PortalNeedsHuman,
    PortalPolicyRefusal,
    PortalStepFailed,
    SupplierPortal,
    portal_for_supplier,
)
from cafeops.services.agent_proposals import insert_proposal
from cafeops.services.media_store import MediaRefusedError, store_image

log = logging.getLogger("cafeops.browser")

AGENT_NAME = "basket_stager"
TOOL_NAME = "browser_stage_supplier_basket"
#: A price seen this far from the order's expectation is worth a warning.
PRICE_TOLERANCE = 0.05


def _utcnow() -> datetime:
    return datetime.now(UTC)


class JobCancelled(Exception):
    """Raised from the heartbeat when `cancel_requested_at` is set."""


class _StopNeedsHuman(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ==========================================================================
# Step recording and heartbeat
# ==========================================================================


def default_heartbeat(session: Session, job: BrowserJob) -> None:
    """Touch `heartbeat_at`, commit, and re-read the cancel columns (another process
    sets them). Short: one UPDATE."""
    job.heartbeat_at = _utcnow()
    session.commit()
    session.refresh(job, attribute_names=["cancel_requested_at", "cancel_requested_by"])


class StepRecorder:
    """Writes `browser_job_step` rows in sequence and drives the heartbeat."""

    def __init__(
        self,
        session: Session,
        job: BrowserJob,
        *,
        heartbeat: Callable[[BrowserJob], None] | None = None,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.session = session
        self.job = job
        self.now = now
        self._heartbeat = heartbeat or (lambda j: default_heartbeat(session, j))
        last = session.scalar(
            select(BrowserJobStep.seq)
            .where(BrowserJobStep.job_id == job.id)
            .order_by(BrowserJobStep.seq.desc())
            .limit(1)
        )
        self.seq = int(last or 0)
        self.counts: dict[str, int] = {}

    def record(self, step: StepRecord) -> BrowserJobStep:
        self.seq += 1
        asset_id: int | None = None
        if step.screenshot_png:
            try:
                asset_id = store_image(
                    self.session, step.screenshot_png, uploaded_by="browser-agent"
                ).asset_id
            except MediaRefusedError as exc:
                log.warning("job %s step %s: screenshot not stored: %s", self.job.id, self.seq, exc)
        row = BrowserJobStep(
            job_id=self.job.id,
            seq=self.seq,
            at=step.at or self.now(),
            source=BrowserStepSource(step.source),
            member=step.member[:60],
            input=step.input,
            outcome=BrowserStepOutcome(step.outcome),
            output=(step.output or None) and str(step.output)[:1000],
            refusal_reason=(step.refusal_reason or None) and str(step.refusal_reason)[:400],
            url=(step.url or None) and str(step.url)[:2000],
            duration_ms=step.duration_ms,
            screenshot_asset_id=asset_id,
        )
        self.session.add(row)
        self.job.steps_total = self.seq
        self.counts[step.source] = self.counts.get(step.source, 0) + 1
        self.session.flush()
        self.pulse()
        return row

    def pulse(self) -> None:
        """Heartbeat, and stop if a person asked to."""
        self._heartbeat(self.job)
        if self.job.cancel_requested_at is not None:
            raise JobCancelled(f"cancelled by {self.job.cancel_requested_by or 'unknown'}")

    def script(
        self,
        member: str,
        *,
        input: dict[str, Any] | None = None,
        outcome: str = "OK",
        output: str | None = None,
        url: str | None = None,
        started: datetime | None = None,
        screenshot_png: bytes | None = None,
        source: str = "SCRIPT",
        refusal_reason: str | None = None,
    ) -> BrowserJobStep:
        now = self.now()
        duration = None if started is None else int((now - started).total_seconds() * 1000)
        return self.record(
            StepRecord(
                source=source,
                member=member,
                input=input or {},
                outcome=outcome,
                output=output,
                refusal_reason=refusal_reason,
                url=url,
                duration_ms=duration,
                screenshot_png=screenshot_png,
                at=started or now,
            )
        )


# ==========================================================================
# Shared pieces
# ==========================================================================


def _resolve(session: Session, job: BrowserJob) -> tuple[Supplier, SupplierPortal, SupplierSession]:
    supplier = session.get(Supplier, job.supplier_id)
    if supplier is None:
        raise LookupError(f"supplier {job.supplier_id} not found")
    # Resolve through the supplier, never by slug alone: adapters whose URLs come
    # from the supplier row (generic, Monolith) are bound per supplier by the
    # registry, and the bare slug would hand back the unconfigured template.
    portal = portal_for_supplier(supplier)
    slug = (job.params or {}).get("portal")
    if portal is None and slug:
        template = REGISTRY.get(str(slug))
        bind = getattr(template, "bind", None)
        portal = bind(supplier) if callable(bind) else template
    if portal is None:
        raise LookupError(f"no portal adapter for supplier {supplier.name!r}")
    if slug and portal.slug != slug:
        raise LookupError(
            f"supplier {supplier.name!r} now resolves to portal {portal.slug!r}, "
            f"but this job was queued for {slug!r}; queue it again"
        )
    row = session.scalar(select(SupplierSession).where(SupplierSession.supplier_id == supplier.id))
    if row is None:
        row = SupplierSession(supplier_id=supplier.id)
        session.add(row)
        session.flush()
    return supplier, portal, row


def _open(
    session_row: SupplierSession,
    portal: SupplierPortal,
    executor_factory: Callable[..., BrowserExecutor] | None,
) -> BrowserExecutor:
    if executor_factory is not None:
        return executor_factory(session_row=session_row, portal=portal)
    return open_supplier_browser(session_row, portal)


def _url(executor: BrowserExecutor) -> str | None:
    try:
        return executor.current_url()
    except Exception:
        return None


def _budget() -> Budget:
    return Budget(
        max_model_calls=settings.browser_max_model_calls,
        max_actions=settings.browser_max_actions,
        max_seconds=settings.browser_max_minutes * 60,
    )


def _finish(job: BrowserJob, status: BrowserJobStatus, *, now: datetime) -> None:
    job.status = status
    job.finished_at = now


def _model_available(client: Any | None) -> bool:
    return client is not None or bool(settings.anthropic_api_key)


# ==========================================================================
# CHECK_SESSION
# ==========================================================================


def run_check_session(
    session: Session,
    job: BrowserJob,
    *,
    executor_factory: Callable[..., BrowserExecutor] | None = None,
    now: Callable[[], datetime] = _utcnow,
    heartbeat: Callable[[BrowserJob], None] | None = None,
) -> None:
    """Open the stored profile and report signed-in or not; update `supplier_session`."""
    recorder = StepRecorder(session, job, heartbeat=heartbeat, now=now)
    _supplier, portal, row = _resolve(session, job)
    job.model = None
    started = now()
    try:
        executor = _open(row, portal, executor_factory)
    except Exception as exc:
        row.status = SupplierSessionStatus.CHECK_FAILED
        row.last_checked_at = now()
        row.last_error = f"browser could not open: {type(exc).__name__}: {str(exc)[:300]}"
        job.error = row.last_error
        job.result = {"signed_in": False, "account_label": None, "error": row.last_error}
        _finish(job, BrowserJobStatus.FAILED, now=now())
        session.flush()
        raise
    try:
        signed_in, label = check_signed_in(executor, portal)
        recorder.script(
            "script:sign_in_check",
            outcome="OK" if signed_in else "ERROR",
            output=f"signed in as {label}" if signed_in else "not signed in",
            url=_url(executor),
            started=started,
        )
        row.last_checked_at = now()
        if signed_in:
            row.status = SupplierSessionStatus.CONNECTED
            row.last_ok_at = row.last_checked_at
            row.last_error = None
            if label:
                row.account_label = label[:200]
            if row.connected_at is None:
                row.connected_at = row.last_checked_at
            if not row.connected_by:
                row.connected_by = job.requested_by
        else:
            row.status = SupplierSessionStatus.EXPIRED
            row.last_error = "the portal asked to sign in again"
        job.result = {"signed_in": signed_in, "account_label": label if signed_in else None}
        _finish(job, BrowserJobStatus.SUCCEEDED, now=now())
    except JobCancelled as exc:
        job.error = str(exc)
        _finish(job, BrowserJobStatus.CANCELLED, now=now())
    finally:
        try:
            executor.close()
        except Exception:  # pragma: no cover - closing a dead browser
            log.warning("job %s: browser close failed", job.id, exc_info=True)
    session.flush()


# ==========================================================================
# STAGE_BASKET
# ==========================================================================


def _lines_from_params(params: dict[str, Any]) -> list[BasketLine]:
    lines: list[BasketLine] = []
    for raw in params.get("lines") or []:
        lines.append(
            BasketLine(
                po_line_id=int(raw.get("po_line_id") or 0),
                ingredient_name=str(raw.get("ingredient_name") or ""),
                sku=str(raw.get("sku") or ""),
                product_url=(str(raw["product_url"]) if raw.get("product_url") else None),
                packs_wanted=int(raw.get("packs") or 0),
                unit_price_expected_pence=int(raw.get("unit_price_pence") or 0),
            )
        )
    return lines


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _apply_model_line(
    line: BasketLine, outcome: str, data: dict[str, Any], note: str
) -> BasketLine:
    packs = _int_or_none(data.get("packs_in_basket"))
    price = _int_or_none(data.get("unit_price_pence"))
    name = data.get("product_name")
    if outcome in ("added", "already"):
        status = outcome
    elif outcome == "not_found":
        status = "not_found"
    else:
        status = "failed"
    return replace(
        line,
        status=status,
        added_by="model" if status in ("added", "already") else line.added_by,
        packs_in_basket=packs if packs is not None else line.packs_in_basket,
        unit_price_seen_pence=price if price is not None else line.unit_price_seen_pence,
        product_name_seen=str(name) if name else line.product_name_seen,
        note=note[:400],
    )


def _norm_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}".lower()


def _norm_name(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _name_matches(row: dict[str, Any], names: set[str]) -> bool:
    seen = _norm_name(str(row.get("name") or ""))
    return bool(seen) and any(n and (n in seen or seen in n) for n in names)


def _match_rows(
    lines: list[BasketLine], rows: list[dict[str, Any]]
) -> tuple[list[BasketLine], list[str]]:
    """Match basket rows to lines: product URL, then SKU, then name containment."""
    unmatched = list(range(len(rows)))
    matched: list[BasketLine] = []

    def take(pred: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
        for idx in list(unmatched):
            if pred(rows[idx]):
                unmatched.remove(idx)
                return rows[idx]
        return None

    for line in lines:
        row: dict[str, Any] | None = None
        url = _norm_url(line.product_url)
        if url:
            row = take(lambda r, u=url: _norm_url(r.get("product_url")) == u)  # type: ignore[misc]
        sku = line.sku.strip().lower()
        if row is None and sku:
            row = take(
                lambda r, s=sku: (
                    s in str(r.get("product_url") or "").lower()  # type: ignore[misc]
                    or s in _norm_name(str(r.get("name") or ""))
                )
            )
        if row is None:
            names = {_norm_name(line.ingredient_name), _norm_name(line.product_name_seen)}
            names.discard("")
            row = take(lambda r, ns=names: _name_matches(r, ns))  # type: ignore[misc]
        if row is None:
            matched.append(line)
            continue
        qty = _int_or_none(row.get("qty"))
        status = line.status
        if status == "pending" and qty is not None:
            status = "already" if qty >= line.packs_wanted else "failed"
        matched.append(
            replace(
                line,
                status=status,
                packs_in_basket=qty if qty is not None else line.packs_in_basket,
                unit_price_seen_pence=_int_or_none(row.get("unit_price_pence"))
                if _int_or_none(row.get("unit_price_pence")) is not None
                else line.unit_price_seen_pence,
                line_total_seen_pence=_int_or_none(row.get("line_total_pence"))
                if _int_or_none(row.get("line_total_pence")) is not None
                else line.line_total_seen_pence,
                product_name_seen=str(row.get("name") or line.product_name_seen or "") or None,
            )
        )
    unexpected = [
        f"{rows[i].get('name') or '?'} x {_qty_text(rows[i].get('qty'))}" for i in unmatched
    ]
    return matched, unexpected


def _qty_text(value: Any) -> str:
    return "?" if value is None else str(value)


def _off_by(seen: int, expected: int) -> bool:
    return abs(seen - expected) / expected > PRICE_TOLERANCE


def _pounds(pence: int | None) -> str:
    return "unread" if pence is None else f"£{pence / 100:,.2f}"


def _warnings(
    lines: list[BasketLine],
    *,
    subtotal_seen: int | None,
    total_expected: int,
    unexpected: list[str],
    budget_note: str | None,
    basket_read: bool,
) -> list[str]:
    out: list[str] = []
    incomplete = [line for line in lines if line.status not in ("added", "already")]
    for line in incomplete:
        out.append(
            f"{line.ingredient_name}: {line.status.replace('_', ' ')}"
            + (f" — {line.note}" if line.note else "")
        )
    for line in lines:
        seen, expected = line.unit_price_seen_pence, line.unit_price_expected_pence
        if seen is not None and expected > 0 and _off_by(seen, expected):
            out.append(
                f"{line.ingredient_name}: price seen {_pounds(seen)} vs {_pounds(expected)} "
                "on the order"
            )
        if (
            line.status in ("added", "already")
            and line.packs_in_basket is not None
            and line.packs_in_basket != line.packs_wanted
        ):
            out.append(
                f"{line.ingredient_name}: basket holds {line.packs_in_basket} pack(s), order wants "
                f"{line.packs_wanted}"
            )
    if not basket_read:
        out.append("the basket page could not be read; check it before paying")
    elif subtotal_seen is None:
        out.append("basket subtotal not read")
    elif total_expected > 0 and _off_by(subtotal_seen, total_expected):
        out.append(
            f"basket subtotal {_pounds(subtotal_seen)} differs from the order's "
            f"{_pounds(total_expected)}"
        )
    if unexpected:
        out.append(
            f"{len(unexpected)} line(s) in the basket the order did not ask for: "
            + "; ".join(unexpected[:6])
        )
    if budget_note:
        out.append(f"stopped early: {budget_note}")
    return out


def _confidence(snapshot: BasketSnapshot) -> str:
    if not snapshot.complete:
        return "low"
    if snapshot.subtotal_seen_pence is None:
        return "medium"
    expected = snapshot.total_expected_pence
    if expected > 0 and abs(snapshot.subtotal_seen_pence - expected) / expected <= PRICE_TOLERANCE:
        return "high"
    return "low"


def _write_audit(
    session: Session,
    job: BrowserJob,
    *,
    supplier: Supplier,
    snapshot: BasketSnapshot | None,
    outcome: AgentToolOutcome,
    summary: str,
    refusal_reason: str | None = None,
) -> None:
    """One `agent_action_log` row per job and, when a basket was staged, one proposal."""
    run_id = job.run_id or uuid.uuid4().hex[:16]
    job.run_id = run_id
    repo = SqlAgentLogRepository(session)
    log_id = repo.log(
        run_id=run_id,
        tool_name=TOOL_NAME,
        inputs={"po_id": job.purchase_order_id, "job_id": job.id, "supplier": supplier.name},
        outcome=outcome,
        output=summary,
        purpose=f"stage a supplier basket for PO {job.purchase_order_id}",
        refusal_reason=refusal_reason,
        proposal_ref=f"supplier_basket:po:{job.purchase_order_id}" if snapshot else None,
        model=job.model,
    )
    row = session.get(AgentActionLog, log_id)
    if row is not None:
        row.agent = AGENT_NAME
    if snapshot is None:
        return
    added = sum(1 for line in snapshot.lines if line.status in ("added", "already"))
    title = (
        f"{supplier.name} basket staged: {added} of {len(snapshot.lines)} lines, "
        f"{_pounds(snapshot.subtotal_seen_pence)} seen"
    )
    figures = [_pounds(snapshot.total_expected_pence)]
    if snapshot.subtotal_seen_pence is not None:
        figures.append(_pounds(snapshot.subtotal_seen_pence))
    payload: dict[str, object] = dict(snapshot.as_dict())
    payload["basket_url"] = snapshot.basket_url
    payload["current"] = {"po_status": (job.params or {}).get("po_status")}
    payload["job_id"] = job.id
    proposal = AgentProposal(
        kind="supplier_basket",
        subject_ref=f"po:{job.purchase_order_id}",
        summary=title,
        payload=payload,
        confidence=_confidence(snapshot),
    )
    job.proposal_id = insert_proposal(
        session,
        run_id=run_id,
        log_id=log_id,
        agent=AGENT_NAME,
        proposal=proposal,
        figures=figures,
        title=title[:80],
        body=summary,
    )


def _summary(snapshot: BasketSnapshot, supplier: Supplier) -> str:
    added = [line for line in snapshot.lines if line.status in ("added", "already")]
    first = (
        f"{supplier.name}: {len(added)} of {len(snapshot.lines)} order lines are in the basket "
        f"(subtotal seen {_pounds(snapshot.subtotal_seen_pence)}, order expects "
        f"{_pounds(snapshot.total_expected_pence)})."
    )
    second = (
        "Warnings: " + " | ".join(snapshot.warnings)
        if snapshot.warnings
        else "No warnings: every line is in at the wanted quantity."
    )
    third = (
        "Staged, not sent. Open the basket, check it, pay on the supplier's site, then press "
        "Mark sent on the order."
    )
    return f"{first}\n{second}\n{third}"


def run_stage_basket(
    session: Session,
    job: BrowserJob,
    *,
    client: Any | None = None,
    executor_factory: Callable[..., BrowserExecutor] | None = None,
    now: Callable[[], datetime] = _utcnow,
    heartbeat: Callable[[BrowserJob], None] | None = None,
) -> None:
    """Fill the supplier's basket from `job.params["lines"]` and stop at the basket."""
    recorder = StepRecorder(session, job, heartbeat=heartbeat, now=now)
    supplier, portal, session_row = _resolve(session, job)
    params: dict[str, Any] = dict(job.params or {})
    lines = _lines_from_params(params)
    po_id = int(job.purchase_order_id or 0)
    job.model = settings.browser_model
    budget = _budget()
    budget.started_at = now()
    guard = PolicyGuard(portal.policy)
    basket_url = portal.policy.basket_url
    hints = portal.agent_hints()
    total_expected = sum(line.packs_wanted * line.unit_price_expected_pence for line in lines)

    executor = _open(session_row, portal, executor_factory)
    budget_note: str | None = None
    rows: list[dict[str, Any]] = []
    subtotal_seen: int | None = None
    basket_read = False
    screenshot_asset_id: int | None = None
    needs_human: str | None = None

    def model_task(task: Any) -> Any:
        return run_model_task(
            executor=executor,
            guard=guard,
            task=task,
            budget=budget,
            record=recorder.record,
            client=client,
            now=now,
        )

    try:
        # --- step 3: open and check the sign-in ------------------------------
        started = now()
        signed_in, label = check_signed_in(executor, portal)
        recorder.script(
            "script:sign_in_check",
            outcome="OK" if signed_in else "ERROR",
            output=f"signed in as {label}" if signed_in else "not signed in",
            url=_url(executor),
            started=started,
        )
        session_row.last_checked_at = now()
        if not signed_in:
            session_row.status = SupplierSessionStatus.EXPIRED
            session_row.last_error = "the portal asked to sign in again"
            raise _StopNeedsHuman("sign-in expired: reconnect the supplier and stage again")
        session_row.last_ok_at = session_row.last_checked_at
        if label:
            session_row.account_label = label[:200]
        page = getattr(executor, "page", None)

        # --- step 4: the lines ----------------------------------------------
        done: list[BasketLine] = []
        for index, line in enumerate(lines):
            exhausted = budget.exhausted(now())
            if exhausted is not None:
                budget_note = exhausted
                done.extend(
                    replace(pending, status="skipped", note=f"not attempted: {exhausted}")
                    for pending in lines[index:]
                )
                break
            result_line = line
            to_model = not portal.supports_scripted_add
            if portal.supports_scripted_add:
                started = now()
                try:
                    result_line = portal.add_line_scripted(page, line)
                    recorder.script(
                        "script:add_line",
                        input={"po_line_id": line.po_line_id, "packs": line.packs_wanted},
                        output=f"{result_line.status}: {result_line.packs_in_basket} in basket",
                        url=_url(executor),
                        started=started,
                    )
                    budget.actions += 1
                except PortalNeedsHuman as exc:
                    recorder.script(
                        "script:add_line",
                        input={"po_line_id": line.po_line_id},
                        outcome="ERROR",
                        output=f"needs a person: {exc}",
                        url=_url(executor),
                        started=started,
                    )
                    done.append(replace(line, status="skipped", note=str(exc)[:400]))
                    done.extend(
                        replace(p, status="skipped", note="not attempted")
                        for p in lines[index + 1 :]
                    )
                    raise _StopNeedsHuman(str(exc)) from exc
                except PortalPolicyRefusal as exc:
                    recorder.script(
                        "script:add_line",
                        input={"po_line_id": line.po_line_id},
                        outcome="REFUSED",
                        source="POLICY",
                        refusal_reason=str(exc)[:400],
                        url=_url(executor),
                        started=started,
                    )
                    result_line = replace(line, status="skipped", note=f"refused: {exc}"[:400])
                except (PortalStepFailed, Exception) as exc:
                    recorder.script(
                        "script:add_line",
                        input={"po_line_id": line.po_line_id},
                        outcome="ERROR",
                        output=f"{type(exc).__name__}: {str(exc)[:300]}",
                        url=_url(executor),
                        started=started,
                    )
                    to_model = True
                    result_line = replace(line, note=f"scripted add failed: {str(exc)[:200]}")
            if to_model:
                if not _model_available(client):
                    result_line = replace(
                        result_line,
                        status="failed",
                        note=(result_line.note + "; " if result_line.note else "")
                        + "no model available for the fallback (CAFEOPS_ANTHROPIC_API_KEY unset)",
                    )
                else:
                    outcome = model_task(basket_line_task(line, hints, basket_url))
                    result_line = _apply_model_line(
                        result_line, outcome.outcome, outcome.data, outcome.note
                    )
                    if outcome.stopped_reason == "needs_human":
                        done.append(replace(result_line, status="skipped"))
                        done.extend(
                            replace(p, status="skipped", note="not attempted")
                            for p in lines[index + 1 :]
                        )
                        raise _StopNeedsHuman(outcome.note or "the portal asked for a person")
                    if outcome.stopped_reason == "budget":
                        budget_note = outcome.note
            done.append(result_line)
        lines = done

        # --- step 5: read the basket ----------------------------------------
        started = now()
        try:
            raw_rows, subtotal_seen = portal.read_basket(page)
            rows = [dict(r) for r in raw_rows]
            basket_read = True
            recorder.script(
                "script:read_basket",
                output=f"{len(rows)} row(s), subtotal {_pounds(subtotal_seen)}",
                url=_url(executor),
                started=started,
            )
            budget.actions += 1
        except PortalNeedsHuman as exc:
            recorder.script(
                "script:read_basket",
                outcome="ERROR",
                output=f"needs a person: {exc}",
                url=_url(executor),
                started=started,
            )
            raise _StopNeedsHuman(str(exc)) from exc
        except Exception as exc:
            recorder.script(
                "script:read_basket",
                outcome="ERROR",
                output=f"{type(exc).__name__}: {str(exc)[:300]}",
                url=_url(executor),
                started=started,
            )
            if _model_available(client) and budget.exhausted(now()) is None:
                outcome = model_task(read_basket_task(hints, basket_url))
                if outcome.stopped_reason == "needs_human":
                    raise _StopNeedsHuman(outcome.note or "the portal asked for a person") from exc
                if outcome.outcome == "read":
                    raw = outcome.data.get("rows")
                    rows = (
                        [dict(r) for r in raw if isinstance(r, dict)]
                        if isinstance(raw, list)
                        else []
                    )
                    subtotal_seen = _int_or_none(outcome.data.get("subtotal_pence"))
                    basket_read = True
                elif outcome.stopped_reason == "budget":
                    budget_note = outcome.note

        if basket_read:
            lines, unexpected = _match_rows(lines, rows)
        else:
            unexpected = []

        # --- final screenshot ------------------------------------------------
        started = now()
        try:
            png = executor.screenshot_png()
        except Exception as exc:
            png = None
            recorder.script(
                "script:screenshot",
                outcome="ERROR",
                output=f"{type(exc).__name__}: {str(exc)[:200]}",
                url=_url(executor),
                started=started,
            )
        if png:
            step = recorder.script(
                "script:screenshot",
                output="basket page",
                url=_url(executor),
                started=started,
                screenshot_png=png,
            )
            screenshot_asset_id = step.screenshot_asset_id

        # --- step 6: snapshot, audit, result --------------------------------
        warnings = _warnings(
            lines,
            subtotal_seen=subtotal_seen,
            total_expected=total_expected,
            unexpected=unexpected,
            budget_note=budget_note,
            basket_read=basket_read,
        )
        snapshot = BasketSnapshot(
            supplier_id=supplier.id,
            supplier_name=supplier.name,
            portal_slug=portal.slug,
            po_id=po_id,
            basket_url=basket_url,
            lines=tuple(lines),
            subtotal_seen_pence=subtotal_seen,
            total_expected_pence=total_expected,
            captured_at=now(),
            screenshot_asset_id=screenshot_asset_id,
            warnings=tuple(warnings),
            unexpected_lines=tuple(unexpected),
        )
        staged_any = any(line.status in ("added", "already") for line in lines)
        summary = _summary(snapshot, supplier)
        job.result = snapshot.as_dict()
        if not basket_read and not staged_any:
            job.error = "nothing could be staged or read"
            _write_audit(
                session,
                job,
                supplier=supplier,
                snapshot=None,
                outcome=AgentToolOutcome.FAILED,
                summary=summary,
            )
            _finish(job, BrowserJobStatus.FAILED, now=now())
        else:
            _write_audit(
                session,
                job,
                supplier=supplier,
                snapshot=snapshot,
                outcome=AgentToolOutcome.AWAITING_HUMAN,
                summary=summary,
            )
            _finish(job, BrowserJobStatus.SUCCEEDED, now=now())
    except _StopNeedsHuman as exc:
        needs_human = exc.reason
        job.needs_human_reason = needs_human[:1000]
        job.result = {
            "lines": [line.as_dict() for line in lines],
            "needs_human_reason": needs_human,
        }
        _write_audit(
            session,
            job,
            supplier=supplier,
            snapshot=None,
            outcome=AgentToolOutcome.AWAITING_HUMAN,
            summary=f"Stopped: {needs_human}. Nothing was staged for order {po_id}.",
        )
        _finish(job, BrowserJobStatus.NEEDS_HUMAN, now=now())
    except JobCancelled as exc:
        job.error = str(exc)
        job.result = {"lines": [line.as_dict() for line in lines]}
        _write_audit(
            session,
            job,
            supplier=supplier,
            snapshot=None,
            outcome=AgentToolOutcome.FAILED,
            summary=f"{exc}. Nothing further was done for order {po_id}.",
            refusal_reason=str(exc)[:400],
        )
        _finish(job, BrowserJobStatus.CANCELLED, now=now())
    finally:
        job.model_calls = budget.model_calls
        job.input_tokens = budget.input_tokens
        job.output_tokens = budget.output_tokens
        try:
            executor.close()
        except Exception:  # pragma: no cover - closing a dead browser
            log.warning("job %s: browser close failed", job.id, exc_info=True)
    session.flush()


__all__ = [
    "AGENT_NAME",
    "TOOL_NAME",
    "JobCancelled",
    "StepRecorder",
    "default_heartbeat",
    "run_check_session",
    "run_stage_basket",
]
