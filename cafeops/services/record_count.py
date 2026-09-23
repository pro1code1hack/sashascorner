"""Record a physical count: re-anchor on-hand, measure drift, run the gate.

One unit of work, in this order, and the order matters:

1. Compute theoretical on-hand **as of the count's own timestamp, before the count is
   written** -- otherwise `latest_count` returns the count itself and drift is always
   zero by construction.
2. Write the `StockCount`. It **re-anchors** on-hand (spec 5.1 reads the latest count
   and sums only what followed). It appends no correcting movement: doing both would
   apply the correction twice.
3. Write the `DriftObservation`, including `waste_factor_at_count`, so a later retune
   leaves the historical decision auditable (ARCHITECTURE.md 2.3).
4. Run the auto-order gate on the resulting history, and write its verdict to
   `par_level`.

All four happen inside the caller's transaction -- the CLI and the jobs wrap this in
`session_scope`, so a crash between steps leaves no count without its observation and
no observation without a gate decision.

## v2: attribution -- which of the two problems is it? (spec 5.2)

A drift number on its own is not actionable, because the two things that cause it have
OPPOSITE fixes. If the loss is stock that expired, the answer is to order less. If it is
not, the answer is to change the recipe or the waste factor. Cut the order when the
recipe was wrong and you cause a stockout with the gap still there; tune the recipe when
you were over-ordering and you make every menu cost wrong as well as keeping the waste.

`DriftObservation.expired_qty_in_window` stores what was known AT THE COUNT, and
`explain_drift_history` recomputes the figure live from the ledger. The two differ often
and the difference is informative rather than a bug: the expiry sweep usually runs after
somebody counted, so a gap that was unexplained on Tuesday is explained by Wednesday's
write-off. The stored value keeps the audit trail honest about what the gate acted on;
the live value is what the report should be read from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.repositories.batch import SqlBatchRepository
from cafeops.db.repositories.drift import SqlDriftRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.par import SqlParLevelRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.drift import (
    DEFAULT_WASTE_DAMPING,
    MAX_WASTE_FACTOR,
    DriftCause,
    DriftExplanation,
    evaluate_drift,
    explain_drift,
)
from cafeops.domain.stock import (
    CountReconciliation,
    batch_expiry_for,
    reconcile_to_count,
    theoretical_on_hand,
)
from cafeops.domain.tiers import GateDecision, evaluate_gate
from cafeops.domain.types import DriftResult, DriftVerdict, IngredientSnapshot, OnHand

#: `StockRepository.latest_count` matches `counted_at <= before`, so finding the count
#: BEFORE a given one means stepping back the smallest representable amount.
_A_MOMENT = timedelta(microseconds=1)

#: How many observations to fetch for the gate. More than `required_consecutive` so the
#: reason can say "8 consecutive counts under 10%" instead of stopping at 2.
_HISTORY_DEPTH = 8


@dataclass(frozen=True, slots=True)
class CountOutcome:
    """What one count did: the number, the drift it revealed, and the gate's verdict."""

    ingredient: IngredientSnapshot
    stock_count_id: int
    counted_qty: Decimal
    counted_at: datetime
    #: Theoretical on-hand at `counted_at`, computed before the count re-anchored it.
    on_hand_before: OnHand
    #: None for the first count of an ingredient: there is no anchor to measure against,
    #: so there is no drift, and inventing one would put a -100% observation in the
    #: history that the gate would then act on.
    drift: DriftResult | None
    drift_observation_id: int | None
    #: Spec 5.2's attribution: over-ordering or a bad recipe. None whenever `drift` is,
    #: because there is nothing to attribute without a gap.
    explanation: DriftExplanation | None
    decision: GateDecision
    par_level_written: bool
    #: What the count did to the batch records. A count re-anchors on-hand, so the
    #: batches are trued up to it (ARCHITECTURE 8F.2). Surfaced rather than discarded
    #: because a large reconciliation is itself a finding: it means the batch records
    #: had drifted from reality, and only batched stock can expire or be written off.
    reconciliation: CountReconciliation | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def alert(self) -> bool:
        """True when the gate raised an alert. Spec 5.2: >15% drift, immediately."""
        return self.decision.alert

    @property
    def verdict(self) -> DriftVerdict | None:
        return None if self.drift is None else self.drift.verdict


