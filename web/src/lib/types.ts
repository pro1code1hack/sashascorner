/**
 * Types transcribed from `web/fixtures/*.json` — the real responses of
 * `cafeops api-fixtures`, which is the contract.
 *
 * These deliberately do NOT match CLAUDE.md §10.9's sketch: the API nests
 * `Cost`, `OnHand` and `Forecast` into types that carry their own rules, which
 * is what makes invariants 6, 8 and 9 impossible for a view to drop.
 * ARCHITECTURE.md §8H has the field-by-field mapping and the reasoning.
 *
 * Two shapes to read carefully:
 *   Cost.pence  — `string | null`. NULL, never 0, when the price is missing.
 *   Forecast    — `qty` is ABSENT from the payload when confidence is low.
 *                 There is no number to render, so the type does not offer one
 *                 without a null check.
 */

export type Unit = 'L' | 'KG' | 'ML' | 'G' | 'EACH'
export type Tier = 'A' | 'B' | 'C'
export type SizeCode = 'S' | 'M' | 'XL' | 'ONE'
export type ComponentRole =
  | 'COFFEE'
  | 'MILK'
  | 'BASE'
  | 'FLAVOUR'
  | 'TOPPING'
  | 'PACKAGING'
  | 'SUNDRY'
export type PriceSource = 'INVOICE' | 'ESTIMATE' | 'SUPPLIER_FEED'
export type Storage = 'AMBIENT' | 'CHILLED' | 'FROZEN'
export type DriftVerdict = 'ELIGIBLE' | 'TUNE_WASTE_FACTOR' | 'FORCE_MANUAL'
export type DriftCause = 'NEGLIGIBLE' | 'EXPIRY' | 'MEASUREMENT' | 'MIXED'
/** Already mapped from the domain vocabulary by the API. Do not re-derive it. */
export type TrustStatus = 'trusted' | 'drifting' | 'excluded'

/** Invariant 8. A missing cost is null and is excluded from aggregates. */
export interface Cost {
  pence: string | null
  source: PriceSource | null
  is_estimate: boolean
  is_missing: boolean
  excluded_from_aggregates: boolean
  note: string | null
}

/** Invariant 6. Both flags are required, not optional. */
export interface OnHand {
  qty: string
  unit: Unit
  as_of: string
  is_theoretical: boolean
  has_count_basis: boolean
  basis_count_qty: string | null
  basis_counted_at: string | null
  movement_sum: string
  movement_count: number
  basis_label: string
  is_negative: boolean
}

/** Invariant 9. `qty` is absent when low-confidence; `reasons` carries what to
 *  render in its place. */
export interface Forecast {
  qty?: string | null
  unit: Unit
  window_days: number
  is_low_confidence: boolean
  reasons: string[]
  used_flat_average: boolean
  history_days: number
}

export interface ShelfLife {
  storage: Storage
  shelf_life_days: number | null
  open_life_days: number | null
  transit_buffer_days: number
  usable_days: number | null
  is_perishable: boolean
  source: PriceSource
}

export interface Batch {
  batch_id: number
  qty_remaining: string
  unit: Unit
  received_at: string
  expires_at: string | null
  opened_at: string | null
  effective_expiry: string | null
  days_left: number | null
  unit_cost_pence: string | null
  value_pence: string | null
}

export interface DriftAttribution {
  cause: DriftCause
  headline: string
  action: string
  gap_qty: string
  expired_qty: string
  measurement_qty: string
  expiry_qty: string
  expiry_share: number
  unexplained_loss_qty: string
  surplus_qty: string
  surplus_note: string | null
  loss_pct_of_consumption: number
}

export interface Drift {
  has_observation: boolean
  observed_at: string | null
  theoretical_qty: string | null
  counted_qty: string | null
  drift_pct: number | null
  verdict: DriftVerdict | null
  mean_abs_drift_pct: number | null
  observation_count: number
  auto_order_enabled: boolean
  auto_order_reason: string
  clean_streak: number
  required_streak: number
  gate_action: string
  trust_status: TrustStatus | null
  attribution: DriftAttribution | null
}

export interface RunOut {
  forecast: Forecast
  daily_rate_qty: string | null
  days: string | null
  on: string | null
  is_out_of_stock: boolean
  note: string | null
}

export interface StockRow {
  ingredient_id: number
  name: string
  tier: Tier
  unit: Unit
  tracking_enabled: boolean
  waste_factor: string
  unit_cost: Cost | null
  on_hand: OnHand
  shelf_life: ShelfLife
  batches: Batch[]
  batch_qty: string
  batch_coverage_gap: string
  soonest_expiry_days: number | null
  is_short_dated: boolean
  drift: Drift
  /** Absent entirely on the `run_out=false` variant. */
  run_out: RunOut | null
}

