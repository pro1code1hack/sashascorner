"""Build a DRAFT purchase order for one supplier. Spec 5.4.

This is the transaction boundary and the only place the forecast knobs are read from
config: `domain/forecast.py` and `domain/ordering.py` take every knob as an argument
precisely so the policy lives here and the arithmetic lives there.

**DRAFT only.** `create_draft_po` writes `POStatus.DRAFT` and there is no code path
here to anything further. `CONFIRMED`, `SENT` and `RECEIVED` require a recorded human
and are enforced by `ck_po_confirmed_requires_human` in the schema (invariant 1,
`ARCHITECTURE.md` 5). Nothing in this module tries to route around it.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import ParLevel
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.purchase_order import SqlPurchaseOrderRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.db.repositories.supplier import SqlSupplierRepository
from cafeops.domain.forecast import forecast_consumption
from cafeops.domain.ordering import (
    OrderCandidate,
    SizingPlan,
    build_suggestion,
    cover_window,
    target_delivery_date,
)
from cafeops.domain.stock import theoretical_on_hand
from cafeops.domain.types import IngredientSnapshot, PackChoice, ParSpec, SupplierSpec, Tier

__all__ = ["ForecastKnobs", "build_order_plan", "create_draft_po", "par_specs"]


@dataclass(frozen=True, slots=True)
class ForecastKnobs:
    """Spec 5.3's knobs, as Decimals, on their way from config into `domain/`.

    `Settings` holds `ewma_alpha` and the clamp bounds as floats because that is what
    `.env` parses to. They are converted here, through `str`, so no float ever reaches
    a quantity calculation.
    """

    ewma_alpha: Decimal
    ewma_window_days: int
    dow_window_weeks: int
    dow_factor_min: Decimal
    dow_factor_max: Decimal
    min_history_days: int

    @classmethod
    def from_settings(cls) -> ForecastKnobs:
        return cls(
            ewma_alpha=Decimal(str(settings.ewma_alpha)),
            ewma_window_days=settings.ewma_window_days,
            dow_window_weeks=settings.dow_window_weeks,
            dow_factor_min=Decimal(str(settings.dow_factor_min)),
            dow_factor_max=Decimal(str(settings.dow_factor_max)),
            min_history_days=settings.min_history_days,
        )

    @property
    def history_days_needed(self) -> int:
        """Days of history to fetch: the wider of the two windows."""
        return max(self.ewma_window_days, self.dow_window_weeks * 7)


def par_specs(session: Session) -> dict[int, ParSpec]:
    """Par levels by ingredient id.

    Read directly rather than through a `SqlParLevelRepository`: the par level's other
    half is the auto-order gate, which belongs to the drift agent, and two agents
    creating that file is how one contract becomes two.
    """
    return {
        row.ingredient_id: ParSpec(
            ingredient_id=row.ingredient_id,
            safety_days=row.safety_days,
            min_qty=row.min_qty,
            max_qty=row.max_qty,
            auto_order_enabled=row.auto_order_enabled,
        )
        for row in session.scalars(select(ParLevel))
    }


def _day_start(day: date, tz: ZoneInfo) -> datetime:
    """Local midnight as a UTC instant.

    On-hand is read at the start of the order date, so the history that feeds the
    forecast ends the previous day. Local, not UTC: the trading day is the café's day
    (`ARCHITECTURE.md` 6.4).
    """
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def _candidate(
    *,
    ingredient: IngredientSnapshot,
    par: ParSpec,
    pack: PackChoice,
    supplier: SupplierSpec,
    order_date: date,
    knobs: ForecastKnobs,
    stock_repo: SqlStockRepository,
    po_repo: SqlPurchaseOrderRepository,
    closed_days: Collection[date],
    reorder_cadence_days: int | None,
    tz: ZoneInfo,
) -> OrderCandidate:
    cover = cover_window(
        order_date=order_date,
        supplier=supplier,
        safety_days=par.safety_days,
        reorder_cadence_days=reorder_cadence_days,
    )
    history_end = order_date - timedelta(days=1)
    history = stock_repo.daily_consumption(
        ingredient.id,
        since=history_end - timedelta(days=knobs.history_days_needed - 1),
        until=history_end,
    )
    forecast = forecast_consumption(
        ingredient_id=ingredient.id,
        history=history,
        as_of=history_end,
        days=cover.days,
        ewma_alpha=knobs.ewma_alpha,
        ewma_window_days=knobs.ewma_window_days,
        dow_window_weeks=knobs.dow_window_weeks,
        dow_factor_min=knobs.dow_factor_min,
        dow_factor_max=knobs.dow_factor_max,
        min_history_days=knobs.min_history_days,
        closed_days=closed_days,
    )

    at = _day_start(order_date, tz)
    latest_count = stock_repo.latest_count(ingredient.id, before=at)
    movement_sum, movement_count = stock_repo.movement_sum_between(
        ingredient.id, after=latest_count[1] if latest_count else None, until=at
    )
    on_hand = theoretical_on_hand(
        ingredient_id=ingredient.id,
        as_of=at,
        latest_count=latest_count,
        movement_sum=movement_sum,
        movement_count=movement_count,
    )

    return OrderCandidate(
        ingredient_id=ingredient.id,
        ingredient_name=ingredient.name,
        unit=ingredient.unit,
        tier=ingredient.tier,
        par=par,
        pack=pack,
        on_hand_qty=on_hand.qty,
        on_open_pos_qty=po_repo.open_qty_for(ingredient.id),
        cover=cover,
        forecast=forecast,
    )


def build_order_plan(
    session: Session,
    *,
    supplier_id: int,
    order_date: date,
    knobs: ForecastKnobs | None = None,
    tiers: Sequence[Tier] = (Tier.A, Tier.B),
    closed_days: Collection[date] = (),
    reorder_cadence_days: int | None = None,
    require_auto_order: bool = False,
    tz: ZoneInfo | None = None,
) -> SizingPlan:
    """Size an order for one supplier as it would have looked on `order_date`.

    Reads nothing after `order_date`: on-hand is taken at local midnight that day and
    the forecast history ends the day before, so the same call replays a past day
    honestly instead of quietly using hindsight.

    `reorder_cadence_days` is passed straight to `ordering.cover_window` and decides how
    spec 5.4's middle term is read -- `None` for the literal "next slot the supplier
    offers", or the caller's real reordering interval. See that function for why it
    matters: a weekly order sized against a one-day gap under-orders sevenfold.

    `require_auto_order=False` by default because every order this service writes is a
    DRAFT a human must confirm (invariant 1), so `par_level.auto_order_enabled` is not
    what gates it. Pass `True` for the unattended path -- a scheduled job that creates
    drafts with nobody looking -- where invariant 2 does gate it.
    """
    knobs = knobs or ForecastKnobs.from_settings()
    tz = tz or settings.tz

    supplier_repo = SqlSupplierRepository(session)
    supplier = supplier_repo.get(supplier_id)
    if supplier is None:
        raise LookupError(f"supplier {supplier_id} not found")

    ingredient_repo = SqlIngredientRepository(session)
    stock_repo = SqlStockRepository(session, tz=tz)
    po_repo = SqlPurchaseOrderRepository(session)
    pars = par_specs(session)

    candidates: list[OrderCandidate] = []
    no_par: list[str] = []
    not_earned: list[str] = []
    for ingredient in ingredient_repo.list_tracked(tiers=tiers):
        # The supplier check comes first: an ingredient this supplier does not stock is
        # not this order's business, and reporting it as "skipped" would bury the two
        # reasons that matter under forty that do not.
        pack = supplier_repo.preferred_pack(ingredient.id, supplier.id)
        if pack is None:
            continue
        par = pars.get(ingredient.id)
        if par is None:
            no_par.append(ingredient.name)
            continue
        if require_auto_order and not par.auto_order_enabled:
            not_earned.append(ingredient.name)
            continue
        candidates.append(
            _candidate(
                ingredient=ingredient,
                par=par,
                pack=pack,
                supplier=supplier,
                order_date=order_date,
                knobs=knobs,
                stock_repo=stock_repo,
                po_repo=po_repo,
                closed_days=closed_days,
                reorder_cadence_days=reorder_cadence_days,
                tz=tz,
            )
        )

    skipped: list[str] = []
    if no_par:
        skipped.append(
            f"{len(no_par)} tracked ingredient(s) have no par level, so there is no "
            f"min/max to size against and they were not ordered: {', '.join(no_par)}"
        )
    if not_earned:
        skipped.append(
            f"{len(not_earned)} ingredient(s) have not earned auto-ordering (invariant 2 "
            "-- two consecutive counts under 10% drift) and were left for a human: "
            f"{', '.join(not_earned)}"
        )
    if not candidates:
        products = len(supplier_repo.packs_for_supplier(supplier.id))
        skipped.append(
            f"{supplier.name} stocks {products} product(s) but none is a tracked tier "
            "A/B ingredient with a par level, so there is nothing here to calculate. "
            "CakeSmiths is the real case: its 20 products are all tier C cake, which is a "
            "yes/no checklist (spec 4.5), not a forecast."
        )

    target = target_delivery_date(order_date=order_date, supplier=supplier)
    # One OrderSuggestion carries one cover window, but safety_days is per par level.
    # Report the widest; each line's own window is in SizingPlan.outcomes.
    window = (
        max((c.cover for c in candidates), key=lambda w: w.length)
        if candidates
        else cover_window(
            order_date=order_date,
            supplier=supplier,
            safety_days=Decimal("0"),
            reorder_cadence_days=reorder_cadence_days,
        )
    )

    plan = build_suggestion(
        supplier=supplier,
        target_delivery_date=target,
        cover_window=window,
        candidates=candidates,
    )
    if not skipped:
        return plan
    return SizingPlan(
        suggestion=replace(plan.suggestion, notes=(*plan.suggestion.notes, *skipped)),
        outcomes=plan.outcomes,
    )


def create_draft_po(session: Session, plan: SizingPlan) -> int | None:
    """Persist the plan as a DRAFT purchase order. `None` when there is nothing to order.

    No empty orders: an order with no lines is a message ("nothing is needed"), and
    writing it as a purchase order would put a row in front of the owner asking her to
    confirm nothing.
    """
    if not plan.suggestion.lines:
        return None
    return SqlPurchaseOrderRepository(session).create_draft(plan.suggestion)
