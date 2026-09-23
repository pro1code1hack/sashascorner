"""Builders shared by every view. The invariant carriers are built here and only here.

`cost_from_*` and `forecast_out` exist so that invariants 8 and 9 are satisfied by
construction in one place rather than by each of six views remembering to. A view that
built a `Cost` by hand could set `pence=0` for an unknown cost; none of them can,
because none of them builds one.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from cafeops.api.encoding import as_pence, as_qty
from cafeops.api.schemas import Cost, Forecast
from cafeops.db.repositories.menu_cost import CachedCost
from cafeops.domain.types import ForecastResult, IngredientSnapshot, PriceSource, Unit

__all__ = [
    "MISSING_COST_NOTE",
    "cost_from_cached",
    "cost_from_ingredient",
    "cost_unknown",
    "forecast_out",
    "local_date",
    "qty_or_zero",
]

MISSING_COST_NOTE = (
    "cost UNKNOWN, not zero (invariant 8). This item is returned and flagged rather "
    "than hidden, and it is excluded from every total on this response."
)


def cost_from_cached(row: CachedCost, *, excluded_from_aggregates: bool = False) -> Cost:
    """A `Cost` from the materialised cost cache.

    `cost_source` is the WEAKEST source among the item's ingredients: one estimated
    ingredient makes the whole item an estimate (ARCHITECTURE 7.3). `has_missing_cost`
    and `cost_pence is None` are both carried because they are different facts -- an
    item can have a priced majority and one unpriced line, and `cost_pence` is then
    None while `has_missing_cost` explains why.
    """
    missing = row.has_missing_cost or row.cost_pence is None
    return Cost(
        pence=as_pence(row.cost_pence),
        source=row.cost_source.value if row.cost_source is not None else None,
        is_estimate=row.cost_source is PriceSource.ESTIMATE,
        is_missing=missing,
        excluded_from_aggregates=excluded_from_aggregates or missing,
        note=MISSING_COST_NOTE if missing else None,
    )


def cost_from_ingredient(snapshot: IngredientSnapshot) -> Cost:
    """The cached unit cost of one ingredient."""
    missing = snapshot.cost_per_unit_pence is None
    return Cost(
        pence=as_pence(snapshot.cost_per_unit_pence),
        source=snapshot.cost_source.value if snapshot.cost_source is not None else None,
        is_estimate=snapshot.cost_source is PriceSource.ESTIMATE,
        is_missing=missing,
        excluded_from_aggregates=missing,
        note=MISSING_COST_NOTE if missing else None,
    )


def cost_unknown(reason: str) -> Cost:
    """No cost at all. Used for a menu item with no cache row: null, never zero."""
    return Cost(
        pence=None,
        source=None,
        is_estimate=False,
        is_missing=True,
        excluded_from_aggregates=True,
        note=reason,
    )


def forecast_out(
    result: ForecastResult,
    *,
    unit: Unit,
    window_days: int,
    total: Decimal | None = None,
) -> Forecast:
    """A forecast with the number WITHHELD when confidence is low. Invariant 9.

    The figure is left out of the payload rather than flagged beside it, so a frontend
    physically cannot render it next to a warning. `domain/ordering.forecast_text`
    takes the same line for the same reason.

    `total` overrides `result.total` for the case where the window reported is NOT the
    window the forecast was computed over -- a shelf-life-capped order line is sized on
    the shorter window, and labelling the full-window sum with the capped day count would
    be a figure that matches neither (`OrderCandidate.forecast_qty` is the capped sum).
    """
    low = result.low_confidence
    figure = result.total if total is None else total
    return Forecast(
        qty=None if low else as_qty(figure),
        unit=unit.value,
        window_days=window_days,
        is_low_confidence=low,
        reasons=tuple(result.confidence_reasons),
        used_flat_average=result.used_flat_average,
        history_days=result.history_days,
    )


def local_date(at: datetime, tz: ZoneInfo) -> date:
    """`at` as a local calendar date. One helper so no view invents its own.

    ARCHITECTURE 6.4: a café's day is the unit a human means by "today", so anything
    bucketed by day is bucketed locally, never by UTC midnight.
    """
    return at.astimezone(tz).date()


def qty_or_zero(value: Decimal | None) -> str:
    return as_qty(value if value is not None else Decimal("0")) or "0"
