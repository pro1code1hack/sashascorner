"""Build DRAFT purchase orders: one per supplier, plus what cannot wait. Spec 5.4, 5.5.

This is the transaction boundary and the only place the forecast knobs are read from
config: `domain/forecast.py`, `domain/ordering.py` and `domain/sourcing.py` take every
knob as an argument precisely so the policy lives here and the arithmetic lives there.

Three things assembled here decide whether the quantities are honest, and all three are
reads this module owns because nothing else can do them:

* **Shelf life** (`shelf_life_specs`) caps the cover window (invariant 4) and decides
  what may never be a top-up (invariant 5).
* **Seasons** (`SqlSeasonRepository`) cap it again and keep out-of-season history out of
  the baseline (spec 4.3).
* **Supplier terms** (`SqlSourcingRepository`) carry the cutoff, the delivery fee, the
  free-delivery threshold and -- for six of eight suppliers -- the fact that all of it
  is invented.

**DRAFT only.** `create_draft_po` writes `POStatus.DRAFT` and there is no code path
here to anything further. `CONFIRMED`, `SENT` and `RECEIVED` require a recorded human
and are enforced by `ck_po_confirmed_requires_human` in the schema (invariant 1,
`ARCHITECTURE.md` 5). Nothing in this module tries to route around it.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import Ingredient, ParLevel
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.purchase_order import SqlPurchaseOrderRepository
from cafeops.db.repositories.season import SqlSeasonRepository
from cafeops.db.repositories.sourcing import SqlSourcingRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.db.repositories.supplier import SqlSupplierRepository
from cafeops.domain.forecast import forecast_consumption, seasonal_forecast
from cafeops.domain.ordering import (
    CoverPlan,
    OrderCandidate,
    SizingPlan,
    build_suggestion,
    cover_plan,
    pounds,
)
from cafeops.domain.sourcing import (
    DEFAULT_POLICY,
    EmergencyPlan,
    EmergencyRequest,
    SourcingPolicy,
    SourcingRequest,
    choose_sources,
    route_to_retail,
)
from cafeops.domain.stock import theoretical_on_hand
from cafeops.domain.types import (
    ForecastResult,
    IngredientSnapshot,
    PackChoice,
    ParSpec,
    SeasonSpec,
    ShelfLifeSpec,
    SourcingOption,
    SupplierSpec,
    SupplierSplit,
    SupplierTerms,
    Tier,
)

__all__ = [
    "RETAIL_SUPPLIER_NAME",
    "ForecastKnobs",
    "build_order_plan",
    "build_split",
    "create_draft_po",
    "par_specs",
    "record_emergency_lines",
    "shelf_life_specs",
]

#: The walk-in supplier an emergency routes to. A name rather than an id because it is
#: the same fact in the seed, the CLI and the report, and spec 4.4 names it.
RETAIL_SUPPLIER_NAME = "Tesco"


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
    #: How far back the seasonal path reads. It must span the previous occurrence of the
    #: season plus the window being forecast, so it is a year and a bit rather than 28
    #: days -- 400 days, which covers a 364-day lookback plus a five-week cover window.
    seasonal_history_days: int = 400

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


def shelf_life_specs(session: Session) -> dict[int, ShelfLifeSpec]:
    """Shelf life by ingredient id -- the input to invariants 4 and 5.

    Read straight off `ingredient` for the same reason `par_specs` is: the protocol puts
    `shelf_life` on `BatchRepository`, which belongs to the batches agent and does not
    exist yet, and two agents creating that file is how one contract becomes two. When
    `db/repositories/batch.py` lands this becomes `BatchRepository.shelf_life` in bulk
    and the rest of this module does not change.

    A row is returned for EVERY tracked ingredient, including those with
    `shelf_life_days IS NULL`. That null is a statement -- cups and napkins do not expire
    -- and the difference between "recorded as non-perishable" and "never recorded"
    matters twice over: the second is barred from top-ups as well (`ARCHITECTURE.md`
    8F.1), and it can only be seen if the first is present.
    """
    return {
        row.id: ShelfLifeSpec(
            ingredient_id=row.id,
            storage=row.storage,
            shelf_life_days=row.shelf_life_days,
            open_life_days=row.open_life_days,
            transit_buffer_days=row.transit_buffer_days,
            source=row.shelf_life_source,
        )
        for row in session.scalars(select(Ingredient))
    }


def _day_start(day: date, tz: ZoneInfo) -> datetime:
    """Local midnight as a UTC instant.

    On-hand is read at the start of the order date, so the history that feeds the
    forecast ends the previous day. Local, not UTC: the trading day is the café's day
    (`ARCHITECTURE.md` 6.4).
    """
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def _forecast_for(
    *,
    ingredient: IngredientSnapshot,
    season: SeasonSpec | None,
    days: Sequence[date],
    history_end: date,
    knobs: ForecastKnobs,
    stock_repo: SqlStockRepository,
    closed_days: Collection[date],
) -> ForecastResult:
    """Choose spec 5.3's ordinary path or its seasonal one, and say which.

    A seasonal ingredient goes down the seasonal path **always**, even when the current
    season has history of its own: spec 5.3 says a seasonal item is forecast from the
    previous season's same calendar window, and a season is a shape rather than a level.
    The history window is therefore a year longer than the EWMA needs, because the
    seasonal path has to reach back to the previous occurrence to find anything.

    With no previous occurrence -- the first year of trading, which is every seasonal
    item here -- the seasonal path flat-rates the first two weeks of the season and marks
    itself low-confidence. That is the spec's own fallback, and it is worth knowing that
    it reads the *start* of the season: two months into a season, a flat rate off its
    opening fortnight is stale. It is still better than an EWMA that has been fed nine
    months of out-of-season zeros, and invariant 9 means the sentence is shown rather
    than the number either way.
    """
    if season is None:
        history = stock_repo.daily_consumption(
            ingredient.id,
            since=history_end - timedelta(days=knobs.history_days_needed - 1),
            until=history_end,
        )
        return forecast_consumption(
            ingredient_id=ingredient.id,
            history=history,
            as_of=history_end,
            days=days,
            ewma_alpha=knobs.ewma_alpha,
            ewma_window_days=knobs.ewma_window_days,
            dow_window_weeks=knobs.dow_window_weeks,
            dow_factor_min=knobs.dow_factor_min,
            dow_factor_max=knobs.dow_factor_max,
            min_history_days=knobs.min_history_days,
            closed_days=closed_days,
        )
    history = stock_repo.daily_consumption(
        ingredient.id,
        since=history_end - timedelta(days=knobs.seasonal_history_days - 1),
        until=history_end,
    )
    return seasonal_forecast(
        ingredient_id=ingredient.id,
        history=history,
        as_of=history_end,
        days=days,
        season=season,
        closed_days=closed_days,
    )


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
    shelf_life: ShelfLifeSpec | None = None,
    season: SeasonSpec | None = None,
    order_time: time | None = None,
    cutoff_time: time | None = None,
) -> OrderCandidate:
    cover = cover_plan(
        order_date=order_date,
        supplier=supplier,
        safety_days=par.safety_days,
        reorder_cadence_days=reorder_cadence_days,
        order_time=order_time,
        cutoff_time=cutoff_time,
    ).window
    history_end = order_date - timedelta(days=1)
    forecast = _forecast_for(
        ingredient=ingredient,
        season=season,
        days=cover.days,
        history_end=history_end,
        knobs=knobs,
        stock_repo=stock_repo,
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
        shelf_life=shelf_life,
        season=season,
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
    order_time: time | None = None,
    respect_cutoff: bool = True,
) -> SizingPlan:
    """Size an order for one supplier as it would have looked on `order_date`.

    Reads nothing after `order_date`: on-hand is taken at local midnight that day and
    the forecast history ends the day before, so the same call replays a past day
    honestly instead of quietly using hindsight.

    `reorder_cadence_days` is passed straight to `ordering.cover_window` and decides how
    spec 5.4's middle term is read -- `None` for the literal "next slot the supplier
    offers", or the caller's real reordering interval. See that function for why it
    matters: a weekly order sized against a one-day gap under-orders sevenfold.

    `order_time` is the LOCAL time of day the order is placed, and with `respect_cutoff`
    it decides whether the supplier's cutoff was made. Pass it for a real run -- a job
    firing at 17:00 has missed Booker's noon cutoff and the cover window must know. Leave
    it out and no cutoff is applied, which is the right default for a replay: the seeded
    history records no time of day for a hypothetical order, and inventing one would
    change quantities on a guess.

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

    sourcing_repo = SqlSourcingRepository(session)
    terms = sourcing_repo.terms(supplier_id)
    cutoff = terms.cutoff_time if (terms is not None and respect_cutoff) else None
    ingredient_repo = SqlIngredientRepository(session)
    stock_repo = SqlStockRepository(session, tz=tz)
    po_repo = SqlPurchaseOrderRepository(session)
    pars = par_specs(session)
    shelf_lives = shelf_life_specs(session)
    seasons = SqlSeasonRepository(session).seasons_by_ingredient()

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
                shelf_life=shelf_lives.get(ingredient.id),
                season=seasons.get(ingredient.id),
                order_time=order_time,
                cutoff_time=cutoff,
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
        if products == 0:
            skipped.append(
                f"{supplier.name} has no supplier products on file at all, so nothing can "
                "be ordered from it. For Nataly that is spec 15 question 5 still "
                "unanswered -- what this supplier covers, and through what channel, is "
                "unknown (ARCHITECTURE.md 8F.4)."
            )
        else:
            skipped.append(
                f"{supplier.name} stocks {products} product(s) but none is a tracked tier "
                "A/B ingredient with a par level, so there is nothing here to calculate. "
                "Cakesmiths is the real case: its 20 products are all tier C cake, which is "
                "a yes/no checklist (spec 4.5), not a forecast."
            )

    plan_for_supplier: CoverPlan = cover_plan(
        order_date=order_date,
        supplier=supplier,
        safety_days=Decimal("0"),
        reorder_cadence_days=reorder_cadence_days,
        order_time=order_time,
        cutoff_time=cutoff,
    )
    # One OrderSuggestion carries one cover window, but safety_days is per par level and
    # the shelf-life cap is per ingredient. Report the widest; each line carries its own
    # effective `cover_days` and each candidate's window is in SizingPlan.outcomes.
    window = (
        max((c.cover for c in candidates), key=lambda w: w.length)
        if candidates
        else plan_for_supplier.window
    )

    plan = build_suggestion(
        supplier=supplier,
        target_delivery_date=plan_for_supplier.target_delivery_date,
        cover_window=window,
        candidates=candidates,
        terms=terms,
        extra_notes=(*plan_for_supplier.notes, *skipped),
    )
    return plan


