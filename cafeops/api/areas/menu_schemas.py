"""Request and response models for the Recipes, Menu items and Ingredients screens.

Same conventions as `api/schemas.py` (its docstring is the contract): integer pence
where the database stores an integer, an exact decimal STRING of pence where the
figure is derived, quantities always strings, and every cost a `Cost` so a missing
one is `null` and flagged -- never `0` (invariant 8). Bodies forbid unknown fields,
so a misspelled key is an error rather than an edit nobody intended.

Every write comes in two halves: `.../preview` writes nothing and returns what the
change would do; `.../apply` takes the same body plus the operator's name and applies
it from today (invariant 3). `apply_from` exists only so a retroactive date can be
refused out loud (409) instead of silently ignored.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

from cafeops.api.schemas import Cost, In, Out

SizeCodeStr = Literal["S", "M", "XL", "ONE"]
RoleStr = Literal["COFFEE", "MILK", "BASE", "FLAVOUR", "TOPPING", "PACKAGING", "SUNDRY"]
UnitStr = Literal["L", "ML", "KG", "G", "EACH"]
PriceSourceStr = Literal["INVOICE", "SUPPLIER_FEED", "ESTIMATE"]
ChangeStatus = Literal["new", "off", "on", "changed", "unchanged"]
MenuKindStr = Literal["DRINKS", "FOOD", "OTHER"]
QtyStr = Annotated[str, Field(pattern=r"^\d+(\.\d+)?$", max_length=24)]

ACTOR = Field(min_length=1, max_length=120, description="Who is doing this (operator name).")
APPLY_FROM = Field(
    default=None,
    description="Omit (or today) to apply from now. A past date is refused with 409.",
)


class ActorIn(In):
    actor: str = ACTOR


# ==========================================================================
# The shared preview shape
# ==========================================================================


class ChangeItemOut(Out):
    """One menu item on both sides of a pending edit."""

    key: str
    menu_item_id: int | None
    name: str
    size_code: str | None
    status: ChangeStatus
    on_till: bool
    price_before: int | None
    price_after: int | None
    cost_before: Cost
    cost_after: Cost
    cost_delta_pence: str | None
    margin_pct_before: float | None
    margin_pct_after: float | None
    labour_cost_pence: str | None
    true_margin_after_pence: str | None
    margin_per_minute_before_pence: str | None
    margin_per_minute_after_pence: str | None
    prep_seconds: int | None
    prep_is_estimate: bool
    units_sold: str


class ChangeImpactOut(Out):
    affected_item_count: int
    items: tuple[ChangeItemOut, ...]
    cost_delta_pence_per_item: str | None = Field(
        description="One figure when every priced item agrees; null with a range otherwise."
    )
    cost_delta_pence_range: tuple[str, str] | None
    monthly_cogs_delta_pence: str | None = Field(
        description="Cost change x units sold in the window. Unknown costs excluded, not zero."
    )
    revenue_delta_pence: str | None = Field(
        description="Price change x units sold in the window, or null when no price changes."
    )
    worst_margin_after: ChangeItemOut | None
    untimed_count: int
    estimated_count: int
    window_days: int
    warnings: tuple[str, ...] = ()


# ==========================================================================
# Recipes
# ==========================================================================


class RecipeRailTemplate(Out):
    template_id: int
    name: str
    category: str | None
    sizes: tuple[str, ...]
    item_count: int = Field(description="Active menu items made from this recipe.")


class RecipeRailProposal(Out):
    proposal_id: str
    name: str
    category: str | None
    menu_item_count: int
    is_hollow: bool
    name_is_ambiguous: bool
    blocked_reason: str | None


class OneOffOut(Out):
    menu_item_id: int
    name: str


class RecipesRailResponse(Out):
    templates: tuple[RecipeRailTemplate, ...]
    proposals: tuple[RecipeRailProposal, ...]
    one_offs: tuple[OneOffOut, ...]


class EditorComponentOut(Out):
    component_id: int
    role: str
    ingredient_id: int | None
    ingredient_name: str | None
    unit: str | None
    qty_by_size: dict[str, str]
    is_substitutable: bool
    is_required: bool
    unit_cost: Cost | None


class EditorOptionOut(Out):
    option_id: int
    axis_id: int
    name: str
    ingredient_id: int | None
    ingredient_name: str | None
    unit: str | None
    qty_by_size: dict[str, str] | None = Field(
        description="Per-size override; null = the slot's quantity applies."
    )
    price_delta_pence: int
    season_id: int | None
    season_name: str | None
    active: bool = Field(description="Any of its menu items is on the menu.")
    missing_ingredient: bool
    menu_item_ids: dict[str, int] = Field(description="size -> menu_item_id")


class EditorAxisOut(Out):
    axis_id: int
    name: str
    role: str
    options: tuple[EditorOptionOut, ...]


class EditorItemOut(Out):
    menu_item_id: int
    name: str
    size_code: str | None
    option_id: int | None
    price_pence: int
    active: bool
    on_till: bool
    cost: Cost
    prep_seconds: int | None
    prep_is_estimate: bool
    availability: str


class SwapOut(Out):
    modifier_id: int
    name: str
    action: str
    target_role: str
    ingredient_id: int | None
    ingredient_name: str | None
    qty_delta: str | None
    qty_multiplier: str | None
    price_pence: int
    price_is_estimate: bool | None
    is_active: bool
    applies_here: bool = Field(
        description="This recipe has a slot of target_role (and, for a swap, it is swappable)."
    )
    version_from: datetime | None


class HistoryEntryOut(Out):
    effective_from: datetime
    actor: str
    kind: str
    summary: str
    lines: tuple[str, ...]


class RecipeHistoryOut(Out):
    template_id: int
    baseline_from: datetime | None
    entries: tuple[HistoryEntryOut, ...]


class RecipeEditorOut(Out):
    template_id: int
    name: str
    category: str | None
    version: str = Field(description="Send back as base_version; a stale one is refused (409).")
    as_of: datetime
    sizes: tuple[str, ...]
    prep_seconds_by_size: dict[str, int]
    prep_is_estimate: bool | None
    base_price_by_size: dict[str, int | None]
    base_price_disagrees: tuple[str, ...] = Field(
        description="Sizes whose items do not share one base price; the mode is shown."
    )
    loaded_hourly_rate_pence: int | None
    components: tuple[EditorComponentOut, ...]
    axes: tuple[EditorAxisOut, ...]
    items: tuple[EditorItemOut, ...]
    swaps: tuple[SwapOut, ...]
    history: RecipeHistoryOut
    item_name_pattern: str
    warnings: tuple[str, ...] = ()


class OpComponentQtyIn(In):
    op: Literal["component.qty"]
    component_id: int
    qty_by_size: dict[SizeCodeStr, QtyStr]


class OpComponentSetIn(In):
    op: Literal["component.set"]
    component_id: int
    role: RoleStr | None = None
    ingredient_id: int | None = Field(default=None, description="Send null to empty the slot.")
    is_substitutable: bool | None = None
    is_required: bool | None = None


class OpComponentAddIn(In):
    op: Literal["component.add"]
    role: RoleStr
    ingredient_id: int | None
    qty_by_size: dict[SizeCodeStr, QtyStr]
    is_substitutable: bool = False
    is_required: bool = True


class OpComponentRemoveIn(In):
    op: Literal["component.remove"]
    component_id: int


class OpPrepSetIn(In):
    op: Literal["prep.set"]
    prep_seconds_by_size: dict[SizeCodeStr, Annotated[int, Field(ge=1, le=3600)]]
    is_estimate: bool


class OpBasePriceIn(In):
    op: Literal["price.base"]
    base_price_pence_by_size: dict[SizeCodeStr, Annotated[int, Field(ge=0, le=100000)]]


class OpOptionSetIn(In):
    op: Literal["option.set"]
    option_id: int
    name: str | None = Field(default=None, max_length=120)
    ingredient_id: int | None = None
    qty_by_size: dict[SizeCodeStr, QtyStr] | None = None
    price_delta_pence: int | None = Field(default=None, ge=-100000, le=100000)
    season_id: int | None = None


class OpOptionAddIn(In):
    op: Literal["option.add"]
    axis_id: int
    name: str = Field(min_length=1, max_length=120)
    ingredient_id: int | None = None
    qty_by_size: dict[SizeCodeStr, QtyStr] | None = None
    price_delta_pence: int = Field(default=0, ge=-100000, le=100000)
    season_id: int | None = None


class OpOptionActiveIn(In):
    op: Literal["option.active"]
    option_id: int
    active: bool


class OpOptionRemoveIn(In):
    op: Literal["option.remove"]
    option_id: int


class OpTemplateRenameIn(In):
    op: Literal["template.rename"]
    name: str = Field(min_length=1, max_length=160)


TemplateOpIn = Annotated[
    OpComponentQtyIn
    | OpComponentSetIn
    | OpComponentAddIn
    | OpComponentRemoveIn
    | OpPrepSetIn
    | OpBasePriceIn
    | OpOptionSetIn
    | OpOptionAddIn
    | OpOptionActiveIn
    | OpOptionRemoveIn
    | OpTemplateRenameIn,
    Field(discriminator="op"),
]


class ChangesetIn(In):
    base_version: str = Field(min_length=1, max_length=64)
    ops: list[TemplateOpIn] = Field(min_length=1, max_length=200)
    window_days: int = Field(default=30, ge=1, le=365)
    loaded_hourly_rate_pence: int | None = Field(
        default=None, ge=1, description="What-if rate for staff time. Never persisted."
    )


class ChangesetApplyIn(ChangesetIn):
    actor: str = ACTOR
    apply_from: date | None = APPLY_FROM


class RefusalOut(Out):
    op_index: int
    message: str


class ChangesetPreviewOut(Out):
    template_id: int
    base_version: str
    at: datetime
    diff: tuple[str, ...]
    refusals: tuple[RefusalOut, ...]
    warnings: tuple[str, ...]
    pos_actions: tuple[str, ...]
    impact: ChangeImpactOut
    items_created: tuple[ChangeItemOut, ...]
    items_deactivated: tuple[ChangeItemOut, ...]
    loaded_hourly_rate_pence: int | None
    writes_nothing: bool = True


class ChangesetAppliedOut(Out):
    template_id: int
    effective_from: datetime
    new_version: str
    summary: str
    diff: tuple[str, ...]
    component_ids_opened: tuple[int, ...]
    component_ids_closed: tuple[int, ...]
    option_ids_opened: tuple[int, ...]
    option_ids_closed: tuple[int, ...]
    menu_items_created: tuple[int, ...]
    menu_items_repriced: tuple[int, ...]
    rollup_items_recosted: int
    rollup_summary: str
    pos_actions: tuple[str, ...]


class SeasonOut(Out):
    season_id: int
    name: str
    starts_on: date
    ends_on: date
    is_recurring_annually: bool
    is_open_today: bool


class SwapChangeIn(In):
    action: Literal["SUBSTITUTE", "ADD", "SCALE"] | None = None
    target_role: RoleStr | None = None
    ingredient_id: int | None = None
    qty_delta: QtyStr | None = None
    qty_multiplier: QtyStr | None = None
    price_pence: int | None = Field(default=None, ge=0, le=100000)
    price_is_estimate: bool | None = None
    is_active: bool | None = None
    window_days: int = Field(default=30, ge=1, le=365)


class SwapApplyIn(SwapChangeIn):
    actor: str = ACTOR
    apply_from: date | None = APPLY_FROM


class SwapPreviewOut(Out):
    modifier_id: int
    name: str
    diff: tuple[str, ...]
    refusals: tuple[str, ...]
    warnings: tuple[str, ...]
    sales_in_window: int
    revenue_delta_pence: int | None
    window_days: int
    recipes_affected: tuple[str, ...]
    writes_nothing: bool = True


class SwapAppliedOut(Out):
    modifier_id: int
    version_id: int
    effective_from: datetime
    diff: tuple[str, ...]
    swap: SwapOut


class ProposalPreviewIn(In):
    allow_conflicts: bool = False


class ProposalConfirmIn(In):
    actor: str = ACTOR
    allow_conflicts: bool = False


class ProposalCostOut(Out):
    menu_item_id: int
    name: str
    size_code: str | None
    price_pence: int
    cost_before: Cost
    cost_after: Cost
    margin_pct_before: float | None
    margin_pct_after: float | None


class ProposalPreviewOut(Out):
    proposal_id: str
    name: str
    blocked_reason: str | None
    would_repoint: int
    would_close_manual_lines: int
    components: int
    options: int
    sizes: tuple[str, ...]
    items_skipped_other_template: tuple[str, ...]
    items_unresolved_option: tuple[str, ...]
    cost_changes: tuple[ProposalCostOut, ...]
    warnings: tuple[str, ...]
    summary: str | None
    writes_nothing: bool = True


# ==========================================================================
# Menu items
# ==========================================================================


class LowestMarginOut(Out):
    pct: float | None
    is_estimate: bool
    is_missing: bool = Field(description="Every priced size has an unknown cost.")
    no_price: bool = Field(description="No size has a price.")


class MenuSizeOut(Out):
    menu_item_id: int
    size_code: str | None
    price_pence: int
    active: bool
    on_till: bool
    cost: Cost
    margin_pct: float | None
    labour_cost_pence: str | None
    manual_recipe: bool
    data_quality_flag: str | None
    has_recipe: bool = Field(
        default=False, description="The cost cache resolved at least one ingredient line."
    )
    prep_seconds: int | None = Field(default=None, description="Null = not timed.")
    prep_is_estimate: bool | None = None
    prep_is_override: bool = Field(
        default=False, description="menu_item.prep_seconds overrides the recipe's time."
    )
    margin_per_minute_pence: str | None = Field(
        default=None,
        description="(price - ingredient cost) per minute of prep. Null when cost or prep unknown.",
    )


class MenuGroupOut(Out):
    key: str = Field(description="Stable group handle: the anchor menu_item_id as a string.")
    anchor_id: int
    name: str
    category: str | None
    kind: MenuKindStr
    note: str | None
    template_id: int | None
    template_name: str | None
    photo_url: str | None
    is_active: bool
    on_till: bool
    sizes: tuple[MenuSizeOut, ...]
    lowest_margin: LowestMarginOut
    season_id: int | None = None
    season_name: str | None = None
    sold_30d: str = Field(default="0", description="Units sold, every size, last 30 days (net).")


class MenuCategoryOut(Out):
    name: str
    kind: MenuKindStr
    count: int
    registered: bool = Field(description="Has a menu_category row (else grouped as OTHER).")


class MenuItemsResponse(Out):
    as_of: datetime
    groups: tuple[MenuGroupOut, ...]
    categories: tuple[MenuCategoryOut, ...]


class MenuLineOut(Out):
    ingredient_id: int
    ingredient_name: str
    qty: str
    unit: str
    unit_cost: Cost
    line_cost: Cost
    category: str | None


class PriceHistoryOut(Out):
    effective_from: datetime
    effective_to: datetime | None
    price_pence: int
    source: str
    set_by: str | None


class MenuItemDetailOut(Out):
    size: MenuSizeOut
    group: MenuGroupOut
    lines: tuple[MenuLineOut, ...]
    editable_lines: bool
    estimate_names: tuple[str, ...]
    prices: tuple[PriceHistoryOut, ...]
    history: tuple[HistoryEntryOut, ...]


class ItemSaleOut(Out):
    sale_id: int
    sold_at: datetime
    receipt_id: str
    menu_item_id: int
    size_code: str | None
    qty: str
    gross_pence: int
    channel: str
    voided: bool
    is_refund: bool
    modifier_names: tuple[str, ...]


class ItemSalesOut(Out):
    menu_item_ids: tuple[int, ...]
    total_rows: int
    page: int
    page_size: int
    units: str = Field(description="Net units over every matched, non-voided line.")
    gross_pence: int = Field(description="Net takings over every matched, non-voided line.")
    first_sold_at: datetime | None
    last_sold_at: datetime | None
    by_channel: dict[str, int] = Field(description="Channel -> non-voided line count.")
    payment_note: str
    rows: tuple[ItemSaleOut, ...]


class PrepIn(In):
    #: menu_item_id -> seconds; null clears the override (the recipe's time applies).
    seconds: dict[int, int | None] = Field(max_length=8)
    is_estimate: bool = False
    actor: str = ACTOR


class PrepOut(Out):
    menu_item_ids: tuple[int, ...]
    summary: str
    rollup_items_recosted: int


class LineIn(In):
    ingredient_id: int
    qty: QtyStr
    unit: UnitStr | None = Field(default=None, description="Null = the ingredient's own unit.")


class SizeCreateIn(In):
    size_code: SizeCodeStr | None
    price_pence: int = Field(ge=0, le=100000)
    lines: list[LineIn] = Field(default_factory=list, max_length=60)


class MenuItemCreateIn(In):
    name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=400)
    sizes: list[SizeCreateIn] = Field(min_length=1, max_length=4)
    actor: str = ACTOR


class MenuGroupIn(In):
    actor: str = ACTOR
    name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=400)
    active: bool | None = None


class MenuSizeIn(In):
    actor: str = ACTOR
    size_code: SizeCodeStr | None
    price_pence: int = Field(ge=0, le=100000)
    copy_from_menu_item_id: int | None = None


class MenuWriteOut(Out):
    menu_item_ids: tuple[int, ...]
    summary: str


class ManualLinesIn(In):
    lines: list[LineIn] = Field(max_length=60)
    also_menu_item_ids: list[int] = Field(default_factory=list, max_length=4)
    window_days: int = Field(default=30, ge=1, le=365)


class ManualLinesApplyIn(ManualLinesIn):
    actor: str = ACTOR
    apply_from: date | None = APPLY_FROM


class LinesPreviewOut(Out):
    menu_item_ids: tuple[int, ...]
    diff: tuple[str, ...]
    impact: ChangeImpactOut
    at: datetime
    writes_nothing: bool = True


class LinesAppliedOut(Out):
    effective_from: datetime
    menu_item_ids: tuple[int, ...]
    lines_closed: int
    lines_opened: int
    rollup_items_recosted: int
    diff: tuple[str, ...]


class PriceItemIn(In):
    menu_item_id: int
    price_pence: int = Field(ge=0, le=100000)


class MenuPricesIn(In):
    prices: list[PriceItemIn] = Field(min_length=1, max_length=400)
    window_days: int = Field(default=30, ge=1, le=365)


class MenuPricesApplyIn(MenuPricesIn):
    actor: str = ACTOR
    apply_from: date | None = APPLY_FROM


class PricesPreviewOut(Out):
    diff: tuple[str, ...]
    pos_actions: tuple[str, ...]
    impact: ChangeImpactOut
    at: datetime
    writes_nothing: bool = True


class PricesAppliedOut(Out):
    effective_from: datetime
    repriced: tuple[int, ...]
    diff: tuple[str, ...]
    pos_actions: tuple[str, ...]


class CategoryIn(In):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["DRINKS", "FOOD", "OTHER"]


class PhotoOut(Out):
    asset_id: int | None
    photo_url: str | None
    width: int | None
    height: int | None
    bytes: int | None
    content_type: str | None
    menu_item_ids: tuple[int, ...]


# ==========================================================================
# Ingredients
# ==========================================================================


class PackOut(Out):
    pack_size: str
    pack_unit: str
    pack_cost_pence: int
    source: str
    supplier_id: int | None
    supplier_name: str | None
    effective_from: datetime
    recorded_by: str | None
    note: str | None


class IngredientSupplierOut(Out):
    supplier_id: int
    name: str
    is_preferred: bool


class IngredientRowOut(Out):
    ingredient_id: int
    name: str
    category: str | None
    unit: str
    unit_cost: Cost
    pack: PackOut | None
    note: str | None
    suppliers: tuple[IngredientSupplierOut, ...]
    used_in_count: int = Field(description="Distinct menu products whose current recipe uses it.")
    retired: bool
    storage: str
    shelf_life_days: int | None
    shelf_life_source: str | None
    open_life_days: int | None = None
    transit_buffer_days: int = 0
    tier: str = "C"
    waste_factor: str = "0"


class SupplierNameOut(Out):
    supplier_id: int
    name: str


class IngredientsResponse(Out):
    rows: tuple[IngredientRowOut, ...]
    categories: tuple[tuple[str, int], ...]
    estimated_count: int
    total: int
    suppliers: tuple[SupplierNameOut, ...]


class OfferOut(Out):
    supplier_product_id: int
    supplier_id: int
    supplier_name: str
    sku: str | None
    pack_size: str
    pack_unit: str
    price_pence: int
    unit_cost_pence: str | None = Field(
        description="Pence per ONE of the ingredient's unit; null when the units cannot convert."
    )
    vs_cheapest_pct: float | None
    is_cheapest: bool
    is_preferred: bool
    last_seen_price_at: datetime | None
    terms_are_placeholders: bool


class UsedInOut(Out):
    menu_item_id: int
    name: str
    via: Literal["one-off", "recipe"]
    template_name: str | None


class IngredientPriceRowOut(Out):
    effective_from: datetime
    effective_to: datetime | None
    pack_size: str
    pack_unit: str
    pack_cost_pence: int
    cost_per_unit_pence: str
    source: str
    supplier_name: str | None
    recorded_by: str | None


class IngredientDetailOut(Out):
    row: IngredientRowOut
    offers: tuple[OfferOut, ...]
    preferred_supplier: str | None
    used_in: tuple[UsedInOut, ...]
    price_history: tuple[IngredientPriceRowOut, ...]
    unit_locked_by: dict[str, int] = Field(
        description="Rows recording quantities in this unit. Non-empty = unit is read-only."
    )
    retire_blocked_by: dict[str, int]


class IngredientPriceIn(In):
    pack_size: QtyStr
    pack_unit: UnitStr
    pack_cost_pence: int = Field(ge=0, le=10_000_000)
    source: PriceSourceStr = Field(
        description="Where the price is from. ESTIMATE keeps it flagged; only a real "
        "source (INVOICE / SUPPLIER_FEED) clears the estimate flag."
    )
    supplier_id: int | None = None
    note: str | None = Field(default=None, max_length=400)


class IngredientPricePreviewIn(IngredientPriceIn):
    window_days: int = Field(default=30, ge=1, le=365)


class IngredientPriceApplyIn(IngredientPriceIn):
    actor: str = ACTOR
    apply_from: date | None = APPLY_FROM


class IngredientPricePreviewOut(Out):
    ingredient_id: int
    unit: str
    unit_cost_before: Cost
    unit_cost_after: Cost
    diff: tuple[str, ...]
    impact: ChangeImpactOut
    at: datetime
    writes_nothing: bool = True


class IngredientPriceAppliedOut(Out):
    ingredient_id: int
    price_id: int
    effective_from: datetime
    unit_cost: Cost
    rollup_items_recosted: int
    rollup_summary: str


class IngredientCreateIn(In):
    name: str = Field(min_length=1, max_length=160)
    unit: UnitStr
    category: str | None = Field(default=None, max_length=80)
    storage: Literal["AMBIENT", "CHILLED", "FROZEN"] = "AMBIENT"
    shelf_life_days: int | None = Field(default=None, ge=1, le=3650)
    open_life_days: int | None = Field(
        default=None, ge=1, le=3650, description="Life once opened; not above shelf life."
    )
    transit_buffer_days: int = Field(default=0, ge=0, le=60)
    waste_factor: QtyStr = Field(
        default="0", description="Fraction lost in use, 0 to 0.5. Stock depletion only."
    )
    note: str | None = Field(default=None, max_length=400)
    price: IngredientPriceIn | None = None
    sku: str | None = Field(
        default=None,
        max_length=80,
        description="Supplier code. When price.supplier_id is set the ingredient is also "
        "linked at that supplier (the preferred link, since it is the first).",
    )
    actor: str = ACTOR


class IngredientMetaIn(In):
    actor: str = ACTOR
    name: str | None = Field(default=None, max_length=160)
    category: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=400)
    unit: UnitStr | None = None


class IngredientWriteOut(Out):
    ingredient_id: int
    summary: str
