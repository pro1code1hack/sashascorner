"""Agent proposals: the agent inserts, a person decides. DECISIONS.md 7, invariant 10.

Two halves, deliberately in one module so the boundary between them is visible:

**The agent side** is `insert_proposal`. It adds one `agent_proposal` row in status
WAITING and nothing else. It is called from `agent/runner.py` on the agent's
audit-only engine, whose guard (`agent/policies.py`) permits an INSERT into this table
and refuses an UPDATE -- so the agent can put a proposal on the table but can never
move one out of WAITING, even by accident.

**The human side** is everything else: listing, `accept_proposal`, `decline_proposal`.
They run on the normal engine from the API. Nothing leaves WAITING without a named
person (`decided_by`), which the table's CHECK enforces as well. Accepting routes to
the service that already owns that kind of change -- the agent never writes stock,
orders or composition, and neither does this module: it calls the service a human
would have called from the CLI.

- **waste_factor**: adopt the latest drift observation's suggestion, and only if it is
  the value proposed -- `record_count.apply_waste_suggestion`.
- **template_grouping**: materialise, when the proposal names an import `proposal_id`
  -- `materialise_template.materialise_proposal`; otherwise open Recipes.
- **channel_import**: import the export the agent parsed -- `jobs.channel_sync.sync_channel`.
- **data_fix**: open the item's editor; no merge service exists (navigate).
- **supplier_basket**: open the staged basket; never submits (navigate).

Order confirmation is not an agent proposal and is not decided here: it happens in
Telegram (DECISIONS.md 1). `waiting_orders` lists those read-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    AgentActionLog,
    AgentProposal,
    Ingredient,
    POStatus,
    ProposalConfidence,
    ProposalKind,
    ProposalStatus,
    PurchaseOrder,
)
from cafeops.domain.types import AgentProposal as DomainProposal

__all__ = [
    "AGENT_LABELS",
    "DecisionOutcome",
    "ProposalConflict",
    "ProposalView",
    "RunRow",
    "RunTool",
    "WaitingOrder",
    "accept_proposal",
    "agent_for",
    "decline_proposal",
    "insert_proposal",
    "list_proposals",
    "proposal_view",
    "run_log",
    "waiting_count",
    "waiting_orders",
]

AGENT_LABELS: dict[str, str] = {
    "drift_explainer": "Drift explainer",
    "import_assistant": "Import assistant",
    "channel_reporter": "Channel reporter",
    "basket_stager": "Basket stager",
    "boundary_check": "Boundary check",
    "agent": "Agent",
}

_TITLE_MAX = 80


class ProposalConflict(Exception):
    """The proposal cannot be decided in its current state (HTTP 409)."""


# ==========================================================================
# The agent side: insert only
# ==========================================================================


def agent_for(purpose: str | None, tool_name: str | None = None) -> str:
    """Which agent a run was, for rows written before `agent_action_log.agent` existed."""
    text = f"{purpose or ''} {tool_name or ''}".lower()
    if "drift" in text or "waste" in text:
        return "drift_explainer"
    if "channel" in text:
        return "channel_reporter"
    if "basket" in text:
        return "basket_stager"
    if "template" in text or "import" in text or "grouping" in text:
        return "import_assistant"
    if "boundary" in text:
        return "boundary_check"
    return "agent"


def _kind(raw: str) -> ProposalKind:
    return ProposalKind(raw.strip().upper())


def _confidence(raw: str | None) -> ProposalConfidence | None:
    if raw is None:
        return None
    try:
        return ProposalConfidence(raw.strip().upper())
    except ValueError:
        return None


def _clip(text: str, n: int = _TITLE_MAX) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _pct_text(raw: object) -> str | None:
    try:
        value = (Decimal(str(raw)) * 100).quantize(Decimal("0.1")).normalize()
    except (InvalidOperation, ValueError):
        return None
    return f"{value:f}%"


def _compose(proposal: DomainProposal) -> tuple[str, str]:
    """(title, body) in plain English, built from the tool's deterministic payload.

    Never model prose: every figure here came out of the tool that made the proposal,
    so the card's numbers are the ones `verify_figures` would accept.
    """
    p = proposal.payload
    kind = proposal.kind.strip().lower()
    summary = proposal.summary.strip()
    name = summary.split(":")[0].strip() or proposal.subject_ref
    if kind == "waste_factor" and "current" in p and "proposed" in p:
        old, new = _pct_text(p["current"]), _pct_text(p["proposed"])
        if old is not None and new is not None:
            direction = (
                "more" if Decimal(str(p["proposed"])) > Decimal(str(p["current"])) else "less"
            )
            reason = str(p.get("reason") or "").strip()
            body = (
                f"Changing {name}'s waste allowance from {old} to {new} would bring the "
                "stock estimate in line with the last count."
            )
            if reason and reason != "no reason given":
                body += f" Why: {reason}."
            body += " Nothing changes until you accept."
            return _clip(f"{name}: allow {direction} waste ({old} → {new})"), body
    if kind == "channel_import" and "path" in p:
        file = Path(str(p["path"])).name
        platform = str(p.get("platform", "")).replace("_", " ").title()
        days, items = p.get("day_rows"), p.get("item_rows")
        title = _clip(f"{platform} export: {days} day(s), {items} item row(s)")
        body = (
            f"{file} was read and mapped without writing anything: {days} day row(s), "
            f"{items} item row(s), {p.get('rejected', 0)} rejected. Import writes them "
            "to the channel figures."
        )
        return title, body
    if kind == "template_grouping" and "item_names" in p:
        raw_items = p.get("item_names")
        items = [str(i) for i in raw_items] if isinstance(raw_items, (list, tuple)) else []
        template = str(p.get("template_name") or name)
        title = _clip(f"{template}: {len(items)} item(s) could share one recipe")
        shown = ", ".join(items[:6]) + (" …" if len(items) > 6 else "")
        tail = summary.split(" -- ", 1)[1].strip() if " -- " in summary else ""
        body = f"{shown}." + (f" {tail}" if tail else "")
        return title, body
    head = summary.split(" -- ")[0].strip() or summary
    return _clip(head), summary


def insert_proposal(
    session: Session,
    *,
    run_id: str,
    log_id: int,
    agent: str,
    proposal: DomainProposal,
    figures: tuple[str, ...] | list[str] = (),
    title: str | None = None,
    body: str | None = None,
) -> int | None:
    """Put one proposal on the table, WAITING. The agent's only write besides its log.

    Returns the new id, or None when there is nothing to propose (`confidence="none"`:
    the tool refused, e.g. no such ingredient or an unmappable file -- a card saying
    "nothing to review" would be noise in a person's queue). Never commits: the runner
    commits it with its log row, so a proposal never exists without the logged call
    that produced it.
    """
    if (proposal.confidence or "").strip().lower() == "none":
        return None
    payload: dict[str, Any] = {str(k): _jsonable(v) for k, v in proposal.payload.items()}
    composed_title, composed_body = _compose(proposal)
    row = AgentProposal(
        created_at=datetime.now(UTC),
        run_id=run_id,
        log_id=log_id,
        agent=agent[:60],
        kind=_kind(proposal.kind),
        subject_ref=proposal.subject_ref[:200],
        title=(title or composed_title)[:120],
        body=body or composed_body,
        payload=payload,
        confidence=_confidence(proposal.confidence),
        figures=[str(f) for f in figures],
        status=ProposalStatus.WAITING,
    )
    session.add(row)
    session.flush()
    return row.id


def _jsonable(value: object) -> object:
    """Decimals as strings (invariant 11); everything else JSON-safe or its str()."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


# ==========================================================================
# Presentation: what a person sees and may press
# ==========================================================================

AcceptMode = Literal["apply", "navigate", "unavailable"]


@dataclass(frozen=True, slots=True)
class ProposalView:
    id: int
    created_at: datetime
    agent: str
    agent_label: str
    kind: str
    subject_ref: str
    title: str
    body: str
    confidence: str | None
    accept_label: str
    decline_label: str
    accept_mode: AcceptMode
    navigate_to: str | None
    unavailable_reason: str | None
    #: A line the card shows under the body ("Changes stock depletion only...").
    note: str | None
    status: str
    decided_at: datetime | None
    decided_by: str | None
    decision_note: str | None
    applied_result: dict[str, Any] | None
    run_id: str


def _presentation(
    row: AgentProposal,
) -> tuple[str, str, AcceptMode, str | None, str | None, str | None]:
    """(accept_label, decline_label, mode, navigate_to, unavailable_reason, note)."""
    p = row.payload or {}
    kind = row.kind
    if kind is ProposalKind.WASTE_FACTOR:
        if "ingredient_id" not in p or "proposed" not in p:
            return (
                "Accept",
                "Decline",
                "unavailable",
                None,
                "This proposal does not name an ingredient and a value, so there is "
                "nothing to apply.",
                None,
            )
        return (
            "Accept",
            "Decline",
            "apply",
            None,
            None,
            "Changes stock depletion only, not menu costs.",
        )
    if kind is ProposalKind.TEMPLATE_GROUPING:
        if isinstance(p.get("proposal_id"), str) and p.get("proposal_id"):
            return ("Confirm recipe", "Not now", "apply", None, None, None)
        return (
            "Review in Recipes",
            "Not now",
            "navigate",
            "#/recipes",
            None,
            "The agent grouped items by name; the recipe itself is set in Recipes.",
        )
    if kind is ProposalKind.CHANNEL_IMPORT:
        if p.get("error") or not p.get("path") or not p.get("platform"):
            return (
                "Import",
                "Decline",
                "unavailable",
                None,
                "The file could not be mapped when the agent read it, so there is "
                "nothing safe to import.",
                None,
            )
        return ("Import", "Decline", "apply", None, None, None)
    if kind is ProposalKind.DATA_FIX:
        return (
            str(p.get("accept_label") or "Open"),
            str(p.get("decline_label") or "Keep separate"),
            "navigate",
            str(p.get("navigate_to") or "#/menu"),
            None,
            "There is no merge service yet; the change is made by hand in the editor.",
        )
    # SUPPLIER_BASKET: order-related, so it only ever navigates (DECISIONS 1).
    return (
        "Open basket",
        "Discard",
        "navigate",
        str(p.get("basket_url") or "#/orders"),
        None,
        "Staged, not sent. Pay on the supplier's site, then press Mark sent on the order.",
    )


def proposal_view(row: AgentProposal) -> ProposalView:
    accept, decline, mode, nav, reason, note = _presentation(row)
    return ProposalView(
        id=row.id,
        created_at=row.created_at,
        agent=row.agent,
        agent_label=AGENT_LABELS.get(row.agent, row.agent.replace("_", " ").capitalize()),
        kind=row.kind.value.lower(),
        subject_ref=row.subject_ref,
        title=row.title,
        body=row.body,
        confidence=None if row.confidence is None else row.confidence.value.lower(),
        accept_label=accept,
        decline_label=decline,
        accept_mode=mode,
        navigate_to=nav,
        unavailable_reason=reason,
        note=note,
        status=row.status.value,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        decision_note=row.decision_note,
        applied_result=row.applied_result,
        run_id=row.run_id,
    )


def waiting_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count(AgentProposal.id)).where(
                AgentProposal.status == ProposalStatus.WAITING
            )
        )
        or 0
    )