export interface StockSummary {
  ingredients: number
  unanchored: number
  negative: number
  short_dated: number
  unbatched: number
  auto_order_enabled: number
  forced_manual: number
  expiring_value_pence: string
  notes: string[]
}

export interface StockResponse {
  as_of: string
  basis: string
  summary: StockSummary
  rows: StockRow[]
}

export interface DriftObservation {
  observed_at: string
  theoretical_qty: string
  counted_qty: string
  drift_pct: number
  waste_factor_at_count: string
  expired_qty_in_window: string
}

export interface StockDetailResponse {
  row: StockRow
  drift_history: DriftObservation[]
  suggested_waste_factor: string | null
  templates_using: string[]
}

/* ------------------------------ composition ----------------------------- */

export interface TemplateSummary {
  id: number
  name: string
  category: string
  sizes: SizeCode[]
  axes: string[]
  item_count: number
  component_count: number
  prep_seconds_by_size: Record<string, number>
  prep_seconds_is_estimate: boolean
}

export interface ComponentRow {
  component_id: number
  role: ComponentRole
  ingredient_id: number | null
  ingredient_name: string | null
  unit: Unit | null
  qty_by_size: Record<string, string>
  is_substitutable: boolean
  is_required: boolean
  effective_from: string
  ingredient_cost: Cost | null
}

export interface AxisOption {
  option_id: number
  name: string
  role: ComponentRole
  ingredient_id: number | null
  ingredient_name: string | null
  qty_override: string | null
  price_delta_pence: number
  season_id: number | null
  season_name: string | null
}

export interface Axis {
  axis_id: number
  name: string
  role: ComponentRole
  is_required: boolean
  options: AxisOption[]
}

export interface Modifier {
  modifier_id: number
  name: string
  action: string
  target_role: ComponentRole
  ingredient_id: number | null
  ingredient_name: string | null
  qty_delta: string | null
  qty_multiplier: string | null
  price_pence: number
}

export interface MenuItemRow {
  menu_item_id: number
  name: string
  size_code: SizeCode
  price_pence: number
  cost: Cost
  margin_pct: number | null
  true_margin_pct: number | null
  margin_per_minute_pence: string | null
  prep_seconds: number | null
  prep_source: string | null
  prep_is_estimate: boolean
  is_available_today: boolean
  availability: string
  availability_reasons: string[]
}

export interface TemplateDetailResponse {
  template: TemplateSummary
  as_of: string
  components: ComponentRow[]
  axes: Axis[]
  modifiers: Modifier[]
  items: MenuItemRow[]
  warnings: string[]
}

export interface PreviewItem {
  menu_item_id: number
  name: string
  size_code: SizeCode
  price_pence: number
  cost_before: Cost
  cost_after: Cost
  cost_delta_pence: string | null
  margin_pct_before: number | null
  margin_pct_after: number | null
}

export interface PreviewLabourItem {
  menu_item_id: number
  label: string
  prep_seconds: number | null
  prep_is_estimate: boolean
  labour_cost_pence: string | null
  true_margin_before_pence: string | null
  true_margin_after_pence: string | null
  true_margin_delta_pence: string | null
  margin_per_minute_before_pence: string | null
  margin_per_minute_after_pence: string | null
  margin_per_minute_delta_pence: string | null
}

export interface PreviewResponse {
  template_id: number
  component_id: number
  component_role: ComponentRole
  ingredient_name: string | null
  qty_by_size_before: Record<string, string>
  qty_by_size_after: Record<string, string>
  at: string
  window_days: number
  preview: {
    affected_item_count: number
    items: PreviewItem[]
    /** null when the delta is not uniform; the range is sent instead. */
    cost_delta_pence_per_item: string | null
    cost_delta_pence_range: [string, string] | null
    monthly_cogs_delta_pence: string | null
    worst_margin_after: PreviewItem | null
    warnings: string[]
  }
  labour: {
    items: PreviewLabourItem[]
    labour_cost_pence_per_item: string | null
    labour_cost_pence_range: [string, string] | null
    true_margin_delta_pence_per_item: string | null
    true_margin_delta_pence_range: [string, string] | null
    labour_cost_window_pence: string | null
    untimed_count: number
    rank_moves: unknown[]
    warnings: string[]
  }
  writes_nothing: boolean
}

/** 409 from the preview endpoint: the slot was superseded by another edit. */
export interface SupersededError {
  detail: {
    error: string
    message?: string
    current_component_id?: number
    [k: string]: unknown
  }
}

