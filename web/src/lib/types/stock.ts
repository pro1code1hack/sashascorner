/**
 * Wire types for the Stock, Orders and Suppliers screens.
 *
 * These mirror cafeops/api/schemas.py (StockRow, SupplierOrderOut, ...) and
 * cafeops/api/areas/stock_schemas.py exactly. Conventions (api/encoding.py):
 *  - quantities are decimal STRINGS, handled with lib/dec, never Number();
 *  - money is integer pence (`number`) where the DB stores an integer, and an
 *    exact decimal string of pence (`PenceStr`) where it is derived;
 *  - a missing cost is null, never 0 (invariant 8);
 *  - a withheld forecast has no number, only reasons (invariant 9).
 */
import type { Cost, DriftAttribution, Forecast, OnHand, PriceSource, Storage, Tier, Unit } from '../types'

export type { Cost, DriftAttribution, Forecast, OnHand, Tier, Unit }

export type Qty = string
export type PenceStr = string
export type ISODate = string
export type ISODateTime = string

/* ------------------------------------------------------------------ stock -- */

export type TrustLabel = 'trusted' | 'drifting' | 'excluded' | 'not_yet_judged' | 'never_counted'

export interface ShelfLife {
  storage: Storage
  /** null = DOES NOT EXPIRE (a statement, not a gap). */
  shelf_life_days: number | null
  open_life_days: number | null
  transit_buffer_days: number
  usable_days: number | null
  is_perishable: boolean
  source: PriceSource | null
}

export interface Batch {
  batch_id: number
  qty_remaining: Qty
  unit: Unit
  received_at: ISODateTime
  expires_at: ISODateTime | null
  opened_at: ISODateTime | null
  effective_expiry: ISODateTime | null
  days_left: number | null
  unit_cost_pence: PenceStr | null
  value_pence: PenceStr | null
  qty_received?: Qty | null
  expiry_assumed?: boolean
  received_by?: string | null
}

export interface Drift {
  has_observation: boolean
  observed_at: ISODateTime | null
  theoretical_qty: Qty | null
  counted_qty: Qty | null
  /** (theoretical − counted) / counted × 100. The screen shows the negation. */
  drift_pct: number | null
  verdict: 'ELIGIBLE' | 'TUNE_WASTE_FACTOR' | 'FORCE_MANUAL' | null
  mean_abs_drift_pct: number | null
  observation_count: number
  auto_order_enabled: boolean
  auto_order_reason: string | null
  clean_streak: number
  required_streak: number
  gate_action: string | null
  trust_status: 'trusted' | 'drifting' | 'excluded' | null
  attribution: DriftAttribution | null
}

export interface RunOut {
  forecast: Forecast | null
  daily_rate_qty: Qty | null
  days: Qty | null
  on: ISODate | null
  is_out_of_stock: boolean
  note: string | null
}

export interface ChecklistState {
  status: 'OK' | 'LOW'
  responded_at: ISODateTime
  responded_by: string
}

export interface SinceCount {
  counted_at: ISODateTime
  delivered: Qty
  sold: Qty
  wasted: Qty
  expired: Qty
  adjusted: Qty
  days: number
}

export interface Par {
  min_qty: Qty
  max_qty: Qty
  safety_days: Qty
  min_qty_set_by: string | null
  min_qty_set_at: ISODateTime | null
}

export interface StockRow {
  ingredient_id: number
  name: string
  tier: Tier
  unit: Unit
  tracking_enabled: boolean
  waste_factor: Qty
  unit_cost: Cost
  on_hand: OnHand
  shelf_life: ShelfLife
  batches: Batch[]
  batch_qty: Qty
  batch_coverage_gap: Qty
  soonest_expiry_days: number | null
  is_short_dated: boolean
  drift: Drift
  run_out: RunOut
  category?: string | null
  trust_label?: TrustLabel
  checklist?: ChecklistState | null
  since_count?: SinceCount | null
  par?: Par | null
  /** Preferred (else cheapest) supplier pack, for "≈ 3 bottles". Display only. */
  pack?: StockPack | null
}

