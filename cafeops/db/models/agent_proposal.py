"""Agent proposals: the only thing an agent may produce that changes anything.

shell-agents spec 6.1, DECISIONS.md 7, CLAUDE.md §9 / invariant 10.

The agent's audit engine may INSERT here (and only insert). Every status change is
made by a human through `services/agent_proposals.py` on the normal engine, and the
CHECK below means nothing leaves WAITING without a recorded human -- the same shape
as invariant 1's `ck_po_confirmed_requires_human`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow
from cafeops.db.models.enums import ProposalConfidence, ProposalKind, ProposalStatus


class AgentProposal(Base):
    __tablename__ = "agent_proposal"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: Joins `agent_action_log.run_id`.
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The PROPOSE tool call that produced this, inserted in the same commit.
    log_id: Mapped[int] = mapped_column(ForeignKey("agent_action_log.id"), nullable=False)
    #: drift_explainer | import_assistant | channel_reporter | basket_stager
    agent: Mapped[str] = mapped_column(String(60), nullable=False)
    kind: Mapped[ProposalKind] = mapped_column(enum_col(ProposalKind), nullable=False)
    #: "ingredient:42", "template:Latte", a path, "po:17".
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    #: <= 80 chars, English.
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Kind-specific. Decimals AS STRINGS (invariant 11). Carries `current` so accept
    #: can detect a stale proposal (-> SUPERSEDED).
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[ProposalConfidence | None] = mapped_column(enum_col(ProposalConfidence))
    #: ToolResult.figures the body is allowed to quote.
    figures: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[ProposalStatus] = mapped_column(
        enum_col(ProposalStatus), nullable=False, default=ProposalStatus.WAITING
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: Operator name from the device (DECISIONS.md 6) or a Telegram username.
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decision_note: Mapped[str | None] = mapped_column(Text)
    #: What the service did (old -> new, ids), or {"error": ...} for APPLY_FAILED.
    applied_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (
        CheckConstraint(
            "status = 'WAITING' OR (decided_at IS NOT NULL AND decided_by IS NOT NULL)",
            name="decided_by_human",
        ),
        Index("ix_agent_proposal_status", "status", "created_at"),
        Index("ix_agent_proposal_run", "run_id"),
        Index("ix_agent_proposal_subject", "kind", "subject_ref"),
    )

    def __repr__(self) -> str:
        return f"<AgentProposal {self.id} {self.kind.value} {self.status.value}>"


__all__ = ["AgentProposal"]