def list_proposals(
    session: Session, *, decided_limit: int = 6
) -> tuple[list[ProposalView], list[ProposalView]]:
    """(waiting oldest-first, the last `decided_limit` decided newest-first)."""
    waiting = [
        proposal_view(r)
        for r in session.scalars(
            select(AgentProposal)
            .where(AgentProposal.status.in_([ProposalStatus.WAITING, ProposalStatus.APPLY_FAILED]))
            .order_by(AgentProposal.created_at.asc(), AgentProposal.id.asc())
        )
    ]
    decided = [
        proposal_view(r)
        for r in session.scalars(
            select(AgentProposal)
            .where(AgentProposal.decided_at.is_not(None))
            .order_by(AgentProposal.decided_at.desc(), AgentProposal.id.desc())
            .limit(decided_limit)
        )
    ]
    return waiting, decided


@dataclass(frozen=True, slots=True)
class WaitingOrder:
    po_id: int
    supplier: str
    total_pence: int
    target_delivery_date: date
    created_at: datetime


def waiting_orders(session: Session) -> list[WaitingOrder]:
    """Orders sent to Telegram and not yet confirmed. Read-only here (DECISIONS 1)."""
    rows = session.scalars(
        select(PurchaseOrder)
        .where(PurchaseOrder.status == POStatus.PENDING_CONFIRM)
        .order_by(PurchaseOrder.created_at.asc())
    )
    return [
        WaitingOrder(
            po_id=po.id,
            supplier=po.supplier.name,
            total_pence=po.total_pence,
            target_delivery_date=po.target_delivery_date,
            created_at=po.created_at,
        )
        for po in rows
    ]


