"""What the model may do in the browser, decided before anything runs.

docs/agents/BROWSER-ORDERING.md §2. The loop asks `PolicyGuard.check` before every
member call the model asks for; the executor's route filter is the second, independent
line (an off-allowlist top-level navigation is refused inside the browser even if this
module were wrong). A refusal is a `browser_job_step` with outcome REFUSED; the job
carries on.

Rules, in the order they are applied:

* `navigate`: scheme http/https only, host on `policy.allowed_hosts`, URL not matching
  `policy.forbidden_url_patterns` (checkout, payment...).
* click members with a `ref` target: `executor.describe_ref(ref)`. Unknown ref: allow
  (the executor reports it stale, which is an ERROR the model can recover from, not a
  policy matter). `describe_ref` raising: refuse -- a control this guard could not
  identify might be "Place order", and the spend-money guard fails closed. Known ref:
  refuse when the accessible name matches `forbidden_control_patterns` ("Place order",
  "Checkout"...) or when the control is a link whose href leaves the allowlist or
  enters a forbidden URL.
* click members with a coordinate target: allowed. The name of what sits under a pixel
  is not known here; the executor's route filter still refuses a checkout navigation,
  and a "Place order" button reached that way would still stop at the payment step the
  policy forbids to navigate into. This is the documented gap the network-layer filter
  exists for.
* `key` with "Enter": allowed. It may submit a search form, which the model needs. The
  assumption, written down: on every portal we drive, checkout requires a click on a
  control whose name the policy forbids (or a navigation into a forbidden URL), and the
  keyboard alone does not reach it. A portal where Enter on the basket page places the
  order would need its own `forbidden_control_patterns` review before it is registered.
* The four opt-in members (`javascript_exec`, `file_upload`, `read_console`,
  `read_network`) are refused "not enabled" -- they are also disabled in the toolset
  config sent to the API, so the model should never ask; the refusal is for the case
  where it does.

`redact` is what goes in the step row: a `type` whose text looks like a secret becomes
"<redacted>", and no string is stored longer than 500 characters.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from cafeops.agent.browser.types import (
    BrowserAction,
    BrowserExecutor,
    PolicyDecision,
)
from cafeops.integrations.suppliers.portals.base import PortalPolicy

CLICK_MEMBERS: frozenset[str] = frozenset(
    {"left_click", "double_click", "right_click", "middle_click", "triple_click"}
)
DISABLED_MEMBERS: frozenset[str] = frozenset(
    {"javascript_exec", "file_upload", "read_console", "read_network"}
)
#: The longest string the step row keeps. The full page text a `read_page` returns is
#: the model's business, not the audit trail's.
MAX_LOGGED_CHARS = 500

log = logging.getLogger("cafeops.browser.policy")

_SECRET_LIKE = re.compile(r"^\S{8,}$")
_SECRET_KEYS = re.compile(r"pass|pwd|secret|token|otp|code|pin", re.IGNORECASE)


def looks_like_secret(text: str) -> bool:
    """8+ characters, no whitespace, at least one digit: a password or a one-time code
    rather than a search term. "oat milk 1l" is not; "Sp4rrow!2026" is."""
    return bool(_SECRET_LIKE.match(text)) and any(ch.isdigit() for ch in text)


class PolicyGuard:
    """One portal's policy, applied to one action at a time."""

    def __init__(self, policy: PortalPolicy) -> None:
        self.policy = policy

    # -- the decision ------------------------------------------------------

    def check(self, action: BrowserAction, executor: BrowserExecutor) -> PolicyDecision:
        member = action.member
        if member in DISABLED_MEMBERS:
            return PolicyDecision(False, f"{member} is not enabled for this job", member)
        if member == "navigate":
            return self._check_url(str(action.input.get("url", "")), what="navigate to")
        if member in CLICK_MEMBERS:
            ref = action.target_ref
            if ref is None:
                # A coordinate click (or a click with no target): the executor's route
                # filter is the guard. See the module docstring.
                return PolicyDecision(True)
            try:
                info = executor.describe_ref(ref)
            except Exception as exc:
                # Fail closed: an unidentified control may be the one that spends money.
                log.warning("refusing click on ref %r: describe_ref raised", ref, exc_info=True)
                return PolicyDecision(
                    False,
                    f"could not identify control ({type(exc).__name__}); read the page "
                    "again and click a control that can be identified",
                    "describe-failed",
                )
            if info is None:
                return PolicyDecision(True)
            matched = self.policy.control_forbidden(info.name)
            if matched is not None:
                return PolicyDecision(
                    False,
                    f"clicking {info.role or 'control'} {info.name!r} would submit or pay; "
                    "a person does that on the supplier's site",
                    matched,
                )
            if info.href:
                decision = self._check_url(info.href, what=f"follow link {info.name!r} to")
                if not decision.allowed:
                    return decision
            return PolicyDecision(True)
        # Everything else (read_page, find, form_input, type, key, scroll, screenshot,
        # zoom, wait, tabs...) is a read or a local edit the policy has no opinion on.
        return PolicyDecision(True)

    def _check_url(self, url: str, *, what: str) -> PolicyDecision:
        if not url:
            return PolicyDecision(False, f"cannot {what} an empty URL", "empty-url")
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            return PolicyDecision(
                False, f"cannot {what} {url[:120]!r}: only http(s) URLs", "scheme"
            )
        host = parsed.hostname or ""
        if not self.policy.host_allowed(host):
            return PolicyDecision(
                False,
                f"cannot {what} {host!r}: not on this portal's allowlist "
                f"({', '.join(self.policy.allowed_hosts)})",
                "host",
            )
        forbidden = self.policy.url_forbidden(url)
        if forbidden is not None:
            return PolicyDecision(
                False,
                f"cannot {what} {url[:120]!r}: that is checkout or payment, which a person does",
                forbidden,
            )
        return PolicyDecision(True)

    # -- what the step row keeps -------------------------------------------

    def redact(self, action: BrowserAction, *, secret_hint: bool = False) -> dict[str, Any]:
        """A copy of the input safe to store: secrets replaced, strings clipped."""
        out: dict[str, Any] = {}
        for key, value in action.input.items():
            if action.member == "type" and key == "text" and isinstance(value, str):
                if secret_hint or looks_like_secret(value):
                    out[key] = "<redacted>"
                    continue
            if action.member == "form_input" and _SECRET_KEYS.search(key) and value:
                out[key] = "<redacted>"
                continue
            out[key] = _clip_value(value)
        return out


def _clip_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= MAX_LOGGED_CHARS else value[:MAX_LOGGED_CHARS] + "…"
    if isinstance(value, dict):
        return {str(k): _clip_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip_value(v) for v in value[:50]]
    return value


__all__ = [
    "CLICK_MEMBERS",
    "DISABLED_MEMBERS",
    "MAX_LOGGED_CHARS",
    "PolicyGuard",
    "looks_like_secret",
]
