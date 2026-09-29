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

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from cafeops.domain.stock import drift_attribution
from cafeops.domain.types import EPSILON, DriftResult, DriftVerdict

__all__ = [
    "DEFAULT_ELIGIBLE_MAX_PCT",
    "DEFAULT_EXPIRY_DOMINANT_SHARE",
    "DEFAULT_MATERIAL_LOSS_PCT_OF_CONSUMPTION",
    "DEFAULT_MEASUREMENT_DOMINANT_SHARE",
    "DEFAULT_WARN_MAX_PCT",
    "DEFAULT_WASTE_DAMPING",
    "MAX_WASTE_FACTOR",
    "WASTE_FACTOR_SCALE",
    "DriftCause",
    "DriftExplanation",
    "classify_drift",
    "drift_pct",
    "evaluate_drift",
    "explain_drift",
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
    pct: Decimal | float,
    *,
    eligible_max_pct: float = DEFAULT_ELIGIBLE_MAX_PCT,
    warn_max_pct: float = DEFAULT_WARN_MAX_PCT,
) -> DriftVerdict:
    """Spec 5.2's table as a value, on the ABSOLUTE drift.

    `ELIGIBLE` says this one observation clears the bar. It does NOT say
    auto-ordering turns on -- that needs two consecutive clears and lives in
    `domain/tiers.py`.
    """
    # Compare exactly. A float here would let a gap of 15.0000001% round DOWN to the
    # tuning band and one of 9.9999999% round UP out of eligibility -- the two edges
    # where invariant 2 is decided.
    magnitude = abs(Decimal(str(pct)) if isinstance(pct, float) else pct)
    if magnitude < Decimal(str(eligible_max_pct)):
        return DriftVerdict.ELIGIBLE
    if magnitude <= Decimal(str(warn_max_pct)):
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
    verdict = classify_drift(pct, eligible_max_pct=eligible_max_pct, warn_max_pct=warn_max_pct)

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


# ==========================================================================
# Attribution: which of the two problems is this? Spec 5.2 (v2)
# ==========================================================================

#: Above this share of the loss, expiry is the story and the fix is to order less.
DEFAULT_EXPIRY_DOMINANT_SHARE = 0.60
#: Below this share, expiry is noise and the fix is the recipe or the waste factor.
DEFAULT_MEASUREMENT_DOMINANT_SHARE = 0.25
#: Loss smaller than this fraction of what the ledger consumed in the window is not
#: worth sending anybody to fix. Without a floor, an ingredient losing 4 ml of syrup
#: gets the same sentence as one losing 12 litres of milk, and the report stops being
#: read.
DEFAULT_MATERIAL_LOSS_PCT_OF_CONSUMPTION = 2.0


class DriftCause(enum.StrEnum):
    """What a drift gap is actually telling you to do.

    Defined here rather than in `domain/types.py` for the same reason `GateAction`
    lives in `domain/tiers.py`: it is the vocabulary of one decision, and that file is
    integrator-owned.
    """

    NEGLIGIBLE = "NEGLIGIBLE"
    EXPIRY = "EXPIRY"
    MEASUREMENT = "MEASUREMENT"
    MIXED = "MIXED"