# ==========================================================================
# The human side: decide
# ==========================================================================

Outcome = Literal["applied", "recorded", "superseded", "failed", "declined"]


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    proposal: ProposalView
    outcome: Outcome
    applied: dict[str, str] | None
    message: str


class _Superseded(Exception):
    """The world moved since the agent proposed this. Nothing was applied."""


def _load_for_decision(session: Session, proposal_id: int) -> AgentProposal:
    row = session.get(AgentProposal, proposal_id)
    if row is None:
        raise LookupError(f"no agent proposal {proposal_id}")
    if row.status not in (ProposalStatus.WAITING, ProposalStatus.APPLY_FAILED):
        who = f" by {row.decided_by}" if row.decided_by else ""
        raise ProposalConflict(
            f"This proposal was already decided ({row.status.value.lower()}{who}). "
            "Nothing was changed."
        )
    return row


def _operator(decided_by: str) -> str:
    name = " ".join(decided_by.split())
    if not name:
        raise ValueError("a decision needs the name of the person making it (decided_by)")
    return name[:120]


def _decimal(raw: object) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise _Superseded(f"the proposed value {raw!r} is not a number") from exc


def _apply_waste_factor(session: Session, row: AgentProposal) -> tuple[dict[str, str], str]:
    from cafeops.services.record_count import apply_waste_suggestion

    p = row.payload or {}
    ingredient = session.get(Ingredient, int(str(p["ingredient_id"])))
    if ingredient is None:
        raise _Superseded("that ingredient no longer exists")
    current = _decimal(p.get("current"))
    proposed = _decimal(p.get("proposed"))
    if ingredient.waste_factor != current:
        raise _Superseded(
            f"{ingredient.name}'s waste allowance is now {ingredient.waste_factor}, not "
            f"{current} as it was when the agent looked"
        )
    result = apply_waste_suggestion(session, ingredient_id=ingredient.id)
    if result is None:
        raise _Superseded(
            f"the latest count no longer suggests a different waste allowance for {ingredient.name}"
        )
    old, new = result
    # The agent narrates, it does not choose numbers (CLAUDE.md §9): only the value the
    # drift report computes may be applied, and only when it is the one proposed.
    if new.quantize(Decimal("0.001")) != proposed.quantize(Decimal("0.001")):
        raise _Superseded(
            f"the drift report now suggests {new.normalize()} for {ingredient.name}, not "
            f"the proposed {proposed.normalize()}"
        )
    applied = {"ingredient": ingredient.name, "old": str(old), "new": str(new)}
    message = (
        f"Accepted. {ingredient.name}'s waste allowance is now {_pct(new)} (was "
        f"{_pct(old)}). Stock estimates use it from now on; menu costs are unchanged."
    )
    return applied, message