def create_draft_po(
    session: Session, plan: SizingPlan, *, routing_reason: str | None = None
) -> int | None:
    """Persist the plan as a DRAFT purchase order. `None` when there is nothing to order.

    No empty orders: an order with no lines is a message ("nothing is needed"), and
    writing it as a purchase order would put a row in front of the owner asking her to
    confirm nothing.

    The delivery fee is read from the supplier's terms and charged only when the order
    misses the free-delivery threshold -- the same decision `build_suggestion` explained
    in the notes, recorded as money so the order total is what will actually be paid.
    """
    suggestion = plan.suggestion
    if not suggestion.lines:
        return None
    terms = SqlSourcingRepository(session).terms(suggestion.supplier.id)
    fee = 0
    if terms is not None and terms.delivery_fee_pence > 0:
        threshold = terms.free_delivery_threshold_pence
        if threshold is None or suggestion.total_pence < threshold:
            fee = terms.delivery_fee_pence
    return SqlPurchaseOrderRepository(session).create_draft(
        suggestion, routing_reason=routing_reason, delivery_fee_pence=fee
    )


# ==========================================================================
# The whole run: N orders, one per supplier, plus what cannot wait (spec 5.5)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class SplitResult:
    """`SupplierSplit` plus the working behind it.

    `SupplierSplit` is the integrator-owned contract and carries the suggestions, the
    sourcing choices, the emergency lines and the notes. It has nowhere to put the
    per-candidate arithmetic, and with no test suite that arithmetic IS the proof -- so
    `plans` travels alongside it for `cafeops simulate` to print. Nothing downstream
    should depend on this wrapper; depend on `split`.
    """

    split: SupplierSplit
    plans: tuple[SizingPlan, ...] = ()
    emergency: EmergencyPlan = field(default_factory=EmergencyPlan)
    #: ingredient id -> the supplier that would have supplied it, for the routing log.
    would_be_supplier: dict[int, int] = field(default_factory=dict)


