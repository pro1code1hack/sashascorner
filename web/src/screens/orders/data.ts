/**
 * The Orders screen's own data layer.
 *
 * `lib/api.ts` is shared by five agents and carries no orders reader, so this
 * file holds one: the same two rules as the shared one (fixtures are imported
 * rather than fetched, so fixture mode has no network at all; live mode sends
 * the one shared password as `X-API-Key`) and nothing else.
 *
 * There is no write path here, and that is not an omission — invariant 1. The
 * API's only two POST routes are composition preview and apply. Nothing in this
 * module can create, confirm or send a purchase order.
 */
import fxDraft from '../../../fixtures/orders-draft.json'
import fxDraftCapped from '../../../fixtures/orders-draft-capped.json'
import fxDraftEmergency from '../../../fixtures/orders-draft-emergency.json'
import fxSuppliers from '../../../fixtures/suppliers.json'
import { API_BASE, LIVE, getKey } from '../../lib/api'
import { add, fromInt, mustDec, type Dec, ZERO } from '../../lib/dec'

/* ------------------------------------------------------------------ types */

export interface Supplier {
  supplier_id: number
  name: string
  lead_time_days: number
  /** ISO 1..7. EMPTY means any day — walk-in retail. */
  delivery_weekdays: number[]
  min_order_pence: number
  order_channel: string
  cutoff_time: string | null
  delivery_fee_pence: number
  free_delivery_threshold_pence: number | null
  /** Six of eight. ARCHITECTURE.md §8F.4. Every quantity below rests on it. */
  terms_are_placeholders: boolean
}

/** Invariant 9: `qty` is absent when confidence is low, and `reasons` carries
 *  the sentence that goes IN ITS PLACE. */
export interface LineForecast {
  qty?: string | null
  unit: string
  window_days: number
  is_low_confidence: boolean
  reasons: string[]
  used_flat_average: boolean
  history_days: number
}

export interface OrderLine {
  ingredient_id: number
  ingredient_name: string
  unit: string
  packs: number
  pack_size: string
  pack_unit: string
  pack_price_pence: number
  line_total_pence: number
  sku: string
  need_qty: string
  on_hand_qty: string
  on_open_pos_qty: string
  resulting_on_hand_qty: string
  /** Over the EFFECTIVE window — the one a cap may have shortened. */
  forecast: LineForecast
  cover_days: number | null
  full_cover_days: number | null
  forecast_full_window_qty: string | null
  /** Invariant 4, as a number rather than only inside the prose. */
  capped_out_qty: string | null
  cap_reason: string | null
  cap_detail: string | null
  is_capped: boolean
  /** Invariant 5. Bought for the supplier's benefit, never a perishable. */
  is_top_up: boolean
  clamped: string | null
}

