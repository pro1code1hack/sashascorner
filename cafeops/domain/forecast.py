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

## A bug in the spec's formula, FIXED by default

`base_daily` as spec 5.3 writes it is an EWMA of *raw* daily consumption, so with
`alpha=0.3` the last day in the window carries 30% of the weight -- and then
`forecast()` multiplies by that weekday's factor a second time. Anchor the window on a
busy Saturday and the result is inflated; anchor it on a quiet Monday and it is
deflated, by 32.6% across seven consecutive anchors on the seeded milk history.

`deseasonalise=True` (the default) smooths `x_t / dow_factor[weekday(t)]` so the
weekday effect is applied exactly once, and `base_daily` means what multiplying it by
a weekday factor already assumed it meant. `deseasonalise=False` restores the literal
spec and says in `confidence_reasons` that the value is anchor-sensitive. See the
DESEASONALISE NOTE in `forecast_consumption` and `ARCHITECTURE.md` 8C.

## Seasons (spec 4.3, spec 5.3)

Two separate jobs, and conflating them is how a seasonal line gets ordered wrong in
both directions:

1. **Out-of-season history must not reach the ordinary baseline.** Pass `season=` to
   `forecast_consumption` and every day outside the season is dropped from the history
   and forecast as zero demand. Without it, nine months of zeros make pumpkin syrup
   read as dead stock in October, and three months of October make it look like a
   staple in July.
2. **A seasonal item is forecast from the previous season, not the last 28 days.**
   `seasonal_forecast` reads the same calendar window one year back and scales it by
   the growth measured season-to-date. With no prior season there is nothing to read,
   so it flat-rates the first two weeks and marks itself low-confidence -- invariant 9
   then puts that sentence on screen INSTEAD of the number, which for a first pumpkin
   season is the honest output.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from cafeops.domain.types import ConsumptionPoint, ForecastPoint, ForecastResult, SeasonSpec

__all__ = [
    "SEASON_LOOKBACK_DAYS",
    "WEEKDAY_NAMES",
    "daily_series",
    "dow_factors",
    "ewma",
    "flat_mean",
    "forecast_consumption",
    "occurrence_start",
    "out_of_season_days",
    "seasonal_forecast",
]

#: One year back, measured in whole weeks. 364 rather than 365 so a Saturday is
#: compared with a Saturday: the calendar window is the same to within a day, and the
#: day-of-week shape -- which for a cafe is most of the signal -- survives the
#: comparison instead of being smeared by a one-day rotation.
SEASON_LOOKBACK_DAYS = 364

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


def out_of_season_days(season: SeasonSpec, *, start: date, end: date) -> set[date]:
    """Every day in `[start, end]` that the season does not cover.

    Spec 4.3's first consequence, as a set the history builder can subtract. Computed
    day by day rather than from the season's endpoints because `SeasonSpec.contains`
    already handles a recurring window that wraps the new year, and re-deriving that
    logic here is how the two answers come to disagree.
    """
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    excluded: set[date] = set()
    day = start
    while day <= end:
        if not season.contains(day):
            excluded.add(day)
        day += timedelta(days=1)
    return excluded


