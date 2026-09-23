"""The stock screen. Spec 5.1, 5.2, 4.1 -- and invariants 6 and 9.

Spec 10 asks for theoretical and counted to be *distinguishable at a glance by
type/weight/position, not a tooltip*. The API's job in that is to make the distinction
impossible to lose: `OnHand` carries `is_theoretical` and `has_count_basis` as required
fields, so a row cannot be serialised without them, and `basis_label` is the sentence
the design can set in a different weight.

Three things are assembled here that the CLI's `stock` and `drift` commands show
separately, because on one screen they answer one question -- *is this number worth
ordering against?*

* **On-hand and batches**, from `services.read_stock.read_on_hand`.
* **Drift, with attribution and the gate**, from `services.record_count`. Spec 5.2's v2
  diagnostic splits the gap into over-ordering versus recipe error, and the two fixes
  are opposite, so the cause travels with the number.
* **Projected run-out**, which is the number a person actually acts on -- and therefore
  the one worst to guess at. When the forecast behind it is low-confidence, the date and
  the day count are **absent from the payload** and the reasons are what the screen
  shows (invariant 9).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from cafeops.api.encoding import as_pence, as_qty, pct
from cafeops.api.schemas import (
    BatchOut,
    DriftAttributionOut,
    DriftHistoryRowOut,
    DriftOut,
    OnHand,
    RunOut,
    ShelfLifeOut,
    StockDetail,
    StockResponse,
    StockRow,
    StockSummary,
)
from cafeops.api.views.common import cost_from_ingredient, forecast_out
from cafeops.config import settings
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.drift import SqlDriftRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.par import SqlParLevelRepository
from cafeops.db.repositories.season import SqlSeasonRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.drift import (
    DEFAULT_WASTE_DAMPING,
    DriftExplanation,
    evaluate_drift,
    mean_abs_drift_pct,
)
from cafeops.domain.types import ForecastResult, IngredientSnapshot, SeasonSpec, ShelfLifeSpec, Tier

# `_forecast_for` is Agent D's private helper in `services/build_order.py`. It is
# imported rather than reimplemented on purpose: it holds the choice between the
# ordinary EWMA path and the seasonal one, and a second copy of that choice would mean
# the stock screen's run-out date and the order screen's quantity could be built on
# different forecasts of the same ingredient -- two screens disagreeing about one
# number, which is the failure this reuse prevents. INTEGRATOR NOTE: it should be
# promoted to a public `forecast_for` in `build_order.__all__`; that is Agent D's file
# and this agent does not edit it.
from cafeops.services.build_order import (
    ForecastKnobs,
    _forecast_for,
    shelf_life_specs,
)
from cafeops.services.read_stock import StockReading, read_on_hand
from cafeops.services.record_count import explain_drift_history, gate_status

__all__ = ["stock_detail_view", "stock_view"]

#: How far ahead the run-out projection looks. Long enough that a slow-moving tier B
#: item gets a real answer rather than "more than a week", short enough that the
#: forecast is not being asked for a quarter it cannot see.
RUN_OUT_HORIZON_DAYS = 28

#: Short-dated threshold, matching `cafeops expiry-sweep --short-dated-days`.
SHORT_DATED_DAYS = 3


# --------------------------------------------------------------------------
# on-hand and batches
# --------------------------------------------------------------------------


def _on_hand(reading: StockReading) -> OnHand:
    on_hand = reading.on_hand
    return OnHand(
        qty=as_qty(on_hand.qty) or "0",
        unit=reading.ingredient.unit.value,
        as_of=on_hand.as_of,
        is_theoretical=on_hand.is_theoretical,
        has_count_basis=on_hand.has_count_basis,
        basis_count_qty=as_qty(on_hand.basis_count_qty),
        basis_counted_at=on_hand.basis_counted_at,
        movement_sum=as_qty(on_hand.movement_sum) or "0",
        movement_count=on_hand.movement_count,
        basis_label=("counted + ledger" if on_hand.has_count_basis else "ledger only - NO COUNT"),
        is_negative=on_hand.qty < 0,
    )


def _batches(reading: StockReading, at: datetime) -> tuple[BatchOut, ...]:
    out: list[BatchOut] = []
    for spec in reading.batches:
        value = None if spec.unit_cost_pence is None else spec.qty_remaining * spec.unit_cost_pence
        out.append(
            BatchOut(
                batch_id=spec.batch_id,
                qty_remaining=as_qty(spec.qty_remaining) or "0",
                unit=reading.ingredient.unit.value,
                received_at=spec.received_at,
                expires_at=spec.expires_at,
                opened_at=spec.opened_at,
                effective_expiry=spec.effective_expiry(reading.open_life_days),
                days_left=spec.days_left(at, reading.open_life_days),
                unit_cost_pence=as_pence(spec.unit_cost_pence),
                value_pence=as_pence(value),
            )
        )
    return tuple(out)


def _shelf_life(reading: StockReading, spec: ShelfLifeSpec | None) -> ShelfLifeOut:
    if spec is None:
        return ShelfLifeOut(
            storage="AMBIENT",
            shelf_life_days=reading.shelf_life_days,
            open_life_days=reading.open_life_days,
            transit_buffer_days=0,
            usable_days=reading.shelf_life_days,
            is_perishable=reading.shelf_life_days is not None,
            source=None,
        )
    return ShelfLifeOut(
        storage=spec.storage.value,
        shelf_life_days=spec.shelf_life_days,
        open_life_days=spec.open_life_days,
        transit_buffer_days=spec.transit_buffer_days,
        usable_days=spec.usable_days,
        is_perishable=spec.is_perishable,
        source=spec.source.value if spec.source is not None else None,
    )


# --------------------------------------------------------------------------
# drift, attribution and the gate (spec 5.2)
# --------------------------------------------------------------------------


def _drift(session: Session, ingredient: IngredientSnapshot, *, history: int = 6) -> DriftOut:
    drift_repo = SqlDriftRepository(session)
    par_repo = SqlParLevelRepository(session)

    rows = drift_repo.history(ingredient.id, limit=max(history, 2))
    decision = gate_status(session, ingredient=ingredient)
    audit = par_repo.audit(ingredient.id)
    latest = rows[0] if rows else None

    attribution: DriftAttributionOut | None = None
    if latest is not None:
        explained = explain_drift_history(session, ingredient_id=ingredient.id, limit=1)
        if explained:
            attribution = _attribution(explained[0].explanation)

    return DriftOut(
        has_observation=latest is not None,
        observed_at=latest.observed_at if latest else None,
        theoretical_qty=as_qty(latest.theoretical_qty) if latest else None,
        counted_qty=as_qty(latest.counted_qty) if latest else None,
        drift_pct=pct(latest.drift_pct) if latest else None,
        verdict=decision.verdict.value if decision.verdict is not None else None,
        mean_abs_drift_pct=pct(mean_abs_drift_pct([row.drift_pct for row in rows])),
        observation_count=len(rows),
        auto_order_enabled=bool(audit and audit.auto_order_enabled),
        auto_order_reason=audit.reason if audit else None,
        clean_streak=decision.clean_streak,
        required_streak=decision.required_streak,
        gate_action=decision.action.value,
        attribution=attribution,
    )


def _attribution(explanation: DriftExplanation) -> DriftAttributionOut:
    return DriftAttributionOut(
        cause=explanation.cause.value,
        headline=explanation.headline,
        action=explanation.action,
        gap_qty=as_qty(explanation.gap_qty) or "0",
        expired_qty=as_qty(explanation.expired_qty) or "0",
        measurement_qty=as_qty(explanation.measurement_qty) or "0",
        expiry_qty=as_qty(explanation.expiry_qty) or "0",
        expiry_share=pct(explanation.expiry_share),
        unexplained_loss_qty=as_qty(explanation.unexplained_loss_qty) or "0",
        surplus_qty=as_qty(explanation.surplus_qty) or "0",
        surplus_note=explanation.surplus_note,
        loss_pct_of_consumption=pct(explanation.loss_pct_of_consumption),
    )


# --------------------------------------------------------------------------
# projected run-out (invariant 9)
# --------------------------------------------------------------------------


def _run_out(
    *,
    reading: StockReading,
    forecast: ForecastResult,
    at: datetime,
) -> RunOut:
    unit = reading.ingredient.unit
    out = forecast_out(forecast, unit=unit, window_days=RUN_OUT_HORIZON_DAYS)
    qty = reading.on_hand.qty

    if forecast.low_confidence:
        # Invariant 9: the reasons go IN PLACE OF the number. Not the daily rate, not
        # the day count, not the date -- a run-out date derived from a withheld
        # forecast is the same guess wearing a different number's clothes.
        return RunOut(
            forecast=out,
            daily_rate_qty=None,
            days=None,
            on=None,
            is_out_of_stock=qty <= 0,
            note=(
                "run-out withheld: the forecast behind it is low-confidence "
                "(invariant 9). Render the reasons, not a date."
            ),
        )

    rate = forecast.base_daily
    if rate <= 0:
        return RunOut(
            forecast=out,
            daily_rate_qty=as_qty(rate),
            days=None,
            on=None,
            is_out_of_stock=qty <= 0,
            note=(
                "nothing is moving: no measured consumption, so there is no run-out date. "
                "This is not 'plenty in stock' -- it is an ingredient the ledger has never "
                "seen consumed, which for an unmodelled drink is expected "
                "(ARCHITECTURE 10) and for a selling one is a recipe that is missing it."
            ),
        )

    if qty <= 0:
        return RunOut(
            forecast=out,
            daily_rate_qty=as_qty(rate),
            days="0",
            on=at.astimezone(settings.tz).date(),
            is_out_of_stock=True,
            note="theoretical on-hand is at or below zero already. Count it.",
        )

    days = qty / rate
    return RunOut(
        forecast=out,
        daily_rate_qty=as_qty(rate),
        days=as_qty(days.quantize(Decimal("0.01"))),
        on=at.astimezone(settings.tz).date() + timedelta(days=int(days)),
        is_out_of_stock=False,
    )


# --------------------------------------------------------------------------
# the rows
# --------------------------------------------------------------------------


def _row(
    session: Session,
    *,
    reading: StockReading,
    shelf_life: ShelfLifeSpec | None,
    season: SeasonSpec | None,
    at: datetime,
    knobs: ForecastKnobs,
    stock_repo: SqlStockRepository,
    with_run_out: bool,
) -> StockRow:
    ingredient = reading.ingredient
    local_today = at.astimezone(settings.tz).date()
    if with_run_out:
        horizon = [local_today + timedelta(days=n) for n in range(RUN_OUT_HORIZON_DAYS)]
        forecast = _forecast_for(
            ingredient=ingredient,
            season=season,
            days=horizon,
            history_end=local_today - timedelta(days=1),
            knobs=knobs,
            stock_repo=stock_repo,
            closed_days=(),
        )
        run_out = _run_out(reading=reading, forecast=forecast, at=at)
    else:
        run_out = RunOut(
            forecast=None,
            note=(
                "run-out not computed: the request passed run_out=false. Absent because it "
                "was not asked for, which is a different thing from absent because the "
                "forecast was withheld -- that case has a forecast with is_low_confidence."
            ),
        )

    days_left = reading.soonest_expiry_days
    return StockRow(
        ingredient_id=ingredient.id,
        name=ingredient.name,
        tier=ingredient.tier.value,
        unit=ingredient.unit.value,
        tracking_enabled=ingredient.tracking_enabled,
        waste_factor=as_qty(ingredient.waste_factor) or "0",
        unit_cost=cost_from_ingredient(ingredient),
        on_hand=_on_hand(reading),
        shelf_life=_shelf_life(reading, shelf_life),
        batches=_batches(reading, at),
        batch_qty=as_qty(reading.batch_qty) or "0",
        batch_coverage_gap=as_qty(reading.batch_coverage_gap) or "0",
        soonest_expiry_days=days_left,
        is_short_dated=days_left is not None and days_left <= SHORT_DATED_DAYS,
        drift=_drift(session, ingredient),
        run_out=run_out,
    )


def stock_view(
    session: Session,
    *,
    as_of: datetime | None = None,
    tiers: tuple[Tier, ...] | None = None,
    include_untracked: bool = False,
    with_run_out: bool = True,
) -> StockResponse:
    at = as_of or datetime.now(UTC)
    readings = read_on_hand(session, as_of=at, tiers=tiers, include_untracked=include_untracked)
    shelf_lives = shelf_life_specs(session)
    seasons = SqlSeasonRepository(session).seasons_by_ingredient()
    knobs = ForecastKnobs.from_settings()
    stock_repo = SqlStockRepository(session, tz=settings.tz)

    rows = tuple(
        _row(
            session,
            reading=reading,
            shelf_life=shelf_lives.get(reading.ingredient.id),
            season=seasons.get(reading.ingredient.id),
            at=at,
            knobs=knobs,
            stock_repo=stock_repo,
            with_run_out=with_run_out,
        )
        for reading in readings
    )
    return StockResponse(as_of=at, summary=_summary(rows), rows=rows)


def _summary(rows: tuple[StockRow, ...]) -> StockSummary:
    unanchored = sum(1 for row in rows if not row.on_hand.has_count_basis)
    negative = sum(1 for row in rows if row.on_hand.is_negative)
    short_dated = sum(1 for row in rows if row.is_short_dated)
    unbatched = sum(
        1 for row in rows if row.batches and abs(Decimal(row.batch_coverage_gap)) > Decimal("0.001")
    )
    auto_on = sum(1 for row in rows if row.drift.auto_order_enabled)
    forced = sum(1 for row in rows if row.drift.verdict == "FORCE_MANUAL")

    expiring: Decimal | None = Decimal("0")
    for row in rows:
        for batch in row.batches:
            if batch.days_left is None or batch.days_left > SHORT_DATED_DAYS:
                continue
            if batch.value_pence is None:
                # Invariant 8: a total that silently omits an unpriced batch understates
                # the loss it exists to warn about. Refuse the total instead.
                expiring = None
                break
            if expiring is not None:
                expiring += Decimal(batch.value_pence)
        if expiring is None:
            break

    notes: list[str] = []
    if unanchored:
        notes.append(
            f"{unanchored} ingredient(s) have no physical count behind their figure -- a "
            "bare movement sum, not good enough to order against (invariant 6)."
        )
    if negative:
        notes.append(
            f"{negative} ingredient(s) are NEGATIVE: the ledger has consumed more than the "
            "last count recorded. Count them."
        )
    if unbatched:
        notes.append(
            f"{unbatched} ingredient(s) hold stock no batch accounts for. FIFO and the "
            "expiry sweep can only see batched stock, so that quantity can never expire "
            "or be counted as waste."
        )
    if expiring is None:
        notes.append(
            "the short-dated value is NOT reported because at least one expiring batch has "
            "no unit cost. A partial total would understate the waste it exists to warn "
            "about (invariant 8)."
        )
    return StockSummary(
        ingredients=len(rows),
        unanchored=unanchored,
        negative=negative,
        short_dated=short_dated,
        unbatched=unbatched,
        auto_order_enabled=auto_on,
        forced_manual=forced,
        expiring_value_pence=as_pence(expiring),
        notes=tuple(notes),
    )


def stock_detail_view(
    session: Session,
    *,
    ingredient_id: int,
    as_of: datetime | None = None,
    history: int = 12,
) -> StockDetail:
    at = as_of or datetime.now(UTC)
    ingredient = SqlIngredientRepository(session).get(ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")

    readings = [
        reading
        for reading in read_on_hand(session, as_of=at, include_untracked=True)
        if reading.ingredient.id == ingredient_id
    ]
    if not readings:
        raise LookupError(f"ingredient {ingredient_id} has no stock reading")

    shelf_lives = shelf_life_specs(session)
    seasons = SqlSeasonRepository(session).seasons_by_ingredient()
    row = _row(
        session,
        reading=readings[0],
        shelf_life=shelf_lives.get(ingredient_id),
        season=seasons.get(ingredient_id),
        at=at,
        knobs=ForecastKnobs.from_settings(),
        stock_repo=SqlStockRepository(session, tz=settings.tz),
        with_run_out=True,
    )

    rows = SqlDriftRepository(session).history(ingredient_id, limit=history)
    templates = SqlCompositionRepository(session).template_ids_using_ingredient(ingredient_id)
    # The latest observation's waste-factor proposal, if it fell in the 10-15% tuning
    # band. Re-evaluated from the STORED observation rather than from today's ledger:
    # the observation is append-only (ARCHITECTURE 2.3) and a proposal recomputed
    # against later movements would not be the one the gate acted on.
    suggested: Decimal | None = None
    if rows:
        observation = rows[0]
        anchor = SqlStockRepository(session, tz=settings.tz).latest_count(
            ingredient_id, before=observation.observed_at - timedelta(seconds=1)
        )
        consumption = SqlDriftRepository(session).consumption_between(
            ingredient_id,
            after=anchor[1] if anchor else None,
            until=observation.observed_at,
        )
        suggested = evaluate_drift(
            ingredient_id=ingredient_id,
            theoretical_qty=observation.theoretical_qty,
            counted_qty=observation.counted_qty,
            observed_at=observation.observed_at,
            current_waste_factor=observation.waste_factor_at_count,
            consumption_since_last_count=consumption or None,
            damping=DEFAULT_WASTE_DAMPING,
        ).suggested_waste_factor

    return StockDetail(
        row=row,
        drift_history=tuple(
            DriftHistoryRowOut(
                observed_at=observation.observed_at,
                theoretical_qty=as_qty(observation.theoretical_qty) or "0",
                counted_qty=as_qty(observation.counted_qty) or "0",
                drift_pct=pct(observation.drift_pct) or 0.0,
                waste_factor_at_count=as_qty(observation.waste_factor_at_count) or "0",
                expired_qty_in_window=as_qty(observation.expired_qty_in_window),
            )
            for observation in rows
        ),
        suggested_waste_factor=as_qty(suggested),
        templates_using=tuple(str(template_id) for template_id in templates),
    )