def _pct(fraction: Decimal) -> str:
    value = (fraction * 100).quantize(Decimal("0.1")).normalize()
    return f"{value:f}%"


def _apply_channel_import(session: Session, row: AgentProposal) -> tuple[dict[str, str], str]:
    from cafeops.db.models import SalesChannelName
    from cafeops.integrations.channels.base import UnmappableReportError
    from cafeops.integrations.channels.csv_source import CsvChannelSource
    from cafeops.jobs.channel_sync import sync_channel

    p = row.payload or {}
    path = Path(str(p["path"]))
    if not path.exists():
        raise _Superseded(f"{path.name} is no longer where the agent found it")
    channel = SalesChannelName(str(p["platform"]).upper())
    source = CsvChannelSource(files=[path], platform=channel)
    try:
        parsed = source.parse_file(path, platform=channel)
    except UnmappableReportError as exc:
        raise _Superseded(f"{path.name} can no longer be read: {exc}") from exc
    dates = [r.metric_date for r in parsed.day_rows] + [r.metric_date for r in parsed.item_rows]
    if not dates:
        raise _Superseded(f"{path.name} has no rows to import")
    report = sync_channel(
        session, channel=channel, since=min(dates), until=max(dates), primary=source
    )
    applied = {
        "file": path.name,
        "channel": channel.value,
        "days": f"+{report.days_inserted}/~{report.days_updated}",
        "items": f"+{report.items_inserted}/~{report.items_updated}",
        "unresolved": str(len(report.unresolved)),
    }
    message = (
        f"Imported {path.name}: {report.days_inserted} new day(s), {report.days_updated} "
        f"updated; {report.items_inserted + report.items_updated} item row(s)."
    )
    if report.unresolved:
        message += f" {len(report.unresolved)} item name(s) matched nothing and were skipped."
    return applied, message


