"""Put the drift report's waste-factor suggestions in the back office's review queue.

Spec 5.2: a count in the 10-15% band proposes a `waste_factor` adjustment and stays
manual. The number was always computed (`record_count.waste_suggestion`), but it reached
a person only through `cafeops drift --apply-waste` or the Claude-powered narration
agent. With neither in use -- the owner works in the web app, and the agent needs an
API key -- a suggestion was computed every week and seen by nobody.

This is the deterministic proposer. It is NOT an agent and chooses nothing: the value
proposed is the drift maths' own, and the accept step (`agent_proposals._apply_waste_
factor`) re-computes it and refuses if it has moved. Every proposal is written with an
`agent_action_log` row, the same record the narration agent leaves (invariant 10's
"every action is logged"), under the agent name `drift_report`.

Services flush; the caller commits (ARCHITECTURE 8Y).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import AgentProposal, Ingredient
from cafeops.db.repositories.agent_log import SqlAgentLogRepository
from cafeops.db.repositories.drift import SqlDriftRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.domain.enums import AgentToolOutcome, ProposalKind, ProposalStatus, Tier
from cafeops.domain.types import AgentProposal as DomainProposal
from cafeops.services.agent_proposals import insert_proposal
from cafeops.services.record_count import waste_suggestion

__all__ = ["AGENT_NAME", "WasteProposalRun", "propose_waste_factors"]

#: The name these proposals and their log rows carry in the Agents screen.
AGENT_NAME = "drift_report"
_TOOL = "propose_waste_factor"


@dataclass(frozen=True, slots=True)
class WasteProposalRun:
    run_id: str
    #: New proposal ids, one per ingredient whose suggestion was put on the table.
    proposed: tuple[int, ...]
    #: Ingredients skipped because a waste-factor proposal is already waiting on them.
    already_waiting: tuple[str, ...]
    #: Ingredients whose latest count was already proposed once, whatever was decided.
    already_proposed: tuple[str, ...] = ()


def _subject(ingredient_id: int) -> str:
    return f"ingredient:{ingredient_id}"


def _existing(session: Session) -> tuple[set[str], set[tuple[str, str]]]:
    """Subjects with a WAITING proposal, and every (subject, observation) ever proposed.

    One count yields at most one proposal. Without the second set, accepting a
    proposal changes the waste factor, the SAME observation then computes a fresh
    suggestion from the new value, and the queue re-proposes off one count forever.
    """
    rows = session.execute(
        select(AgentProposal.subject_ref, AgentProposal.status, AgentProposal.payload).where(
            AgentProposal.kind == ProposalKind.WASTE_FACTOR
        )
    )
    waiting: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for subject, status, payload in rows:
        if status is ProposalStatus.WAITING:
            waiting.add(subject)
        observed = (payload or {}).get("observed_at")
        if observed:
            seen.add((subject, str(observed)))
    return waiting, seen


def _pct(value: object) -> str:
    return f"{float(str(value)) * 100:.1f}%"


def propose_waste_factors(
    session: Session,
    *,
    at: datetime,
    tiers: tuple[Tier, ...] = (Tier.A, Tier.B),
) -> WasteProposalRun:
    """One WAITING proposal per tracked ingredient whose latest count suggests a new
    waste factor, unless one is already waiting for it. Applies nothing."""
    run_id = f"drift-{at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    log = SqlAgentLogRepository(session)
    drift_repo = SqlDriftRepository(session)
    waiting, seen = _existing(session)
    proposed: list[int] = []
    skipped: list[str] = []
    done_before: list[str] = []

    for snapshot in SqlIngredientRepository(session).list_tracked(tiers=tiers):
        found = waste_suggestion(session, ingredient_id=snapshot.id)
        if found is None:
            continue
        current, suggested = found
        if suggested == current:
            continue
        subject = _subject(snapshot.id)
        if subject in waiting:
            skipped.append(snapshot.name)
            continue
        latest = drift_repo.history(snapshot.id, limit=1)
        observed_at = latest[0].observed_at.isoformat() if latest else ""
        if (subject, observed_at) in seen:
            done_before.append(snapshot.name)
            continue
        ingredient = session.get(Ingredient, snapshot.id)
        name = ingredient.name if ingredient is not None else snapshot.name
        reason = (
            "the latest count is in the 10-15% tuning band, and this is the allowance the "
            "drift report computes from the consumption since the previous count"
        )
        summary = f"{name}: waste_factor {current:.3f} -> {suggested:.3f} -- {reason}"
        inputs: dict[str, object] = {
            "ingredient": name,
            "waste_factor": str(suggested),
            "current": str(current),
        }
        log_id = log.log(
            run_id=run_id,
            tool_name=_TOOL,
            inputs=inputs,
            outcome=AgentToolOutcome.AWAITING_HUMAN,
            output=f"Proposed (not applied): {summary}",
            purpose="weekly drift report: tuning-band waste allowance",
            agent=AGENT_NAME,
        )
        proposal_id = insert_proposal(
            session,
            run_id=run_id,
            log_id=log_id,
            agent=AGENT_NAME,
            proposal=DomainProposal(
                kind="waste_factor",
                subject_ref=subject,
                summary=summary,
                payload={
                    "ingredient_id": snapshot.id,
                    "current": str(current),
                    "proposed": str(suggested),
                    "reason": reason,
                    "observed_at": observed_at,
                },
                confidence="medium",
            ),
            figures=(f"{current:.3f}", f"{suggested:.3f}"),
            title=f"{name}: waste allowance {_pct(current)} -> {_pct(suggested)}",
        )
        if proposal_id is not None:
            proposed.append(proposal_id)
            waiting.add(subject)

    return WasteProposalRun(
        run_id=run_id,
        proposed=tuple(proposed),
        already_waiting=tuple(skipped),
        already_proposed=tuple(done_before),
    )
