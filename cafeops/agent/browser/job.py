"""One browser job, end to end: tiers, scripted steps, model fallback, snapshot, audit.

docs/agents/BROWSER-ORDERING.md §4 steps 3-6 and §10. `run_stage_basket` takes a
claimed RUNNING `browser_job` and leaves it SUCCEEDED (a basket was staged, possibly
with warnings), NEEDS_HUMAN (the portal wants a sign-in, a code, a CAPTCHA, or a
display the worker does not have), CANCELLED (a person asked) or FAILED (nothing
could be read at all; an escaped exception is the worker's FAILED). The purchase
order is never touched.

### The tier ladder (§10)

Tier 0 (a cart link, no browser at all) is decided in `services/browser_jobs.py`
before a job is queued; when the link covers every line the job never reaches this
module. What arrives here is the rest of the ladder, in order:

1. `params["cart_link"]` -- a partial link: opened once in the worker's browser
   (`script:cart_link_open`), which pre-fills the lines it covers.
2. `portal.quick_order(page, lines)` -- the product-code pad (Booker, Brakes): every
   remaining line with a SKU in ONE form (`script:quick_order`). When the scripted
   pad breaks, ONE model task drives the pad for all of those lines.
3. The per-line path: `add_line_scripted`, then the model for the step that broke.

`result["tier"]` is the best tier that put at least one line in the basket and
`result["tiers_used"]` lists the tiers that ran; each line's `added_by` says which
half put it in ("cart_link" | "quick_order" | "script" | "model").

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
worker's ordinary session with the same helpers the engine uses (`report.py`),
INSERT only, in the same commit as the job's result, so a proposal never exists
without the job and log row that made it. Invariant 10 holds structurally: nothing
here imports a stock, order or composition service, and the worker's session is bound
to `policies.browser_worker_engine`, whose connection refuses any write to a table on
`FORBIDDEN_TABLES`.

### Transactions

SQLite has one writer. The job commits (a) right after resolving the supplier session,
before Chromium is launched, and (b) after every step (`default_heartbeat`), so no
write lock is held across a browser launch, a page load or a model call.
`heartbeat_at` itself is written only by the worker's heartbeat thread.

Every `StepRecord` becomes a `browser_job_step` row as it happens (with its screenshot
in `media_asset`), committed at once, and a cancel request is honoured at the next
step boundary.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.agent.browser.loop import (
    basket_line_task,
    quick_order_task,
    read_basket_task,
    run_model_task,
)
from cafeops.agent.browser.policy import PolicyGuard
from cafeops.agent.browser.report import (
    highest_tier,
    int_or_none,
    lines_from_params,
    pounds,
    summary,
    warnings_for,
    write_audit,
)
from cafeops.agent.browser.session import (
    SignInCheckFailed,
    check_signed_in,
    open_supplier_browser,
)
from cafeops.agent.browser.types import BrowserAction, BrowserExecutor, Budget, StepRecord
from cafeops.clock import utcnow
from cafeops.config import settings
from cafeops.db.models import (
    BrowserJob,
    BrowserJobStatus,
    BrowserJobStep,
    BrowserStepOutcome,
    BrowserStepSource,
    Supplier,
    SupplierSession,
    SupplierSessionStatus,
)
from cafeops.domain.types import AgentToolOutcome
from cafeops.integrations.suppliers import portals as _portals  # noqa: F401 -- registers adapters
from cafeops.integrations.suppliers.portals.base import (
    REGISTRY,
    BasketLine,
    BasketSnapshot,
    Bindable,
    PortalNeedsHuman,
    PortalPolicyRefusal,
    QuickOrderCapable,
    SupplierPortal,
    portal_for_supplier,
)
from cafeops.services.media_store import MediaRefusedError, store_image

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger("cafeops.browser")

#: `AGENT_NAME`, `TOOL_NAME` and the snapshot/audit helpers live in `report.py`.
__all__ = [
    "JobCancelled",
    "StepRecorder",
    "default_heartbeat",
    "run_check_session",
    "run_stage_basket",
]


class JobCancelled(Exception):
    """Raised from the heartbeat when `cancel_requested_at` is set."""


class _StopNeedsHuman(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _StopFailed(Exception):
    """The job cannot go on and nothing was staged (the sign-in could not be checked)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ==========================================================================