def _sourcing_requests(
    plans: Sequence[SizingPlan],
    *,
    options: dict[int, list[SourcingOption]],
    shelf_lives: dict[int, ShelfLifeSpec],
) -> list[SourcingRequest]:
    """One request per ingredient actually being bought.

    Only ordered lines: sourcing is a question about a purchase, and asking it of the
    forty candidates that need nothing produces forty answers nobody can read. Top-up
    lines are included -- they are still money leaving the account.
    """
    requests: dict[int, SourcingRequest] = {}
    for plan in plans:
        for outcome in plan.ordered:
            line = outcome.line
            if line is None or line.ingredient_id in requests:
                continue
            candidate = outcome.candidate
            available = options.get(line.ingredient_id, [])
            if len(available) < 2:
                # Nothing to choose between. A single-source ingredient still gets a
                # SourcingChoice from `choose_source`, but building the request is only
                # worth it where a decision exists.
                continue
            shelf = shelf_lives.get(line.ingredient_id)
            pack_qty = candidate.pack_qty()
            requests[line.ingredient_id] = SourcingRequest(
                ingredient_id=line.ingredient_id,
                ingredient_name=line.ingredient_name,
                unit=line.unit,
                options=tuple(available),
                # What is being bought, not what was forecast: the comparison that
                # matters is between two till receipts for the same purchase.
                need_qty=Decimal(line.packs) * pack_qty,
                daily_rate=candidate.forecast.base_daily,
                usable_days=shelf.usable_days if shelf is not None else None,
            )
    return list(requests.values())


