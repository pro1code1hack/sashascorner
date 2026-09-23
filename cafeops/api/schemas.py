"""Pydantic v2 response models. The contract the React app is built against.

Three of these types exist to make an invariant structurally impossible to lose
between the service layer and the screen, rather than merely documented:

* **`Cost`** (invariant 8). Every cost anywhere in the API is one of these. `pence` is
  `null` when the cost is unknown -- never `0` -- and `source` carries the weakest
  provenance among the ingredients behind it. 292 of the 318 costed menu items in the
  seeded database are ESTIMATE, so `is_estimate` is the common case and a UI that only
  styles the exception has styled the wrong half. `excluded_from_aggregates` says
  outright that a figure was left out of a total, so a reader never has to infer it
  from a total that does not add up.

* **`Forecast`** (invariant 9). A low-confidence forecast has `qty: null` and its
  reasons populated. The number is **absent from the payload**, not merely flagged
  beside it -- a frontend cannot render what it was never sent, which is the only way
  this invariant survives contact with a designer under deadline. `domain/ordering.py`
  takes the same line in `forecast_text`.

* **`OnHand`** (invariant 6). `is_theoretical` and `has_count_basis` are required, not
  optional, so no row can be serialised without saying which kind of number it is.

Money and quantity encoding is `encoding.py`'s docstring: integer pence where the
database stores an integer, an exact decimal *string* of pence where the figure is
derived and fractional, quantities always strings. No float touches money anywhere
below (invariant 11).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ApplyResponse",
    "BatchOut",
    "ChannelFinding",
    "ChannelPerformanceOut",
    "ChannelsResponse",
    "ComponentOut",
    "ComponentQtyChange",
    "ConversionGapOut",
    "Cost",
    "DraftOrdersResponse",
    "DriftOut",
    "EmergencyLineOut",
    "Forecast",
    "Health",
    "ImpactPreviewOut",
    "ImpactedItemOut",
    "LabourImpactOut",
    "MarginItemOut",
    "MarginResponse",
    "Meta",
    "ModifierOut",
    "OnHand",
    "OrderLineOut",
    "PreviewResponse",
    "RankedItemOut",
    "RollupOut",
    "RunOut",
    "SourcingChoiceOut",
    "StockDetail",
    "StockResponse",
    "StockRow",
    "SupplierOrderOut",
    "SupplierOut",
    "TemplateDetail",
    "TemplateItemOut",
    "TemplateSummary",
    "TodayResponse",
    "UncostedItemOut",
    "VariantAxisOut",
    "VariantOptionOut",
]


class Out(BaseModel):
    """Base for every response model. Frozen so a router cannot patch a figure."""

    model_config = ConfigDict(frozen=True)


class In(BaseModel):
    """Base for every request body. `extra="forbid"` so a typo is an error.

    A recipe editor that silently ignores a misspelled field would apply an edit the
    reviewer did not intend, and `apply` writes to composition.
    """

    model_config = ConfigDict(extra="forbid")


# ==========================================================================
# The three invariant carriers
# ==========================================================================


class Cost(Out):
    """A money figure and how much it is worth trusting. Invariant 8.

    `pence` is an exact decimal string of pence, or `null` when unknown. It is never
    `0` for "unknown": zero is a real and different claim.
    """

    pence: str | None = Field(
        description="Exact decimal pence as a string, or null when the cost is unknown."
    )
    source: str | None = Field(
        default=None,
        description=(
            "Weakest PriceSource among the ingredients behind this figure "
            "(INVOICE|ESTIMATE|SUPPLIER_FEED), or null when the cost is unknown."
        ),
    )
    is_estimate: bool = Field(
        default=False, description="True when any ingredient behind this figure is priced ESTIMATE."
    )
    is_missing: bool = Field(
        default=False, description="True when at least one ingredient behind it has no price."
    )
    excluded_from_aggregates: bool = Field(
        default=False,
        description=(
            "True when this figure was left out of the totals on the same response, so "
            "the reader is told rather than left to infer it from a total that does not add up."
        ),
    )
    note: str | None = None


class Forecast(Out):
    """A forecast, or the reason there is no number to show. Invariant 9.

    When `is_low_confidence` is true, `qty` is `null` and `reasons` carries the
    sentence to render **in its place**. The figure is withheld from the payload
    deliberately; see the module docstring.
    """

    qty: str | None = Field(
        description="Total forecast quantity over the window, or null when low-confidence."
    )
    unit: str
    window_days: int
    is_low_confidence: bool
    reasons: tuple[str, ...] = ()
    used_flat_average: bool = False
    history_days: int = 0


class OnHand(Out):
    """A stock figure that cannot be serialised without saying what kind it is.

    Invariant 6. `is_theoretical` is always true -- this is the ledger's number, not a
    count. `has_count_basis` is the one that matters operationally: false means the
    figure is a bare movement sum with no physical anchor behind it, which is not good
    enough to order against.
    """

    qty: str
    unit: str
    as_of: datetime
    is_theoretical: bool
    has_count_basis: bool
    basis_count_qty: str | None
    basis_counted_at: datetime | None
    movement_sum: str
    movement_count: int
    basis_label: str = Field(
        description="Human label for the basis: 'counted + ledger' or 'ledger only - NO COUNT'."
    )
    is_negative: bool = Field(
        default=False,
        description="The ledger has consumed more than the last count recorded. Count it.",
    )


# ==========================================================================
# Health and meta
# ==========================================================================


class Health(Out):
    """Open, unauthenticated. Says whether the door is locked, never what the key is."""

    status: str
    database_dialect: str
    auth_configured: bool
    ingredients: int
    menu_items: int
    templates: int
    movements: int


class Meta(Out):
    """Conventions and vocabularies, so the frontend does not hard-code enum strings."""

    money_encoding: str
    quantity_encoding: str
    timestamp_encoding: str
    units: tuple[str, ...]
    tiers: tuple[str, ...]
    size_codes: tuple[str, ...]
    component_roles: tuple[str, ...]
    price_sources: tuple[str, ...]
    storage: tuple[str, ...]
    movement_types: tuple[str, ...]
    drift_verdicts: tuple[str, ...]
    drift_causes: tuple[str, ...]
    order_channels: tuple[str, ...]
    sales_channels: tuple[str, ...]
    loaded_hourly_rate_pence: int | None
    local_timezone: str
    invariant_notes: tuple[str, ...]


# ==========================================================================
# Templates and the composition editor (spec 4.2, 5.5)
# ==========================================================================


class TemplateSummary(Out):
    id: int
    name: str
    category: str | None
    sizes: tuple[str, ...]
    axes: tuple[str, ...]
    item_count: int
    component_count: int
    prep_seconds_by_size: dict[str, int] = Field(default_factory=dict)
    prep_seconds_is_estimate: bool | None = None


class ComponentOut(Out):
    """One template slot. `qty_by_size` is strings in and strings out (ARCHITECTURE 7.4)."""

    component_id: int
    role: str
    ingredient_id: int | None
    ingredient_name: str | None = Field(
        default=None, description="Null when the slot is filled by a variant axis."
    )
    unit: str | None
    qty_by_size: dict[str, str]
    is_substitutable: bool
    is_required: bool
    effective_from: datetime
    ingredient_cost: Cost | None = Field(
        default=None, description="Cost per unit of the slot's ingredient, if it has one."
    )


class VariantOptionOut(Out):
    option_id: int
    name: str
    role: str
    ingredient_id: int | None
    ingredient_name: str | None
    qty_override: str | None
    price_delta_pence: int
    season_id: int | None
    season_name: str | None


class VariantAxisOut(Out):
    axis_id: int
    name: str
    role: str | None
    is_required: bool
    options: tuple[VariantOptionOut, ...]


class ModifierOut(Out):
    modifier_id: int
    name: str
    action: str
    target_role: str
    ingredient_id: int | None
    ingredient_name: str | None
    qty_delta: str | None
    qty_multiplier: str | None
    price_pence: int


class TemplateItemOut(Out):
    """A sellable leaf of the template, with its cached cost and both margins."""

    menu_item_id: int
    name: str
    size_code: str | None
    price_pence: int
    cost: Cost
    margin_pct: float | None
    true_margin_pct: float | None
    margin_per_minute_pence: str | None
    prep_seconds: int | None
    prep_source: str | None
    prep_is_estimate: bool | None
    is_available_today: bool
    availability: str
    availability_reasons: tuple[str, ...] = ()


class TemplateDetail(Out):
    """Everything the three-pane composition editor renders for one template."""

    template: TemplateSummary
    as_of: datetime
    components: tuple[ComponentOut, ...]
    axes: tuple[VariantAxisOut, ...]
    modifiers: tuple[ModifierOut, ...]
    items: tuple[TemplateItemOut, ...]
    warnings: tuple[str, ...] = ()


class ComponentQtyChange(In):
    """The body of both `/preview` and `/apply`.

    Quantities are **strings**. A float here is refused by the service layer
    (`edit_composition._validated`) and by this schema's type, in that order.
    """

    component_id: int
    qty_by_size: dict[str, str] = Field(
        description='Per-size quantities as decimal strings, e.g. {"S": "0.12", "M": "0.18"}.'
    )
    window_days: int = Field(
        default=30, ge=1, le=365, description="Sales window for the COGS projection."
    )


class ComponentQtyApply(ComponentQtyChange):
    actor: str = Field(
        min_length=1,
        max_length=120,
        description="Who is applying this. Recorded on the cost rollup's trigger.",
    )


class ImpactedItemOut(Out):
    menu_item_id: int
    name: str
    size_code: str | None
    price_pence: int
    cost_before: Cost
    cost_after: Cost
    cost_delta_pence: str | None
    margin_pct_before: float | None
    margin_pct_after: float | None


class LabourItemOut(Out):
    menu_item_id: int
    label: str
    prep_seconds: int | None
    prep_is_estimate: bool
    labour_cost_pence: str | None
    true_margin_before_pence: str | None
    true_margin_after_pence: str | None
    true_margin_delta_pence: str | None
    margin_per_minute_before_pence: str | None
    margin_per_minute_after_pence: str | None
    margin_per_minute_delta_pence: str | None


class LabourImpactOut(Out):
    """Spec 5.6's half of the preview.

    `labour_cost_pence_per_item` is one figure only when every affected item agrees;
    otherwise it is null and `labour_cost_pence_range` carries the spread. An average
    across items that disagree is a number describing nothing.
    """

    items: tuple[LabourItemOut, ...]
    labour_cost_pence_per_item: str | None
    labour_cost_pence_range: tuple[str, str] | None
    true_margin_delta_pence_per_item: str | None
    true_margin_delta_pence_range: tuple[str, str] | None
    labour_cost_window_pence: str | None
    untimed_count: int
    rank_moves: tuple[tuple[str, int, int], ...] = Field(
        default=(),
        description=(
            "(item label, margin-per-minute rank before, rank after) for items the edit moves."
        ),
    )
    warnings: tuple[str, ...] = ()


class ImpactPreviewOut(Out):
    affected_item_count: int
    items: tuple[ImpactedItemOut, ...]
    cost_delta_pence_per_item: str | None = Field(
        description=(
            "One figure only when every affected item agrees. Null when they do not -- an "
            "average across items that disagree is a number describing nothing."
        )
    )
    cost_delta_pence_range: tuple[str, str] | None = Field(
        default=None,
        description=(
            "The spread, when `cost_delta_pence_per_item` is null. `ImpactPreview` itself "
            "puts this only in a warning sentence; it is computed here from the per-item "
            "deltas so a screen can render a range rather than parse prose. See the "
            "integrator note about `ImpactPreview`."
        ),
    )
    monthly_cogs_delta_pence: str | None
    worst_margin_after: ImpactedItemOut | None
    warnings: tuple[str, ...] = ()


class PreviewResponse(Out):
    """What the edit WOULD do. This endpoint writes nothing -- see `views.preview_edit`."""

    template_id: int
    component_id: int
    component_role: str
    ingredient_name: str | None
    qty_by_size_before: dict[str, str]
    qty_by_size_after: dict[str, str]
    at: datetime
    window_days: int
    preview: ImpactPreviewOut
    labour: LabourImpactOut
    writes_nothing: bool = True


class ApplyResponse(Out):
    """The edit, applied from today. The old component row is closed, a new one opened.

    There is no retroactive mode (invariant 3). `effective_from` before the start of
    the local trading day is refused with 409.
    """

    template_id: int
    component_id: int
    new_component_id: int
    effective_from: datetime
    qty_by_size_before: dict[str, str]
    qty_by_size_after: dict[str, str]
    preview: ImpactPreviewOut
    labour: LabourImpactOut
    rollup_summary: str
    rollup_items_recosted: int
    summary: str


# ==========================================================================
# Stock (spec 5.1, 5.2, 4.1)
# ==========================================================================


class BatchOut(Out):
    batch_id: int
    qty_remaining: str
    unit: str
    received_at: datetime
    expires_at: datetime | None
    opened_at: datetime | None
    effective_expiry: datetime | None = Field(
        description="Earlier of the carton date and opened_at + open_life_days."
    )
    days_left: int | None = Field(description="Null when this ingredient does not expire.")
    unit_cost_pence: str | None
    value_pence: str | None


class DriftAttributionOut(Out):
    """Which problem this is, and therefore what to fix. Spec 5.2's v2 diagnostic.

    The two fixes are opposite -- order less versus change the recipe -- so a single
    undifferentiated percentage sends the reader the wrong way about half the time.
    """

    cause: str
    headline: str
    action: str
    gap_qty: str
    expired_qty: str
    measurement_qty: str
    expiry_qty: str
    expiry_share: float | None
    unexplained_loss_qty: str
    surplus_qty: str
    surplus_note: str | None
    loss_pct_of_consumption: float | None


class DriftOut(Out):
    """The latest observation, the gate's verdict, and the attribution behind both."""

    has_observation: bool
    observed_at: datetime | None
    theoretical_qty: str | None
    counted_qty: str | None
    drift_pct: float | None
    verdict: str | None
    mean_abs_drift_pct: float | None
    observation_count: int
    auto_order_enabled: bool
    auto_order_reason: str | None
    clean_streak: int
    required_streak: int
    gate_action: str | None
    #: The spec's presentation vocabulary -- "trusted" | "drifting" | "excluded"
    #: (spec 10.2, 10.9) -- derived from the gate verdict here so every screen reads the
    #: same word. `verdict` and `gate_action` stay alongside it because the domain
    #: vocabulary is the more precise one and the drift screen shows it; this exists so
    #: that mapping is not invented separately in each view that needs a badge.
    trust_status: str | None = None
    attribution: DriftAttributionOut | None = None