export interface SkippedCandidate {
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

export interface EmergencyLine {
  ingredient_id: number
  ingredient_name: string
  qty: string
  unit: string
  reason: string
  retail_unit_price_pence: string | null
  preferred_unit_price_pence: string | null
  /** Extra actually paid. Never negative. Null when unpriced. */
  premium_pence: string | null
  /** Signed. Negative means retail was cheaper all along. */
  raw_premium_pence: string | null
  retail_is_cheaper: boolean
}

export interface SourcingChoice {
  ingredient_id: number
  ingredient_name: string
  chosen_supplier_id: number
  chosen_supplier_name: string | null
  chosen_is_preferred: boolean
  chosen_unit_price_pence: string | null
  reason: string
  cheaper_rejected_supplier_id: number | null
  cheaper_rejected_unit_price_pence: string | null
  forgone_saving_pence: string | null
  alternative_count: number
}

export interface SupplierOrder {
  supplier: Supplier
  target_delivery_date: string
  cover_window_days: number
  cover_window_from: string | null
  cover_window_to: string | null
  lead_time_days: number
  days_until_next_delivery: number
  lines: OrderLine[]
  skipped: SkippedCandidate[]
  subtotal_pence: number
  delivery_fee_pence: number
  total_pence: number
  meets_minimum: boolean
  min_order_topped_up: boolean
  capped_line_count: number
  low_confidence_line_count: number
  /** Always DRAFT, and nothing in this app can change it. Invariant 1. */
  status: string
  notes: string[]
}

export interface DraftOrders {
  order_date: string
  computed_at: string
  reorder_cadence_days: number | null
  tiers: string[]
  suppliers: SupplierOrder[]
  sourcing_choices: SourcingChoice[]
  emergency: EmergencyLine[]
  /** Null rather than a partial sum: a total missing two of five lines
   *  understates the argument it exists to make. */
  emergency_total_premium_pence: string | null
  /** Cases deliberately NOT routed to retail — the half that stops the panic
   *  buy report becoming a licence to shop. */
  emergency_notes: string[]
  total_pence: number
  capped_line_count: number
  placeholder_supplier_names: string[]
  notes: string[]
  writes_nothing: boolean
}

/* ------------------------------------------------------------------- runs */

/**
 * The ordering runs on record. `GET /api/orders/draft` answers for ONE date,
 * and whether a run shows a shelf-life cap or a Tesco emergency depends on that
 * day's stock — so the fixture set deliberately records three days. The screen
 * reads all three because the panic-buy report is an argument about a PATTERN
 * (spec §4.4) and one run cannot make it.
 */
export const RUNS: { date: string; label: string; note: string }[] = [
  { date: '2026-09-17', label: '17 Sep', note: 'a shelf-life cap' },
  { date: '2026-09-21', label: '21 Sep', note: 'a Tesco emergency' },
  { date: '2026-09-23', label: '23 Sep', note: 'the latest run' },
]

export const LATEST_RUN = '2026-09-23'

const FIXTURES: Record<string, unknown> = {
  '2026-09-17': fxDraftCapped,
  '2026-09-21': fxDraftEmergency,
  '2026-09-23': fxDraft,
}

async function get<T>(path: string): Promise<T> {
  const key = getKey()
  const res = await fetch(`${API_BASE}${path}`, {
    headers: key ? { 'X-API-Key': key } : {},
  })
  const text = await res.text()
  if (!res.ok) throw new Error(`${res.status} on ${path}`)
  return JSON.parse(text) as T
}

export function fetchDraft(orderDate: string): Promise<DraftOrders> {
  if (LIVE) return get<DraftOrders>(`/api/orders/draft?order_date=${encodeURIComponent(orderDate)}`)
  const fx = FIXTURES[orderDate]
  if (!fx) return Promise.reject(new Error(`No recorded ordering run for ${orderDate}.`))
  return Promise.resolve(fx as DraftOrders)
}

export function fetchSuppliers(): Promise<Supplier[]> {
  if (LIVE) return get<Supplier[]>('/api/suppliers')
  return Promise.resolve(fxSuppliers as unknown as Supplier[])
}

/* ---------------------------------------------------------------- helpers */

/** Integer pence, summed exactly. No float touches money (invariant 11). */
export function sumPence(values: readonly number[]): Dec {
  return values.reduce((acc, n) => add(acc, fromInt(n)), ZERO)
}

export function decOf(text: string): Dec {
  return mustDec(text)
}

const DAYS = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/** ISO 1..7, in week order. An EMPTY list means any day (walk-in retail) and
 *  the caller must say that rather than print nothing. */
export function weekdayNames(iso: readonly number[]): string {
  if (iso.length === 0) return 'any day'
  return [...iso]
    .sort((a, b) => a - b)
    .map((d) => DAYS[d] ?? String(d))
    .join(' ')
}

export function shortTime(t: string | null): string | null {
  if (!t) return null
  const parts = t.split(':')
  if (parts.length < 2) return t
  return `${parts[0]}:${parts[1]}`
}

/** The API writes its em dashes as `--` and its prose is rendered verbatim
 *  otherwise: no number in this app is ever read out of a sentence. */
export function prose(s: string): string {
  return s.replace(/ -- /g, ' — ')
}

export function channelLabel(c: string): string {
  return c.replace(/_/g, ' ').toLowerCase()
}
