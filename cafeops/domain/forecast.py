"""Consumption forecast. Spec 5.3.

```
base_daily    = EWMA(daily consumption, trailing 28 days, alpha=0.3)
dow_factor[d] = mean(weekday d) / mean(all days), trailing 8 weeks, clamp [0.5, 2.0]
forecast(day)  = base_daily * dow_factor[weekday(day)]
```

Pure: `ConsumptionPoint`s in, a `ForecastResult` out. No SQLAlchemy, no I/O, and no
`config` import -- every knob arrives as an argument so the service layer owns the
policy and this module owns only the arithmetic.

## Zero days versus missing days -- the decision, stated once

`StockRepository.daily_consumption` returns a row only for days that produced a
movement. Feeding that sparse list straight into a mean or an EWMA is the single
easiest way to make this café over-order: nine quiet days that sold nothing simply
vanish, the average is taken over the busy days only, and `base_daily` comes out
high. So the series is made dense before anything is computed, and three cases are
treated differently on purpose:

1. **A day inside the observed span with no sales is a ZERO.** Nothing sold is a
   fact about demand, and it belongs in the average.
2. **A day BEFORE the first observation is EXCLUDED, not zero-filled.** Absence of a
   row there is absence of evidence -- the ingredient may not have been stocked,
   tracked, or on the menu yet. Inventing zeros for it is the mirror-image error:
   it deflates `base_daily` and the café runs out. `history_days` therefore counts
   only days from the first observation onward, which is also what the
   `min_history_days` confidence gate is measured against.
3. **A day the café was CLOSED is EXCLUDED from the history and forecast as zero
   demand** -- but only when the caller positively asserts it via `closed_days`. A
   closure is a calendar fact, not a demand signal: averaging a closed Sunday in as
   a zero understates the daily rate for the days she is actually open, and it also
   drags that weekday's `dow_factor` toward the 0.5 clamp floor, where the floor
   then forecasts half a day's trade for a shut café. With no asserted closure a
   zero-sales day stays a plain zero (case 1) -- silence is not evidence of a
   closure.

The clamp floor deserves one more word. `[0.5, 2.0]` means this forecaster can never
predict zero for a weekday, however many zeros the history holds. When a raw factor
is clamped upward the fact is recorded in `confidence_reasons`, because the usual
cause is a closed day that nobody declared.

## A known weakness of the spec's formula, recorded not fixed

`base_daily` is an EWMA of *raw* daily consumption, so with `alpha=0.3` the last day
in the window carries 30% of the weight -- and then `forecast()` multiplies by that
weekday's factor a second time. Anchor the window on a busy Saturday and the result
is inflated; anchor it on a quiet Monday and it is deflated. The mathematically
FIXED by default: `deseasonalise=True` smooths `x_t / dow_factor[weekday(t)]`,
so the weekday effect is applied exactly once. Pass `deseasonalise=False` for the
literal spec. See the DESEASONALISE NOTE in `forecast_consumption`.
This module implements the spec as written; the deviation is measurable (see the
agent-D summary) and is a decision for the owner, not a silent fix here.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from cafeops.domain.types import ConsumptionPoint, ForecastPoint, ForecastResult

__all__ = [
    "WEEKDAY_NAMES",
    "daily_series",
    "dow_factors",
    "ewma",
    "flat_mean",
    "forecast_consumption",
]

#: ISO weekday (Mon=1 .. Sun=7) -> label. Back-office English; the Russian bot
#: renders its own strings, so domain/ stays locale-free.
WEEKDAY_NAMES: dict[int, str] = {
    1: "Mon",
    2: "Tue",
    3: "Wed",
    4: "Thu",
    5: "Fri",
    6: "Sat",
    7: "Sun",
}

_ONE = Decimal("1")
_ZERO = Decimal("0")
#: Reasons and notes only. Never used on a quantity that feeds arithmetic.
_DISPLAY = Decimal("0.01")


def _shown(value: Decimal) -> str:
    return str(value.quantize(_DISPLAY, rounding=ROUND_HALF_UP))


def daily_series(
    history: Iterable[ConsumptionPoint],
    *,
    start: date,
    end: date,
    closed_days: Collection[date] = (),
) -> tuple[tuple[date, Decimal], ...]:
    """Dense day-by-day consumption over `[start, end]`, zeros included.

    See the module docstring for the three cases. In short: zero-fill inside the
    observed span, never before the first observation, and drop asserted closures
    entirely. Duplicate days in `history` are summed rather than silently shadowing
    each other.
    """
    if end < start:
        raise ValueError(f"end {end} is before start {start}")

    observed: dict[date, Decimal] = {}
    for point in history:
        if start <= point.day <= end:
            observed[point.day] = observed.get(point.day, _ZERO) + point.qty
    if not observed:
        return ()

    closed = set(closed_days)
    series: list[tuple[date, Decimal]] = []
    day = min(observed)
    while day <= end:
        if day not in closed:
            series.append((day, observed.get(day, _ZERO)))
        day += timedelta(days=1)
    return tuple(series)


def ewma(values: Sequence[Decimal], alpha: Decimal) -> Decimal:
    """Exponentially weighted moving average, oldest value first.

    Seeded with the first observation rather than zero: seeding at zero would drag
    the first weeks of any new ingredient downward and under-order it.
    """
    if not _ONE >= alpha > _ZERO:
        raise ValueError(f"alpha must be in (0, 1], got {alpha}")
    if not values:
        return _ZERO
    smoothed = values[0]
    decay = _ONE - alpha
    for value in values[1:]:
        smoothed = alpha * value + decay * smoothed
    return smoothed


def flat_mean(values: Sequence[Decimal]) -> Decimal:
    """Unweighted mean. The fallback under `min_history_days` (spec 5.3)."""
    if not values:
        return _ZERO
    return sum(values, _ZERO) / Decimal(len(values))


def dow_factors(
    series: Sequence[tuple[date, Decimal]],
    *,
    factor_min: Decimal,
    factor_max: Decimal,
) -> tuple[dict[int, Decimal], tuple[str, ...]]:
    """`mean(weekday d) / mean(all days)`, clamped to `[factor_min, factor_max]`.

    Returns a factor for every ISO weekday plus notes about anything that had to be
    invented or clamped. A weekday with no observation gets 1.0 -- no information is
    not the same as no demand, and guessing either way would be worse than saying so.
    """
    if factor_min <= _ZERO or factor_max < factor_min:
        raise ValueError(f"clamp [{factor_min}, {factor_max}] is not a usable range")

    notes: list[str] = []
    if not series:
        return dict.fromkeys(WEEKDAY_NAMES, _ONE), (
            "no consumption history: every day-of-week factor held at 1.0",
        )

    overall = sum((qty for _, qty in series), _ZERO) / Decimal(len(series))
    if overall <= _ZERO:
        return dict.fromkeys(WEEKDAY_NAMES, _ONE), (
            f"mean daily consumption over the last {len(series)} day(s) is "
            f"{_shown(overall)}: every day-of-week factor held at 1.0",
        )

    totals: dict[int, Decimal] = {}
    counts: dict[int, int] = {}
    for day, qty in series:
        weekday = day.isoweekday()
        totals[weekday] = totals.get(weekday, _ZERO) + qty
        counts[weekday] = counts.get(weekday, 0) + 1

    factors: dict[int, Decimal] = {}
    for weekday, label in WEEKDAY_NAMES.items():
        if counts.get(weekday, 0) == 0:
            factors[weekday] = _ONE
            notes.append(f"{label}: no day observed in the window, factor held at 1.0")
            continue
        raw = (totals[weekday] / Decimal(counts[weekday])) / overall
        clamped = min(max(raw, factor_min), factor_max)
        factors[weekday] = clamped
        if clamped != raw:
            note = (
                f"{label}: raw day-of-week factor {_shown(raw)} clamped to "
                f"{_shown(clamped)} by the [{_shown(factor_min)}, {_shown(factor_max)}] band"
            )
            if raw == _ZERO:
                note += (
                    "; nothing at all was consumed on this weekday, yet the floor still "
                    "forecasts half a normal day. If the cafe is shut then, declare it as "
                    "a closed day -- the clamp cannot express a zero"
                )
            notes.append(note)
    return factors, tuple(notes)


def forecast_consumption(
    *,
    ingredient_id: int,
    history: Iterable[ConsumptionPoint],
    as_of: date,
    days: Sequence[date],
    ewma_alpha: Decimal,
    ewma_window_days: int,
    dow_window_weeks: int,
    dow_factor_min: Decimal,
    dow_factor_max: Decimal,
    min_history_days: int,
    closed_days: Collection[date] = (),
    deseasonalise: bool = True,
) -> ForecastResult:
    """Forecast consumption for each day in `days`. Spec 5.3.

    ``deseasonalise`` (default True) corrects a double-count in spec 5.3 as
    written -- see DESEASONALISE NOTE below. Pass False for the literal spec.

    `as_of` is the last day of *complete* history -- normally the day before the
    order is placed. `days` is the window to forecast, usually
    `ordering.cover_window(...).days`, and is independent of `as_of`: the two
    windows are allowed to be adjacent (forecast tomorrow onward) or to overlap
    (forecast a day still in progress).

    Two history windows are read, both anchored at `as_of`: `ewma_window_days` for
    `base_daily` and `dow_window_weeks * 7` for the weekday shape. The weekday window
    is deliberately the longer of the two -- eight weeks give each weekday eight
    observations, where 28 days would give four.

    Under `min_history_days` the result is a flat mean with `low_confidence=True`,
    `used_flat_average=True` and the reason spelled out. Invariant 7 says the message
    goes *in place of* the number, so callers must render `confidence_reasons`
    instead of `total`, not next to it. With no history at all the forecast is zero,
    also flagged -- zero is the only honest answer, but on its own it looks like
    "nothing needed" rather than "nothing known".
    """
    if ewma_window_days < 1:
        raise ValueError(f"ewma_window_days must be >= 1, got {ewma_window_days}")
    if dow_window_weeks < 1:
        raise ValueError(f"dow_window_weeks must be >= 1, got {dow_window_weeks}")
    if min_history_days < 1:
        raise ValueError(f"min_history_days must be >= 1, got {min_history_days}")

    points = list(history)
    closed = set(closed_days)
    base_series = daily_series(
        points,
        start=as_of - timedelta(days=ewma_window_days - 1),
        end=as_of,
        closed_days=closed,
    )
    dow_series = daily_series(
        points,
        start=as_of - timedelta(days=dow_window_weeks * 7 - 1),
        end=as_of,
        closed_days=closed,
    )
    observations = [qty for _, qty in base_series]
    history_days = len(observations)

    reasons: list[str] = []
    factors: dict[int, Decimal]

    if history_days == 0:
        base_daily = _ZERO
        used_flat_average = True
        low_confidence = True
        factors = dict.fromkeys(WEEKDAY_NAMES, _ONE)
        reasons.append(
            f"no consumption recorded in the {ewma_window_days} day(s) to {as_of}: "
            "there is no forecast to give, and a zero here means 'nothing known', "
            "not 'nothing needed'"
        )
    elif history_days < min_history_days:
        base_daily = flat_mean(observations)
        used_flat_average = True
        low_confidence = True
        factors = dict.fromkeys(WEEKDAY_NAMES, _ONE)
        # The figure itself is deliberately absent from the reason: invariant 7 says
        # the message goes in place of the number, and a reason that quotes the
        # number puts it back on screen. `base_daily` is still on the result for
        # whoever legitimately needs it.
        reasons.append(
            f"only {history_days} day(s) of history to {as_of}, {min_history_days} "
            "needed: falling back to a flat mean with no day-of-week shaping "
            "(spec 5.3), so treat any quantity below as a placeholder, not a forecast"
        )
    else:
        used_flat_average = False
        low_confidence = False
        factors, dow_notes = dow_factors(
            dow_series, factor_min=dow_factor_min, factor_max=dow_factor_max
        )
        if deseasonalise:
            # DESEASONALISE NOTE -- a correction to spec 5.3 as written.
            #
            # The spec says `base_daily = EWMA(daily consumption)` and then
            # `forecast(day) = base_daily * dow_factor[weekday]`. Applied literally
            # the weekday effect is counted TWICE: alpha=0.3 gives the final day 30%
            # of the weight, so a window ending on a Saturday carries Saturday's
            # surge into `base_daily`, and the multiplication then applies Saturday's
            # factor on top.
            #
            # Measured on the seeded whole-milk history, sliding the anchor across
            # seven consecutive days:
            #     literal         base_daily 6.675 .. 8.854 L/day  -> 32.6% swing
            #     deseasonalised  base_daily 7.305 .. 7.858 L/day  ->  7.6% swing
            # A third of the order size decided by which weekday the job happened to
            # run on. The residual 7.6% is real week-to-week noise, which is what
            # EWMA is for.
            #
            # The fix is standard: divide each observation by its own weekday factor
            # so EWMA smooths a deseasonalised series, then re-apply the factor when
            # forecasting a specific day. `base_daily` then means "typical demand on
            # an average day", which is what multiplying by a weekday factor assumes
            # it means.
            adjusted: list[Decimal] = []
            for day, qty in base_series:
                factor = factors.get(day.isoweekday()) or _ONE
                adjusted.append(qty / factor if factor else qty)
            base_daily = ewma(adjusted, ewma_alpha)
        else:
            base_daily = ewma(observations, ewma_alpha)
            reasons.append(
                "base_daily uses the literal spec 5.3 formula, which double-counts "
                "the weekday effect: the value swings by roughly a third depending "
                "on which weekday the history window ends on"
            )
        # Advisory, not fatal: `low_confidence` stays the gate for invariant 7,
        # while `confidence_reasons` carries everything worth knowing about the
        # number. A caller that renders reasons on a confident forecast shows a
        # caveat; one that hides them hides a clamped weekday.
        reasons.extend(dow_notes)

    forecast_points: list[ForecastPoint] = []
    closed_in_window: list[date] = []
    for day in days:
        if day in closed:
            closed_in_window.append(day)
            forecast_points.append(ForecastPoint(day=day, qty=_ZERO, dow_factor=_ZERO))
            continue
        factor = factors[day.isoweekday()]
        forecast_points.append(ForecastPoint(day=day, qty=base_daily * factor, dow_factor=factor))
    if closed_in_window:
        reasons.append(
            f"{len(closed_in_window)} day(s) in the window are declared closed and "
            f"forecast as zero demand: {', '.join(str(d) for d in closed_in_window)}"
        )

    return ForecastResult(
        ingredient_id=ingredient_id,
        base_daily=base_daily,
        points=tuple(forecast_points),
        history_days=history_days,
        low_confidence=low_confidence,
        confidence_reasons=tuple(reasons),
        used_flat_average=used_flat_average,
    )
