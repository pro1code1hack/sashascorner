"""Views for the shell area: session in, finished response model out.

Same rule as `api/views/`: build the Pydantic model inside the worker thread, because
the session closes when the view returns (runtime.py). No business rule lives here --
each one is in a service (`services/auth.py`, `sync_runs.py`, `setup_status.py`,
`shell_status.py`, `agent_proposals.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.api.areas.shell_schemas import (
    AgentLabelOut,
    AgentProposalOut,
    AgentProposalsOut,
    AgentRunOut,
    AgentRunsOut,
    BannerActionOut,
    BannerOut,
    DecisionOut,
    RunToolOut,
    SettingsAgentOut,
    SettingsAuthOut,
    SettingsLabourOut,
    SettingsLightspeedOut,
    SettingsOut,
    SettingsTelegramOut,
    SetupCtaOut,
    SetupOut,
    SetupStepOut,
    SetupWarningOut,
    ShellBadgesOut,
    ShellOut,
    ShellSetupOut,
    ShellSyncOut,
    SyncAttemptOut,
    SyncRunOut,
    WaitingOrderOut,
)
from cafeops.config import settings
from cafeops.db.models import AuthCredential, Sale, SyncRun
from cafeops.services import agent_proposals as proposals
from cafeops.services.auth import MIN_PASSWORD_LENGTH, active_session_count, credential_source
from cafeops.services.setup_status import setup_status
from cafeops.services.shell_status import shell_status
from cafeops.services.sync_runs import (
    LIGHTSPEED_ENV_VARS,
    SyncRunView,
    latest_attempt,
    latest_ok,
    lightspeed_ready,
    run_view,
)

# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------


def shell_view(session: Session) -> ShellOut:
    s = shell_status(session)
    return ShellOut(
        sync=ShellSyncOut(
            lightspeed_configured=s.sync.lightspeed_configured,
            last_ok_finished_at=s.sync.last_ok_finished_at,
            last_attempt=(
                None
                if s.sync.last_attempt is None
                else SyncAttemptOut(
                    status=cast(Any, s.sync.last_attempt.status),
                    finished_at=s.sync.last_attempt.finished_at,
                    detail=s.sync.last_attempt.detail,
                )
            ),
            last_sale_at=s.sync.last_sale_at,
            stale_after_hours=s.sync.stale_after_hours,
            is_stale=s.sync.is_stale,
        ),
        badges=ShellBadgesOut(
            orders_waiting=s.orders_waiting,
            proposals_waiting=s.proposals_waiting,
            shop_new=s.shop_new,
        ),
        banners=tuple(
            BannerOut(
                id=cast(Any, b.id),
                instance_key=b.instance_key,
                tone=cast(Any, b.tone),
                text=b.text,
                action=(
                    None
                    if b.action_label is None
                    else BannerActionOut(
                        label=b.action_label,
                        kind=cast(Any, b.action_kind),
                        route=b.action_route,
                    )
                ),
            )
            for b in s.banners
        ),
        setup=ShellSetupOut(empty_install=s.empty_install, open_steps=s.open_steps),
    )


# --------------------------------------------------------------------------
# sync
# --------------------------------------------------------------------------


def sync_run_out(v: SyncRunView) -> SyncRunOut:
    return SyncRunOut(
        id=v.id,
        trigger=cast(Any, v.trigger),
        source=cast(Any, v.source),
        status=cast(Any, v.status),
        started_at=v.started_at,
        finished_at=v.finished_at,
        window_since=v.window_since,
        window_until=v.window_until,
        receipts_seen=v.receipts_seen,
        lines_ingested=v.lines_ingested,
        unresolved_count=v.unresolved_count,
        detail=v.detail,
        requested_by=v.requested_by,
    )


def sync_run_view(session: Session, run_id: int) -> SyncRunOut:
    row = session.get(SyncRun, run_id)
    if row is None:
        raise LookupError(f"no sync run {run_id}")
    return sync_run_out(run_view(row))


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


def settings_view(session: Session) -> SettingsOut:
    cred = session.get(AuthCredential, 1)
    attempt = latest_attempt(session)
    ok = latest_ok(session)
    return SettingsOut(
        auth=SettingsAuthOut(
            source=credential_source(session),
            set_at=cred.set_at if cred else None,
            set_by=cred.set_by if cred else None,
            active_sessions=active_session_count(session),
            min_length=MIN_PASSWORD_LENGTH,
        ),
        lightspeed=SettingsLightspeedOut(
            configured=lightspeed_ready(),
            env_vars=LIGHTSPEED_ENV_VARS,
            last_ok_finished_at=ok.finished_at if ok else None,
            last_sale_at=session.scalar(select(func.max(Sale.sold_at))),
            last_attempt=(
                None
                if attempt is None
                else SyncAttemptOut(
                    status=cast(Any, attempt.status.value),
                    finished_at=attempt.finished_at,
                    detail=attempt.detail,
                )
            ),
        ),
        telegram=SettingsTelegramOut(
            bot_configured=bool(settings.telegram_bot_token),
            owner_chat_configured=bool(settings.telegram_owner_chat_id),
            language="ru",
        ),
        agent=SettingsAgentOut(
            narration="model" if settings.anthropic_api_key else "template",
            model=settings.agent_model if settings.anthropic_api_key else None,
        ),
        labour=SettingsLabourOut(loaded_hourly_rate_pence=settings.loaded_hourly_rate_pence),
    )


# --------------------------------------------------------------------------
# setup
# --------------------------------------------------------------------------


def setup_view(session: Session) -> SetupOut:
    s = setup_status(session)
    return SetupOut(
        empty_install=s.empty_install,
        open_steps=s.open_steps,
        steps=tuple(
            SetupStepOut(
                n=step.n,
                key=step.key,
                done=step.done,
                remaining=step.remaining,
                title=step.title,
                body=step.body,
                cta=(
                    None
                    if step.cta_label is None or step.cta_route is None
                    else SetupCtaOut(label=step.cta_label, route=step.cta_route)
                ),
                cli_fix=step.cli_fix,
            )
            for step in s.steps
        ),
        warnings=tuple(
            SetupWarningOut(severity=cast(Any, w.severity), name=w.name, detail=w.detail, fix=w.fix)
            for w in s.warnings
        ),
    )


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------


def _proposal_out(v: proposals.ProposalView) -> AgentProposalOut:
    return AgentProposalOut(
        id=v.id,
        created_at=v.created_at,
        agent=v.agent,
        agent_label=v.agent_label,
        kind=cast(Any, v.kind),
        subject_ref=v.subject_ref,
        title=v.title,
        body=v.body,
        confidence=cast(Any, v.confidence),
        accept_label=v.accept_label,
        decline_label=v.decline_label,
        accept_mode=v.accept_mode,
        navigate_to=v.navigate_to,
        unavailable_reason=v.unavailable_reason,
        note=v.note,
        status=cast(Any, v.status),
        decided_at=v.decided_at,
        decided_by=v.decided_by,
        decision_note=v.decision_note,
        applied_result=v.applied_result,
        run_id=v.run_id,
    )


def proposals_view(session: Session, *, decided_limit: int) -> AgentProposalsOut:
    waiting, decided = proposals.list_proposals(session, decided_limit=decided_limit)
    return AgentProposalsOut(
        waiting=tuple(_proposal_out(p) for p in waiting),
        decided=tuple(_proposal_out(p) for p in decided),
        waiting_count=sum(1 for p in waiting if p.status == "WAITING"),
        orders_waiting=tuple(
            WaitingOrderOut(
                po_id=o.po_id,
                supplier=o.supplier,
                total_pence=o.total_pence,
                target_delivery_date=o.target_delivery_date,
                created_at=o.created_at,
                note="Waiting in Telegram. Orders are confirmed there, not here.",
            )
            for o in proposals.waiting_orders(session)
        ),
    )


def _decision_out(d: proposals.DecisionOutcome) -> DecisionOut:
    return DecisionOut(
        proposal=_proposal_out(d.proposal),
        outcome=d.outcome,
        applied=d.applied,
        message=d.message,
    )


def decide_view(
    session: Session,
    *,
    proposal_id: int,
    accept: bool,
    decided_by: str,
    note: str | None,
) -> DecisionOut:
    try:
        if accept:
            outcome = proposals.accept_proposal(
                session, proposal_id, decided_by=decided_by, note=note
            )
        else:
            outcome = proposals.decline_proposal(
                session, proposal_id, decided_by=decided_by, note=note
            )
    except proposals.ProposalConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"message": str(exc)}
        ) from None
    return _decision_out(outcome)


def runs_view(
    session: Session, *, agent: str | None, limit: int, before: datetime | None
) -> AgentRunsOut:
    rows, agents, has_more = proposals.run_log(session, agent=agent, limit=limit, before=before)
    return AgentRunsOut(
        runs=tuple(
            AgentRunOut(
                key=r.key,
                run_id=r.run_id,
                agent=r.agent,
                agent_label=r.agent_label,
                tool_label=r.tool_label,
                started_at=r.started_at,
                finished_at=r.finished_at,
                produced=r.produced,
                result=r.result,
                result_tone=r.result_tone,
                read_summary=r.read_summary,
                wrote_summary=r.wrote_summary,
                model=r.model,
                is_decision=r.is_decision,
                tools=tuple(
                    RunToolOut(
                        tool_name=t.tool_name,
                        label=t.label,
                        kind=t.kind,
                        outcome=cast(Any, t.outcome),
                        inputs=t.inputs,
                        output=t.output,
                        refusal_reason=t.refusal_reason,
                    )
                    for t in r.tools
                ),
            )
            for r in rows
        ),
        agents=tuple(AgentLabelOut(agent=a, label=label) for a, label in agents),
        has_more=has_more,
    )


def parse_before(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{raw!r}: expected an ISO timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