def _decide(
    row: AgentProposal,
    status: ProposalStatus,
    *,
    decided_by: str,
    note: str | None,
    applied_result: dict[str, Any] | None,
) -> None:
    row.status = status
    row.decided_at = datetime.now(UTC)
    row.decided_by = decided_by
    row.decision_note = (note or "").strip() or None
    row.applied_result = applied_result


def accept_proposal(
    session: Session, proposal_id: int, *, decided_by: str, note: str | None = None
) -> DecisionOutcome:
    """Accept, through the kind's service. The caller commits.

    SUPERSEDED and APPLY_FAILED are decisions too, recorded with the person's name:
    the world moved, or the service refused, and nothing was applied. A failed apply
    rolls back to a savepoint, so the refusal is on record but none of the change is.
    """
    name = _operator(decided_by)
    row = _load_for_decision(session, proposal_id)
    _, _, mode, nav, reason, _ = _presentation(row)

    if mode == "unavailable":
        raise ProposalConflict(reason or "This proposal cannot be applied here.")

    if mode == "navigate":
        # The change was made by hand in another screen; this records that it was.
        _decide(
            row,
            ProposalStatus.ACCEPTED,
            decided_by=name,
            note=note,
            applied_result={"applied": "nothing here", "where": nav or ""},
        )
        return DecisionOutcome(
            proposal=proposal_view(row),
            outcome="recorded",
            applied=None,
            message="Marked as done. Nothing was changed from this screen.",
        )

    if row.kind is ProposalKind.TEMPLATE_GROUPING:
        return _accept_template(session, row, decided_by=name, note=note)

    try:
        with session.begin_nested():
            if row.kind is ProposalKind.WASTE_FACTOR:
                applied, message = _apply_waste_factor(session, row)
            elif row.kind is ProposalKind.CHANNEL_IMPORT:
                applied, message = _apply_channel_import(session, row)
            else:  # pragma: no cover - _presentation makes every other kind navigate
                raise ProposalConflict("no service applies this kind of proposal")
    except _Superseded as exc:
        _decide(
            row,
            ProposalStatus.SUPERSEDED,
            decided_by=name,
            note=note,
            applied_result={"superseded": str(exc)},
        )
        return DecisionOutcome(
            proposal=proposal_view(row),
            outcome="superseded",
            applied=None,
            message=f"This changed since the agent proposed it: {exc}. Nothing was applied.",
        )
    except ProposalConflict:
        raise
    except Exception as exc:
        _decide(
            row,
            ProposalStatus.APPLY_FAILED,
            decided_by=name,
            note=note,
            applied_result={"error": f"{type(exc).__name__}: {exc}"[:1000]},
        )
        return DecisionOutcome(
            proposal=proposal_view(row),
            outcome="failed",
            applied=None,
            message=f"It didn't apply, and nothing was changed: {exc}",
        )

    _decide(row, ProposalStatus.ACCEPTED, decided_by=name, note=note, applied_result=applied)
    return DecisionOutcome(
        proposal=proposal_view(row), outcome="applied", applied=applied, message=message
    )