class RunOut(Out):
    """Projected run-out, or the reason there is no date to show.

    Invariant 9 governs this the same way it governs an order quantity: when the
    forecast behind the date is low-confidence, `days` and `on` are null and
    `forecast.reasons` is what the screen shows instead. A run-out date is the most
    actionable number on the stock screen and therefore the worst one to guess at.
    """

    forecast: Forecast | None = Field(
        default=None,
        description=(
            "Null only when the caller asked for run_out=false. A WITHHELD forecast is a "
            "present Forecast with is_low_confidence=true and qty=null -- a different "
            "thing from not having asked."
        ),
    )
    daily_rate_qty: str | None = Field(
        default=None, description="Forecast daily consumption, or null when withheld."
    )
    days: str | None = Field(default=None, description="Days of cover left, as a decimal string.")
    on: date | None = Field(default=None, description="Projected local date stock reaches zero.")
    is_out_of_stock: bool = False
    note: str | None = None


class ShelfLifeOut(Out):
    storage: str
    shelf_life_days: int | None = Field(
        description="Null means DOES NOT EXPIRE -- a statement, not a gap (ARCHITECTURE 8F.1)."
    )
    open_life_days: int | None
    transit_buffer_days: int
    usable_days: int | None = Field(
        description="Shelf life less transit buffer. The real cap on a cover window."
    )
    is_perishable: bool
    source: str | None = Field(
        default=None, description="All 113 are seeded ESTIMATE (ARCHITECTURE 8F.1)."
    )