export interface StockPack {
  supplier_product_id: number
  supplier_id: number
  supplier_name: string
  pack_size: Qty
  pack_unit: Unit
  /** The pack in the ingredient's own unit; null across dimensions. */
  size_in_unit: Qty | null
  price_pence: number
  is_preferred: boolean
}

export interface WrittenOff {
  month: string
  count: number
  value: Cost
}

export interface StockSummary {
  ingredients: number
  unanchored: number
  negative: number
  short_dated: number
  unbatched: number
  auto_order_enabled: number
  forced_manual: number
  expiring_value_pence: PenceStr | null
  notes: string[]
  short_dated_batches?: number
  written_off_month?: WrittenOff | null
}

export interface StockResponse {
  as_of: ISODateTime
  basis: string
  summary: StockSummary
  rows: StockRow[]
}

export interface CountHistoryRow {
  stock_count_id: number
  counted_at: ISODateTime
  counted_qty: Qty
  counted_by: string
  /** null = a first count (an anchor), or no observation. */
  drift_pct: number | null
}

export interface StockDetail {
  row: StockRow
  drift_history: unknown[]
  suggested_waste_factor: Qty | null
  templates_using: string[]
  counts?: CountHistoryRow[]
}

export interface CountIn {
  counted_qty: Qty
  counted_by: string
  counted_at?: ISODateTime
  note?: string
}

export interface CountOut {
  ingredient_id: number
  ingredient_name: string
  unit: Unit
  stock_count_id: number
  counted_qty: Qty
  counted_at: ISODateTime
  counted_by: string
  theoretical_before: OnHand
  drift_pct: number | null
  verdict: string | null
  trust_label: TrustLabel
  auto_order_enabled: boolean
  gate_reason: string
  alert_level: 'NONE' | 'NOTICE' | 'ALARM' | string
  attribution: DriftAttribution | null
  reconciliation_note: string | null
  notes: string[]
}

export interface DeliveryIn {
  qty: Qty
  received_by: string
  expires_on?: ISODate
  unit_cost_pence?: PenceStr
  note?: string
}

export interface DeliveryOut {
  batch_id: number
  ingredient_id: number
  ingredient_name: string
  qty: Qty
  unit: Unit
  received_at: ISODateTime
  expires_at: ISODateTime | null
  expiry_assumed: boolean
  warnings: string[]
  order_completed: boolean
}

export type WriteOffReason = 'WENT_OFF' | 'SPILLED' | 'STAFF' | 'OTHER'

export interface WriteOffIn {
  qty: Qty
  reason: WriteOffReason
  recorded_by: string
  note?: string
}

export interface WriteOffOut {
  ingredient_id: number
  ingredient_name: string
  unit: Unit
  qty: Qty
  reason: WriteOffReason
  movement_type: 'WASTE' | 'STAFF'
  movement_ids: number[]
  batches_drawn: { batch_id: number; qty: Qty }[]
  shortfall_qty: Qty
  value: Cost
}

export interface ChecklistIn {
  status: 'OK' | 'LOW'
  responded_by: string
}

export interface ChecklistOut {
  ingredient_id: number
  ingredient_name: string
  status: 'OK' | 'LOW'
  responded_at: ISODateTime
  responded_by: string
}

export interface ParIn {
  min_qty: Qty
  changed_by: string
}

export interface ParChangeOut {
  ingredient_id: number
  ingredient_name: string
  min_qty_before: Qty
  min_qty_after: Qty
  max_qty: Qty
  auto_order_enabled: boolean
  set_by: string
  set_at: ISODateTime
}

export interface TierIn {
  tier: Tier
  changed_by: string
  /** Optional: the owner dropped the "why" prompt (DECISIONS 22). */
  reason?: string
}

export interface TierOut {
  ingredient_id: number
  ingredient_name: string
  tier_before: Tier
  tier_after: Tier
  would_clear_gate: boolean
  clean_streak: number
  required_streak: number
  auto_order_enabled: boolean
  tracking_enabled: boolean
  note: string
}

/* ----------------------------------------------------------------- orders -- */

