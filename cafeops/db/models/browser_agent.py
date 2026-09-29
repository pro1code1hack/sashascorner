"""Browser ordering agents: stored supplier sign-ins, the job queue and its step trace.

docs/agents/BROWSER-ORDERING.md. Three tables:

* `supplier_session`: one row per supplier that has a web shop we drive. Holds the
  *state* of the persistent browser profile (signed in or not), never a password.
  A person signs in once, in a real browser window; the profile keeps the cookies.
  `storage_state_enc` carries a Playwright storage state, Fernet-encrypted with
  `settings.browser_session_key`, so a sign-in made on the owner's laptop can be
  moved to the server without typing anything into the server.
* `browser_job`: the queue. The API and the scheduler only INSERT here; the worker
  process (`cafeops browser-worker`) is the only thing that runs a browser. A job
  ends at a staged basket and a SUPPLIER_BASKET proposal, never at a submitted
  order (invariant 1, CLAUDE.md §9).
* `browser_job_step`: every action the job took, scripted or model-chosen, with the
  URL it happened on and a screenshot where one was taken. This is the audit trail
  a person reads when a basket looks wrong. The coarse per-run row still goes to
  `agent_action_log` (invariant 10); this table is the fine grain under it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow
from cafeops.domain.enums import (
    BrowserJobKind,
    BrowserJobStatus,
    BrowserStepOutcome,
    BrowserStepSource,
    SupplierSessionStatus,
)

if TYPE_CHECKING:
    from cafeops.db.models.supplier import Supplier


class SupplierSession(Base):
    __tablename__ = "supplier_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("supplier.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[SupplierSessionStatus] = mapped_column(
        enum_col(SupplierSessionStatus),
        nullable=False,
        default=SupplierSessionStatus.NOT_CONNECTED,
    )
    #: What the person sees as "signed in as": a masked email or account name. Never
    #: a password; the row has no column for one on purpose.
    account_label: Mapped[str | None] = mapped_column(String(200))
    #: Directory name (under `settings.browser_data_dir`) of the persistent Chromium
    #: profile. NULL until the first browser has been opened for this supplier.
    profile_dir: Mapped[str | None] = mapped_column(String(200))
    #: Fernet-encrypted Playwright `storage_state()` JSON (cookies + localStorage),
    #: the transportable form of the sign-in. NULL when nothing was imported.
    storage_state_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    connected_by: Mapped[str | None] = mapped_column(String(120))
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    supplier: Mapped[Supplier] = relationship()

    __table_args__ = (
        CheckConstraint(
            "status <> 'CONNECTED' OR (connected_at IS NOT NULL AND connected_by IS NOT NULL)",
            name="ck_supplier_session_connected_by_human",
        ),
    )

    def __repr__(self) -> str:
        return f"<SupplierSession supplier={self.supplier_id} {self.status.value}>"


class BrowserJob(Base):
    __tablename__ = "browser_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    kind: Mapped[BrowserJobKind] = mapped_column(enum_col(BrowserJobKind), nullable=False)
    status: Mapped[BrowserJobStatus] = mapped_column(
        enum_col(BrowserJobStatus), nullable=False, default=BrowserJobStatus.QUEUED
    )
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier.id"), nullable=False)
    #: Set for STAGE_BASKET. The order must be DRAFT or CONFIRMED when queued; the
    #: job never changes its status (that is a person's click on the order page).
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order.id"))
    #: Operator name, or "scheduler". Every job has a requester.
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)
    #: web | cli | scheduler
    requested_via: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Kind-specific inputs, e.g. {"lines": [...]} snapshot of the order at enqueue.
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: Touched by the worker every few seconds while RUNNING; a stale heartbeat means
    #: a dead worker, and the next worker re-queues or fails the job.
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    worker_id: Mapped[str | None] = mapped_column(String(80))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_requested_by: Mapped[str | None] = mapped_column(String(120))

    #: Joins `agent_action_log.run_id` for the coarse audit row(s).
    run_id: Mapped[str | None] = mapped_column(String(64))
    #: The SUPPLIER_BASKET proposal the job produced, when it got that far.
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("agent_proposal.id"))
    #: Kind-specific outcome: a BasketSnapshot (as dict, decimals as strings) for
    #: STAGE_BASKET, {"signed_in": bool, "account_label": ...} for CHECK_SESSION.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    #: Why the job stopped for a person (NEEDS_HUMAN): "one-time code requested" etc.
    needs_human_reason: Mapped[str | None] = mapped_column(Text)

    model: Mapped[str | None] = mapped_column(String(80))
    steps_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    steps: Mapped[list[BrowserJobStep]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="BrowserJobStep.seq"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING') OR finished_at IS NOT NULL",
            name="ck_browser_job_terminal_has_finished_at",
        ),
        CheckConstraint(
            "kind <> 'STAGE_BASKET' OR purchase_order_id IS NOT NULL",
            name="ck_browser_job_basket_has_po",
        ),
        Index("ix_browser_job_status", "status", "created_at"),
        Index("ix_browser_job_po", "purchase_order_id"),
        Index("ix_browser_job_supplier", "supplier_id", "created_at"),
    )

    @property
    def is_active(self) -> bool:
        return self.status in (BrowserJobStatus.QUEUED, BrowserJobStatus.RUNNING)

    def __repr__(self) -> str:
        return f"<BrowserJob {self.id} {self.kind.value} {self.status.value}>"


class BrowserJobStep(Base):
    __tablename__ = "browser_job_step"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("browser_job.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    source: Mapped[BrowserStepSource] = mapped_column(enum_col(BrowserStepSource), nullable=False)
    #: Browser toolset member name (`navigate`, `left_click`, `form_input`...) or the
    #: scripted step name (`script:add_line`, `script:read_basket`, `script:sign_in_check`).
    member: Mapped[str] = mapped_column(String(60), nullable=False)
    #: The member input, with any `type` text redacted when it looks like a secret.
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    outcome: Mapped[BrowserStepOutcome] = mapped_column(
        enum_col(BrowserStepOutcome), nullable=False
    )
    #: Text result or error, truncated to a few hundred characters.
    output: Mapped[str | None] = mapped_column(Text)
    #: Why the policy refused (outcome REFUSED).
    refusal_reason: Mapped[str | None] = mapped_column(String(400))
    url: Mapped[str | None] = mapped_column(String(2000))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    screenshot_asset_id: Mapped[int | None] = mapped_column(ForeignKey("media_asset.id"))

    job: Mapped[BrowserJob] = relationship(back_populates="steps")

    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_browser_job_step_seq"),)

    def __repr__(self) -> str:
        return f"<BrowserJobStep {self.job_id}#{self.seq} {self.member} {self.outcome.value}>"


__all__ = ["BrowserJob", "BrowserJobStep", "SupplierSession"]