class StockRow(Out):
    ingredient_id: int
    name: str
    tier: str
    unit: str
    tracking_enabled: bool
    waste_factor: str
    unit_cost: Cost
    on_hand: OnHand
    shelf_life: ShelfLifeOut
    batches: tuple[BatchOut, ...]
    batch_qty: str
    batch_coverage_gap: str = Field(
        description=(
            "Theoretical on-hand less what the batches account for. A positive gap is "
            "stock FIFO and the expiry sweep cannot see, so it can never expire or be "
            "counted as waste."
        )
    )
    soonest_expiry_days: int | None
    is_short_dated: bool
    drift: DriftOut
    run_out: RunOut


class StockSummary(Out):
    ingredients: int
    unanchored: int = Field(description="No physical count behind the figure. Weak.")
    negative: int
    short_dated: int
    unbatched: int
    auto_order_enabled: int
    forced_manual: int
    expiring_value_pence: str | None
    notes: tuple[str, ...] = ()


class StockResponse(Out):
    as_of: datetime
    basis: str = Field(
        default="THEORETICAL",
        description="Every qty on this response is theoretical. Counts are the source of truth.",
    )
    summary: StockSummary
    rows: tuple[StockRow, ...]


class DriftHistoryRowOut(Out):
    observed_at: datetime
    theoretical_qty: str
    counted_qty: str
    drift_pct: float
    waste_factor_at_count: str
    expired_qty_in_window: str | None