def _accept_template(
    session: Session, row: AgentProposal, *, decided_by: str, note: str | None
) -> DecisionOutcome:
    """Materialise commits its own transaction, so the decision is recorded after it."""
    from cafeops.services.materialise_template import (
        AmbiguousProposal,
        ProposalAlreadyMaterialised,
        ProposalHasConflicts,
        ProposalNotFound,
        materialise_proposal,
    )

    key = str((row.payload or {})["proposal_id"])
    row_id = row.id
    try:
        report = materialise_proposal(session, key, actor=decided_by)
    except (ProposalAlreadyMaterialised, ProposalNotFound) as exc:
        session.rollback()
        fresh = session.get(AgentProposal, row_id)
        if fresh is None:  # pragma: no cover
            raise LookupError(f"no agent proposal {row_id}") from exc
        _decide(
            fresh,
            ProposalStatus.SUPERSEDED,
            decided_by=decided_by,
            note=note,
            applied_result={"superseded": str(exc)},
        )
        return DecisionOutcome(
            proposal=proposal_view(fresh),
            outcome="superseded",
            applied=None,
            message=f"This changed since the agent proposed it: {exc}. Nothing was applied.",
        )
    except (ProposalHasConflicts, AmbiguousProposal) as exc:
        session.rollback()
        raise ProposalConflict(
            f"{exc} Review it in Recipes, where the conflicting quantities are shown."
        ) from exc

    fresh = session.get(AgentProposal, row_id)
    if fresh is None:  # pragma: no cover
        raise LookupError(f"no agent proposal {row_id}")
    applied = {
        "template": report.proposal_name,
        "template_id": str(report.template_id),
        "items_repointed": str(report.items_repointed),
    }
    _decide(
        fresh, ProposalStatus.ACCEPTED, decided_by=decided_by, note=note, applied_result=applied
    )
    return DecisionOutcome(
        proposal=proposal_view(fresh),
        outcome="applied",
        applied=applied,
        message=(
            f"Confirmed. {report.proposal_name} is now a recipe covering "
            f"{report.items_repointed} menu item(s), from today."
        ),
    )


def decline_proposal(
    session: Session, proposal_id: int, *, decided_by: str, note: str | None = None
) -> DecisionOutcome:
    name = _operator(decided_by)
    row = _load_for_decision(session, proposal_id)
    _decide(row, ProposalStatus.DECLINED, decided_by=name, note=note, applied_result=None)
    return DecisionOutcome(
        proposal=proposal_view(row),
        outcome="declined",
        applied=None,
        message="Declined. Nothing was changed.",
    )


# ==========================================================================
# The run log
# ==========================================================================

_TOOL_LABELS: dict[str, str] = {
    "read_drift_report": "read: drift report",
    "read_expiry_writeoffs": "read: expiry write-offs",
    "read_channel_performance": "read: channel performance",
    "browser_plan_channel_report": "browser: partner portal → reports",
    "browser_stage_supplier_basket": "browser: supplier portal → basket",
    "propose_waste_factor": "propose: waste allowance",
    "propose_template_grouping": "propose: recipe grouping",
    "propose_channel_import": "propose: channel import",
    "narrate": "narrate",
}
_READ_NOUNS: dict[str, str] = {
    "read_drift_report": "drift report",
    "read_expiry_writeoffs": "write-offs",
    "read_channel_performance": "channel performance",
    "browser_plan_channel_report": "partner portal reports",
    "browser_stage_supplier_basket": "the draft order",
}


def _tool_kind(name: str) -> str:
    if name.startswith("read_"):
        return "READ"
    if name.startswith("propose_"):
        return "PROPOSE"
    if name.startswith("browser_"):
        return "BROWSER"
    if name == "narrate":
        return "NARRATE"
    return "OTHER"


