"""Request/response models for the shell, auth, sync, setup, settings and agents.

The TypeScript side mirrors these in `web/src/lib/types/shell.ts`; shell-agents spec
3.4, 4.5, 5 and 6.2 are the sketches they started from. Timestamps are tz-aware UTC
ISO strings; money is integer pence.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


class SignInIn(In):
    password: str = Field(min_length=1, max_length=512)
    #: Per-device operator name (DECISIONS 6), for the audit row only.
    actor: str | None = Field(default=None, max_length=120)


class SessionOut(Out):
    token: str
    expires_at: datetime


class PasswordChangeIn(In):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=512)
    actor: str | None = Field(default=None, max_length=120)


class TelegramNotice(Out):
    sent: bool
    detail: str


class PasswordChangeOut(Out):
    token: str
    expires_at: datetime
    revoked_sessions: int
    message: str
    telegram: TelegramNotice


# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------


SyncStatusName = Literal["RUNNING", "OK", "PARTIAL", "SKIPPED", "FAILED"]


class SyncAttemptOut(Out):
    status: SyncStatusName
    finished_at: datetime | None
    detail: str | None


class ShellSyncOut(Out):
    lightspeed_configured: bool
    last_ok_finished_at: datetime | None
    last_attempt: SyncAttemptOut | None
    last_sale_at: datetime | None
    stale_after_hours: int
    is_stale: bool


class BannerActionOut(Out):
    label: str
    kind: Literal["sync_now", "navigate"]
    route: str | None = None


class BannerOut(Out):
    id: Literal["stale_sync", "lightspeed_not_connected", "cash_discrepancy"]
    instance_key: str
    tone: Literal["stale", "alert"]
    text: str
    action: BannerActionOut | None


class ShellBadgesOut(Out):
    orders_waiting: int
    proposals_waiting: int
    #: Online orders waiting to be accepted (sidebar badge on Live orders).
    shop_new: int = 0


class ShellSetupOut(Out):
    empty_install: bool
    open_steps: int


class ShellOut(Out):
    sync: ShellSyncOut
    badges: ShellBadgesOut
    banners: tuple[BannerOut, ...]
    setup: ShellSetupOut


# --------------------------------------------------------------------------
# sync
# --------------------------------------------------------------------------


class SyncIn(In):
    requested_by: str | None = Field(default=None, max_length=120)


class SyncStartedOut(Out):
    run_id: int
    message: str


class SyncRunOut(Out):
    id: int
    trigger: Literal["SCHEDULED", "MANUAL_WEB", "CLI"]
    source: Literal["LIVE", "FIXTURES"]
    status: SyncStatusName
    started_at: datetime
    finished_at: datetime | None
    window_since: date
    window_until: date
    receipts_seen: int | None
    lines_ingested: int | None
    unresolved_count: int | None
    detail: str | None
    requested_by: str | None


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


class SettingsAuthOut(Out):
    source: Literal["database", "environment"] | None
    set_at: datetime | None
    set_by: str | None
    active_sessions: int
    min_length: int


class SettingsLightspeedOut(Out):
    configured: bool
    env_vars: tuple[str, ...]
    last_ok_finished_at: datetime | None
    last_sale_at: datetime | None
    last_attempt: SyncAttemptOut | None


class SettingsTelegramOut(Out):
    bot_configured: bool
    owner_chat_configured: bool
    language: Literal["ru"]


class SettingsAgentOut(Out):
    narration: Literal["model", "template"]
    model: str | None


class SettingsLabourOut(Out):
    loaded_hourly_rate_pence: int | None


class SettingsOut(Out):
    auth: SettingsAuthOut
    lightspeed: SettingsLightspeedOut
    telegram: SettingsTelegramOut
    agent: SettingsAgentOut
    labour: SettingsLabourOut


# --------------------------------------------------------------------------
# setup
# --------------------------------------------------------------------------


class SetupCtaOut(Out):
    label: str
    route: str


class SetupStepOut(Out):
    n: int
    key: Literal["import", "shelf_life", "supplier_terms", "lightspeed", "first_count"]
    done: bool
    remaining: int | None
    title: str
    body: str
    cta: SetupCtaOut | None
    cli_fix: str | None


class SetupWarningOut(Out):
    severity: Literal["FAIL", "WARN", "INFO"]
    name: str
    detail: str
    fix: str


class SetupOut(Out):
    empty_install: bool
    open_steps: int
    steps: tuple[SetupStepOut, ...]
    warnings: tuple[SetupWarningOut, ...]


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------

ProposalKindName = Literal[
    "waste_factor", "template_grouping", "channel_import", "data_fix", "supplier_basket"
]
ProposalStatusName = Literal["WAITING", "ACCEPTED", "DECLINED", "SUPERSEDED", "APPLY_FAILED"]


class AgentProposalOut(Out):
    id: int
    created_at: datetime
    agent: str
    agent_label: str
    kind: ProposalKindName
    subject_ref: str
    title: str
    body: str
    confidence: Literal["high", "medium", "low", "none"] | None
    accept_label: str
    decline_label: str
    accept_mode: Literal["apply", "navigate", "unavailable"]
    navigate_to: str | None
    unavailable_reason: str | None
    note: str | None
    status: ProposalStatusName
    decided_at: datetime | None
    decided_by: str | None
    decision_note: str | None
    applied_result: dict[str, Any] | None
    run_id: str


class WaitingOrderOut(Out):
    po_id: int
    supplier: str
    total_pence: int
    target_delivery_date: date
    created_at: datetime
    note: str


class AgentProposalsOut(Out):
    waiting: tuple[AgentProposalOut, ...]
    decided: tuple[AgentProposalOut, ...]
    waiting_count: int
    #: Orders waiting for a named person to confirm (DECISIONS 28). Read-only here.
    orders_waiting: tuple[WaitingOrderOut, ...]


class DecisionIn(In):
    decided_by: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)


class DecisionOut(Out):
    proposal: AgentProposalOut
    outcome: Literal["applied", "recorded", "superseded", "failed", "declined"]
    applied: dict[str, str] | None
    message: str


class RunToolOut(Out):
    tool_name: str
    label: str
    kind: str
    outcome: Literal["OK", "REFUSED", "FAILED", "AWAITING_HUMAN"]
    inputs: dict[str, Any]
    output: str | None
    refusal_reason: str | None


class AgentRunOut(Out):
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
    is_decision: bool
    tools: tuple[RunToolOut, ...]


class AgentLabelOut(Out):
    agent: str
    label: str


class AgentRunsOut(Out):
    runs: tuple[AgentRunOut, ...]
    agents: tuple[AgentLabelOut, ...]
    has_more: bool