def _same_day_in_year(day: date, year: int) -> date:
    """`day` moved to `year`, stepping 29 February back to the 28th.

    A season starting on 29 February has no anniversary in three years out of four.
    Failing there would take out the whole forecast for a calendar artefact.
    """
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def occurrence_start(season: SeasonSpec, at: date) -> date | None:
    """The first day of the occurrence of `season` that contains `at`. None if out.

    For a recurring season this is not simply "this year's start date": a Nov 15 - Feb
    28 winter season that contains 10 January started the PREVIOUS November, and using
    this January's 15 November would measure the season-to-date growth over a window
    that has not happened yet.
    """
    if not season.contains(at):
        return None
    if not season.is_recurring_annually:
        return season.starts_on
    candidate = _same_day_in_year(season.starts_on, at.year)
    if candidate > at:
        candidate = _same_day_in_year(season.starts_on, at.year - 1)
    return candidate


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
    season: SeasonSpec | None = None,
) -> ForecastResult:
    """Forecast consumption for each day in `days`. Spec 5.3.

    `season` excludes out-of-season history from the baseline and forecasts
    out-of-season days as zero (spec 4.3). It is the *non-seasonal* arithmetic applied
    to a seasonal item's in-season days -- use `seasonal_forecast` when the item's own
    history is too short to carry it and last year's season is the better evidence.

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
    season_reasons: list[str] = []
    if season is not None:
        # Out-of-season days are removed the same way a closed day is -- not zero-filled.
        # A zero on a day the item was not on the menu is not evidence about demand, and
        # averaging nine months of them in is how a seasonal line reads as dead stock.
        widest = max(ewma_window_days, dow_window_weeks * 7)
        excluded = out_of_season_days(season, start=as_of - timedelta(days=widest - 1), end=as_of)
        if excluded:
            season_reasons.append(
                f"{len(excluded)} of the last {widest} day(s) fall outside "
                f"{season.name} and were EXCLUDED from the baseline, not counted as "
                "zero demand (spec 4.3): out-of-season silence is not evidence about "
                "in-season demand, in either direction"
            )
        closed = closed | excluded

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

    reasons.extend(season_reasons)

    forecast_points: list[ForecastPoint] = []
    closed_in_window: list[date] = []
    out_of_season_in_window: list[date] = []
    declared_closed = set(closed_days)
    for day in days:
        if season is not None and not season.contains(day):
            out_of_season_in_window.append(day)
            forecast_points.append(ForecastPoint(day=day, qty=_ZERO, dow_factor=_ZERO))
            continue
        if day in declared_closed:
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
    if out_of_season_in_window and season is not None:
        reasons.append(
            f"{len(out_of_season_in_window)} day(s) in the window fall outside "
            f"{season.name} and are forecast as zero demand: "
            f"{out_of_season_in_window[0]}..{out_of_season_in_window[-1]}. Ordering for "
            "them would be buying stock for a drink that is off the menu (spec 4.3)"
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


# ==========================================================================
# Seasonal items: last year's season, scaled (spec 5.3, 4.3)
# ==========================================================================


def _growth_factor(
    by_day: dict[date, Decimal],
    *,
    season_start: date,
    as_of: date,
    lookback_days: int,
    growth_min: Decimal,
    growth_max: Decimal,
    min_growth_days: int,
) -> tuple[Decimal, str]:
    """Season-to-date this year over the same span last year, clamped.

    Returns `(factor, explanation)` and never raises: a missing or zero prior span
    gives 1.0 with a sentence saying the level was carried across unscaled, which is
    the honest fallback -- inventing growth from a division by nothing is worse than
    admitting there is no growth figure.
    """
    current = _span_total(by_day, start=season_start, end=as_of)
    prior_start = season_start - timedelta(days=lookback_days)
    prior_end = as_of - timedelta(days=lookback_days)
    prior = _span_total(by_day, start=prior_start, end=prior_end)
    span = (as_of - season_start).days + 1
    if prior <= _ZERO:
        return _ONE, (
            f"no consumption recorded in the {span} matching day(s) of the previous "
            f"season ({prior_start}..{prior_end}), so last year's level is carried "
            "across unscaled: there is nothing to compute a year-on-year ratio against"
        )
    # The symmetric case, and the dangerous one. With no sales yet THIS season the
    # ratio is 0/prior = 0, which clamps to growth_min and halves the forecast --
    # at precisely the moment you are stocking up for a season that has not started
    # selling. That is a guaranteed stockout every time a season opens.
    #
    # A zero cannot distinguish "the season has not started" from "we dropped this
    # line", so it is not evidence of decline. The same reasoning already applied to
    # a missing PRIOR span applies here: carry last year's level across unscaled and
    # say so, rather than invent a trend from nothing.
    if current <= _ZERO:
        return _ONE, (
            f"no consumption recorded yet in the {span} day(s) of {season_start}'s "
            "season, so last year's level is carried across unscaled: a zero this "
            "early cannot tell a season that has not started from a line that was "
            "dropped, and treating it as a decline would under-order the opening"
        )
    # A ratio off a handful of days is noise before it is a trend, and acting on it
    # moves real money. Below the threshold, report the ratio but do not apply it.
    if span < min_growth_days:
        raw_early = current / prior
        return _ONE, (
            f"only {span} day(s) into {season_start}'s season, fewer than the "
            f"{min_growth_days} needed to read a year-on-year trend: last year's level "
            f"is carried across unscaled (the season-to-date ratio so far is "
            f"{_shown(raw_early)}x, shown but NOT applied)"
        )
    raw = current / prior
    factor = min(max(raw, growth_min), growth_max)
    note = (
        f"year-on-year growth {_shown(factor)}x, from {_shown(current)} in the first "
        f"{span} day(s) of {season_start}'s season against {_shown(prior)} in the same "
        f"days a year earlier"
    )
    if factor != raw:
        note += (
            f" (raw ratio {_shown(raw)} clamped into [{_shown(growth_min)}, "
            f"{_shown(growth_max)}]; a season-to-date ratio off a handful of days is "
            "noise before it is a trend)"
        )
    return factor, note


def _span_total(by_day: dict[date, Decimal], *, start: date, end: date) -> Decimal:
    total = _ZERO
    day = start
    while day <= end:
        total += by_day.get(day, _ZERO)
        day += timedelta(days=1)
    return total


def seasonal_forecast(
    *,
    ingredient_id: int,
    history: Iterable[ConsumptionPoint],
    as_of: date,
    days: Sequence[date],
    season: SeasonSpec,
    flat_rate_days: int = 14,
    lookback_days: int = SEASON_LOOKBACK_DAYS,
    growth_min: Decimal = Decimal("0.5"),
    growth_max: Decimal = Decimal("2"),
    #: Days into the season before a year-on-year ratio is trusted enough to apply.
    min_growth_days: int = 14,
    closed_days: Collection[date] = (),
) -> ForecastResult:
    """Forecast a seasonal item from the previous season, scaled by growth. Spec 5.3.

    ```
    forecast(day) = consumption(day - 364 days) * year_on_year_growth
    ```

    For each in-season day in `days` the matching day of the previous occurrence is
    read straight out of the history and scaled. Not an average of last season: a
    pumpkin season ramps, peaks at half term and falls away, and the shape is most of
    what is worth knowing about it. An EWMA of the last 28 days cannot see any of that,
    which is why spec 5.3 gives seasonal items their own path rather than a factor.

    Out-of-season days in `days` are zero, for the same reason as in
    `forecast_consumption`: no menu, no demand.

    **With no prior season** there is nothing to read, and the spec's answer is a flat
    rate from the first two weeks marked low-confidence. That is implemented literally
    and it is the common case in the first year of trading -- so `low_confidence` is
    set, `used_flat_average` is set, and invariant 9 requires the caller to render
    `confidence_reasons` INSTEAD of the quantity. A seasonal quantity extrapolated from
    nine days of a brand-new season is a guess, and it must not be shown as anything
    else.

    `base_daily` is the mean of the forecast days, so a caller that wants a daily rate
    for a cover calculation still has one. It is a *result* here rather than an input,
    which is the opposite of the non-seasonal path.
    """
    if flat_rate_days < 1:
        raise ValueError(f"flat_rate_days must be >= 1, got {flat_rate_days}")
    if lookback_days < 1:
        raise ValueError(f"lookback_days must be >= 1, got {lookback_days}")
    if growth_min <= _ZERO or growth_max < growth_min:
        raise ValueError(f"growth clamp [{growth_min}, {growth_max}] is not a usable range")

    closed = set(closed_days)
    by_day: dict[date, Decimal] = {}
    for point in history:
        by_day[point.day] = by_day.get(point.day, _ZERO) + point.qty

    reasons: list[str] = []
    in_season_days = [d for d in days if season.contains(d) and d not in closed]
    out_days = [d for d in days if not season.contains(d)]

    prior_days = {d: d - timedelta(days=lookback_days) for d in in_season_days}
    prior_observed = [d for d in prior_days.values() if d in by_day]
    start = occurrence_start(season, as_of)

    forecast_points: list[ForecastPoint] = []
    low_confidence = False
    used_flat_average = False

    if prior_observed and start is not None:
        factor, growth_note = _growth_factor(
            by_day,
            season_start=start,
            as_of=as_of,
            lookback_days=lookback_days,
            growth_min=growth_min,
            growth_max=growth_max,
            min_growth_days=min_growth_days,
        )
        reasons.append(
            f"seasonal forecast from {season.name} one year back "
            f"({lookback_days} days = {lookback_days // 7} weeks, so weekdays line up), "
            f"{len(prior_observed)} of {len(in_season_days)} day(s) matched"
        )
        reasons.append(growth_note)
        missing = [d for d, prior in prior_days.items() if prior not in by_day]
        if missing:
            # A gap in last season's ledger is not zero demand: it is a day nobody
            # recorded. Filling it with zero would under-order the same day this year.
            in_season_mean = _span_total(by_day, start=start, end=as_of) / Decimal(
                max((as_of - start).days + 1, 1)
            )
            reasons.append(
                f"{len(missing)} day(s) of last season have no record and were filled "
                f"with this season's running mean rather than zero: an unrecorded day is "
                "not a day nothing sold, and zero-filling it would under-order the same "
                "day this year"
            )
        else:
            in_season_mean = _ZERO
        for day in days:
            if day in out_days or day in closed:
                forecast_points.append(ForecastPoint(day=day, qty=_ZERO, dow_factor=_ZERO))
                continue
            prior = prior_days[day]
            raw = by_day.get(prior)
            qty = (raw * factor) if raw is not None else in_season_mean
            forecast_points.append(ForecastPoint(day=day, qty=qty, dow_factor=factor))
    else:
        # No prior season. Spec 5.3: flat-rate the first two weeks, low confidence.
        low_confidence = True
        used_flat_average = True
        window_start = start if start is not None else as_of - timedelta(days=flat_rate_days - 1)
        window_end = min(window_start + timedelta(days=flat_rate_days - 1), as_of)
        observed = [
            by_day.get(window_start + timedelta(days=offset), _ZERO)
            for offset in range((window_end - window_start).days + 1)
            if (window_start + timedelta(days=offset)) not in closed
        ]
        rate = flat_mean(observed) if observed else _ZERO
        reasons.append(
            f"{season.name} has no previous occurrence in the history, so there is no "
            f"season to scale: this is a FLAT RATE from the first {len(observed)} day(s) "
            f"of the current season ({window_start}..{window_end}), spec 5.3's "
            "no-prior-season fallback. It carries no weekday shape and no ramp, and the "
            "first weeks of a season are its least representative -- treat the quantity "
            "as a placeholder and show this sentence instead of it (invariant 9)"
        )
        if not observed:
            reasons.append(
                f"the season has not started yet as of {as_of}, so even the flat rate "
                "has nothing behind it: this forecast is zero because nothing is known, "
                "not because nothing is needed"
            )
        for day in days:
            qty = _ZERO if (day in out_days or day in closed) else rate
            forecast_points.append(ForecastPoint(day=day, qty=qty, dow_factor=_ONE))

    if out_days:
        reasons.append(
            f"{len(out_days)} day(s) of the window fall outside {season.name} and are "
            f"forecast as zero demand: {out_days[0]}..{out_days[-1]}"
        )
    counted = [p.qty for p in forecast_points]
    base_daily = flat_mean(counted)
    return ForecastResult(
        ingredient_id=ingredient_id,
        base_daily=base_daily,
        points=tuple(forecast_points),
        history_days=len(by_day),
        low_confidence=low_confidence,
        confidence_reasons=tuple(reasons),
        used_flat_average=used_flat_average,
    )