class StockDetail(Out):
    row: StockRow
    drift_history: tuple[DriftHistoryRowOut, ...]
    suggested_waste_factor: str | None
    templates_using: tuple[str, ...] = ()


# ==========================================================================
# Orders and suppliers (spec 4.4, 5.4, 5.5)
# ==========================================================================


class SupplierOut(Out):
    supplier_id: int
    name: str
    lead_time_days: int
    delivery_weekdays: tuple[int, ...] = Field(
        description="ISO 1..7. EMPTY means any day (walk-in retail)."
    )
    min_order_pence: int
    order_channel: str
    cutoff_time: str | None
    delivery_fee_pence: int
    free_delivery_threshold_pence: int | None
    terms_are_placeholders: bool = Field(
        description=(
            "Six of eight suppliers' terms are INVENTED (ARCHITECTURE 8F.4). Every cover "
            "window built on them is only as good as the guess. This must reach the screen."
        )
    )


class OrderLineOut(Out):
    ingredient_id: int
    ingredient_name: str
    unit: str
    packs: int
    pack_size: str
    pack_unit: str
    pack_price_pence: int
    line_total_pence: int
    sku: str
    need_qty: str
    on_hand_qty: str
    on_open_pos_qty: str
    resulting_on_hand_qty: str
    forecast: Forecast = Field(
        description=(
            "Over the EFFECTIVE cover window -- the one the line was sized on, which a "
            "shelf-life or season cap may have shortened."
        )
    )
    cover_days: int | None = Field(description="The effective cover window this line was sized on.")
    full_cover_days: int | None = Field(
        default=None,
        description="The uncapped window: lead time + gap to the next delivery + safety days.",
    )
    forecast_full_window_qty: str | None = Field(
        default=None,
        description=(
            "Forecast over the FULL window. Null when the forecast is withheld "
            "(invariant 9) -- the difference of two withheld numbers is still the number."
        ),
    )
    capped_out_qty: str | None = Field(
        default=None,
        description=(
            "Quantity deliberately NOT ordered because of the cap. The answer to 'by how "
            "much?', as a number rather than only inside cap_detail's prose."
        ),
    )
    cap_reason: str | None = Field(
        description=(
            "Invariant 4. Set when shelf life or a season shortened this line's cover "
            "window, e.g. 'capped at 5 days -- Whole milk shelf life'. The user must see "
            "that the system under-ordered ON PURPOSE, or they will override it and "
            "create the waste the cap prevented."
        )
    )
    cap_detail: str | None = Field(
        default=None, description="The arithmetic behind the cap: by how much, and from what."
    )
    is_capped: bool = False
    is_top_up: bool = Field(
        default=False,
        description=(
            "Added to clear a minimum or free-delivery threshold. Never a perishable (invariant 5)."
        ),
    )
    clamped: str | None = Field(
        default=None, description="'min_qty' or 'max_qty' when a par clamp moved the number."
    )


