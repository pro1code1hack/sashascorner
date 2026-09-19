"""Drift -- the trust metric. Spec 5.2.

```
drift_pct = (theoretical - counted) / max(counted, EPSILON) * 100
```

Positive drift means the ledger thinks there is MORE stock than the shelf holds:
consumption was under-recorded, which is the normal direction for a café (spillage,
staff drinks, a foamed pitcher poured away). Negative drift means the opposite and
usually points at an unrecorded delivery rather than at the waste factor.

The gate reads the **absolute** value, because a system that over-states stock by 20%
and one that under-states it by 20% are both untrustworthy, and only one of them
would be caught by a signed threshold.

Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O, and deliberately no
`config` import -- every threshold and the damping factor arrive as arguments, so a
caller may pass the values from `settings` without `domain/` knowing that settings
exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from cafeops.domain.types import EPSILON, DriftResult, DriftVerdict

__all__ = [
    "DEFAULT_ELIGIBLE_MAX_PCT",
    "DEFAULT_WARN_MAX_PCT",
    "DEFAULT_WASTE_DAMPING",
    "MAX_WASTE_FACTOR",
    "WASTE_FACTOR_SCALE",
    "classify_drift",
    "drift_pct",
    "evaluate_drift",
    "mean_abs_drift_pct",
    "suggest_waste_factor",
]

#: Spec 5.2's table. Mirrored in config as `drift_auto_order_max_pct` /
#: `drift_warn_max_pct`; these are the fallbacks for callers outside the app.
DEFAULT_ELIGIBLE_MAX_PCT = 10.0
DEFAULT_WARN_MAX_PCT = 15.0

#: How far a single observation may move `waste_factor` toward the loss rate it
#: implies. Drift is systematic but noisy: a count lands on one Tuesday, and the
#: shortfall it measures includes that week's accidents as well as the standing
#: rate. Jumping the whole way makes the factor chase noise and oscillate, so each
#: count moves it half-way and the next count judges the result.
DEFAULT_WASTE_DAMPING = Decimal("0.5")

#: A waste factor above this is not waste, it is a broken recipe or a theft problem,
#: and quietly absorbing it into depletion would hide the real fault.
MAX_WASTE_FACTOR = Decimal("0.35")

#: `ingredient.waste_factor` is a Qty column; three places is plenty for a ratio and
#: keeps the stored value readable in an audit.
WASTE_FACTOR_SCALE = Decimal("0.001")


def drift_pct(*, theoretical_qty: Decimal, counted_qty: Decimal) -> Decimal:
    """Spec 5.2, exactly. `EPSILON` guards the denominator at an empty shelf.

    Decimal in, Decimal out: `DriftResult.drift_pct` is a float because a ratio is
    not money, but the division itself stays exact so two callers computing the same
    drift never disagree in the last place.
    """
    return (theoretical_qty - counted_qty) / max(counted_qty, EPSILON) * Decimal(100)


def classify_drift(
    pct: float,
    *,
    eligible_max_pct: float = DEFAULT_ELIGIBLE_MAX_PCT,
    warn_max_pct: float = DEFAULT_WARN_MAX_PCT,
) -> DriftVerdict:
    """Spec 5.2's table as a value, on the ABSOLUTE drift.

    `ELIGIBLE` says this one observation clears the bar. It does NOT say
    auto-ordering turns on -- that needs two consecutive clears and lives in
    `domain/tiers.py`.
    """
    magnitude = abs(pct)
    if magnitude < eligible_max_pct:
        return DriftVerdict.ELIGIBLE
    if magnitude <= warn_max_pct:
        return DriftVerdict.TUNE_WASTE_FACTOR
    return DriftVerdict.FORCE_MANUAL


def suggest_waste_factor(
    *,
    current_waste_factor: Decimal,
    theoretical_qty: Decimal,
    counted_qty: Decimal,
    consumption_since_last_count: Decimal | None = None,
    damping: Decimal = DEFAULT_WASTE_DAMPING,
    max_waste_factor: Decimal = MAX_WASTE_FACTOR,
) -> Decimal | None:
    """A `waste_factor` that moves TOWARD the observed loss, not onto it.

    The shortfall `theoretical - counted` is consumption the ledger missed. When the
    caller can say how much of the ingredient the ledger *did* consume over the same
    window, the implied factor follows exactly:

        recorded   = base * (1 + w_now)          <- what the ledger depleted
        actual     = recorded + shortfall        <- what the shelf says happened
        1 + w_true = (1 + w_now) * actual / recorded

    and the proposal is `w_now + damping * (w_true - w_now)`. Without a consumption
    figure the shortfall is only measurable against the stock on hand, which is a
    weaker signal, so the nudge falls back to the drift fraction itself -- still in
    the right direction, still damped.

    Returns None when there is nothing to propose: the move rounds away, or it would
    leave the sane range. Never returns a negative factor, and never silently exceeds
    `max_waste_factor` -- a loss rate that large is a fault to investigate, not a
    number to absorb.
    """
    if not (Decimal("0") < damping <= Decimal("1")):
        raise ValueError(f"damping must be in (0, 1], got {damping}")

    shortfall = theoretical_qty - counted_qty
    if consumption_since_last_count is not None and consumption_since_last_count > 0:
        recorded = consumption_since_last_count
        implied = (Decimal("1") + current_waste_factor) * (recorded + shortfall) / recorded
        implied -= Decimal("1")
    else:
        implied = current_waste_factor + shortfall / max(counted_qty, EPSILON)

    proposal = current_waste_factor + damping * (implied - current_waste_factor)
    if proposal < 0:
        proposal = Decimal("0")
    if proposal > max_waste_factor:
        proposal = max_waste_factor

    quantized = proposal.quantize(WASTE_FACTOR_SCALE)
    if quantized == current_waste_factor.quantize(WASTE_FACTOR_SCALE):
        return None
    return quantized


def evaluate_drift(
    *,
    ingredient_id: int,
    theoretical_qty: Decimal,
    counted_qty: Decimal,
    observed_at: datetime,
    current_waste_factor: Decimal,
    consumption_since_last_count: Decimal | None = None,
    eligible_max_pct: float = DEFAULT_ELIGIBLE_MAX_PCT,
    warn_max_pct: float = DEFAULT_WARN_MAX_PCT,
    damping: Decimal = DEFAULT_WASTE_DAMPING,
    max_waste_factor: Decimal = MAX_WASTE_FACTOR,
) -> DriftResult:
    """One count -> one drift observation, with its verdict.

    The waste-factor proposal is populated in the tuning band only. Below 10% there
    is nothing worth chasing; above 15% the number is not a waste problem -- a 20%
    gap is a miscounted pack size, an unrecorded delivery or a wrong recipe, and
    burying it in `waste_factor` would make the ledger agree with the shelf while
    both stopped describing reality.
    """
    pct = drift_pct(theoretical_qty=theoretical_qty, counted_qty=counted_qty)
    verdict = classify_drift(
        float(pct), eligible_max_pct=eligible_max_pct, warn_max_pct=warn_max_pct
    )

    suggested: Decimal | None = None
    if verdict is DriftVerdict.TUNE_WASTE_FACTOR:
        suggested = suggest_waste_factor(
            current_waste_factor=current_waste_factor,
            theoretical_qty=theoretical_qty,
            counted_qty=counted_qty,
            consumption_since_last_count=consumption_since_last_count,
            damping=damping,
            max_waste_factor=max_waste_factor,
        )

    return DriftResult(
        ingredient_id=ingredient_id,
        theoretical_qty=theoretical_qty,
        counted_qty=counted_qty,
        drift_pct=float(pct),
        observed_at=observed_at,
        verdict=verdict,
        suggested_waste_factor=suggested,
    )


def mean_abs_drift_pct(pcts: Sequence[float]) -> float | None:
    """The rolling view spec 5.2 asks for. None when there is no history to roll."""
    if not pcts:
        return None
    return sum(abs(p) for p in pcts) / len(pcts)
