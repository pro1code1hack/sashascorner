"""Par level queries, and the single write path for `auto_order_enabled`.

INVARIANT 2: auto-order eligibility is earned per ingredient through drift history,
never set manually as a shortcut. The enforcement is here, at the only place that can
write the column: `apply_gate_decision` accepts a `GateDecision` from
`domain/tiers.py` and refuses anything else, and `set_auto_order` -- the protocol's
generic setter -- raises when asked to enable.

Revocation is deliberately NOT gated. Turning auto-ordering off is always safe and
anybody may do it; turning it on is the thing that spends money.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import ParLevel
from cafeops.domain.tiers import GateDecision, authorises_enable
from cafeops.domain.types import ParSpec


class AutoOrderGrantRefused(PermissionError):
    """Something tried to enable auto-ordering without the gate's decision.

    A hard failure rather than a log line: silently ignoring the write would leave the
    caller believing auto-ordering is on, and silently honouring it would defeat the
    one rule spec 5.2 asks to be enforced in code.
    """


@dataclass(frozen=True, slots=True)
class AutoOrderAudit:
    """`par_level`'s answer to "who turned this on, and on what evidence"."""

    ingredient_id: int
    auto_order_enabled: bool
    granted_at: datetime | None
    revoked_at: datetime | None
    reason: str | None


class SqlParLevelRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, ingredient_id: int) -> ParSpec | None:
        row = self._row(ingredient_id)
        if row is None:
            return None
        return ParSpec(
            ingredient_id=row.ingredient_id,
            safety_days=row.safety_days,
            min_qty=row.min_qty,
            max_qty=row.max_qty,
            auto_order_enabled=row.auto_order_enabled,
        )

    def audit(self, ingredient_id: int) -> AutoOrderAudit | None:
        row = self._row(ingredient_id)
        if row is None:
            return None
        return AutoOrderAudit(
            ingredient_id=row.ingredient_id,
            auto_order_enabled=row.auto_order_enabled,
            granted_at=row.auto_order_granted_at,
            revoked_at=row.auto_order_revoked_at,
            reason=row.auto_order_reason,
        )

    def set_auto_order(
        self, ingredient_id: int, enabled: bool, *, reason: str, at: datetime
    ) -> None:
        """Protocol entry point. Enabling through it is refused (invariant 2).

        Disabling is allowed from anywhere: a human who wants auto-ordering off gets it
        off, and the gate will not turn it back on until the drift history says so.
        """
        if enabled:
            raise AutoOrderGrantRefused(
                "auto_order_enabled = True is only reachable through "
                "domain/tiers.evaluate_gate -> apply_gate_decision (invariant 2)"
            )
        self._write(ingredient_id, enabled=False, reason=reason, at=at)

    def apply_gate_decision(self, decision: GateDecision, *, at: datetime) -> bool:
        """Write the gate's verdict. Returns True when the stored flag changed.

        The audit fields are written on every change, granted/revoked by direction, and
        `reason` is refreshed even when the flag holds -- a held decision's reason is
        the current evidence, and staleness there is how an old justification outlives
        the numbers that produced it.
        """
        if decision.auto_order_enabled and not authorises_enable(decision):
            raise AutoOrderGrantRefused(
                f"decision for ingredient {decision.ingredient_id} carries no gate "
                "authorisation; it was not produced by domain/tiers.evaluate_gate"
            )
        return self._write(
            decision.ingredient_id,
            enabled=decision.auto_order_enabled,
            reason=decision.reason,
            at=at,
            changed=decision.changed,
        )

    def _row(self, ingredient_id: int) -> ParLevel | None:
        return self.session.scalar(select(ParLevel).where(ParLevel.ingredient_id == ingredient_id))

    def _write(
        self,
        ingredient_id: int,
        *,
        enabled: bool,
        reason: str,
        at: datetime,
        changed: bool | None = None,
    ) -> bool:
        row = self._row(ingredient_id)
        if row is None:
            return False
        if changed is None:
            changed = row.auto_order_enabled is not enabled
        row.auto_order_enabled = enabled
        row.auto_order_reason = reason
        if changed:
            if enabled:
                row.auto_order_granted_at = at
            else:
                row.auto_order_revoked_at = at
        return changed