# Step recording and heartbeat
# ==========================================================================


def default_heartbeat(session: Session, job: BrowserJob) -> None:
    """The per-step pulse: commit what the step wrote (so the web shows progress and the
    write lock is released before the next slow thing) and re-read the cancel columns
    (another process sets them).

    It does NOT touch `heartbeat_at`: the worker's `_Heartbeat` thread is the one
    writer of that column, every few seconds, which also covers a long model call or a
    slow page where no step is recorded. Two writers of one column was one too many."""
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
        now: Callable[[], datetime] = utcnow,
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
        portal = template.bind(supplier) if isinstance(template, Bindable) else template
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
    """Launch the browser. A `PortalNeedsHuman` from here (tier 2: the portal wants a
    visible window and the worker has no display) is the job's NEEDS_HUMAN, raised
    before anything is opened."""
    if executor_factory is not None:
        return executor_factory(session_row=session_row, portal=portal)
    return open_supplier_browser(session_row, portal)


def _page_of(executor: BrowserExecutor) -> Page:
    """The executor's Playwright page, which the scripted portal steps drive directly.
    The protocol does not promise one; an executor without it cannot stage a basket
    (step 5 always reads the basket page), so say so rather than fail inside an adapter."""
    page: Page | None = getattr(executor, "page", None)
    if page is None:
        raise RuntimeError(
            f"{type(executor).__name__} exposes no Playwright page; the scripted portal "
            "steps (quick order, add line, read basket) need one"
        )
    return page


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


def _record_check_failed(
    row: SupplierSession, job: BrowserJob, error: str, *, at: datetime
) -> None:
    """The sign-in could not be looked at. CHECK_FAILED, not EXPIRED: nobody should be
    sent to sign in again because the network or the adapter failed."""
    row.status = SupplierSessionStatus.CHECK_FAILED
    row.last_checked_at = at
    row.last_error = error[:1000]
    job.error = row.last_error
    job.result = {"signed_in": None, "account_label": None, "error": row.last_error}
    _finish(job, BrowserJobStatus.FAILED, now=at)


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
    now: Callable[[], datetime] = utcnow,
    heartbeat: Callable[[BrowserJob], None] | None = None,
) -> None:
    """Open the stored profile and report signed-in or not; update `supplier_session`.

    Every outcome is recorded on the job and returned normally, so the worker's
    transaction commits it: a failure raised from here would be rolled back with
    everything this function wrote about it.
    """
    recorder = StepRecorder(session, job, heartbeat=heartbeat, now=now)
    _supplier, portal, row = _resolve(session, job)
    job.model = None
    # Commit before Chromium launches (seconds): nothing may hold SQLite's write lock
    # across it, or the bot and the API wait out busy_timeout and fail "locked".
    session.commit()
    started = now()
    try:
        executor = _open(row, portal, executor_factory)
    except PortalNeedsHuman as exc:
        # Tier 2: no display for a portal that refuses headless. Not a failed check --
        # the sign-in was never looked at -- so the session row is left alone.
        job.needs_human_reason = str(exc)[:1000]
        job.result = {"signed_in": None, "account_label": None, "needs_human_reason": str(exc)}
        _finish(job, BrowserJobStatus.NEEDS_HUMAN, now=now())
        session.flush()
        return
    except Exception as exc:
        log.exception("job %s: the browser could not open", job.id)
        _record_check_failed(
            row,
            job,
            f"browser could not open: {type(exc).__name__}: {str(exc)[:300]}",
            at=now(),
        )
        session.flush()
        return
    try:
        verdict: tuple[bool, str | None] | None = None
        needs_human: str | None = None
        failure: str | None = None
        try:
            verdict = check_signed_in(executor, portal)
        except PortalNeedsHuman as exc:
            needs_human = str(exc)
        except SignInCheckFailed as exc:
            failure = str(exc)
        if needs_human is not None:
            recorder.script(
                "script:sign_in_check",
                outcome="ERROR",
                output=f"needs a person: {needs_human}"[:1000],
                url=_url(executor),
                started=started,
            )
            job.needs_human_reason = needs_human[:1000]
            job.result = {
                "signed_in": None,
                "account_label": None,
                "needs_human_reason": needs_human,
            }
            _finish(job, BrowserJobStatus.NEEDS_HUMAN, now=now())
        elif verdict is None:
            error = f"sign-in check failed: {failure}"
            recorder.script(
                "script:sign_in_check",
                outcome="ERROR",
                output=error[:1000],
                url=_url(executor),
                started=started,
            )
            _record_check_failed(row, job, error, at=now())
        else:
            signed_in, label = verdict
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