export interface Supplier {
  supplier_id: number
  name: string
  lead_time_days: number
  /** ISO 1..7; EMPTY means any day (walk-in retail). */
  delivery_weekdays: number[]
  min_order_pence: number
  order_channel: OrderChannel
  cutoff_time: string | null
  delivery_fee_pence: number
  free_delivery_threshold_pence: number | null
  terms_are_placeholders: boolean
  kind?: string | null
  contact?: string | null
  order_url?: string | null
  notes?: string | null
  email?: string | null
  phone?: string | null
  product_count?: number | null
  archived?: boolean
}

export type OrderChannel = 'PORTAL' | 'EMAIL' | 'EDI' | 'MANUAL' | 'BROWSER_AGENT'

export interface OrderLine {
  ingredient_id: number
  ingredient_name: string
  unit: Unit
  packs: number
  pack_size: Qty
  pack_unit: Unit
  pack_price_pence: number
  line_total_pence: number
  sku: string
  /** "withheld" when the forecast is low-confidence. */
  need_qty: string
  on_hand_qty: Qty
  on_open_pos_qty: Qty
  resulting_on_hand_qty: string
  forecast: Forecast
  cover_days: number | null
  full_cover_days: number | null
  forecast_full_window_qty: Qty | null
  capped_out_qty: Qty | null
  cap_reason: string | null
  cap_detail: string | null
  is_capped: boolean
  is_top_up: boolean
  clamped: string | null
  cap_kind?: 'SHELF_LIFE' | 'SEASON_END' | 'OUT_OF_SEASON' | 'OTHER' | null
  daily_rate_qty?: Qty | null
  supplier_product_id?: number | null
}

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

export interface TopUpCandidate {
  ingredient_id: number
  name: string
  supplier_product_id: number
  pack_size: Qty
  pack_unit: Unit
  pack_price_pence: number
  cover_days: Qty | null
}

export type POStatus = 'DRAFT' | 'PENDING_CONFIRM' | 'CONFIRMED' | 'SENT' | 'RECEIVED' | 'CANCELLED'

export interface PersistedLine {
  po_line_id: number
  ingredient_id: number
  ingredient_name: string
  unit: Unit
  suggested_packs: number
  final_packs: number
  unit_price_pence: number
  pack_size: Qty
  pack_unit: Unit
  received_qty: Qty | null
  received_expires_at: ISODateTime | null
  cap_reason: string | null
  is_top_up: boolean
  checklist_requested_by: string | null
}

export interface PersistedOrder {
  po_id: number
  status: POStatus
  created_at: ISODateTime
  target_delivery_date: ISODate
  confirmed_by: string | null
  confirmed_at: ISODateTime | null
  sent_at: ISODateTime | null
  sent_by: string | null
  total_pence: number
  delivery_fee_pence: number
  lines: PersistedLine[]
}

export interface SupplierOrder {
  supplier: Supplier
  target_delivery_date: ISODate
  cover_window_days: number
  cover_window_from: ISODate | null
  cover_window_to: ISODate | null
  lead_time_days: number
  days_until_next_delivery: number
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
  order_by_date?: ISODate | null
  next_delivery_date?: ISODate | null
  fee_applies?: boolean
  top_up_candidates?: TopUpCandidate[]
  persisted?: PersistedOrder | null
}

export interface EmergencyLine {
  ingredient_id: number
  ingredient_name: string
  qty: Qty
  unit: Unit
  reason: string
  retail_unit_price_pence: PenceStr | null
  preferred_unit_price_pence: PenceStr | null
  premium_pence: PenceStr | null
  raw_premium_pence: PenceStr | null
  retail_is_cheaper: boolean
}

export interface NamedIngredient {
  ingredient_id: number
  name: string
}

export interface DraftOrdersResponse {
  order_date: ISODate
  computed_at: ISODateTime
  reorder_cadence_days: number | null
  tiers: string[]
  suppliers: SupplierOrder[]
  sourcing_choices: unknown[]
  emergency: EmergencyLine[]
  emergency_total_premium_pence: PenceStr | null
  emergency_notes: string[]
  total_pence: number
  capped_line_count: number
  placeholder_supplier_names: string[]
  notes: string[]
  writes_nothing: boolean
  uncounted?: NamedIngredient[]
  unsourced?: NamedIngredient[]
  supplier_count?: number
}