@dataclass
class BackfillReport:
    counts_seen: int = 0
    observations_written: int = 0
    anchors_skipped: int = 0
    decisions: list[CountOutcome] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.counts_seen} counts without an observation",
            f"wrote {self.observations_written} drift observations",
        ]
        if self.anchors_skipped:
            parts.append(f"{self.anchors_skipped} were first counts (anchors, no drift)")
        if self.decisions:
            parts.append(f"ran the gate on {len(self.decisions)} ingredient(s)")
        return "; ".join(parts)


def record_count(
    session: Session,
    *,
    ingredient_id: int,
    counted_qty: Decimal,
    counted_at: datetime | None = None,
    counted_by: str,
    note: str | None = None,
    decided_at: datetime | None = None,
    eligible_max_pct: float | None = None,
    warn_max_pct: float | None = None,
    required_consecutive: int | None = None,
    damping: Decimal = DEFAULT_WASTE_DAMPING,
    max_waste_factor: Decimal = MAX_WASTE_FACTOR,
) -> CountOutcome:
    """Record one physical count and run everything that follows from it."""
    if isinstance(counted_qty, float):  # pragma: no cover - guarded like Qty does
        raise TypeError("counted_qty must be Decimal, not float (invariant 8)")
    if counted_qty < 0:
        raise ValueError(f"counted_qty must not be negative, got {counted_qty}")

    counted_at = counted_at or datetime.now(UTC)
    if counted_at.tzinfo is None:
        raise ValueError("counted_at must be timezone-aware (spec 4: all timestamps UTC)")
    decided_at = decided_at or datetime.now(UTC)

    ingredients = SqlIngredientRepository(session)
    stock = SqlStockRepository(session)
    drift_repo = SqlDriftRepository(session)

    ingredient = ingredients.get(ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")

    notes: list[str] = []

    # --- 1. theoretical on-hand, BEFORE the count re-anchors it -------------------
    anchor = stock.latest_count(ingredient_id, before=counted_at)
    movement_sum, movement_count = stock.movement_sum_between(
        ingredient_id, after=anchor[1] if anchor else None, until=counted_at
    )
    on_hand_before = theoretical_on_hand(
        ingredient_id=ingredient_id,
        as_of=counted_at,
        latest_count=anchor,
        movement_sum=movement_sum,
        movement_count=movement_count,
    )

    latest_any = stock.latest_count(ingredient_id, before=datetime.now(UTC))
    if latest_any is not None and counted_at < latest_any[1]:
        notes.append(
            f"back-dated: a later count already exists at {latest_any[1]:%Y-%m-%d %H:%M}, "
            "so this count does not re-anchor today's on-hand"
        )

    # --- 2. the count itself. Source of truth, no correcting movement -------------
    stock_count_id = stock.record_count(ingredient_id, counted_qty, counted_at, counted_by, note)

    # --- 3. drift ----------------------------------------------------------------
    drift: DriftResult | None = None
    observation_id: int | None = None
    explanation: DriftExplanation | None = None
    if not on_hand_before.has_count_basis:
        notes.append(
            "first count for this ingredient: it establishes the anchor, so there is "
            "nothing to measure drift against"
        )
    else:
        consumption = drift_repo.consumption_between(
            ingredient_id, after=anchor[1] if anchor else None, until=counted_at
        )
        # Spec 5.2's second diagnostic, over exactly the window the drift was measured
        # across. Read from the ledger rather than from the sweep's report: a write-off
        # entered by any path counts, and one the sweep has not run yet does not.
        expired_in_window = stock.expired_qty_between(
            ingredient_id, after=anchor[1] if anchor else None, until=counted_at
        )
        drift = evaluate_drift(
            ingredient_id=ingredient_id,
            theoretical_qty=on_hand_before.qty,
            counted_qty=counted_qty,
            observed_at=counted_at,
            current_waste_factor=ingredient.waste_factor,
            consumption_since_last_count=consumption or None,
            eligible_max_pct=_eligible(eligible_max_pct),
            warn_max_pct=_warn(warn_max_pct),
            damping=damping,
            max_waste_factor=max_waste_factor,
        )
        observation_id = drift_repo.record(
            ingredient_id,
            stock_count_id,
            drift.theoretical_qty,
            drift.counted_qty,
            drift.drift_pct,
            ingredient.waste_factor,
            drift.observed_at,
            expired_qty_in_window=expired_in_window,
        )
        explanation = explain_drift(
            ingredient_id=ingredient_id,
            observed_at=drift.observed_at,
            theoretical_qty=drift.theoretical_qty,
            counted_qty=drift.counted_qty,
            expired_qty=expired_in_window,
            consumption_qty=consumption or None,
        )

    # --- 3b. re-anchor the batches -----------------------------------------------
    # A count re-anchors theoretical on-hand (spec 5.1), and ARCHITECTURE 8F.2 lists
    # batch re-anchoring as behaviour to keep. Without it the batches keep their
    # pre-count quantities for ever: StockReading.batch_coverage_gap grows with every
    # count, and because FIFO and the expiry sweep can only see BATCHED stock, an
    # unreconciled surplus can never expire and never be counted as waste. Only
    # `rebuild_batches` repaired that, which meant the production path diverged and the
    # repair tool was the only thing that agreed with itself.
    reconciliation = _reanchor_batches(
        session, ingredient=ingredient, counted_qty=counted_qty, counted_at=counted_at
    )

    # --- 4. the gate -------------------------------------------------------------
    decision, written = run_gate(
        session,
        ingredient=ingredient,
        at=decided_at,
        eligible_max_pct=eligible_max_pct,
        warn_max_pct=warn_max_pct,
        required_consecutive=required_consecutive,
    )

    return CountOutcome(
        ingredient=ingredient,
        stock_count_id=stock_count_id,
        counted_qty=counted_qty,
        counted_at=counted_at,
        on_hand_before=on_hand_before,
        drift=drift,
        drift_observation_id=observation_id,
        explanation=explanation,
        decision=decision,
        par_level_written=written,
        reconciliation=reconciliation,
        notes=tuple(notes),
    )


def run_gate(
    session: Session,
    *,
    ingredient: IngredientSnapshot,
    at: datetime,
    eligible_max_pct: float | None = None,
    warn_max_pct: float | None = None,
    required_consecutive: int | None = None,
) -> tuple[GateDecision, bool]:
    """Evaluate the gate from stored history and write the verdict to `par_level`.

    Separated from `record_count` so the backfill can run it once per ingredient after
    replaying a whole history, rather than once per replayed count.
    """
    drift_repo = SqlDriftRepository(session)
    par_repo = SqlParLevelRepository(session)

    required = _required(required_consecutive)
    par = par_repo.get(ingredient.id)
    recent = drift_repo.recent_drift_pcts(ingredient.id, limit=max(required, _HISTORY_DEPTH))

    decision = evaluate_gate(
        ingredient_id=ingredient.id,
        tier=ingredient.tier,
        recent_drift_pcts=recent,
        currently_enabled=bool(par and par.auto_order_enabled),
        has_par_level=par is not None,
        required_consecutive=required,
        eligible_max_pct=_eligible(eligible_max_pct),
        warn_max_pct=_warn(warn_max_pct),
    )
    written = par_repo.apply_gate_decision(decision, at=at)
    return decision, written


def gate_status(
    session: Session,
    *,
    ingredient: IngredientSnapshot,
    eligible_max_pct: float | None = None,
    warn_max_pct: float | None = None,
    required_consecutive: int | None = None,
) -> GateDecision:
    """The gate's verdict as a read-only view. Writes nothing -- for reports."""
    drift_repo = SqlDriftRepository(session)
    par_repo = SqlParLevelRepository(session)
    required = _required(required_consecutive)
    par = par_repo.get(ingredient.id)
    return evaluate_gate(
        ingredient_id=ingredient.id,
        tier=ingredient.tier,
        recent_drift_pcts=drift_repo.recent_drift_pcts(
            ingredient.id, limit=max(required, _HISTORY_DEPTH)
        ),
        currently_enabled=bool(par and par.auto_order_enabled),
        has_par_level=par is not None,
        required_consecutive=required,
        eligible_max_pct=_eligible(eligible_max_pct),
        warn_max_pct=_warn(warn_max_pct),
    )


def backfill_drift_observations(
    session: Session,
    *,
    ingredient_id: int | None = None,
    limit: int | None = None,
    decided_at: datetime | None = None,
    eligible_max_pct: float | None = None,
    warn_max_pct: float | None = None,
    required_consecutive: int | None = None,
    damping: Decimal = DEFAULT_WASTE_DAMPING,
    max_waste_factor: Decimal = MAX_WASTE_FACTOR,
) -> BackfillReport:
    """Measure drift for counts recorded before drift existed, then run the gate.

    The demo seed (and any pre-Phase-1 count) writes `stock_count` rows with no
    observation. Replaying them is not the same as inventing history: each count's
    theoretical figure is recomputed from the count that preceded it and the movements
    between, which is exactly what `record_count` would have done at the time.

    One honest limitation: `waste_factor_at_count` is filled from the ingredient's
    CURRENT factor, because no history of the factor is kept. Backfilling after a
    retune therefore mislabels the older rows, and the report says so.
    """
    report = BackfillReport()
    decided_at = decided_at or datetime.now(UTC)

    ingredients = SqlIngredientRepository(session)
    stock = SqlStockRepository(session)
    drift_repo = SqlDriftRepository(session)

    rows = drift_repo.counts_missing_observations(ingredient_id=ingredient_id, limit=limit)
    report.counts_seen = len(rows)
    touched: dict[int, IngredientSnapshot] = {}

    for count_id, ing_id, counted_qty, counted_at in rows:
        ingredient = touched.get(ing_id) or ingredients.get(ing_id)
        if ingredient is None:
            report.warnings.append(f"stock_count {count_id}: ingredient {ing_id} not found")
            continue
        touched[ing_id] = ingredient

        anchor = stock.latest_count(ing_id, before=counted_at - _A_MOMENT)
        if anchor is None:
            report.anchors_skipped += 1
            continue

        movement_sum, _ = stock.movement_sum_between(ing_id, after=anchor[1], until=counted_at)
        expired_in_window = stock.expired_qty_between(ing_id, after=anchor[1], until=counted_at)
        drift = evaluate_drift(
            ingredient_id=ing_id,
            theoretical_qty=anchor[0] + movement_sum,
            counted_qty=counted_qty,
            observed_at=counted_at,
            current_waste_factor=ingredient.waste_factor,
            consumption_since_last_count=drift_repo.consumption_between(
                ing_id, after=anchor[1], until=counted_at
            )
            or None,
            eligible_max_pct=_eligible(eligible_max_pct),
            warn_max_pct=_warn(warn_max_pct),
            damping=damping,
            max_waste_factor=max_waste_factor,
        )
        drift_repo.record(
            ing_id,
            count_id,
            drift.theoretical_qty,
            drift.counted_qty,
            drift.drift_pct,
            ingredient.waste_factor,
            drift.observed_at,
            expired_qty_in_window=expired_in_window,
        )
        report.observations_written += 1

    for ingredient in touched.values():
        decision, written = run_gate(
            session,
            ingredient=ingredient,
            at=decided_at,
            eligible_max_pct=eligible_max_pct,
            warn_max_pct=warn_max_pct,
            required_consecutive=required_consecutive,
        )
        report.decisions.append(
            CountOutcome(
                ingredient=ingredient,
                stock_count_id=0,
                counted_qty=Decimal("0"),
                counted_at=decided_at,
                on_hand_before=theoretical_on_hand(
                    ingredient_id=ingredient.id,
                    as_of=decided_at,
                    latest_count=None,
                    movement_sum=Decimal("0"),
                ),
                drift=None,
                drift_observation_id=None,
                explanation=None,
                decision=decision,
                par_level_written=written,
                notes=("gate re-run after backfill",),
            )
        )
    return report


@dataclass(frozen=True, slots=True)
class DriftExplanationRow:
    """One stored observation, attributed. What `cafeops drift --explain` prints."""

    observation_id: int
    ingredient: IngredientSnapshot
    explanation: DriftExplanation
    #: The start of the window this observation measures: the count before it. None
    #: means the observation has no predecessor, which only happens if a count was
    #: deleted -- the first count of an ingredient never produces an observation.
    window_start: datetime | None
    #: The figure recorded WHEN THE COUNT WAS TAKEN, from
    #: `DriftObservation.expired_qty_in_window`. None for pre-v2 rows.
    stored_expired_qty: Decimal | None

    @property
    def sweep_ran_after_count(self) -> bool:
        """True when a write-off dated inside the window was booked after the count.

        Not a discrepancy -- it is the ordinary case, and the reason attribution is
        worth doing at all. Somebody counted a shelf that was already short; the sweep
        later named the reason. The gate acted on the stored figure, the report should
        be read from the live one, and the report says which is which.
        """
        stored = self.stored_expired_qty or Decimal("0")
        return self.explanation.expired_qty > stored

    @property
    def cause(self) -> DriftCause:
        return self.explanation.cause


def explain_drift_history(
    session: Session,
    *,
    ingredient_id: int,
    limit: int = 6,
) -> list[DriftExplanationRow]:
    """Attribute each stored drift observation: over-ordering, or the recipe?

    The expiry figure is RECOMPUTED from the ledger rather than read from the stored
    column. Both are returned -- see `DriftExplanationRow.sweep_ran_after_count` -- but
    the live one is the answer to "what do I fix", because the ledger is append-only and
    a write-off booked after the count is still a write-off that happened in the window.
    """
    ingredients = SqlIngredientRepository(session)
    drift_repo = SqlDriftRepository(session)
    stock = SqlStockRepository(session)

    ingredient = ingredients.get(ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")

    rows: list[DriftExplanationRow] = []
    for observation in drift_repo.history(ingredient_id, limit=limit):
        anchor = stock.latest_count(ingredient_id, before=observation.observed_at - _A_MOMENT)
        window_start = anchor[1] if anchor else None
        rows.append(
            DriftExplanationRow(
                observation_id=observation.id,
                ingredient=ingredient,
                explanation=explain_drift(
                    ingredient_id=ingredient_id,
                    observed_at=observation.observed_at,
                    theoretical_qty=observation.theoretical_qty,
                    counted_qty=observation.counted_qty,
                    expired_qty=stock.expired_qty_between(
                        ingredient_id, after=window_start, until=observation.observed_at
                    ),
                    consumption_qty=drift_repo.consumption_between(
                        ingredient_id, after=window_start, until=observation.observed_at
                    )
                    or None,
                ),
                window_start=window_start,
                stored_expired_qty=observation.expired_qty_in_window,
            )
        )
    return rows


def apply_waste_suggestion(
    session: Session,
    *,
    ingredient_id: int,
    damping: Decimal = DEFAULT_WASTE_DAMPING,
    max_waste_factor: Decimal = MAX_WASTE_FACTOR,
    eligible_max_pct: float | None = None,
    warn_max_pct: float | None = None,
) -> tuple[Decimal, Decimal] | None:
    """Adopt the waste factor proposed by the latest observation. A deliberate act.

    Spec 5.1: `waste_factor` is tuned from observed drift, not guessed once. It is a
    separate call rather than something `record_count` does on its own, because an
    automatic retune would move the number the gate is judging at the same moment it is
    judged, and two counts later nobody could say which change caused which reading.

    Returns (old, new) or None when the latest observation proposes nothing.
    """
    ingredients = SqlIngredientRepository(session)
    drift_repo = SqlDriftRepository(session)
    stock = SqlStockRepository(session)

    ingredient = ingredients.get(ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")

    history = drift_repo.history(ingredient_id, limit=1)
    if not history:
        return None
    latest = history[0]

    anchor = stock.latest_count(ingredient_id, before=latest.observed_at - _A_MOMENT)
    consumption = drift_repo.consumption_between(
        ingredient_id, after=anchor[1] if anchor else None, until=latest.observed_at
    )
    drift = evaluate_drift(
        ingredient_id=ingredient_id,
        theoretical_qty=latest.theoretical_qty,
        counted_qty=latest.counted_qty,
        observed_at=latest.observed_at,
        current_waste_factor=ingredient.waste_factor,
        consumption_since_last_count=consumption or None,
        eligible_max_pct=_eligible(eligible_max_pct),
        warn_max_pct=_warn(warn_max_pct),
        damping=damping,
        max_waste_factor=max_waste_factor,
    )
    if drift.suggested_waste_factor is None:
        return None

    old = ingredient.waste_factor
    ingredients.set_waste_factor(ingredient_id, drift.suggested_waste_factor)
    return old, drift.suggested_waste_factor


def _eligible(value: float | None) -> float:
    return settings.drift_auto_order_max_pct if value is None else value


def _warn(value: float | None) -> float:
    return settings.drift_warn_max_pct if value is None else value


def _required(value: int | None) -> int:
    return settings.drift_consecutive_counts_required if value is None else value


def _reanchor_batches(
    session: Session,
    *,
    ingredient: IngredientSnapshot,
    counted_qty: Decimal,
    counted_at: datetime,
) -> CountReconciliation:
    """Make the batch records agree with what was physically counted.

    Writes no movement: the count IS the re-anchor, and an ADJUSTMENT here would book
    the same correction twice. A surplus opens a batch, because stock that
    demonstrably exists has to live somewhere and unbatched stock is invisible to both
    FIFO and the expiry sweep.
    """
    batch_repo = SqlBatchRepository(session)
    # Shelf life comes from the repository rather than the snapshot: `IngredientSnapshot`
    # deliberately does not carry it, and one source for "how long does this last" is
    # what keeps the expiry the sweep enforces and the expiry a count reasons about the
    # same number.
    shelf_life = batch_repo.shelf_life(ingredient.id)
    open_life_days = shelf_life.open_life_days if shelf_life else None
    open_batches = batch_repo.open_batches(ingredient.id, at=counted_at)
    plan = reconcile_to_count(
        counted_qty=counted_qty,
        batches=open_batches,
        open_life_days=open_life_days,
    )
    if plan.is_noop:
        return plan

    if plan.reductions:
        batch_repo.apply_allocations(plan.reductions, at=counted_at)
    if plan.surplus_qty > 0:
        batch_repo.create_batch(
            ingredient.id,
            qty=plan.surplus_qty,
            received_at=counted_at,
            expires_at=batch_expiry_for(received_at=counted_at, shelf_life=shelf_life),
            # `cost_per_unit_pence` on the snapshot, not the ORM column name.
            unit_cost_pence=ingredient.cost_per_unit_pence or Decimal("0"),
            note="count surplus -- stock found that no batch explained",
        )
    return plan