def _apply_model_line(
    line: BasketLine, outcome: str, data: dict[str, Any], note: str
) -> BasketLine:
    packs = int_or_none(data.get("packs_in_basket"))
    price = int_or_none(data.get("unit_price_pence"))
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


def _apply_quick_order_result(
    lines: list[BasketLine], result_lines: tuple[BasketLine, ...], rejected: dict[int, str]
) -> list[BasketLine]:
    """Merge what the pad reported into the job's lines. A line the pad accepted is
    `added` by "quick_order"; a rejected one stays pending with the pad's own words
    as its note, so the per-line path picks it up."""
    by_id = {line.po_line_id: line for line in result_lines}
    out: list[BasketLine] = []
    for line in lines:
        if line.status != "pending":
            out.append(line)
            continue
        if line.po_line_id in rejected:
            out.append(replace(line, note=f"quick order pad: {rejected[line.po_line_id]}"[:400]))
            continue
        got = by_id.get(line.po_line_id)
        if got is None:
            out.append(line)
            continue
        if got.status in ("added", "already"):
            out.append(
                replace(
                    got,
                    added_by="quick_order",
                    packs_in_basket=(
                        got.packs_in_basket
                        if got.packs_in_basket is not None
                        else line.packs_wanted
                    ),
                )
            )
        elif got.status == "not_found":
            out.append(got)
        else:
            # Anything else the pad said is not final: the per-line path tries again.
            out.append(replace(got, status="pending"))
    return out