@dataclass(frozen=True, slots=True)
class RunTool:
    tool_name: str
    label: str
    kind: str
    outcome: str
    inputs: dict[str, Any]
    output: str | None
    refusal_reason: str | None


@dataclass(frozen=True, slots=True)
class RunRow:
    """One agent run, or one human decision rendered into the log (`is_decision`)."""

    key: str
    run_id: str | None
    agent: str
    agent_label: str
    tool_label: str
    started_at: datetime
    finished_at: datetime
    produced: str
    result: str
    result_tone: Literal["plain", "alert"]
    read_summary: str
    wrote_summary: str
    model: str | None
    is_decision: bool = False
    tools: tuple[RunTool, ...] = field(default_factory=tuple)


def _short(text: str | None, n: int) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def _read_summary(tools: list[AgentActionLog]) -> str:
    nouns: list[str] = []
    for t in tools:
        noun = _READ_NOUNS.get(t.tool_name)
        if noun is None:
            continue
        inputs = t.inputs or {}
        subject = inputs.get("ingredient") or inputs.get("channel") or inputs.get("po_id")
        if subject is not None:
            noun = f"{noun} ({subject})"
        if noun not in nouns:
            nouns.append(noun)
    return ", ".join(nouns) if nouns else "nothing"


def _run_row(tools: list[AgentActionLog], proposals: list[AgentProposal]) -> RunRow:
    tools = sorted(tools, key=lambda t: (t.occurred_at, t.id))
    first = tools[0]
    agent = next((t.agent for t in tools if t.agent), None) or agent_for(
        first.purpose, " ".join(t.tool_name for t in tools)
    )
    names = [t.tool_name for t in tools]
    kinds = {_tool_kind(n) for n in names}
    failed = [t for t in tools if t.outcome.value == "FAILED"]
    refused = [t for t in tools if t.outcome.value == "REFUSED"]
    narration = next((t for t in reversed(tools) if t.tool_name == "narrate"), None)
    tone: Literal["plain", "alert"]

    if failed and "BROWSER" in kinds:
        result, tone = "Failed. Use a CSV export", "alert"
    elif failed:
        result, tone = "Failed", "alert"
    elif refused:
        result, tone = "Refused: " + _short(refused[0].refusal_reason, 60), "plain"
    elif proposals:
        status = proposals[-1].status
        result = {
            ProposalStatus.WAITING: "Proposal waiting",
            ProposalStatus.ACCEPTED: "Accepted",
            ProposalStatus.DECLINED: "Declined",
            ProposalStatus.SUPERSEDED: "Out of date",
            ProposalStatus.APPLY_FAILED: "Didn't apply",
        }[status]
        tone = "alert" if status is ProposalStatus.APPLY_FAILED else "plain"
    elif "browser_stage_supplier_basket" in names:
        result, tone = "Staged for review", "plain"
    elif narration is not None:
        result, tone = "Explained", "plain"
    else:
        result, tone = "Done", "plain"

    if proposals:
        produced = proposals[-1].title
    elif narration is not None and narration.output:
        produced = f"“{_short(narration.output, 160)}”"
    else:
        last = next((t for t in reversed(tools) if t.output), None)
        produced = _short(last.output if last else None, 160) or "nothing"

    wrote = "nothing directly"
    if "PROPOSE" in kinds:
        wrote += " (proposal only)"
    if "browser_stage_supplier_basket" in names:
        wrote += " (basket staged, not sent)"

    headline_tool = next((t for t in tools if t.tool_name != "narrate"), first).tool_name
    return RunRow(
        key=f"run:{first.run_id}",
        run_id=first.run_id,
        agent=agent,
        agent_label=AGENT_LABELS.get(agent, agent.replace("_", " ").capitalize()),
        tool_label=_TOOL_LABELS.get(headline_tool, headline_tool),
        started_at=first.occurred_at,
        finished_at=tools[-1].occurred_at,
        produced=produced,
        result=result,
        result_tone=tone,
        read_summary=_read_summary(tools),
        wrote_summary=wrote,
        model=next((t.model for t in reversed(tools) if t.model), None),
        tools=tuple(
            RunTool(
                tool_name=t.tool_name,
                label=_TOOL_LABELS.get(t.tool_name, t.tool_name),
                kind=_tool_kind(t.tool_name),
                outcome=t.outcome.value,
                inputs=dict(t.inputs or {}),
                output=t.output,
                refusal_reason=t.refusal_reason,
            )
            for t in tools
        ),
    )


