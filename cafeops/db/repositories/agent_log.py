"""`agent_action_log` persistence. Implements `protocols.AgentLogRepository`.

Spec 9's hard rule -- "every action logged to `agent_action_log` with inputs, output
and tool" -- is only worth anything if the log is written by the *machinery* rather
than by whoever remembered to. So nothing in `cafeops/agent/` calls `log()` directly:
`agent/runner.py` writes a row around every dispatch, on every path, including the
refusals and the crashes. This module is the narrow thing it writes through.

`outcome = REFUSED` rows are the interesting ones. They are the evidence that the
allowlist held, and `refusals()` exists so they can be read back without knowing the
enum's storage.

One deliberate asymmetry: this repository is the single write the agent machinery is
permitted to make, and it writes only to this table. It is constructed from its own
session in `agent/runner.py` precisely so the log survives even when the surrounding
unit of work is rolled back -- a refused action that leaves no trace is the one
failure mode this table exists to prevent.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.db.models import AgentActionLog
from cafeops.domain.types import AgentToolOutcome

#: Output is TEXT; a model that runs long must not fail an insert. Truncation is
#: marked so a reader knows the row is abridged rather than complete.
MAX_OUTPUT_CHARS = 20_000
TRUNCATION_MARK = "\n...[truncated]"


@dataclass(frozen=True, slots=True)
class AgentLogRow:
    """One logged action, flattened for display."""

    id: int
    occurred_at: datetime
    run_id: str
    tool_name: str
    outcome: AgentToolOutcome
    purpose: str | None
    inputs: dict[str, Any]
    output: str | None
    refusal_reason: str | None
    proposal_ref: str | None
    model: str | None


def _jsonable(value: object) -> object:
    """Make inputs storable without losing what they were.

    A `Decimal`, a `date` or a dataclass in the inputs dict must not crash the log
    write -- the log is the audit trail, and an audit trail that fails on an
    unexpected type stops being one exactly when something unexpected happened.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


class SqlAgentLogRepository:
    """Spec 9: every agent action logged with inputs, output and the tool called."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def log(
        self,
        *,
        run_id: str,
        tool_name: str,
        inputs: dict[str, object],
        outcome: object,
        output: str | None = None,
        purpose: str | None = None,
        refusal_reason: str | None = None,
        proposal_ref: str | None = None,
        model: str | None = None,
    ) -> int:
        if not isinstance(outcome, AgentToolOutcome):
            raise TypeError(f"outcome must be an AgentToolOutcome, got {outcome!r}")
        if output is not None and len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + TRUNCATION_MARK
        row = AgentActionLog(
            run_id=run_id,
            tool_name=tool_name,
            inputs={str(k): _jsonable(v) for k, v in inputs.items()},
            outcome=outcome,
            output=output,
            purpose=purpose,
            refusal_reason=refusal_reason,
            proposal_ref=proposal_ref,
            model=model,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def for_run(self, run_id: str) -> list[object]:
        stmt = (
            select(AgentActionLog)
            .where(AgentActionLog.run_id == run_id)
            .order_by(AgentActionLog.id)
        )
        return list(self.session.scalars(stmt))

    def refusals(self, *, since: datetime) -> list[object]:
        """Actions the whitelist blocked. The interesting rows."""
        stmt = (
            select(AgentActionLog)
            .where(
                AgentActionLog.outcome == AgentToolOutcome.REFUSED,
                AgentActionLog.occurred_at >= since,
            )
            .order_by(AgentActionLog.occurred_at.desc(), AgentActionLog.id.desc())
        )
        return list(self.session.scalars(stmt))

    # -- reads for the CLI -------------------------------------------------

    def rows(
        self,
        *,
        run_id: str | None = None,
        outcome: AgentToolOutcome | None = None,
        limit: int = 50,
    ) -> list[AgentLogRow]:
        stmt = select(AgentActionLog)
        if run_id is not None:
            stmt = stmt.where(AgentActionLog.run_id == run_id)
        if outcome is not None:
            stmt = stmt.where(AgentActionLog.outcome == outcome)
        stmt = stmt.order_by(AgentActionLog.id.desc()).limit(limit)
        return [
            AgentLogRow(
                id=r.id,
                occurred_at=r.occurred_at,
                run_id=r.run_id,
                tool_name=r.tool_name,
                outcome=r.outcome,
                purpose=r.purpose,
                inputs=dict(r.inputs or {}),
                output=r.output,
                refusal_reason=r.refusal_reason,
                proposal_ref=r.proposal_ref,
                model=r.model,
            )
            for r in self.session.scalars(stmt)
        ]

    def runs(self, *, limit: int = 20) -> list[tuple[str, int, datetime]]:
        """(run_id, action count, most recent action), newest first."""
        stmt = (
            select(
                AgentActionLog.run_id,
                func.count(AgentActionLog.id),
                func.max(AgentActionLog.occurred_at),
            )
            .group_by(AgentActionLog.run_id)
            .order_by(func.max(AgentActionLog.occurred_at).desc())
            .limit(limit)
        )
        return [(r[0], int(r[1]), r[2]) for r in self.session.execute(stmt)]

    def outcome_counts(self) -> dict[AgentToolOutcome, int]:
        stmt = select(AgentActionLog.outcome, func.count(AgentActionLog.id)).group_by(
            AgentActionLog.outcome
        )
        return {row[0]: int(row[1]) for row in self.session.execute(stmt)}


def render_rows(rows: Sequence[AgentLogRow]) -> str:
    """Plain-text rendering, so a refusal can be shown without the CLI's table."""
    lines: list[str] = []
    for row in rows:
        lines.append(
            f"[{row.occurred_at:%Y-%m-%d %H:%M:%S}] run={row.run_id} tool={row.tool_name} "
            f"outcome={row.outcome.value} model={row.model or '-'}"
        )
        lines.append(f"    inputs: {json.dumps(row.inputs, sort_keys=True)}")
        if row.refusal_reason:
            lines.append(f"    refused: {row.refusal_reason}")
        if row.proposal_ref:
            lines.append(f"    proposal: {row.proposal_ref}")
        if row.output:
            head = row.output if len(row.output) <= 400 else row.output[:400] + "..."
            lines.append(f"    output: {head}")
    return "\n".join(lines)