export interface MetaResponse {
  money_encoding: string
  quantity_encoding: string
  timestamp_encoding: string
  loaded_hourly_rate_pence: number
  local_timezone: string
  invariant_notes: string[]
}

/* ------------------------------------------------------- orders / draft --- */

/** A supplier's terms. `terms_are_placeholders` is the load-bearing field:
 *  6 of 8 suppliers' terms were invented, and every cover window built on them
 *  inherits that. The flag travels with the data so no screen can forget it. */
export interface Supplier {
  supplier_id: number
  name: string
  lead_time_days: number
  delivery_weekdays: number[]
  min_order_pence: number
  order_channel: string
  cutoff_time: string | null
  delivery_fee_pence: number
  free_delivery_threshold_pence: number | null
  terms_are_placeholders: boolean
}

export interface LineForecast {
  qty: string
  unit: Unit
  window_days: number
  is_low_confidence: boolean
  reasons: string[]
  used_flat_average: boolean
  history_days: number
}

export interface OrderLine {
  ingredient_id: number
  ingredient_name: string
  unit: Unit
  packs: number
  pack_size: string
  pack_unit: Unit
  pack_price_pence: number
  line_total_pence: number
  sku: string
  need_qty: string
  on_hand_qty: string
  on_open_pos_qty: string
  resulting_on_hand_qty: string
  forecast: LineForecast
  cover_days: number
  full_cover_days: number
  forecast_full_window_qty: string | null
  capped_out_qty: string | null
  cap_reason: string | null
  cap_detail: string | null
  is_capped: boolean
  is_top_up: boolean
  clamped: string | null
}

/** An ingredient deliberately NOT ordered, and why. This is not an error list:
 *  a shelf-life cap skipping 6.084 L of milk is the system working. */
export interface SkippedLine {
  ingredient_id: number
  ingredient_name: string
  reason: string
  below_par_floor: boolean
  out_of_season: boolean
  is_capped: boolean
  cap_reason: string | null
  data_error: string | null
  clamp_blocked: string | null
}

export interface SupplierOrder {
  supplier: Supplier
  target_delivery_date: string | null
  cover_window_days: number
  cover_window_from: string | null
  cover_window_to: string | null
  lead_time_days: number
  days_until_next_delivery: number | null
  lines: OrderLine[]
  skipped: SkippedLine[]
  subtotal_pence: number
  delivery_fee_pence: number
  total_pence: number
  meets_minimum: boolean
  min_order_topped_up: boolean
  capped_line_count: number
  low_confidence_line_count: number
  status: string
  notes: string[]
}

/** A line moved off the preferred supplier, with the saving that justified it
 *  — or the saving forgone by staying put. */
export interface SourcingChoice {
  ingredient_id: number
  ingredient_name: string
  chosen_supplier_id: number
  chosen_supplier_name: string
  chosen_is_preferred: boolean
  chosen_unit_price_pence: string | number | null
  reason: string
  cheaper_rejected_supplier_id: number | null
  cheaper_rejected_unit_price_pence: string | number | null
  forgone_saving_pence: number | null
  alternative_count: number
}

export interface EmergencyLine {
  ingredient_id: number
  ingredient_name: string
  reason: string
  premium_pence: number
  raw_premium_pence: number
  retail_is_cheaper: boolean
}

export interface OrdersDraftResponse {
  order_date: string
  computed_at: string
  reorder_cadence_days: number | null
  tiers: string[]
  suppliers: SupplierOrder[]
  sourcing_choices: SourcingChoice[]
  emergency: EmergencyLine[]
  emergency_total_premium_pence: string | number | null
  emergency_notes: string[]
  total_pence: number
  capped_line_count: number
  placeholder_supplier_names: string[]
  notes: string[]
  /** The draft is a read. Confirming it in Telegram is what writes. */
  writes_nothing: boolean
}

/* --------------------------------------------------------------- margin --- */

export interface MarginRow {
  menu_item_id: number
  name: string
  size_code: string
  label: string
  template_id: number | null
  template_name: string | null
  price_pence: number
  cost: Cost
  /** A string, not a number -- `schemas.py:855` types it `str`. */
  units_sold: string
  prep_seconds: number | null
  prep_source: string | null
  prep_is_estimate: boolean
  /* The four *_pence fields below are EXACT DECIMAL STRINGS of pence
   * ("42.291667"), not integers. Do not pass them to `money()`/`pence()`:
   * `fromMoney()` raises "integer pence expected" on a fractional number, and
   * rounding them to integers before summing loses money per item. Parse with
   * `lib/dec.ts` and format at the edge. */
  labour_cost_pence: string | null
  /** A percentage as a JSON float (91.577), not a pence string. */
  margin_pct: number | null
  /** Margin after labour, as a JSON float. */
  true_margin_pct: number | null
  true_margin_pence: string | null
  contribution_pence: string | null
  margin_per_minute_pence: string | null
}