def _emergency_requests(
    plans: Sequence[SizingPlan],
    *,
    order_date: date,
    options: dict[int, list[SourcingOption]],
    retail_supplier_id: int | None,
    terms: dict[int, SupplierTerms],
) -> tuple[list[EmergencyRequest], dict[int, int]]:
    """Who might not last until their delivery, and what Tesco could bridge it with.

    The gap measured is `order_date` to the **target delivery date** of the order being
    placed now -- not to the end of the cover window. The scheduled order is still
    coming; the only question an emergency answers is whether the shelf survives until
    it arrives.
    """
    requests: list[EmergencyRequest] = []
    would_be: dict[int, int] = {}
    seen: set[int] = set()
    for plan in plans:
        suggestion = plan.suggestion
        days_until = max((suggestion.target_delivery_date - order_date).days, 0)
        if days_until <= 0:
            # A walk-in supplier delivers today; there is nothing to bridge.
            continue
        for outcome in plan.outcomes:
            candidate = outcome.candidate
            if candidate.ingredient_id in seen:
                continue
            seen.add(candidate.ingredient_id)
            available = options.get(candidate.ingredient_id, [])
            retail = next(
                (o for o in available if o.supplier_id == retail_supplier_id),
                None,
            )
            preferred = next(
                (
                    o
                    for o in available
                    if o.supplier_product_id == candidate.pack.supplier_product_id
                ),
                None,
            )
            would_be[candidate.ingredient_id] = suggestion.supplier.id
            requests.append(
                EmergencyRequest(
                    ingredient_id=candidate.ingredient_id,
                    ingredient_name=candidate.ingredient_name,
                    unit=candidate.unit,
                    available_qty=candidate.available_qty,
                    daily_rate=candidate.forecast.base_daily,
                    days_until_delivery=days_until,
                    preferred=preferred,
                    retail=retail,
                    low_confidence=candidate.forecast.low_confidence,
                    supplier_name=terms[suggestion.supplier.id].name
                    if suggestion.supplier.id in terms
                    else suggestion.supplier.name,
                )
            )
    return requests, would_be


