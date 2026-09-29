"""Builders shared by every view. The invariant carriers are built here and only here.

`cost_from_*` and `forecast_out` exist so that invariants 8 and 9 are satisfied by
construction in one place rather than by each of six views remembering to. A view that
built a `Cost` by hand could set `pence=0` for an unknown cost; none of them can,
because none of them builds one.

The drift block, the supplier row and the cutoff parser live here for the same reason:
the original views and the `api/areas/` views both render them, and an area view that
imported another module's private helper was a copy waiting to happen.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from cafeops.api.encoding import as_pence, as_qty, pct
from cafeops.api.params import HTTP_422
from cafeops.api.schemas import Cost, DriftAttributionOut, DriftOut, Forecast, SupplierOut
from cafeops.db.models import Supplier
from cafeops.db.repositories.drift import SqlDriftRepository
from cafeops.db.repositories.menu_cost import CachedCost
from cafeops.db.repositories.par import SqlParLevelRepository
from cafeops.domain.drift import DriftExplanation, mean_abs_drift_pct
from cafeops.domain.types import (
    DriftVerdict,
    ForecastResult,
    IngredientSnapshot,
    PriceSource,
    SupplierTerms,
    Unit,
)
from cafeops.services.record_count import explain_drift_history, gate_status
from cafeops.services.suppliers import contact_details

__all__ = [
    "MISSING_COST_NOTE",
    "cost_from_cached",
    "cost_from_ingredient",
    "cost_unknown",
    "drift_attribution_out",
    "drift_out",
    "forecast_out",
    "local_date",
    "parse_cutoff",
    "qty_or_zero",
    "supplier_out",
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


def _trust_status(verdict: DriftVerdict | None, has_observation: bool) -> str | None:
    """Map the gate verdict onto the spec's three presentation words.

    `ELIGIBLE -> trusted`, `TUNE_WASTE_FACTOR -> drifting`, `FORCE_MANUAL -> excluded`.
    Trivial, which is exactly why it belongs in one place: the same badge appears on the
    stock list, the ingredient detail and the digest, and three independent mappings
    will not stay in step.

    None when there is no drift observation -- a genuine fourth state, and calling it
    "trusted" would assert confidence nothing has earned.

    Do NOT label that state "never counted". An ingredient can have a physical count and
    still have no observation: drift needs an ANCHOR plus a later count, so the first
    count of anything produces a basis and no reading. Chocolate powder on the seeded data
    has exactly one count and zero observations. Calling it "never counted" would
    contradict the basis column two cells to its left, which is the specific confusion the
    stock screen exists to prevent (invariant 6). The frontend renders it as
    "not yet judged" / "no evidence either way", which is what it actually is.
    """
    if verdict is None or not has_observation:
        return None
    return {
        DriftVerdict.ELIGIBLE: "trusted",
        DriftVerdict.TUNE_WASTE_FACTOR: "drifting",
        DriftVerdict.FORCE_MANUAL: "excluded",
    }.get(verdict)


def drift_out(session: Session, ingredient: IngredientSnapshot, *, history: int = 6) -> DriftOut:
    """The drift block of a stock row: latest observation, gate, attribution (spec 5.2)."""
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
            attribution = drift_attribution_out(explained[0].explanation)

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
        trust_status=_trust_status(decision.verdict, latest is not None),
        attribution=attribution,
    )


def drift_attribution_out(explanation: DriftExplanation) -> DriftAttributionOut:
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


def supplier_out(
    terms: SupplierTerms, row: Supplier | None = None, product_count: int | None = None
) -> SupplierOut:
    return SupplierOut(
        kind=row.kind if row is not None else None,
        contact=row.contact if row is not None else None,
        order_url=row.order_url if row is not None else None,
        notes=row.notes if row is not None else None,
        email=contact_details(row)["email"] if row is not None else None,
        phone=contact_details(row)["phone"] if row is not None else None,
        archived=row is not None and row.archived_at is not None,
        product_count=product_count,
        supplier_id=terms.supplier_id,
        name=terms.name,
        lead_time_days=terms.lead_time_days,
        delivery_weekdays=tuple(terms.delivery_weekdays),
        min_order_pence=terms.min_order_pence,
        order_channel=terms.order_channel.value,
        cutoff_time=terms.cutoff_time.isoformat() if terms.cutoff_time is not None else None,
        delivery_fee_pence=terms.delivery_fee_pence,
        free_delivery_threshold_pence=terms.free_delivery_threshold_pence,
        terms_are_placeholders=terms.terms_are_placeholders,
    )


def parse_cutoff(raw: str | None) -> time | None:
    if raw is None or raw == "":
        return None
    try:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm))
    except ValueError:
        raise HTTPException(
            status_code=HTTP_422,
            detail={"message": f"cutoff_time must be HH:MM. Got {raw!r}."},
        ) from None