export interface RankedRow {
  menu_item_id: number
  label: string
  margin_rank: number
  margin_per_minute_rank: number
  rank_delta: number
  /** A JSON float, as on `MarginRow`. */
  margin_pct: number | null
  /** An exact decimal string of pence. Never `money()`. */
  margin_per_minute_pence: string | null
  prep_seconds: number | null
}

export interface UncostedItem {
  menu_item_id: number
  name: string
  size_code: string
  price_pence: number
  active: boolean
  cost: Cost
  reason: string
}

/**
 * A costed rollup over a set of menu items -- one template, or the whole menu.
 * Every `*_total` is a string because these are exact decimal sums of pence, not
 * integers: rounding them to pence before summing loses money per item.
 *
 * `is_complete` false means `excluded` is non-empty and the totals therefore
 * cover only `items_included` of `items_total`. Invariant 8: the excluded items
 * are NOT counted as zero, so any per-item average taken from these totals must
 * divide by `items_included`.
 */
export interface MenuRollup {
  label: string
  /** null on the whole-menu rollup. */
  template_id: number | null
  items_total: number
  items_included: number
  units_total: string
  staff_hours: string
  prep_seconds_total: string
  labour_cost_pence_total: string
  ingredient_cost_pence_total: string
  revenue_pence_total: string
  contribution_pence_total: string
  true_margin_pence_total: string
  margin_per_minute_pence: string | null
  labour_share_of_revenue_pct: string | null
  is_complete: boolean
  /** `[item label, why it could not be costed or ranked]`. */
  excluded: string[][]
  /** The rollup stated as a sentence by the backend. Safe to print verbatim. */
  summary: string
}

export interface MarginResponse {
  window_days: number
  since: string
  until: string
  loaded_hourly_rate_pence: number
  rate_is_set: boolean
  items_costed: number
  rankable_count: number
  /** False means ranking by margin % and by margin per minute disagree — which
   *  is the whole point of the screen. */
  orderings_agree: boolean
  by_margin_pct: MarginRow[]
  by_margin_per_minute: MarginRow[]
  ranked: RankedRow[]
  biggest_disagreements: RankedRow[]
  excluded: string[][]
  uncosted_items: UncostedItem[]
  /** The whole menu as one rollup. Not a sum of `by_template`. */
  menu: MenuRollup
  by_template: MenuRollup[]
  /** How many of the ranked items are costed from ESTIMATE prices vs invoices.
   *  280 of 298 are estimates today, so "estimated" is the ambient condition and
   *  the aggregate COGS figure is not trustworthy to the penny. */
  estimated_cost_item_count: number
  invoice_cost_item_count: number
  /** Backend-authored caveats about these figures. Print them; do not summarise
   *  them away -- each one names a specific reason a total is narrower than it
   *  looks. */
  warnings: string[]
}

/* ------------------------------------------------------------- channels --- */

export interface ChannelsResponse {
  since: string
  until: string
  channels_present: string[]
  sources_present: string[]
  performance: unknown[]
  findings: unknown[]
  notes: string[]
}

/* --------------------------------------------------------------- health --- */

export interface HealthResponse {
  status: string
  database_dialect: string
  auth_configured: boolean
  /* The counts are **null to an unauthenticated caller**. `/api/health` has to
   * answer before anyone has the password -- otherwise "is it up?" and "is my
   * password right?" become the same question -- but a liveness probe does not
   * need the size of the inventory, and this runs on a real domain. Null means
   * "not disclosed", never zero. */
  ingredients: number | null
  menu_items: number | null
  templates: number | null
  movements: number | null
}

/* ------------------------------------------------- confirmations (writes) --- */

/**
 * Terms confirmed WITH a supplier. All of them together, on purpose: the cover
 * window is computed from several at once, so a partial confirmation would clear
 * the invented-terms warning on an order that is still partly fiction.
 */
export interface SupplierTermsIn {
  lead_time_days: number
  /** ISO 1..7, at least one. A supplier with no delivery day can never satisfy a
   *  cover window; a walk-in supplier delivers every day. */
  delivery_weekdays: number[]
  min_order_pence: number
  delivery_fee_pence: number
  /** "HH:MM", or null for none. */
  cutoff_time: string | null
  free_delivery_threshold_pence: number | null
}