class SkippedOut(Out):
    """A candidate that produced no line, and which of the good reasons applied.

    `is_capped` here is not a contradiction. A shelf-life cap can shorten the window far
    enough that the forecast over it is already covered by stock on hand, so the cap
    applied and *then* there was nothing to order. That is the cap working, and it is worth
    saying: without it the reader sees an absent line and cannot tell "not needed" from
    "the cap decided", which are different answers to "should I add this by hand?".
    """

    ingredient_id: int
    ingredient_name: str
    reason: str
    below_par_floor: bool = False
    out_of_season: bool = False
    is_capped: bool = False
    cap_reason: str | None = None
    data_error: str | None = None
    clamp_blocked: str | None = None


class SupplierOrderOut(Out):
    supplier: SupplierOut
    target_delivery_date: date
    cover_window_days: int
    cover_window_from: date | None
    cover_window_to: date | None
    lead_time_days: int
    days_until_next_delivery: int
    lines: tuple[OrderLineOut, ...]
    skipped: tuple[SkippedOut, ...]
    subtotal_pence: int
    delivery_fee_pence: int
    total_pence: int
    meets_minimum: bool
    min_order_topped_up: bool
    capped_line_count: int
    low_confidence_line_count: int
    status: str = Field(
        default="DRAFT",
        description=(
            "Always DRAFT and nothing here can change it. Invariant 1: nothing is ordered "
            "without human confirmation, and confirmation happens in Telegram. This API has "
            "no endpoint that creates, confirms or sends a purchase order."
        ),
    )
    notes: tuple[str, ...] = ()