def _apply_quick_order_model(
    lines: list[BasketLine], data: dict[str, Any], note: str
) -> list[BasketLine]:
    """The model drove the pad for every remaining coded line at once; apply its
    per-line verdicts. `gave_up` leaves a line pending for the per-line path."""
    raw = data.get("lines")
    verdicts: dict[int, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                po_line_id = int_or_none(item.get("po_line_id"))
                if po_line_id is not None:
                    verdicts[po_line_id] = item
    out: list[BasketLine] = []
    for line in lines:
        verdict = verdicts.get(line.po_line_id)
        if line.status != "pending" or not line.sku or verdict is None:
            out.append(line)
            continue
        status = str(verdict.get("status") or "")
        line_note = str(verdict.get("note") or note or "")[:400]
        packs = int_or_none(verdict.get("packs_in_basket"))
        if status == "added":
            out.append(
                replace(
                    line,
                    status="added",
                    added_by="model",
                    packs_in_basket=packs if packs is not None else line.packs_wanted,
                    note=line_note,
                )
            )
        elif status == "not_found":
            out.append(replace(line, status="not_found", note=line_note))
        else:
            out.append(replace(line, note=line_note))
    return out


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

    Pred = Callable[[dict[str, Any]], bool]

    def take(pred: Pred) -> dict[str, Any] | None:
        for idx in list(unmatched):
            if pred(rows[idx]):
                unmatched.remove(idx)
                return rows[idx]
        return None

    def by_url(url: str) -> Pred:
        return lambda r: _norm_url(r.get("product_url")) == url

    def by_sku(sku: str) -> Pred:
        return lambda r: (
            sku in str(r.get("product_url") or "").lower()
            or sku in _norm_name(str(r.get("name") or ""))
        )

    def by_name(names: set[str]) -> Pred:
        return lambda r: _name_matches(r, names)

    for line in lines:
        row: dict[str, Any] | None = None
        url = _norm_url(line.product_url)
        if url:
            row = take(by_url(url))
        sku = line.sku.strip().lower()
        if row is None and sku:
            row = take(by_sku(sku))
        if row is None:
            names = {_norm_name(line.ingredient_name), _norm_name(line.product_name_seen)}
            names.discard("")
            row = take(by_name(names))
        if row is None:
            matched.append(line)
            continue
        qty = int_or_none(row.get("qty"))
        status = line.status
        if status == "pending" and qty is not None:
            status = "already" if qty >= line.packs_wanted else "failed"
        matched.append(
            replace(
                line,
                status=status,
                packs_in_basket=qty if qty is not None else line.packs_in_basket,
                unit_price_seen_pence=int_or_none(row.get("unit_price_pence"))
                if int_or_none(row.get("unit_price_pence")) is not None
                else line.unit_price_seen_pence,
                line_total_seen_pence=int_or_none(row.get("line_total_pence"))
                if int_or_none(row.get("line_total_pence")) is not None
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


def _skip_rest(lines: list[BasketLine], note: str) -> list[BasketLine]:
    return [
        replace(line, status="skipped", note=note) if line.status == "pending" else line
        for line in lines
    ]


def run_stage_basket(
    session: Session,
    job: BrowserJob,
    *,
    client: Any | None = None,
    executor_factory: Callable[..., BrowserExecutor] | None = None,
    now: Callable[[], datetime] = utcnow,
    heartbeat: Callable[[BrowserJob], None] | None = None,
) -> None:
    """Fill the supplier's basket from `job.params["lines"]` and stop at the basket."""
    recorder = StepRecorder(session, job, heartbeat=heartbeat, now=now)
    supplier, portal, session_row = _resolve(session, job)
    params: dict[str, Any] = dict(job.params or {})
    lines = lines_from_params(params)
    po_id = int(job.purchase_order_id or 0)
    job.model = settings.browser_model
    budget = _budget()
    budget.started_at = now()
    guard = PolicyGuard(portal.policy)
    basket_url = portal.policy.basket_url
    hints = portal.agent_hints()
    total_expected = sum(line.packs_wanted * line.unit_price_expected_pence for line in lines)
    tiers_used: list[str] = []

    def used(tier: str) -> None:
        if tier not in tiers_used:
            tiers_used.append(tier)

    def result_dict(base: dict[str, Any]) -> dict[str, Any]:
        base["tier"] = highest_tier(lines)
        base["tiers_used"] = list(tiers_used)
        return base

    # Commit before Chromium launches (see run_check_session): no write lock across it.
    session.commit()
    try:
        executor = _open(session_row, portal, executor_factory)
    except PortalNeedsHuman as exc:
        # Tier 2 refused before anything opened: the portal wants a visible window and
        # this worker has no display. Zero browser launches, zero steps.
        reason = str(exc)
        job.needs_human_reason = reason[:1000]
        job.result = result_dict(
            {"lines": [line.as_dict() for line in lines], "needs_human_reason": reason}
        )
        write_audit(
            session,
            job,
            supplier=supplier,
            snapshot=None,
            outcome=AgentToolOutcome.AWAITING_HUMAN,
            summary_text=f"Stopped: {reason}. Nothing was staged for order {po_id}.",
        )
        _finish(job, BrowserJobStatus.NEEDS_HUMAN, now=now())
        session.flush()
        return

    budget_note: str | None = None
    rows: list[dict[str, Any]] = []
    subtotal_seen: int | None = None
    basket_read = False
    screenshot_asset_id: int | None = None
    needs_human: str | None = None

    def record_step(step: StepRecord) -> None:
        recorder.record(step)

    def model_task(task: Any) -> Any:
        return run_model_task(
            executor=executor,
            guard=guard,
            task=task,
            budget=budget,
            record=record_step,
            client=client,
            now=now,
        )

    try:
        # --- step 3: open and check the sign-in ------------------------------
        started = now()
        try:
            signed_in, label = check_signed_in(executor, portal)
        except PortalNeedsHuman as exc:
            recorder.script(
                "script:sign_in_check",
                outcome="ERROR",
                output=f"needs a person: {exc}"[:1000],
                url=_url(executor),
                started=started,
            )
            lines = _skip_rest(lines, "not attempted")
            raise _StopNeedsHuman(str(exc)) from exc
        except SignInCheckFailed as exc:
            error = f"sign-in check failed: {exc}"
            recorder.script(
                "script:sign_in_check",
                outcome="ERROR",
                output=error[:1000],
                url=_url(executor),
                started=started,
            )
            session_row.status = SupplierSessionStatus.CHECK_FAILED
            session_row.last_checked_at = now()
            session_row.last_error = error[:1000]
            lines = _skip_rest(lines, "not attempted")
            raise _StopFailed(error) from exc
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
        page = _page_of(executor)

        # --- tier 0, partial: open the cart link in the worker's browser -------
        plan = params.get("cart_link")
        if isinstance(plan, dict) and plan.get("url"):
            started = now()
            covered = {int(v) for v in (plan.get("covered") or []) if int_or_none(v) is not None}
            result = executor.execute(BrowserAction("navigate", {"url": str(plan["url"])}))
            budget.actions += 1
            if result.is_error:
                recorder.script(
                    "script:cart_link_open",
                    input={"covered": sorted(covered)},
                    outcome="ERROR",
                    output=str(result.content)[:300],
                    url=_url(executor),
                    started=started,
                )
            else:
                recorder.script(
                    "script:cart_link_open",
                    input={"covered": sorted(covered)},
                    output=f"{plan.get('label') or 'cart link'}: {len(covered)} line(s) pre-filled",
                    url=_url(executor),
                    started=started,
                )
                used("cart_link")
                lines = [
                    replace(
                        line,
                        status="added",
                        added_by="cart_link",
                        packs_in_basket=line.packs_wanted,
                        note="pre-filled by the cart link",
                    )
                    if line.po_line_id in covered and line.status == "pending"
                    else line
                    for line in lines
                ]

        # --- tier 1: the quick-order pad ---------------------------------------
        coded = [line for line in lines if line.status == "pending" and line.sku]
        if isinstance(portal, QuickOrderCapable) and coded:
            exhausted = budget.exhausted(now())
            if exhausted is not None:
                budget_note = exhausted
            else:
                used("quick_order")
                started = now()
                pad_failed: str | None = None
                pad_input = {"po_line_ids": [ln.po_line_id for ln in coded]}
                try:
                    qr = portal.quick_order(page, coded)
                except JobCancelled:
                    raise
                except PortalNeedsHuman as exc:
                    recorder.script(
                        "script:quick_order",
                        input=pad_input,
                        outcome="ERROR",
                        output=f"needs a person: {exc}",
                        url=_url(executor),
                        started=started,
                    )
                    lines = _skip_rest(lines, "not attempted")
                    raise _StopNeedsHuman(str(exc)) from exc
                except PortalPolicyRefusal as exc:
                    recorder.script(
                        "script:quick_order",
                        input=pad_input,
                        outcome="REFUSED",
                        source="POLICY",
                        refusal_reason=str(exc)[:400],
                        url=_url(executor),
                        started=started,
                    )
                except Exception as exc:  # PortalStepFailed, or the adapter broke
                    pad_failed = f"{type(exc).__name__}: {str(exc)[:300]}"
                    recorder.script(
                        "script:quick_order",
                        input=pad_input,
                        outcome="ERROR",
                        output=pad_failed,
                        url=_url(executor),
                        started=started,
                    )
                else:
                    # Outside the `try`: a cancel raised by the step's pulse, or a
                    # database error writing it, is not "the pad failed".
                    budget.actions += 1
                    accepted = sum(1 for ln in qr.lines if ln.status in ("added", "already"))
                    rejected = dict(qr.rejected)
                    recorder.script(
                        "script:quick_order",
                        input=pad_input,
                        output=(
                            f"{accepted} of {len(coded)} codes accepted"
                            + (f"; rejected: {len(rejected)}" if rejected else "")
                            + (f"; {qr.note}" if qr.note else "")
                        ),
                        url=_url(executor),
                        started=started,
                    )
                    lines = _apply_quick_order_result(lines, tuple(qr.lines), rejected)
                if pad_failed is not None and _model_available(client):
                    outcome = model_task(
                        quick_order_task(
                            coded,
                            str(portal.quick_order_hints()),
                            str(portal.quick_order_url or ""),
                            basket_url,
                        )
                    )
                    if outcome.stopped_reason == "needs_human":
                        lines = _skip_rest(lines, "not attempted")
                        raise _StopNeedsHuman(outcome.note or "the portal asked for a person")
                    lines = _apply_quick_order_model(lines, outcome.data, outcome.note)
                    if outcome.stopped_reason == "budget":
                        budget_note = outcome.note

        # --- the browser tier: one line at a time --------------------------------
        done: list[BasketLine] = []
        for index, line in enumerate(lines):
            if line.status != "pending":
                done.append(line)
                continue
            exhausted = budget.exhausted(now())
            if exhausted is not None:
                budget_note = exhausted
                done.append(replace(line, status="skipped", note=f"not attempted: {exhausted}"))
                done.extend(_skip_rest(lines[index + 1 :], f"not attempted: {exhausted}"))
                break
            used("browser")
            result_line = line
            to_model = not portal.supports_scripted_add
            if portal.supports_scripted_add:
                started = now()
                try:
                    added = portal.add_line_scripted(page, line)
                except JobCancelled:
                    raise
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
                    done.extend(_skip_rest(lines[index + 1 :], "not attempted"))
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
                except Exception as exc:  # PortalStepFailed, or the adapter broke
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
                else:
                    result_line = added
                    recorder.script(
                        "script:add_line",
                        input={"po_line_id": line.po_line_id, "packs": line.packs_wanted},
                        output=f"{result_line.status}: {result_line.packs_in_basket} in basket",
                        url=_url(executor),
                        started=started,
                    )
                    budget.actions += 1
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
                        done.extend(_skip_rest(lines[index + 1 :], "not attempted"))
                        raise _StopNeedsHuman(outcome.note or "the portal asked for a person")
                    if outcome.stopped_reason == "budget":
                        budget_note = outcome.note
            done.append(result_line)
        lines = done

        # --- step 5: read the basket ----------------------------------------
        started = now()
        try:
            raw_rows, subtotal_seen = portal.read_basket(page)
        except JobCancelled:
            raise
        except PortalNeedsHuman as exc:
            recorder.script(
                "script:read_basket",
                outcome="ERROR",
                output=f"needs a person: {exc}",
                url=_url(executor),
                started=started,
            )
            raise _StopNeedsHuman(str(exc)) from exc
        except Exception as exc:  # PortalStepFailed, or the adapter broke
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
                    subtotal_seen = int_or_none(outcome.data.get("subtotal_pence"))
                    basket_read = True
                elif outcome.stopped_reason == "budget":
                    budget_note = outcome.note
        else:
            rows = [dict(r) for r in raw_rows]
            basket_read = True
            recorder.script(
                "script:read_basket",
                output=f"{len(rows)} row(s), subtotal {pounds(subtotal_seen)}",
                url=_url(executor),
                started=started,
            )
            budget.actions += 1

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
        warnings = warnings_for(
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
        tier = highest_tier(lines)
        summary_text = summary(snapshot, supplier)
        job.result = result_dict(snapshot.as_dict())
        if not basket_read and not staged_any:
            job.error = "nothing could be staged or read"
            write_audit(
                session,
                job,
                supplier=supplier,
                snapshot=None,
                outcome=AgentToolOutcome.FAILED,
                summary_text=summary_text,
            )
            _finish(job, BrowserJobStatus.FAILED, now=now())
        else:
            write_audit(
                session,
                job,
                supplier=supplier,
                snapshot=snapshot,
                outcome=AgentToolOutcome.AWAITING_HUMAN,
                summary_text=summary_text,
                tier=tier,
            )
            _finish(job, BrowserJobStatus.SUCCEEDED, now=now())
    except _StopNeedsHuman as exc:
        needs_human = exc.reason
        job.needs_human_reason = needs_human[:1000]
        job.result = result_dict(
            {"lines": [line.as_dict() for line in lines], "needs_human_reason": needs_human}
        )
        write_audit(
            session,
            job,
            supplier=supplier,
            snapshot=None,
            outcome=AgentToolOutcome.AWAITING_HUMAN,
            summary_text=f"Stopped: {needs_human}. Nothing was staged for order {po_id}.",
        )
        _finish(job, BrowserJobStatus.NEEDS_HUMAN, now=now())
    except _StopFailed as exc:
        job.error = exc.reason[:2000]
        job.result = result_dict({"lines": [line.as_dict() for line in lines], "error": exc.reason})
        write_audit(
            session,
            job,
            supplier=supplier,
            snapshot=None,
            outcome=AgentToolOutcome.FAILED,
            summary_text=f"Stopped: {exc.reason}. Nothing was staged for order {po_id}.",
        )
        _finish(job, BrowserJobStatus.FAILED, now=now())
    except JobCancelled as exc:
        job.error = str(exc)
        job.result = result_dict({"lines": [line.as_dict() for line in lines]})
        write_audit(
            session,
            job,
            supplier=supplier,
            snapshot=None,
            outcome=AgentToolOutcome.FAILED,
            summary_text=f"{exc}. Nothing further was done for order {po_id}.",
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