export interface SupplierTermsResponse {
  supplier_id: number
  name: string
  /** True if this call is what cleared the invented-terms warning. */
  was_placeholder: boolean
  /** Which terms actually moved, as "label: before -> after". */
  changed: string[]
  supplier: Supplier
}

export interface ShelfLifeIn {
  shelf_life_days: number
  open_life_days: number | null
  /** "supplier" (they told you) or "packaging" (you read it off the pack).
   *  "estimate" is refused -- it is the default this call leaves behind. */
  source: 'supplier' | 'packaging'
}

export interface ShelfLifeResponse {
  ingredient_id: number
  name: string
  shelf_life_days_before: number | null
  shelf_life_days_after: number
  open_life_days_after: number | null
  transit_buffer_days: number
  source_before: string | null
  source_after: string
  /** What actually caps an order: shelf life less the transit buffer. */
  usable_days_after: number
  /** How far the ORDER CAP moved -- the consequence worth showing. Null when
   *  there was no previous shelf life to compare against. */
  usable_days_changed_by: number | null
}

/* --------------------------------------------------- import review (spec 6) --- */

export interface ProposalComponent {
  role: string
  /** Null when the slot is filled by a variant axis rather than a fixed ingredient. */
  ingredient_name: string | null
  /** Size code -> quantity as an exact decimal STRING. Never a float. */
  qty_by_size: Record<string, string>
}

export interface ProposalAxis {
  name: string
  role: string
  /** Option name -> ingredient name. */
  options: Record<string, string>
  option_count: number
}

/** Legacy rows that disagree about one quantity. A human decides which is right. */
export interface ProposalConflict {
  role: string
  ingredient_name: string
  size_code: string | null
  /** Menu item name -> the quantity it uses. */
  quantities: Record<string, string>
  /** The conflict as one sentence, written by the backend. Print it. */
  describe: string
}

export interface Proposal {
  /** The stable, unambiguous handle. NAMES ARE NOT UNIQUE -- two pairs collide
   *  today. Always confirm by this. */
  proposal_id: string
  name: string
  category: string | null
  menu_item_count: number
  base_item_names: string[]
  sizes: string[]
  components: ProposalComponent[]
  axes: ProposalAxis[]
  conflicts: ProposalConflict[]
  /** No components and no axes: there is no recipe here, and confirming is refused. */
  is_hollow: boolean
  /** Another proposal shares this name. They are different recipes. */
  name_is_ambiguous: boolean
  already_materialised: boolean
  /** Why this cannot be confirmed as it stands, or null. */
  blocked_reason: string | null
}

export interface ProposalsResponse {
  proposals: Proposal[]
  total: number
  with_conflicts: number
  /** One-off groups are not patterns and stay manual recipes. Counted, not listed. */
  singletons_excluded: number
  writes_nothing: boolean
}

export interface MaterialiseIn {
  actor: string
  /** Accept the LOWEST quantity at each size where the legacy rows disagree.
   *  Arbitrary by construction -- the result needs verifying against the real recipes. */
  allow_conflicts: boolean
}

export interface MaterialiseResponse {
  proposal_name: string
  template_id: number | null
  sizes: string[]
  components: number
  axis_filled_slots: number
  axes: number
  options: number
  items_repointed: number
  manual_lines_closed: number
  accepted_conflicts: number
  items_unresolved_option: string[]
  items_skipped_other_template: string[]
  items_not_found: string[]
  missing_ingredients: string[]
  warnings: string[]
  /** The whole result as one sentence, written by the backend. */
  summary: string
}

/* ------------------------------------------------------------- takings --- */

/**
 * What the cafe took over a window. The missing half of Money & P&L.
 *
 * Every nullable total means "some day in the window did not report it", NEVER
 * zero. `net_pence` is null unless every deduction was reported on every day:
 * subtracting only the days that reported fees yields a net that is too high and
 * entirely plausible, which is the worst kind of wrong (invariant 8).
 */
export interface TakingsResponse {
  since: string
  until: string
  window_days: number
  /** Days that reported at all. The rest are absent, not zero. */
  days_reported: number
  /** Null when nothing reported at all — not a window in which nothing was taken. */
  gross_pence: number | null
  refunds_pence: number | null
  fees_pence: number | null
  discounts_pence: number | null
  net_pence: number | null
  transactions: number | null
  by_method_pence: Record<string, number>
  /** What could not be summed, and why. Print these; do not summarise them. */
  caveats: string[]
  source_note: string
}