def build_split(
    session: Session,
    *,
    order_date: date,
    knobs: ForecastKnobs | None = None,
    tiers: Sequence[Tier] = (Tier.A, Tier.B),
    closed_days: Collection[date] = (),
    reorder_cadence_days: int | None = None,
    require_auto_order: bool = False,
    tz: ZoneInfo | None = None,
    order_time: time | None = None,
    respect_cutoff: bool = True,
    supplier_ids: Sequence[int] | None = None,
    retail_supplier_name: str = RETAIL_SUPPLIER_NAME,
    policy: SourcingPolicy = DEFAULT_POLICY,
    min_order_pence: int | None = None,
) -> SplitResult:
    """One ordering run: N draft orders, the sourcing choices, and the Tesco lines.

    Spec 5.5's shape exactly -- "a set of orders each with a one-line rationale, not one
    basket".

    **Two passes, because sizing and sourcing need each other.** Pass one sizes every
    supplier independently, which is what produces the per-supplier subtotals the
    minimum-order test in spec 4.4 needs. Pass two applies the sourcing decision: an
    ingredient appears only in the order of the supplier that won it.

    The second pass is not a refinement, it is a correctness fix. Sizing every supplier
    independently orders the same ingredient from all of them -- the seeded data has
    12oz cups at both Cups Direct and Amazon, and a single pass bought them twice, £25
    of cups nobody needed and no line in the output admitting it. The sourcing decision
    is what makes "one order per supplier" add up to one purchase per ingredient.

    `min_order_pence` overrides every supplier's minimum, for the what-if that is the
    only honest way to exercise the top-up when six of the stored minimums are invented.
    """
    knobs = knobs or ForecastKnobs.from_settings()
    tz = tz or settings.tz
    sourcing_repo = SqlSourcingRepository(session)
    supplier_repo = SqlSupplierRepository(session)
    terms_by_id = sourcing_repo.terms_by_id()
    retail_id = next(
        (t.supplier_id for t in terms_by_id.values() if t.name == retail_supplier_name), None
    )
    shelf_lives = shelf_life_specs(session)

    # The retail supplier IS sized, unlike an emergency-only reading of spec 4.4 would
    # suggest -- but only so sourcing can consider it. It keeps a line in pass two only
    # where it actually won one on price, which for this cafe is a real answer: the owner
    # already walks to a shop most mornings (spec 1), and Tesco's terms are among the two
    # that are not invented.
    wanted = (
        [s for s in supplier_repo.list_all() if s.id in set(supplier_ids)]
        if supplier_ids is not None
        else supplier_repo.list_all()
    )

    plans: list[SizingPlan] = []
    for supplier in wanted:
        plan = build_order_plan(
            session,
            supplier_id=supplier.id,
            order_date=order_date,
            knobs=knobs,
            tiers=tiers,
            closed_days=closed_days,
            reorder_cadence_days=reorder_cadence_days,
            require_auto_order=require_auto_order,
            tz=tz,
            order_time=order_time,
            respect_cutoff=respect_cutoff,
        )
        if min_order_pence is not None:
            plan = _resize_with_minimum(plan, min_order_pence=min_order_pence)
        plans.append(plan)

    ingredient_ids = sorted({o.candidate.ingredient_id for plan in plans for o in plan.outcomes})
    options = sourcing_repo.options_for_many(ingredient_ids)
    baskets = {plan.suggestion.supplier.id: plan.suggestion.total_pence for plan in plans}

    choices = choose_sources(
        _sourcing_requests(plans, options=options, shelf_lives=shelf_lives),
        policy=policy,
        terms=terms_by_id,
        supplier_baskets=baskets,
    )
    plans = _apply_sourcing(
        plans,
        choices=choices,
        terms=terms_by_id,
        min_order_pence=min_order_pence,
    )
    emergency_requests, would_be = _emergency_requests(
        plans,
        order_date=order_date,
        options=options,
        retail_supplier_id=retail_id,
        terms=terms_by_id,
    )
    emergency = route_to_retail(emergency_requests, retail_supplier_name=retail_supplier_name)

    notes: list[str] = []
    placeholders = sourcing_repo.placeholder_suppliers()
    if placeholders:
        notes.append(
            f"{len(placeholders)} of {len(terms_by_id)} suppliers' terms are INVENTED "
            f"PLACEHOLDERS: {', '.join(placeholders)}. Lead time, delivery days, cutoff, "
            "minimum order and free-delivery threshold were never confirmed with any of "
            "them (ARCHITECTURE.md 8F.4), and every cover window below is built on them. "
            f"Only {retail_supplier_name} and Amazon have terms anybody has checked."
        )
    forgone = [c for c in choices if c.forgone_saving_pence is not None]
    if forgone:
        total = sum((c.forgone_saving_pence or Decimal("0")) for c in forgone)
        notes.append(
            f"SOURCING: {len(forgone)} line(s) kept a dearer supplier on purpose, forgoing "
            f"{pounds(int(total))} in total. Each reason is on its choice -- the saving was "
            "either too small to be worth a second invoice, or taking it would have pushed "
            "another supplier's order below its minimum. Spec 4.4 says surface this "
            "trade-off, so here it is: it is a decision, not an oversight."
        )
    switched = [c for c in choices if not c.chosen.is_preferred]
    if switched:
        notes.append(
            f"SOURCING: {len(switched)} line(s) moved away from the preferred supplier: "
            + "; ".join(f"{c.reason}" for c in switched)
        )
    notes.extend(emergency.notes)
    premium = emergency.total_premium_pence
    if emergency.lines and premium is not None:
        notes.append(
            f"EMERGENCY PREMIUM this run: {pounds(int(premium))} paid over what the "
            "scheduled suppliers charge for the same goods. That figure is the argument "
            "for fixing the ordering cadence (spec 4.4), and it is logged so it can be "
            "summed over a quarter rather than forgotten each week."
        )

    split = SupplierSplit(
        suggestions=tuple(plan.suggestion for plan in plans),
        emergency=emergency.lines,
        choices=choices,
        notes=tuple(notes),
    )
    return SplitResult(
        split=split,
        plans=tuple(plans),
        emergency=emergency,
        would_be_supplier=would_be,
    )