class SourcingChoiceOut(Out):
    """Which supplier won an ingredient and what choosing it cost. Spec 4.4.

    `forgone_saving_pence` is the trade-off surfaced rather than resolved silently: a
    cheaper option existed and was not taken, either because the saving was too small
    to be worth a second invoice or because taking it would have pushed another
    supplier's order below its minimum.
    """

    ingredient_id: int
    ingredient_name: str
    chosen_supplier_id: int
    chosen_supplier_name: str | None
    chosen_is_preferred: bool
    chosen_unit_price_pence: str | None
    reason: str
    cheaper_rejected_supplier_id: int | None
    cheaper_rejected_unit_price_pence: str | None
    forgone_saving_pence: str | None
    alternative_count: int


class EmergencyLineOut(Out):
    """A Tesco run: what could not wait, and the retail premium it cost. Spec 4.4.

    Two premium figures, and the difference is load-bearing. `premium_pence` is the extra
    actually paid and is never negative. `raw_premium_pence` is signed, and when it is
    negative `retail_is_cheaper` is true: retail would have been cheaper for this line all
    along, which is a **sourcing finding** about the wrong default supplier, not a saving
    to net off the cost of panic-buying. A screen that showed only the signed figure could
    make a week of emergencies look like a discount.
    """

    ingredient_id: int
    ingredient_name: str
    qty: str
    unit: str
    reason: str
    retail_unit_price_pence: str | None
    preferred_unit_price_pence: str | None
    premium_pence: str | None = Field(
        description="Extra actually paid by going to retail. Never negative. Null when unpriced."
    )
    raw_premium_pence: str | None = Field(
        default=None, description="Signed. Negative means retail was cheaper for this line."
    )
    retail_is_cheaper: bool = Field(
        default=False,
        description=(
            "A sourcing finding, not a premium: the preferred supplier is the wrong default "
            "for this ingredient. Surfaced separately so it is arguable rather than absorbed "
            "into a zero."
        ),
    )


