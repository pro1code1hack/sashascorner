"""Shared dataclasses and enums -- the contract between domain and everything else.

`domain/` is pure: no SQLAlchemy imports, no I/O. It takes these dataclasses in and
returns these dataclasses out. That is what makes `resolve_recipe` testable without
a database, and it is non-negotiable.

The enums live in db/models/enums.py and are re-exported here. They are plain
`enum.Enum` with no ORM dependency, so re-exporting keeps one definition rather
than two that drift apart.

THIS FILE IS INTEGRATOR-OWNED. An agent that needs a new field or a changed
signature raises it. It does not edit this file.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from cafeops.db.models.enums import (
    AgentToolOutcome,
    ChannelSourceKind,
    ChecklistStatus,
    ComponentRole,
    ModifierAction,
    MovementType,
    OrderChannel,
    POStatus,
    PriceSource,
    SaleChannel,
    SalesChannelName,
    SizeCode,
    Storage,
    Tier,
    Unit,
)

__all__ = [
    "EPSILON",
    "AgentProposal",
    "AgentToolOutcome",
    "BatchSpec",
    "ChannelSourceKind",
    "ChecklistStatus",
    "ComponentRole",
    "ComponentSpec",
    "ConsumptionPoint",
    "CostBreakdownLine",
    "CoverWindow",
    "DepletionAllocation",
    "DriftResult",
    "DriftVerdict",
    "EmergencyLine",
    "ExpiryLoss",
    "ForecastPoint",
    "ForecastResult",
    "ImpactPreview",
    "ImpactedItem",
    "IngredientSnapshot",
    "LabourCost",
    "MenuItemSpec",
    "ModifierAction",
    "ModifierSpec",
    "MovementSpec",
    "MovementType",
    "OnHand",
    "OrderChannel",
    "OrderSuggestion",
    "POStatus",
    "PackChoice",
    "ParSpec",
    "PriceSource",
    "ResolvedLine",
    "ResolvedRecipe",
    "SaleChannel",
    "SaleLine",
    "SalesChannelName",
    "SeasonSpec",
    "ShelfLifeSpec",
    "SizeCode",
    "SourcingChoice",
    "SourcingOption",
    "Storage",
    "SubstitutionError",
    "SuggestedLine",
    "SupplierSpec",
    "SupplierSplit",
    "SupplierTerms",
    "Tier",
    "Unit",
    "VariantOptionSpec",
]

#: Guards the drift denominator (spec 5.2).
EPSILON = Decimal("0.0001")


# ==========================================================================
# Composition (spec 4.2, 4.3)
# ==========================================================================


class SubstitutionError(ValueError):
    """A SUBSTITUTE modifier hit a slot with is_substitutable=False.

    Spec 4.3 rule 4: this is an error, not a silent no-op. Silently ignoring it
    would mean a customer charged for oat milk got dairy, and the ledger would
    show the wrong ingredient depleting.
    """


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    """One template slot, already narrowed to a single size.

    `ingredient_id` is None when the slot is filled by a variant axis.
    """

    component_id: int
    role: ComponentRole
    ingredient_id: int | None
    qty: Decimal | None
    is_substitutable: bool
    is_required: bool


@dataclass(frozen=True, slots=True)
class VariantOptionSpec:
    """A chosen option on an axis."""

    option_id: int
    axis_id: int
    name: str
    role: ComponentRole
    ingredient_id: int | None
    qty: Decimal | None  # overrides the slot's quantity when not None
    price_delta_pence: int = 0
    #: Spec 4.3. Carried on the spec rather than passed alongside it, so a caller
    #: cannot silently lose the out-of-season warning by forgetting an argument --
    #: the "every caller must remember" failure this module exists to prevent.
    season_id: int | None = None


@dataclass(frozen=True, slots=True)
class ModifierSpec:
    """A modifier as the resolver sees it. Targets a ROLE, not an ingredient."""

    modifier_id: int
    name: str
    action: ModifierAction
    target_role: ComponentRole
    ingredient_id: int | None = None
    qty_delta: Decimal | None = None
    qty_multiplier: Decimal | None = None
    price_pence: int = 0


@dataclass(frozen=True, slots=True)
class MenuItemSpec:
    """A sellable item handed to `resolve_recipe`."""

    menu_item_id: int
    name: str
    size_code: SizeCode | None
    template_id: int | None
    price_pence: int
    manual_recipe: bool = False
    #: Spec 4.3. See VariantOptionSpec.season_id for why this is a field.
    season_id: int | None = None
    #: Components already narrowed to this item's size, valid at the resolve date.
    components: tuple[ComponentSpec, ...] = ()
    #: Options selected on each axis, already resolved from selected_options.
    options: tuple[VariantOptionSpec, ...] = ()
    #: Only for manual_recipe items: (ingredient_id, qty) pairs valid at the date.
    manual_lines: tuple[tuple[int, Decimal], ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedLine:
    """One concrete ingredient requirement after resolution."""

    ingredient_id: int
    qty: Decimal
    role: ComponentRole
    #: Which modifier, if any, put this line here or changed it.
    from_modifier_id: int | None = None
    #: Which variant option filled it, if any.
    from_option_id: int | None = None


@dataclass(frozen=True, slots=True)
class CostBreakdownLine:
    ingredient_id: int
    ingredient_name: str
    qty: Decimal
    #: None when the ingredient is unknown to the resolver -- which is itself a
    #: data problem worth surfacing rather than defaulting away.
    unit: Unit | None
    cost_per_unit_pence: Decimal | None
    line_cost_pence: Decimal | None
    source: PriceSource | None

    @property
    def is_missing_cost(self) -> bool:
        return self.cost_per_unit_pence is None


@dataclass(frozen=True, slots=True)
class ResolvedRecipe:
    """The output of `resolve_recipe`.

    `lines` carry RECIPE quantities -- no waste factor. Invariant 5: waste affects
    stock depletion only, never menu cost. `depletion_lines` is the same recipe
    with waste applied, and the two are deliberately separate values so nothing
    can conflate them by accident.
    """

    menu_item_id: int
    size_code: SizeCode | None
    resolved_at: datetime
    lines: tuple[ResolvedLine, ...]
    #: Same ingredients, quantities multiplied by (1 + waste_factor).
    depletion_lines: tuple[ResolvedLine, ...] = ()
    cost_breakdown: tuple[CostBreakdownLine, ...] = ()
    applied_modifier_ids: tuple[int, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def cost_pence(self) -> Decimal | None:
        """None when any ingredient has no price -- never silently zero."""
        total = Decimal("0")
        for line in self.cost_breakdown:
            if line.line_cost_pence is None:
                return None
            total += line.line_cost_pence
        return total

    @property
    def has_missing_cost(self) -> bool:
        return any(line.is_missing_cost for line in self.cost_breakdown)

    @property
    def cost_source(self) -> PriceSource | None:
        """The WEAKEST source among the ingredients.

        One estimated ingredient makes the whole item's cost an estimate
        (invariant 6). Missing beats estimate: if anything is unpriced, the
        answer is None.
        """
        if not self.cost_breakdown:
            return None
        if self.has_missing_cost:
            return None
        if any(line.source is PriceSource.ESTIMATE for line in self.cost_breakdown):
            return PriceSource.ESTIMATE
        if any(line.source is PriceSource.SUPPLIER_FEED for line in self.cost_breakdown):
            return PriceSource.SUPPLIER_FEED
        return PriceSource.INVOICE


# ==========================================================================
# Impact preview (spec 5.5)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class ImpactedItem:
    menu_item_id: int
    name: str
    size_code: SizeCode | None
    cost_before_pence: Decimal | None
    cost_after_pence: Decimal | None
    price_pence: int

    @property
    def cost_delta_pence(self) -> Decimal | None:
        if self.cost_before_pence is None or self.cost_after_pence is None:
            return None
        return self.cost_after_pence - self.cost_before_pence

    def margin_pct(self, cost: Decimal | None) -> float | None:
        if cost is None or self.price_pence <= 0:
            return None
        return float((Decimal(self.price_pence) - cost) / Decimal(self.price_pence) * 100)


@dataclass(frozen=True, slots=True)
class ImpactPreview:
    """What a composition edit would do, computed BEFORE it is committed.

    A domain function, not a UI concern (spec 5.5). The UI renders it; it does not
    compute it.
    """

    affected_item_count: int
    items: tuple[ImpactedItem, ...] = ()
    cost_delta_pence_per_item: Decimal | None = None
    #: Projected using the last 30 days of sales volume.
    monthly_cogs_delta_pence: Decimal | None = None
    worst_margin_after: ImpactedItem | None = None
    warnings: tuple[str, ...] = ()


# ==========================================================================
# Stock (spec 5.1)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class IngredientSnapshot:
    id: int
    name: str
    unit: Unit
    tier: Tier
    tracking_enabled: bool
    waste_factor: Decimal
    cost_per_unit_pence: Decimal | None = None
    cost_source: PriceSource | None = None


@dataclass(frozen=True, slots=True)
class MovementSpec:
    ingredient_id: int
    type: MovementType
    qty: Decimal  # signed
    occurred_at: datetime
    ref_type: str | None = None
    ref_id: int | None = None
    note: str | None = None
    #: Which batch this movement drew from or created. None for movements that are
    #: about the ingredient as a whole (ADJUSTMENT, COUNT_RESET), and for a refund --
    #: a refunded latte's milk is not back in the carton.
    #:
    #: Added because its absence forced a SECOND write path into `stock_movement`:
    #: batch-linked rows went through the batch repository while everything else went
    #: through the stock repository. Two writers into one append-only table diverge
    #: eventually, and the ledger is the last place that should happen.
    batch_id: int | None = None


@dataclass(frozen=True, slots=True)
class OnHand:
    """Theoretical on-hand and the evidence behind it.

    `is_theoretical` is always True. It exists so no formatter can print a number
    without saying which kind it is (invariant 4). A counted number is a
    StockCount, not this.
    """

    ingredient_id: int
    qty: Decimal
    as_of: datetime
    basis_count_qty: Decimal | None
    basis_counted_at: datetime | None
    movement_sum: Decimal
    movement_count: int
    is_theoretical: bool = True

    @property
    def has_count_basis(self) -> bool:
        """False means on-hand is a movement sum with no anchor -- weak."""
        return self.basis_counted_at is not None


@dataclass(frozen=True, slots=True)
class SaleLine:
    """A sold line handed to recipe expansion."""

    sale_id: int
    menu_item_id: int
    qty: Decimal
    sold_at: datetime
    modifier_ids: tuple[int, ...] = ()
    #: Requested by the stock agent: expansion infers a refund from `qty < 0`, which
    #: is correct, but without these it cannot verify that an `is_refund` row really
    #: carries a negative quantity, nor see a receipt voided AFTER expansion without
    #: a second query.
    is_refund: bool = False
    voided: bool = False


@dataclass(frozen=True, slots=True)
class ConsumptionPoint:
    """Consumption of one ingredient on one LOCAL calendar day."""

    day: date
    qty: Decimal


# ==========================================================================
# Drift (spec 5.2)
# ==========================================================================


class DriftVerdict(enum.StrEnum):
    """Spec 5.2's table as a value.

    ELIGIBLE means this ONE observation clears the bar -- not that auto-ordering
    turns on. Two consecutive clears are required, which is a property of the
    history, not of a single measurement.
    """

    ELIGIBLE = "ELIGIBLE"  # < 10%
    TUNE_WASTE_FACTOR = "TUNE_WASTE_FACTOR"  # 10-15%: propose a change, stay manual
    FORCE_MANUAL = "FORCE_MANUAL"  # > 15%: revoke and alert


@dataclass(frozen=True, slots=True)
class DriftResult:
    ingredient_id: int
    theoretical_qty: Decimal
    counted_qty: Decimal
    drift_pct: float
    observed_at: datetime
    verdict: DriftVerdict
    #: Populated in the tuning band.
    suggested_waste_factor: Decimal | None = None

    @property
    def abs_drift_pct(self) -> float:
        return abs(self.drift_pct)


# ==========================================================================
# Forecast (spec 5.3)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    day: date
    qty: Decimal
    dow_factor: Decimal


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """A forecast plus an honest account of how much to trust it.

    Invariant 7: a low-confidence forecast says so IN PLACE OF the number, not
    beside it. These fields are what let the bot and the API do that.
    """

    ingredient_id: int
    base_daily: Decimal
    points: tuple[ForecastPoint, ...]
    history_days: int
    low_confidence: bool
    confidence_reasons: tuple[str, ...] = ()
    used_flat_average: bool = False

    @property
    def total(self) -> Decimal:
        return sum((p.qty for p in self.points), Decimal("0"))


# ==========================================================================
# Ordering (spec 5.4)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class ParSpec:
    ingredient_id: int
    safety_days: Decimal
    min_qty: Decimal
    max_qty: Decimal
    auto_order_enabled: bool


@dataclass(frozen=True, slots=True)
class SupplierSpec:
    id: int
    name: str
    lead_time_days: int
    #: ISO weekdays 1..7. EMPTY means any day (walk-in retail, e.g. Tesco).
    delivery_weekdays: tuple[int, ...]
    min_order_pence: int
    order_channel: OrderChannel


@dataclass(frozen=True, slots=True)
class PackChoice:
    supplier_product_id: int
    ingredient_id: int
    pack_size: Decimal
    pack_unit: Unit
    price_pence: int
    sku: str = ""


@dataclass(frozen=True, slots=True)
class CoverWindow:
    days: tuple[date, ...]
    lead_time_days: int
    days_until_next_delivery: int
    safety_days: Decimal

    @property
    def length(self) -> int:
        return len(self.days)


@dataclass(frozen=True, slots=True)
class SuggestedLine:
    ingredient_id: int
    ingredient_name: str
    pack: PackChoice
    packs: int
    need_qty: Decimal
    forecast_qty: Decimal
    on_hand_qty: Decimal
    on_open_pos_qty: Decimal
    resulting_on_hand: Decimal
    unit: Unit
    is_top_up: bool = False
    clamped: str | None = None  # "min_qty" | "max_qty" when a clamp actually bit
    low_confidence: bool = False
    confidence_reasons: tuple[str, ...] = ()
    #: Requested by the ordering agent: safety_days lives on ParSpec, so the cover
    #: window is genuinely per line and only the order carried one. The bot and the
    #: API need it per line to explain a quantity.
    cover_days: int | None = None
    #: Why this line is smaller than the raw forecast asked for (spec 5.4). The user
    #: must know the system chose to under-order deliberately, or they will override
    #: it and create the waste the cap was preventing.
    cap_reason: str | None = None
    #: Set when stock is under the par floor but nothing is moving, so no line was
    #: created. An absent line cannot explain itself; this lets the caller say why.
    below_par_floor: bool = False

    @property
    def line_total_pence(self) -> int:
        return self.packs * self.pack.price_pence


@dataclass(frozen=True, slots=True)
class OrderSuggestion:
    supplier: SupplierSpec
    target_delivery_date: date
    cover_window: CoverWindow
    lines: tuple[SuggestedLine, ...] = ()
    min_order_topped_up: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_pence(self) -> int:
        return sum(line.line_total_pence for line in self.lines)

    @property
    def meets_minimum(self) -> bool:
        return self.total_pence >= self.supplier.min_order_pence

    @property
    def low_confidence(self) -> bool:
        return any(line.low_confidence for line in self.lines)


# ==========================================================================
# Batches, shelf life and expiry (spec 4.1)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class BatchSpec:
    """One received lot, as the domain sees it.

    FIFO is by `expires_at`, not `received_at`: a delivery with a short date must go
    out before older stock with a longer one, which is what a person standing at the
    fridge actually does.
    """

    batch_id: int
    ingredient_id: int
    qty_remaining: Decimal
    received_at: datetime
    expires_at: datetime | None
    opened_at: datetime | None = None
    unit_cost_pence: Decimal | None = None

    def effective_expiry(self, open_life_days: int | None) -> datetime | None:
        """The earlier of the unopened expiry and opened_at + open_life_days.

        A 270-day carton of oat milk lasts 5 days once opened, so the open life can
        pull the expiry much earlier. Whichever comes first is the real one.
        """
        if self.opened_at is None or open_life_days is None:
            return self.expires_at
        opened_deadline = self.opened_at + timedelta(days=open_life_days)
        if self.expires_at is None:
            return opened_deadline
        return min(self.expires_at, opened_deadline)

    def days_left(self, at: datetime, open_life_days: int | None = None) -> int | None:
        expiry = self.effective_expiry(open_life_days)
        if expiry is None:
            return None
        return (expiry - at).days


@dataclass(frozen=True, slots=True)
class ShelfLifeSpec:
    """What constrains how much of this may be bought at once."""

    ingredient_id: int
    storage: Storage
    shelf_life_days: int | None
    open_life_days: int | None
    transit_buffer_days: int = 0
    source: PriceSource | None = None

    @property
    def is_perishable(self) -> bool:
        """Invariant 5: perishables may never be used for a min-order top-up."""
        return self.shelf_life_days is not None

    @property
    def usable_days(self) -> int | None:
        """Shelf life less transit buffer -- the real cap on a cover window."""
        if self.shelf_life_days is None:
            return None
        return max(1, self.shelf_life_days - self.transit_buffer_days)


@dataclass(frozen=True, slots=True)
class DepletionAllocation:
    """How one consumption was taken from batches, oldest-expiring first."""

    batch_id: int | None
    qty: Decimal

    @property
    def is_unbatched(self) -> bool:
        """True when stock had to come from nowhere -- a negative-stock condition.

        Kept rather than refused: the ledger must record what was actually sold even
        when the batch record cannot explain it, and a shortfall is a real signal
        that counts are wrong or a delivery was never entered.
        """
        return self.batch_id is None


@dataclass(frozen=True, slots=True)
class ExpiryLoss:
    """A batch that reached its expiry with stock left. The honest waste figure."""

    batch_id: int
    ingredient_id: int
    qty: Decimal
    expired_at: datetime
    unit_cost_pence: Decimal | None = None

    @property
    def loss_pence(self) -> Decimal | None:
        if self.unit_cost_pence is None:
            return None
        return self.qty * self.unit_cost_pence


# ==========================================================================
# Seasons (spec 4.3)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class SeasonSpec:
    season_id: int
    name: str
    starts_on: date
    ends_on: date
    is_recurring_annually: bool = False

    def contains(self, day: date) -> bool:
        if not self.is_recurring_annually:
            return self.starts_on <= day <= self.ends_on
        # Recurring: compare month/day, handling a season that wraps the new year.
        start = (self.starts_on.month, self.starts_on.day)
        end = (self.ends_on.month, self.ends_on.day)
        probe = (day.month, day.day)
        if start <= end:
            return start <= probe <= end
        return probe >= start or probe <= end

    def days_remaining(self, at: date) -> int | None:
        """Days left in the current occurrence, or None if `at` is out of season.

        Spec 4.3: do not order a seasonal syrup with 3 weeks left on a 6-week cover.
        """
        if not self.contains(at):
            return None
        if not self.is_recurring_annually:
            return (self.ends_on - at).days
        end = self.ends_on.replace(year=at.year)
        if end < at:
            end = self.ends_on.replace(year=at.year + 1)
        return (end - at).days


# ==========================================================================
# Labour (spec 5.6)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class LabourCost:
    """Prep time turned into money, and the ranking number that matters at a rush.

    `margin_per_minute` reorders the menu against plain margin %: a drink at 85%
    margin taking three minutes loses to one at 70% taking forty seconds when there
    is a queue. All fields are None when prep time or the loaded rate is unknown --
    a labour figure derived from a guessed rate is a guess wearing a number's
    clothes.
    """

    menu_item_id: int
    prep_seconds: int | None
    loaded_hourly_rate_pence: int | None
    ingredient_cost_pence: Decimal | None
    price_pence: int

    @property
    def labour_cost_pence(self) -> Decimal | None:
        if self.prep_seconds is None or self.loaded_hourly_rate_pence is None:
            return None
        return Decimal(self.prep_seconds) / Decimal(3600) * Decimal(self.loaded_hourly_rate_pence)

    @property
    def true_margin_pence(self) -> Decimal | None:
        labour = self.labour_cost_pence
        if self.ingredient_cost_pence is None or labour is None:
            return None
        return Decimal(self.price_pence) - self.ingredient_cost_pence - labour

    @property
    def margin_per_minute_pence(self) -> Decimal | None:
        if self.ingredient_cost_pence is None or not self.prep_seconds:
            return None
        minutes = Decimal(self.prep_seconds) / Decimal(60)
        if minutes <= 0:
            return None
        return (Decimal(self.price_pence) - self.ingredient_cost_pence) / minutes


# ==========================================================================
# Sourcing and the supplier split (spec 4.4, 5.5)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class SupplierTerms:
    """Everything that decides when and how much to order from one supplier.

    `terms_are_placeholders` is not metadata -- it must reach the user. A cover
    window is only as good as the lead time behind it, and a confident quantity
    derived from an invented delivery schedule is worse than no quantity.
    """

    supplier_id: int
    name: str
    lead_time_days: int
    delivery_weekdays: tuple[int, ...]  # ISO 1..7; EMPTY means any day (walk-in)
    min_order_pence: int
    order_channel: OrderChannel
    cutoff_time: time | None = None
    delivery_fee_pence: int = 0
    free_delivery_threshold_pence: int | None = None
    terms_are_placeholders: bool = False


@dataclass(frozen=True, slots=True)
class SourcingOption:
    """One way to buy one ingredient: a supplier, a pack and a unit price."""

    supplier_product_id: int
    supplier_id: int
    ingredient_id: int
    pack_size: Decimal
    pack_unit: Unit
    price_pence: int
    is_preferred: bool
    moq_packs: int = 1
    sku: str = ""

    @property
    def unit_price_pence(self) -> Decimal | None:
        if self.pack_size <= 0:
            return None
        return Decimal(self.price_pence) / self.pack_size


@dataclass(frozen=True, slots=True)
class SourcingChoice:
    """Which option was chosen for an ingredient, and what it cost to choose it.

    Spec 4.4: an alternate is taken only when it is materially cheaper per unit AND
    the switch does not push another supplier's order below its minimum. That
    trade-off is surfaced here, never resolved silently.
    """

    ingredient_id: int
    chosen: SourcingOption
    alternatives: tuple[SourcingOption, ...] = ()
    reason: str = ""
    #: Set when a cheaper option existed but was not taken, with the saving forgone.
    cheaper_rejected: SourcingOption | None = None
    forgone_saving_pence: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SupplierSplit:
    """The output of one ordering run: N orders, one per supplier, plus rationale.

    Spec 5.5: "a set of orders with a one-line rationale each, not one
    undifferentiated basket."
    """

    suggestions: tuple[OrderSuggestion, ...] = ()
    #: Items that could not wait for a scheduled delivery. Every one of these is a
    #: retail premium paid, and the log of them is the argument for fixing the
    #: ordering cadence (spec 4.4).
    emergency: tuple[EmergencyLine, ...] = ()
    choices: tuple[SourcingChoice, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def total_pence(self) -> int:
        return sum(s.total_pence for s in self.suggestions)


@dataclass(frozen=True, slots=True)
class EmergencyLine:
    """A Tesco run. Recorded so the pattern becomes visible and arguable."""

    ingredient_id: int
    ingredient_name: str
    qty: Decimal
    unit: Unit
    reason: str
    retail_unit_price_pence: Decimal | None = None
    preferred_unit_price_pence: Decimal | None = None

    @property
    def raw_premium_pence(self) -> Decimal | None:
        """Retail less scheduled-supplier price, signed. Can be NEGATIVE.

        Negative means retail is genuinely cheaper for this line. That is a sourcing
        finding worth acting on -- the preferred supplier is the wrong default -- but
        it is not an emergency premium, and it must never be netted off the cost of
        panic-buying. Use `premium_pence` for money.
        """
        if self.retail_unit_price_pence is None or self.preferred_unit_price_pence is None:
            return None
        return (self.retail_unit_price_pence - self.preferred_unit_price_pence) * self.qty

    @property
    def premium_pence(self) -> Decimal | None:
        """The extra actually paid by going to retail. Never negative.

        Floored here rather than at each call site. Three separate agents have now
        reached for a side channel because a shared value did not carry its own rule,
        and this is the same shape: a raw signed figure that every caller must
        remember to clamp. One that forgets under-reports the cost of panic-buying,
        and could even make a week of emergencies look like a saving.
        """
        raw = self.raw_premium_pence
        if raw is None:
            return None
        return raw if raw > 0 else Decimal("0")

    @property
    def retail_is_cheaper(self) -> bool:
        """True when this line would have been cheaper at retail all along.

        Not a premium -- a sourcing finding. Surfaced separately so it is arguable
        rather than silently absorbed into a zero.
        """
        raw = self.raw_premium_pence
        return raw is not None and raw < 0


# ==========================================================================
# Agent (spec 9)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class AgentProposal:
    """What the agent is allowed to produce instead of a write.

    Spec 9's hard rule: the agent never writes to stock, orders or composition
    directly. It calls a service, or it emits one of these and a human confirms.
    """

    kind: str  # "waste_factor" | "template_grouping" | "channel_import" | ...
    subject_ref: str
    summary: str
    payload: dict[str, object] = field(default_factory=dict)
    confidence: str | None = None
