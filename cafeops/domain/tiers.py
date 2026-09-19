"""The auto-order gate. Spec 5.2, invariant 2.

> **No ingredient enters auto-ordering without two consecutive counts under 10%.**

That sentence is the whole module. Everything here exists to make it true in code
rather than in documentation, because the failure it prevents is expensive and silent:
a system that trusts a single lucky count and orders £200 of milk nobody needed.

Three properties are deliberate.

1. **Eligibility is a property of the history, not of a measurement.** One `ELIGIBLE`
   observation is not enough. `evaluate_gate` reads the recent drift series
   newest-first and counts the leading clean run; anything less than
   `required_consecutive` holds the ingredient manual and says how far along it is.

2. **`FORCE_MANUAL` revokes immediately.** Not after a second confirming count, not
   after a grace period, not "warn first". A >15% gap means the ledger no longer
   describes the shelf, and every order sized from it is wrong from this moment on.
   The alert is raised in the same breath.

3. **Only this module decides the flag.** `GateDecision` carries a token that only
   `evaluate_gate` can attach, and the par-level repository refuses to write
   `auto_order_enabled = True` without it. A human may always revoke; nobody --
   human, CLI, bot or job -- may grant by hand (invariant 2).

Tier movement is NOT automated here. A tier B ingredient whose history would clear
the gate is reported by `would_clear_gate` so somebody can promote it deliberately;
nothing in this module writes `ingredient.tier`, so A -> B cannot happen by accident.

Pure: no SQLAlchemy, no I/O, no `config` import. Thresholds arrive as arguments.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field

from cafeops.domain.drift import (
    DEFAULT_ELIGIBLE_MAX_PCT,
    DEFAULT_WARN_MAX_PCT,
    classify_drift,
)
from cafeops.domain.types import DriftVerdict, Tier

__all__ = [
    "DEFAULT_REQUIRED_CONSECUTIVE",
    "GateAction",
    "GateDecision",
    "authorises_enable",
    "clean_streak",
    "evaluate_gate",
    "would_clear_gate",
]

#: Spec 5.2: "two consecutive counts under 10%". Mirrored in config as
#: `drift_consecutive_counts_required`.
DEFAULT_REQUIRED_CONSECUTIVE = 2

#: The only object that authorises writing `auto_order_enabled = True`. Module-private
#: on purpose: a caller that goes looking for it is circumventing the gate knowingly,
#: which is a different problem from doing it by accident.
_GRANT_TOKEN = object()


class GateAction(enum.StrEnum):
    """What the gate did to `par_level.auto_order_enabled` this time."""

    GRANT = "GRANT"
    REVOKE = "REVOKE"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class GateDecision:
    """The gate's verdict on one ingredient, with the evidence behind it.

    `reason` is written verbatim to `par_level.auto_order_reason`, so it has to read
    as an answer to "why is this on/off" months later.
    """

    ingredient_id: int
    action: GateAction
    auto_order_enabled: bool
    was_enabled: bool
    #: None when the ingredient has no drift observation at all -- no count, no verdict.
    verdict: DriftVerdict | None
    reason: str
    alert: bool
    clean_streak: int
    required_streak: int
    considered_pcts: tuple[float, ...] = ()
    #: Set by `evaluate_gate` alone. See `authorises_enable`.
    grant_token: object | None = field(default=None, repr=False, compare=False)

    @property
    def changed(self) -> bool:
        return self.auto_order_enabled is not self.was_enabled


def authorises_enable(decision: GateDecision) -> bool:
    """True only for a decision this module produced that actually grants.

    The par-level repository calls this before writing True. Without it, invariant 2
    would be a convention, and conventions are what "just set the flag for now"
    defeats.
    """
    return decision.auto_order_enabled and decision.grant_token is _GRANT_TOKEN


def clean_streak(
    recent_drift_pcts: Sequence[float], *, eligible_max_pct: float = DEFAULT_ELIGIBLE_MAX_PCT
) -> int:
    """How many of the most recent counts, in an unbroken run, are under the bar.

    `recent_drift_pcts` is newest-first (`DriftRepository.recent_drift_pcts` returns
    it that way). Absolute values: a 12% over-statement breaks the run exactly as a
    12% under-statement does.
    """
    streak = 0
    for pct in recent_drift_pcts:
        if abs(pct) >= eligible_max_pct:
            break
        streak += 1
    return streak


def evaluate_gate(
    *,
    ingredient_id: int,
    tier: Tier,
    recent_drift_pcts: Sequence[float],
    currently_enabled: bool,
    has_par_level: bool = True,
    required_consecutive: int = DEFAULT_REQUIRED_CONSECUTIVE,
    eligible_max_pct: float = DEFAULT_ELIGIBLE_MAX_PCT,
    warn_max_pct: float = DEFAULT_WARN_MAX_PCT,
) -> GateDecision:
    """Decide `auto_order_enabled` from the drift history. The only place that may.

    `recent_drift_pcts` must be newest-first and must already include the observation
    just recorded -- the gate judges history, and the count that triggered it is part
    of that history.

    The disabling branches are deliberately unglamorous: every path that is not "two
    consecutive clean counts on a tier A ingredient with a par level" ends in
    `auto_order_enabled = False`. That includes the tuning band. An ingredient drifting
    at 12% no longer has two consecutive counts under 10%, so whatever it was granted
    on has expired; keeping the flag on until something worse happened would mean the
    rule held at grant time and never again.
    """
    if required_consecutive < 1:
        raise ValueError(f"required_consecutive must be >= 1, got {required_consecutive}")

    considered = tuple(recent_drift_pcts[:required_consecutive])
    streak = clean_streak(recent_drift_pcts, eligible_max_pct=eligible_max_pct)

    def decide(
        *,
        enabled: bool,
        verdict: DriftVerdict | None,
        reason: str,
        alert: bool = False,
    ) -> GateDecision:
        if enabled and not currently_enabled:
            action = GateAction.GRANT
        elif not enabled and currently_enabled:
            action = GateAction.REVOKE
        else:
            action = GateAction.HOLD
        return GateDecision(
            ingredient_id=ingredient_id,
            action=action,
            auto_order_enabled=enabled,
            was_enabled=currently_enabled,
            verdict=verdict,
            reason=reason,
            alert=alert,
            clean_streak=streak,
            required_streak=required_consecutive,
            considered_pcts=considered,
            grant_token=_GRANT_TOKEN if enabled else None,
        )

    if not recent_drift_pcts:
        return decide(
            enabled=False,
            verdict=None,
            reason="no drift observation on record: auto-ordering cannot be earned yet",
        )

    latest = recent_drift_pcts[0]
    verdict = classify_drift(latest, eligible_max_pct=eligible_max_pct, warn_max_pct=warn_max_pct)

    # Checked before tier and par level: a >15% gap is an alert about the ledger
    # itself, and it is worth raising whether or not the ingredient was a candidate.
    if verdict is DriftVerdict.FORCE_MANUAL:
        return decide(
            enabled=False,
            verdict=verdict,
            reason=(
                f"drift {latest:+.2f}% exceeds {warn_max_pct:.1f}%: auto-ordering refused"
                " and revoked immediately, theoretical stock is not trustworthy"
            ),
            alert=True,
        )

    if tier is not Tier.A:
        return decide(
            enabled=False,
            verdict=verdict,
            reason=(
                f"tier {tier.value} is never auto-ordered (spec 4.5): consumption is"
                " calculated but every order stays under human review"
            ),
        )

    if not has_par_level:
        return decide(
            enabled=False,
            verdict=verdict,
            reason="no par_level row: nothing defines min/max, so nothing can be sized",
        )

    if verdict is DriftVerdict.TUNE_WASTE_FACTOR:
        return decide(
            enabled=False,
            verdict=verdict,
            reason=(
                f"drift {latest:+.2f}% is in the {eligible_max_pct:.1f}-{warn_max_pct:.1f}%"
                " tuning band: stays manual, tune waste_factor and count again"
            ),
        )

    if streak < required_consecutive:
        return decide(
            enabled=False,
            verdict=verdict,
            reason=(
                f"{streak} of {required_consecutive} consecutive counts under"
                f" {eligible_max_pct:.1f}% (latest {latest:+.2f}%): stays manual until the"
                " next clean count"
            ),
        )

    series = ", ".join(f"{p:+.2f}%" for p in considered)
    return decide(
        enabled=True,
        verdict=verdict,
        reason=(
            f"{streak} consecutive counts under {eligible_max_pct:.1f}% ({series}):"
            " auto-ordering earned"
        ),
    )


def would_clear_gate(
    *,
    recent_drift_pcts: Sequence[float],
    required_consecutive: int = DEFAULT_REQUIRED_CONSECUTIVE,
    eligible_max_pct: float = DEFAULT_ELIGIBLE_MAX_PCT,
) -> bool:
    """Would this drift history clear the gate if the ingredient were tier A?

    For the B -> A promotion review, which is a human decision (spec 4.5: tier A
    membership is earned, not assigned). This function reports; it never promotes.
    """
    return (
        clean_streak(recent_drift_pcts, eligible_max_pct=eligible_max_pct) >= required_consecutive
    )