class DraftOrdersResponse(Out):
    order_date: date
    computed_at: datetime
    reorder_cadence_days: int | None
    tiers: tuple[str, ...]
    suppliers: tuple[SupplierOrderOut, ...]
    sourcing_choices: tuple[SourcingChoiceOut, ...]
    emergency: tuple[EmergencyLineOut, ...]
    emergency_total_premium_pence: str | None = Field(
        description=(
            "Null rather than a partial sum when any line cannot price its own premium. "
            "A total missing two of five lines understates the argument it exists to make."
        )
    )
    emergency_notes: tuple[str, ...] = Field(
        default=(),
        description=(
            "Cases deliberately NOT routed to retail. The half that stops this becoming a "
            "licence to shop."
        ),
    )
    total_pence: int
    capped_line_count: int
    placeholder_supplier_names: tuple[str, ...]
    notes: tuple[str, ...] = ()
    writes_nothing: bool = True


# ==========================================================================
# Menu margin (spec 5.6)
# ==========================================================================


class MarginItemOut(Out):
    menu_item_id: int
    name: str
    size_code: str | None
    label: str
    template_id: int | None
    template_name: str | None
    price_pence: int
    cost: Cost
    units_sold: str
    prep_seconds: int | None
    prep_source: str
    prep_is_estimate: bool
    labour_cost_pence: str | None
    margin_pct: float | None
    true_margin_pct: float | None
    true_margin_pence: str | None
    contribution_pence: str | None
    margin_per_minute_pence: str | None


class RankedItemOut(Out):
    """One item's position in BOTH orderings. The disagreement is the finding (spec 5.6)."""

    menu_item_id: int
    label: str
    margin_rank: int
    margin_per_minute_rank: int
    rank_delta: int = Field(
        description="Positive means margin-per-minute rates it HIGHER than margin % does."
    )
    margin_pct: float | None
    margin_per_minute_pence: str | None
    prep_seconds: int | None


class RollupOut(Out):
    label: str
    template_id: int | None
    items_total: int
    items_included: int
    units_total: str
    staff_hours: str | None
    prep_seconds_total: str | None
    labour_cost_pence_total: str | None
    ingredient_cost_pence_total: str | None
    revenue_pence_total: str | None
    contribution_pence_total: str | None
    true_margin_pence_total: str | None
    margin_per_minute_pence: str | None
    labour_share_of_revenue_pct: float | None
    is_complete: bool
    excluded: tuple[tuple[str, str], ...] = ()
    summary: str = ""


class UncostedItemOut(Out):
    """A menu item with NO cached cost at all. Invariant 8, the strongest form.

    These are not in `menu_item_cost` because their recipe does not resolve -- in the
    seeded workbook, `'card' (£3.00)` and `Syrup Gift Set`, both on the known
    data-quality list (ARCHITECTURE 8, 8.1). They are returned here rather than
    silently absent: an item missing from the margin screen is indistinguishable from
    an item nobody sells, and the honest answer is "we cannot say what this costs".
    """

    menu_item_id: int
    name: str
    size_code: str | None
    price_pence: int
    active: bool
    cost: Cost
    reason: str