def _apply_sourcing(
    plans: Sequence[SizingPlan],
    *,
    choices: Sequence[Any],
    terms: dict[int, SupplierTerms],
    min_order_pence: int | None,
) -> list[SizingPlan]:
    """Pass two: each ingredient stays only in the order of the supplier that won it.

    Re-sized rather than filtered, because dropping a line changes the order's subtotal
    and therefore both top-up decisions -- a supplier that loses two lines to a cheaper
    source may now be under its minimum, which is spec 5.5's "re-source or defer a group
    short of its minimum" arriving as a consequence rather than a special case.

    The losing supplier's order says what left it and where it went. An order that
    silently shrinks between one run and the next is indistinguishable from a forecast
    that changed its mind.
    """
    chosen_by_ingredient = {c.ingredient_id: c.chosen.supplier_id for c in choices}
    reason_by_ingredient = {c.ingredient_id: c.reason for c in choices}
    resized: list[SizingPlan] = []
    for plan in plans:
        supplier = plan.suggestion.supplier
        keep: list[OrderCandidate] = []
        moved: list[str] = []
        for outcome in plan.outcomes:
            ingredient_id = outcome.candidate.ingredient_id
            winner = chosen_by_ingredient.get(ingredient_id, supplier.id)
            if winner == supplier.id:
                keep.append(outcome.candidate)
                continue
            if outcome.line is not None:
                winner_name = terms[winner].name if winner in terms else f"supplier {winner}"
                spend = pounds(outcome.line.line_total_pence)
                moved.append(f"{outcome.candidate.ingredient_name} ({spend}) -> {winner_name}")
        if len(keep) == len(plan.outcomes):
            resized.append(plan)
            continue
        notes: list[str] = []
        if moved:
            notes.append(
                f"{supplier.name}: SOURCING MOVED {len(moved)} line(s) off this order: "
                + "; ".join(moved)
                + ". The reason is on each sourcing choice. This order was then re-sized, "
                "so its minimum and any top-up were re-decided against what is left."
            )
            notes.extend(
                reason_by_ingredient[c.ingredient_id]
                for c in choices
                if c.chosen.supplier_id != supplier.id
                and any(
                    o.candidate.ingredient_id == c.ingredient_id and o.line is not None
                    for o in plan.outcomes
                )
            )
        rebuilt = build_suggestion(
            supplier=(
                replace(supplier, min_order_pence=min_order_pence)
                if min_order_pence is not None
                else supplier
            ),
            target_delivery_date=plan.suggestion.target_delivery_date,
            cover_window=plan.suggestion.cover_window,
            candidates=tuple(keep),
            terms=terms.get(supplier.id) if min_order_pence is None else None,
            # The run's own notes first -- a missed cutoff or a skipped ingredient is a
            # fact about this run, and a rebuilt suggestion cannot rediscover it.
            extra_notes=(*plan.extra_notes, *notes),
        )
        resized.append(rebuilt)
    return resized