def _decision_row(p: AgentProposal) -> RunRow:
    assert p.decided_at is not None
    verb = {
        ProposalStatus.ACCEPTED: "accept",
        ProposalStatus.DECLINED: "decline",
        ProposalStatus.SUPERSEDED: "accept (out of date)",
        ProposalStatus.APPLY_FAILED: "accept (didn't apply)",
    }.get(p.status, p.status.value.lower())
    return RunRow(
        key=f"decision:{p.id}",
        run_id=p.run_id,
        agent="person",
        agent_label=f"{p.decided_by or 'Someone'} (web)",
        tool_label=verb,
        started_at=p.decided_at,
        finished_at=p.decided_at,
        produced=p.title,
        result={
            ProposalStatus.ACCEPTED: "Accepted",
            ProposalStatus.DECLINED: "Declined",
            ProposalStatus.SUPERSEDED: "Out of date",
            ProposalStatus.APPLY_FAILED: "Didn't apply",
        }.get(p.status, p.status.value.lower()),
        result_tone="alert" if p.status is ProposalStatus.APPLY_FAILED else "plain",
        read_summary="",
        wrote_summary="",
        model=None,
        is_decision=True,
    )


#: How many runs the log scans. The table is small (a handful of runs a day at most);
#: a cap keeps a pathological log from making the screen slow.
_SCAN_RUNS = 400


def run_log(
    session: Session,
    *,
    agent: str | None = None,
    limit: int = 30,
    before: datetime | None = None,
) -> tuple[list[RunRow], list[tuple[str, str]], bool]:
    """(rows newest-first, the agents seen [(agent, label)], has_more).

    Human decisions from `agent_proposal.decided_*` are interleaved as rows of their
    own. They are NOT stored in `agent_action_log`: that table is every *agent*
    action, and only the agent's audit engine writes it.
    """
    summaries = session.execute(
        select(AgentActionLog.run_id, func.max(AgentActionLog.occurred_at))
        .group_by(AgentActionLog.run_id)
        .order_by(func.max(AgentActionLog.occurred_at).desc())
        .limit(_SCAN_RUNS)
    ).all()
    run_ids = [r[0] for r in summaries]
    by_run: dict[str, list[AgentActionLog]] = {}
    if run_ids:
        for t in session.scalars(select(AgentActionLog).where(AgentActionLog.run_id.in_(run_ids))):
            by_run.setdefault(t.run_id, []).append(t)
    props_by_run: dict[str, list[AgentProposal]] = {}
    if run_ids:
        for p in session.scalars(
            select(AgentProposal)
            .where(AgentProposal.run_id.in_(run_ids))
            .order_by(AgentProposal.created_at, AgentProposal.id)
        ):
            props_by_run.setdefault(p.run_id, []).append(p)

    rows = [_run_row(by_run[rid], props_by_run.get(rid, [])) for rid in run_ids if rid in by_run]
    agents = sorted({(r.agent, r.agent_label) for r in rows}, key=lambda a: a[1])
    if agent:
        rows = [r for r in rows if r.agent == agent]

    decisions = [
        _decision_row(p)
        for p in session.scalars(
            select(AgentProposal)
            .where(AgentProposal.decided_at.is_not(None))
            .order_by(AgentProposal.decided_at.desc())
            .limit(_SCAN_RUNS)
        )
        if agent is None or p.agent == agent
    ]
    merged = sorted(rows + decisions, key=lambda r: r.finished_at, reverse=True)
    if before is not None:
        merged = [r for r in merged if r.finished_at < before]
    return merged[:limit], agents, len(merged) > limit
