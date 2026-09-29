"""Request/response models for supplier portal integrations and the browser job queue.

docs/agents/BROWSER-ORDERING.md §7 is the sketch these started from. The screens are
not built yet, so these field names are the contract the frontend will build against:
change one here and the TypeScript mirror (when it exists) changes with it. Timestamps
are tz-aware UTC ISO strings; money stays integer pence in every field -- the only
formatted money is the text of `result_summary`, which is a sentence, not a number.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


Name = Annotated[str, Field(min_length=1, max_length=120)]

SupplierSessionStatusName = Literal["NOT_CONNECTED", "CONNECTED", "EXPIRED", "CHECK_FAILED"]
BrowserJobKindName = Literal["STAGE_BASKET", "CHECK_SESSION"]
BrowserJobStatusName = Literal[
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_HUMAN"
]
BrowserStepSourceName = Literal["SCRIPT", "MODEL", "POLICY"]
BrowserStepOutcomeName = Literal["OK", "ERROR", "REFUSED"]
#: The tier ladder (docs/agents/BROWSER-ORDERING.md §10), best first: a cart link
#: opened in the owner's own browser, the portal's quick-order pad, the per-line
#: browser path (scripted, then the model for what breaks).
TierName = Literal["cart_link", "quick_order", "browser"]


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


class BrowserJobOut(Out):
    id: int
    kind: BrowserJobKindName
    status: BrowserJobStatusName
    supplier_id: int
    supplier_name: str
    purchase_order_id: int | None
    requested_by: str
    requested_via: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    error: str | None
    needs_human_reason: str | None
    model: str | None
    steps_total: int
    model_calls: int
    input_tokens: int
    output_tokens: int
    proposal_id: int | None
    run_id: str | None
    #: One sentence derived from `result`: "browser: 6 of 6 lines in basket, £48.20 seen
    #: (expected £47.90)", "cart link ready: 3 items" or "signed in as s***@x.com". None
    #: until the job has a result.
    result_summary: str | None
    #: The best tier that put a line in the basket (`result["tier"]`); None for a
    #: CHECK_SESSION job, a job still running, or one that staged nothing.
    tier: TierName | None = None


class BrowserStepOut(Out):
    seq: int
    at: datetime
    source: BrowserStepSourceName
    member: str
    input: dict[str, Any]
    outcome: BrowserStepOutcomeName
    output: str | None
    refusal_reason: str | None
    url: str | None
    duration_ms: int | None
    has_screenshot: bool
    screenshot_url: str | None


class BrowserJobDetailOut(BrowserJobOut):
    steps: tuple[BrowserStepOut, ...]
    #: `browser_job.result` verbatim: a BasketSnapshot dict for STAGE_BASKET,
    #: `{"signed_in", "account_label"}` for CHECK_SESSION. Pence fields stay integers.
    snapshot: dict[str, Any] | None


class BrowserJobsOut(Out):
    jobs: tuple[BrowserJobOut, ...]
    has_more: bool


# --------------------------------------------------------------------------
# integrations
# --------------------------------------------------------------------------


class PortalOut(Out):
    slug: str
    label: str
    start_url: str
    #: What this adapter can do, best first; "browser" is always last.
    tiers: tuple[TierName, ...] = ("browser",)
    #: The site refuses headless browsers, so the worker needs a display (Xvfb) for it.
    prefers_headed: bool = False


class SessionOut(Out):
    status: SupplierSessionStatusName
    account_label: str | None
    connected_at: datetime | None
    connected_by: str | None
    last_ok_at: datetime | None
    last_checked_at: datetime | None
    last_error: str | None


class IntegrationOut(Out):
    supplier_id: int
    supplier_name: str
    portal: PortalOut | None
    session: SessionOut
    auto_stage: bool
    #: True when a basket can be staged now. With a "cart_link" tier this is True
    #: even with the worker off and no stored sign-in: the link opens in the owner's
    #: own browser. Otherwise the worker must be on and the sign-in CONNECTED.
    can_stage: bool
    cannot_stage_reason: str | None
    last_job: BrowserJobOut | None
    #: Same as `portal.tiers`; empty when the supplier has no adapter.
    tiers: tuple[TierName, ...] = ()


class IntegrationsOut(Out):
    suppliers: tuple[IntegrationOut, ...]
    worker_enabled: bool
    #: Every registered adapter, whether or not a supplier is mapped to it.
    portals: tuple[PortalOut, ...]


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


class SessionImportIn(In):
    """Exactly one of `storage_state` (plain Playwright storage state) or
    `storage_state_enc_b64` (Fernet ciphertext from `cafeops portal export-session`,
    base64 for transport) -- the service encrypts a plain one before storing it."""

    storage_state: dict[str, Any] | None = None
    storage_state_enc_b64: str | None = Field(default=None, max_length=2_000_000)
    account_label: str | None = Field(default=None, max_length=200)
    connected_by: Name


class ByIn(In):
    by: Name


class RequestedByIn(In):
    requested_by: Name


class AutoStageIn(In):
    enabled: bool
    by: Name