export type OrderAction = 'confirm' | 'cancel' | 'mark_sent' | 'receive'

export interface PurchaseOrder extends PersistedOrder {
  supplier_id: number
  supplier_name: string
  supplier_archived: boolean
  terms_are_placeholders: boolean
  cancelled_at: ISODateTime | null
  cancelled_by: string | null
  cancel_reason: string | null
  routing_reason: string | null
  /** Photo of the delivery note / receipt, when one was added. */
  receipt_url?: string | null
  receipt_uploaded_by?: string | null
  actions: OrderAction[]
}

export interface OrdersListResponse {
  orders: PurchaseOrder[]
  counts: { open: number; waiting: number }
}

export interface ReceiveIn {
  received_by: string
  lines: { po_line_id: number; received_packs?: number; received_qty?: Qty; expires_on?: ISODate }[]
}

export interface ReceiveOut {
  order: PurchaseOrder
  receipts: DeliveryOut[]
}

export interface ShopRunIn {
  bought_by: string
  where: string
  reason?: string
  lines: { ingredient_id: number; qty: Qty; paid_pence?: number; expires_on?: ISODate }[]
}

export interface ShopRunOut {
  routing_ids: number[]
  receipts: DeliveryOut[]
  paid_pence: number | null
  premium_pence: number | null
}

export interface ShopRunRow {
  occurred_at: ISODateTime
  where: string
  note: string
  ingredient_name: string | null
  amount_pence: number | null
  premium_pence: number | null
  source: 'tesco_routing' | 'expense'
  bought_by: string | null
}

export interface ShopRunMonth {
  month: string
  amount_pence: number | null
  priced_amount_pence: number
  runs: number
}

export interface ShopRunsResponse {
  runs: ShopRunRow[]
  by_month: ShopRunMonth[]
  total_pence: number | null
  priced_total_pence: number
  priced_runs: number
  runs_count: number
  premium_total_pence: number
  notes: string[]
}

/* -------------------------------------------------------------- suppliers -- */

export interface SupplierWriteOut {
  supplier: Supplier
  changed: string[]
  restarred: string[]
}

export interface SupplierProduct {
  supplier_product_id: number
  supplier_id: number
  ingredient_id: number
  ingredient_name: string
  ingredient_unit: Unit
  sku: string
  pack_size: Qty
  pack_unit: Unit
  price_pence: number
  unit_price_pence: PenceStr | null
  price_source: PriceSource | null
  is_preferred: boolean
  moq_packs: number
  last_seen_price_at: ISODateTime | null
  vs_best_other: { supplier_name: string; unit_price_pence: PenceStr; diff_pct: number } | null
}

export interface IngredientOption {
  ingredient_id: number
  name: string
  unit: Unit
  category: string | null
}

export interface SupplierProductsResponse {
  supplier: Supplier
  products: SupplierProduct[]
  ingredients: IngredientOption[]
}

export interface ProductWriteOut {
  product: SupplierProduct
  changed: string[]
  recosted_items: number
  price_row_id: number | null
  restarred: string | null
}

export interface SupplierCreateIn {
  name: string
  created_by: string
  order_channel?: OrderChannel
  kind?: string | null
  contact?: string | null
  order_url?: string | null
  notes?: string | null
  email?: string | null
  phone?: string | null
  /** Terms, optional: all or none. Stored as a guess unless `terms_confirmed`. */
  lead_time_days?: number
  delivery_weekdays?: number[]
  min_order_pence?: number
  delivery_fee_pence?: number
  cutoff_time?: string | null
  free_delivery_threshold_pence?: number | null
  terms_confirmed?: boolean
}

export interface SupplierPatchIn {
  changed_by: string
  name?: string
  order_channel?: OrderChannel
  kind?: string | null
  contact?: string | null
  order_url?: string | null
  notes?: string | null
  email?: string | null
  phone?: string | null
}
