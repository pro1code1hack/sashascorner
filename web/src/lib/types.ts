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