@dataclass(frozen=True, slots=True)
class DriftExplanation:
    """A drift gap split into the two problems, with the action each one implies.

    Spec 5.2: *"If EXPIRED movements explain most of the gap, the problem is
    over-ordering, not a bad recipe."* The two fixes are opposite -- order less versus
    change the recipe -- so a single undifferentiated percentage tells the owner to do
    the wrong thing roughly half the time.

    One subtlety decides how these numbers must be read. **An `EXPIRED` movement that
    the sweep has already written is inside theoretical on-hand**, so it REDUCES the
    gap rather than inflating it. Attribution therefore only has work to do while the
    physical loss is visible to a count and the write-off is not yet in the ledger --
    the ordinary case, because the milk goes in the bin before the job runs. That is
    why `expired_qty` is carried and reported next to the gap rather than folded into
    it, and why `expiry_share` is measured against the whole loss (unexplained gap
    plus recorded write-off) instead of against the gap alone. An ingredient with a
    clean count and eight litres written off is an over-ordering problem, and a report
    that only looked at the gap would call it healthy.
    """

    ingredient_id: int
    observed_at: datetime
    theoretical_qty: Decimal
    counted_qty: Decimal
    #: Signed: positive means the ledger thinks there is MORE stock than the shelf has.
    gap_qty: Decimal
    #: Magnitude of EXPIRED movements in the same window.
    expired_qty: Decimal
    #: `drift_attribution`'s split of the gap.
    measurement_qty: Decimal
    expiry_qty: Decimal
    #: What the ledger believes was consumed in the window, for the materiality floor.
    consumption_qty: Decimal | None
    cause: DriftCause

    @property
    def unexplained_loss_qty(self) -> Decimal:
        """`measurement_qty`, but only when the gap is a SHORTFALL.

        `drift_attribution` works on the absolute gap, because the auto-order gate
        rightly distrusts over-statement and under-statement equally. Attribution is a
        different question: a NEGATIVE gap means the shelf holds more than the ledger
        knows, which is not a loss at all -- it is almost always an unrecorded delivery
        (`domain/drift.py` module docstring). Counting it as loss would invent waste out
        of a bookkeeping omission and send somebody to cut an order that was fine.
        """
        return self.measurement_qty if self.gap_qty > 0 else Decimal("0")

    @property
    def surplus_qty(self) -> Decimal:
        """Stock the count found that the ledger did not know about. Not a loss."""
        return -self.gap_qty if self.gap_qty < 0 else Decimal("0")

    @property
    def total_loss_qty(self) -> Decimal:
        """Everything lost beyond sales: unexplained shortfall plus recorded write-off."""
        return self.unexplained_loss_qty + self.expired_qty

    @property
    def expiry_share(self) -> float | None:
        """Fraction of the loss that expiry accounts for. None when there is no loss."""
        total = self.total_loss_qty
        if total <= 0:
            return None
        return float(self.expired_qty / total)

    @property
    def loss_pct_of_consumption(self) -> float | None:
        if self.consumption_qty is None or self.consumption_qty <= 0:
            return None
        return float(self.total_loss_qty / self.consumption_qty * Decimal(100))

    @property
    def headline(self) -> str:
        return {
            DriftCause.EXPIRY: "OVER-ORDERING",
            DriftCause.MEASUREMENT: "RECIPE OR WASTE FACTOR",
            DriftCause.MIXED: "BOTH",
            DriftCause.NEGLIGIBLE: "NOTHING TO FIX",
        }[self.cause]

    @property
    def action(self) -> str:
        """The sentence somebody acts on. One cause, one instruction."""
        share = self.expiry_share
        pct = f"{share * 100:.0f}%" if share is not None else "n/a"
        if self.cause is DriftCause.NEGLIGIBLE:
            return (
                "no material loss in this window: the count agrees with the ledger and "
                "nothing expired. Do not tune anything."
            )
        if self.cause is DriftCause.EXPIRY:
            return (
                f"{pct} of the loss is stock that expired, not consumption the recipe "
                "missed. ORDER LESS: shorten the cover window or cut the pack count. "
                "Tuning waste_factor here would hide a purchasing problem inside the "
                "recipe and make every cost wrong as well."
            )
        if self.cause is DriftCause.MEASUREMENT:
            return (
                f"only {pct} of the loss is expiry, so the stock left unrecorded. FIX THE "
                "RECIPE or tune waste_factor: order size is not the problem, and cutting "
                "it would cause a stockout while the real gap stayed."
            )
        return (
            f"{pct} of the loss is expiry and the rest is unrecorded. Fix the ordering "
            "first -- it is the half that is measured -- then re-count before touching "
            "waste_factor, or you will tune against a gap that is about to move."
        )

    @property
    def surplus_note(self) -> str | None:
        """Said separately from `action`, because it is a different problem.

        A surplus is not waste and has nothing to do with the recipe: the shelf holds
        stock the ledger never recorded arriving. Merging it into the loss sentence would
        make one number out of two unrelated faults.
        """
        if self.surplus_qty <= 0:
            return None
        return (
            f"the count also found {self.surplus_qty} MORE than the ledger expected. That "
            "is not a loss -- it points at a delivery nobody entered, or a miscount. Chase "
            "it separately; it is not evidence about the recipe or the order size."
        )


def explain_drift(
    *,
    ingredient_id: int,
    observed_at: datetime,
    theoretical_qty: Decimal,
    counted_qty: Decimal,
    expired_qty: Decimal,
    consumption_qty: Decimal | None = None,
    expiry_dominant_share: float = DEFAULT_EXPIRY_DOMINANT_SHARE,
    measurement_dominant_share: float = DEFAULT_MEASUREMENT_DOMINANT_SHARE,
    material_loss_pct: float = DEFAULT_MATERIAL_LOSS_PCT_OF_CONSUMPTION,
) -> DriftExplanation:
    """Split one drift observation into over-ordering versus recipe error.

    `expired_qty` is the magnitude of `EXPIRED` movements over the same window the
    drift was measured across -- `StockRepository.expired_qty_between` returns it.
    """
    gap = theoretical_qty - counted_qty
    expired = abs(expired_qty)
    measurement, expiry = drift_attribution(total_gap=gap, expired_qty=expired)
    # Only a SHORTFALL counts as loss. See `DriftExplanation.unexplained_loss_qty`.
    total = (measurement if gap > 0 else Decimal("0")) + expired

    cause = DriftCause.NEGLIGIBLE
    if total > 0:
        immaterial = (
            consumption_qty is not None
            and consumption_qty > 0
            and total < consumption_qty * Decimal(str(material_loss_pct)) / Decimal(100)
        )
        if not immaterial:
            share = float(expired / total)
            if share >= expiry_dominant_share:
                cause = DriftCause.EXPIRY
            elif share <= measurement_dominant_share:
                cause = DriftCause.MEASUREMENT
            else:
                cause = DriftCause.MIXED

    return DriftExplanation(
        ingredient_id=ingredient_id,
        observed_at=observed_at,
        theoretical_qty=theoretical_qty,
        counted_qty=counted_qty,
        gap_qty=gap,
        expired_qty=expired,
        measurement_qty=measurement,
        expiry_qty=expiry,
        consumption_qty=consumption_qty,
        cause=cause,
    )
