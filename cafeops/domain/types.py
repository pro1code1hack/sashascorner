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
from datetime import date, datetime
from decimal import Decimal

from cafeops.db.models.enums import (
    ChecklistStatus,
    ComponentRole,
    ModifierAction,
    MovementType,
    OrderChannel,
    POStatus,
    PriceSource,
    SaleChannel,
    SizeCode,
    Tier,
    Unit,
)

__all__ = [
    "EPSILON",
    "ChecklistStatus",
    "ComponentRole",
    "ComponentSpec",
    "ConsumptionPoint",
    "CostBreakdownLine",
    "CoverWindow",
    "DriftResult",
    "DriftVerdict",
    "ForecastPoint",
    "ForecastResult",
    "ImpactPreview",
    "ImpactedItem",
    "IngredientSnapshot",
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
    "SizeCode",
    "SubstitutionError",
    "SuggestedLine",
    "SupplierSpec",
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