class MarginResponse(Out):
    window_days: int
    since: date | None
    until: date | None
    loaded_hourly_rate_pence: int | None
    rate_is_set: bool
    items_costed: int
    rankable_count: int
    orderings_agree: bool = Field(
        description=(
            "Expected false. True on a real menu means every ranked item takes the same "
            "time to make, or the prep times are not real."
        )
    )
    by_margin_pct: tuple[MarginItemOut, ...]
    by_margin_per_minute: tuple[MarginItemOut, ...]
    ranked: tuple[RankedItemOut, ...]
    biggest_disagreements: tuple[RankedItemOut, ...]
    excluded: tuple[tuple[str, str], ...] = Field(
        default=(),
        description=(
            "(label, reason) for items that cannot be ranked. Named, never sorted to the "
            "bottom as zeroes."
        ),
    )
    uncosted_items: tuple[UncostedItemOut, ...] = ()
    menu: RollupOut
    by_template: tuple[RollupOut, ...]
    estimated_cost_item_count: int
    invoice_cost_item_count: int
    warnings: tuple[str, ...] = ()


# ==========================================================================
# Channels (spec 4.6)
# ==========================================================================


class FieldCoverageOut(Out):
    field_name: str
    reported: int
    missing: int
    complete: bool


class ChannelPerformanceOut(Out):
    channel: str
    since: date
    until: date
    days: int
    sources: tuple[str, ...] = Field(
        description=(
            "Provenance of the rows. A hand export and a scrape are not equally trustworthy."
        )
    )
    gross_pence: int | None
    commission_pence: int | None
    ad_spend_pence: int | None
    attributed_revenue_pence: int | None
    orders: int | None
    impressions: int | None
    menu_views: int | None
    net_pence: int | None = Field(
        description=(
            "Contribution after commission AND ad spend, over the days that reported all "
            "three. A day missing one is dropped whole rather than part-subtracted."
        )
    )
    net_complete_days: int
    net_incomplete_days: int
    roas_bp: int | None = Field(description="Basis points. 10000 = 1.0x.")
    commission_rate_bp: int | None
    net_margin_bp: int | None
    conversion_bp: int | None
    coverage: tuple[FieldCoverageOut, ...]
    caveats: tuple[str, ...] = ()


class ConversionGapOut(Out):
    menu_item_id: int
    item_name: str
    best_rank: int
    views: int
    orders: int
    conversion_bp: int
    benchmark_bp: int
    shortfall_bp: int
    relative_shortfall_bp: int
    lost_orders: int
    lost_revenue_pence: int | None
    days: int
    sentence: str


class ChannelFinding(Out):
    """Ranks well, converts badly: the cheapest thing on the channel screen to fix."""

    channel: str
    benchmark_bp: int | None
    considered: int
    min_views: int
    rank_threshold: int
    gaps: tuple[ConversionGapOut, ...]
    caveats: tuple[str, ...] = ()


class ChannelsResponse(Out):
    since: date
    until: date
    channels_present: tuple[str, ...]
    sources_present: tuple[str, ...]
    performance: tuple[ChannelPerformanceOut, ...]
    findings: tuple[ChannelFinding, ...]
    notes: tuple[str, ...] = ()


# ==========================================================================
# Today (spec 10's "today / money" screen, read-only)
# ==========================================================================


class TodayAlert(Out):
    kind: str
    severity: str = Field(
        description="'info' | 'watch' | 'act'. Colour is only for a crossed threshold."
    )
    subject: str | None
    message: str


class TodayResponse(Out):
    as_of: datetime
    local_date: date
    stock: StockSummary
    short_dated: tuple[str, ...]
    expiry_write_offs_due: int
    expiry_write_offs_value_pence: str | None
    drift_forced_manual: tuple[str, ...]
    drift_tuning_band: tuple[str, ...]
    auto_order_enabled_count: int
    draft_order_total_pence: int | None
    draft_order_supplier_count: int | None
    capped_line_count: int | None
    emergency_line_count: int | None
    unavailable_menu_items: tuple[str, ...]
    data_quality: tuple[TodayAlert, ...]
    alerts: tuple[TodayAlert, ...]
    notes: tuple[str, ...] = ()