def _resize_with_minimum(plan: SizingPlan, *, min_order_pence: int) -> SizingPlan:
    """Re-size a plan against a what-if minimum. Sizing, not relabelling.

    The top-up is part of sizing: overriding the minimum and then reporting the old
    lines would show an order that was never built.
    """
    suggestion = plan.suggestion
    supplier = replace(suggestion.supplier, min_order_pence=min_order_pence)
    return build_suggestion(
        supplier=supplier,
        target_delivery_date=suggestion.target_delivery_date,
        cover_window=suggestion.cover_window,
        candidates=tuple(o.candidate for o in plan.outcomes),
        terms=None,
        extra_notes=(
            *plan.extra_notes,
            f"WHAT-IF: this order was sized against a {pounds(min_order_pence)} minimum "
            "supplied on the command line, not the stored one.",
        ),
    )


def record_emergency_lines(
    session: Session,
    result: SplitResult,
    *,
    at: datetime,
) -> list[int]:
    """Write every emergency line to `tesco_routing` with its premium. Spec 4.4.

    Separate from `build_split` because building is a read and this is a write: the
    replay in `cafeops simulate` computes the same routings on every run, and logging
    them every time would turn one bad Tuesday into forty entries in the report that
    argues for changing the ordering cadence.
    """
    repo = SqlSourcingRepository(session)
    written: list[int] = []
    for line in result.emergency.lines:
        written.append(
            repo.record_emergency_routing(
                line.ingredient_id,
                reason=line.reason,
                at=at,
                retail_unit_price_pence=_int_or_none(line.retail_unit_price_pence),
                preferred_unit_price_pence=_int_or_none(line.preferred_unit_price_pence),
                would_be_supplier_id=result.would_be_supplier.get(line.ingredient_id),
                qty=line.qty,
            )
        )
    return written


def _int_or_none(value: Decimal | None) -> int | None:
    """Pence as an integer, or None. Money never becomes a float (invariant 11)."""
    if value is None:
        return None
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))
