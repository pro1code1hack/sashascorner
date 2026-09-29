"""Contracts between the three halves of a browser job.

docs/agents/BROWSER-ORDERING.md §4. The pieces:

* `BrowserExecutor` (executor.py): owns one Playwright page in a persistent profile
  and can run any browser toolset member (`navigate`, `read_page`, `find`,
  `left_click`, `form_input`, `screenshot`, ...) exactly as Anthropic's
  `browser_toolset_20260801` defines them, returning the content blocks the API
  expects. It knows nothing about orders or models.
* The loop (loop.py): runs the Messages API conversation with the toolset, asks the
  policy before each action, calls the executor, records steps, and stops at the
  caps. It knows nothing about Playwright.
* The job (job.py / worker.py): scripted steps first, the loop for what breaks, the
  basket snapshot at the end, the proposal and the audit rows.

Nothing here imports SQLAlchemy or Playwright; both sides import this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class BrowserAction:
    """One browser toolset member call, from the model or from a script."""

    member: str
    input: dict[str, Any]
    #: `tool_use.id` when the model asked for it; None for scripted calls.
    tool_use_id: str | None = None

    @property
    def target_ref(self) -> str | None:
        target = self.input.get("target")
        if isinstance(target, dict) and target.get("type") == "ref":
            ref = target.get("ref")
            return str(ref) if ref is not None else None
        return None

    @property
    def target_coordinate(self) -> tuple[int, int] | None:
        target = self.input.get("target")
        if isinstance(target, dict) and target.get("type") == "coordinate":
            return int(target["x"]), int(target["y"])
        return None


@dataclass(slots=True)
class ActionResult:
    """What the executor hands back: the `tool_result.content` for the API, plus
    what the audit trail wants to know."""

    #: Content blocks exactly as the Messages API takes them: text, image (base64
    #: PNG) and at most one browser_state block. A plain string is allowed for
    #: errors.
    content: list[dict[str, Any]] | str
    is_error: bool = False
    #: URL of the active tab after the action.
    url: str | None = None
    #: One line for the step row ("clicked button 'Add' [ref_12]", "read 84 elements").
    summary: str = ""
    #: PNG bytes when the action produced a screenshot (for media_asset).
    screenshot_png: bytes | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ElementInfo:
    """What the executor knows about a ref, for the policy check before a click."""

    ref: str
    role: str | None
    name: str | None
    tag: str | None
    href: str | None


class BrowserExecutor(Protocol):
    """Runs toolset members against a real browser.

    Implementations are sync (Playwright sync API on the worker thread). Every member
    in the toolset's default-enabled set must be handled; the four opt-in members
    (`javascript_exec`, `file_upload`, `read_console`, `read_network`) may return an
    error result saying they are disabled. Off-allowlist top-level navigations must be
    refused inside the browser (route interception), not only in the loop.
    """

    def execute(self, action: BrowserAction) -> ActionResult: ...

    def describe_ref(self, ref: str) -> ElementInfo | None:
        """Role/name/href for a ref issued by the last `read_page`/`find`, or None
        when stale. The loop uses this to refuse forbidden controls before clicking."""
        ...

    def current_url(self) -> str: ...

    def screenshot_png(self) -> bytes: ...

    def browser_state(self) -> dict[str, Any]:
        """A `browser_state` content block for the active tab set."""
        ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None
    #: The policy pattern that matched, for the step row.
    matched: str | None = None


@dataclass(slots=True)
class StepRecord:
    """One row of browser_job_step, before it is written."""

    source: str  # SCRIPT | MODEL | POLICY
    member: str
    input: dict[str, Any]
    outcome: str  # OK | ERROR | REFUSED
    output: str | None = None
    refusal_reason: str | None = None
    url: str | None = None
    duration_ms: int | None = None
    screenshot_png: bytes | None = None
    at: datetime | None = None


@dataclass(slots=True)
class Budget:
    """Per-job ceilings. Counted by the loop; exceeded means the job ends with what
    it has, as SUCCEEDED-with-warnings if the basket was read, else FAILED."""

    max_model_calls: int
    max_actions: int
    max_seconds: int
    model_calls: int = 0
    actions: int = 0
    started_at: datetime | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    def exhausted(self, now: datetime) -> str | None:
        if self.model_calls >= self.max_model_calls:
            return f"model call cap reached ({self.max_model_calls})"
        if self.actions >= self.max_actions:
            return f"action cap reached ({self.max_actions})"
        if (
            self.started_at is not None
            and (now - self.started_at).total_seconds() >= self.max_seconds
        ):
            return f"time cap reached ({self.max_seconds}s)"
        return None


@dataclass(slots=True)
class ModelTask:
    """What the loop is asked to do in one bounded conversation with the model."""

    #: Plain-English goal, e.g. "Add 3 packs of 'Oatly Barista 1L' to the basket.
    #: Product page: https://... . Do not change any other line."
    goal: str
    #: The portal adapter's hints, appended to the system prompt.
    portal_hints: str
    #: Text the loop expects the model to end with, as a JSON object matching this
    #: schema (validated by the loop). Example: {"outcome": "added"|"already"|
    #: "not_found"|"needs_human"|"gave_up", "packs_in_basket": int|null,
    #: "unit_price_pence": int|null, "product_name": str|null, "note": str}
    result_schema: dict[str, Any]
    #: Model calls this task may use out of the job's budget.
    max_model_calls: int = 12


@dataclass(slots=True)
class ModelTaskResult:
    outcome: str
    data: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    model_calls: int = 0
    stopped_reason: str | None = None  # budget | refusal | needs_human | done


__all__ = [
    "ActionResult",
    "BrowserAction",
    "BrowserExecutor",
    "Budget",
    "ElementInfo",
    "ModelTask",
    "ModelTaskResult",
    "PolicyDecision",
    "StepRecord",
]
