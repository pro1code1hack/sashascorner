/**
 * The data layer.
 *
 * Every screen renders from the static responses in `web/fixtures/` by default
 * (spec §10.10) — those are 19 real responses from `cafeops api-fixtures`, not
 * samples. Set `VITE_API_BASE` to point at a running API instead; auth is one
 * shared password sent as `X-API-Key`, kept in sessionStorage rather than
 * localStorage because it is a shared password on a shared laptop.
 *
 * Fixtures are imported, never fetched, so fixture mode has no network at all
 * and no loading race to design around.
 */
import fxTemplates from '../../fixtures/templates.json'
import fxTemplateDetail from '../../fixtures/template-detail.json'
import fxPreview from '../../fixtures/template-preview.json'
import fxStockTierA from '../../fixtures/stock-tier-a.json'
import fxStockDetail from '../../fixtures/stock-detail.json'
import fxToday from '../../fixtures/today.json'
import fxMeta from '../../fixtures/meta.json'
import fxOrders from '../../fixtures/orders-draft.json'
import fxMargin from '../../fixtures/margin.json'
import fxChannels from '../../fixtures/channels.json'
import fxSuppliers from '../../fixtures/suppliers.json'
import fxHealth from '../../fixtures/health.json'
import fxProposals from '../../fixtures/proposals.json'
import fxTakings from '../../fixtures/takings.json'
import { cmp, parseDec } from './dec'
import type {
  ChannelsResponse,
  TakingsResponse,
  MaterialiseIn,
  MaterialiseResponse,
  ProposalsResponse,
  ShelfLifeIn,
  ShelfLifeResponse,
  SupplierTermsIn,
  SupplierTermsResponse,
  HealthResponse,
  MarginResponse,
  MetaResponse,
  OrdersDraftResponse,
  Supplier,
  PreviewResponse,
  StockDetailResponse,
  StockResponse,
  TemplateDetailResponse,
  TemplateSummary,
} from './types'

/**
 * Where the data comes from.
 *
 * `VITE_API_BASE` is an ORIGIN ("https://ops.example.com"), never a path prefix:
 * every request path below already begins "/api/", so a "/api" base would build
 * "/api/api/today" and 404. Same-origin deployments -- the normal case, with the
 * API behind the same Caddy -- leave the base empty and set `VITE_LIVE=1`, which
 * is why liveness cannot simply be "is the base non-empty".
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')
export const LIVE = API_BASE !== '' || import.meta.env.VITE_LIVE === '1'

const KEY_STORE = 'cafeops.key'

export function getKey(): string | null {
  try {
    return sessionStorage.getItem(KEY_STORE)
  } catch {
    return null
  }
}

export function setKey(k: string): void {
  try {
    sessionStorage.setItem(KEY_STORE, k)
  } catch {
    /* private mode; the key stays in memory for this page only */
  }
}

export function clearKey(): void {
  try {
    sessionStorage.removeItem(KEY_STORE)
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly payload: unknown,
    message: string,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const key = getKey()
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(key ? { 'X-API-Key': key } : {}),
      ...(init?.headers ?? {}),
    },
  })
  const text = await res.text()
  let payload: unknown = null
  try {
    payload = text === '' ? null : JSON.parse(text)
  } catch {
    payload = text
  }
  if (!res.ok) {
    throw new ApiError(res.status, payload, `${res.status} on ${path}`)
  }
  return payload as T
}

/* ---------------------------------------------------------------- reads --- */

/**
 * One thing today needs a decision about. `severity` orders the screen:
 * `act` before `watch` before `info`. `subject` is a display string and may be
 * a comma-joined list or null -- the typed arrays on `TodaySummary` are the
 * machine-readable form, so render chips from those, not by splitting this.
 */
export interface TodayAlert {
  kind: string
  severity: 'act' | 'watch' | 'info'
  subject: string | null
  message: string
}

/** The stock half of /api/today. Counts, not money, except where noted. */
export interface TodayStock {
  ingredients: number
  unanchored: number
  negative: number
  short_dated: number
  unbatched: number
  auto_order_enabled: number
  forced_manual: number
  /** null when a batch in the expiring set has no unit cost. A partial total
   *  would understate the very waste it warns about, so the API withholds it
   *  rather than summing what it has (invariant 8). */
  expiring_value_pence: string | null
  notes: string[]
}

/**
 * /api/today in full. The four `draft_order_*` fields are null until a draft
 * has been computed for the day -- null means "not computed", never "zero", so
 * the screen must render the difference.
 */
export interface TodaySummary {
  as_of: string
  local_date: string
  stock: TodayStock
  /** Display strings like "Whole milk (3d)". `schemas.py` types this
   *  `tuple[str, ...]`; the fixture is empty today, which is why it was
   *  originally typed `unknown[]`. Not to be confused with
   *  `TodayStock.short_dated`, which is a count. */
  short_dated: string[]
  expiry_write_offs_due: number
  /** null when a batch in the write-off set has no price: the total is then
   *  unknowable, which is not the same as zero. */
  expiry_write_offs_value_pence: string | null
  drift_forced_manual: string[]
  drift_tuning_band: string[]
  auto_order_enabled_count: number
  draft_order_total_pence: number | null
  draft_order_supplier_count: number | null
  capped_line_count: number | null
  emergency_line_count: number | null
  unavailable_menu_items: string[]
  /** Things needing a decision, already severity-ranked by the backend. */
  alerts: TodayAlert[]
  /** Standing caveats about the data itself -- estimate prices, estimate shelf
   *  lives. True every day, so they are separate from `alerts`. */
  data_quality: TodayAlert[]
  /** Whole-response caveats, invariant 6 among them. Print verbatim. */
  notes: string[]
}

export const api = {
  meta: (): Promise<MetaResponse> =>
    LIVE ? request('/api/meta') : Promise.resolve(fxMeta as unknown as MetaResponse),

  today: (): Promise<TodaySummary> =>
    LIVE ? request('/api/today') : Promise.resolve(fxToday as unknown as TodaySummary),

  templates: (): Promise<TemplateSummary[]> =>
    LIVE ? request('/api/templates') : Promise.resolve(fxTemplates as unknown as TemplateSummary[]),

  templateDetail: (id: number): Promise<TemplateDetailResponse> =>
    LIVE
      ? request(`/api/templates/${id}`)
      : Promise.resolve(fxTemplateDetail as unknown as TemplateDetailResponse),

  stock: (tier: string): Promise<StockResponse> =>
    LIVE
      ? request(`/api/stock?tier=${encodeURIComponent(tier)}&as_of=today`)
      : Promise.resolve(fxStockTierA as unknown as StockResponse),

  /** In fixture mode only one ingredient has a recorded detail response, and
   *  showing Whole milk's count history under another ingredient's name would
   *  be a lie. So it refuses rather than substitutes. */
  stockDetail: (id: number): Promise<StockDetailResponse> => {
    if (LIVE) return request(`/api/stock/${id}`)
    const fx = fxStockDetail as unknown as StockDetailResponse
    if (fx.row.ingredient_id === id) return Promise.resolve(fx)
    return Promise.reject(
      new ApiError(
        404,
        null,
        'Count history for this ingredient is not in the fixture set — only Whole milk is. Point VITE_API_BASE at a running API to read the rest.',
      ),
    )
  },

  /** The draft purchase order. A pure read: `writes_nothing` is true, and
   *  nothing is committed until it is confirmed in Telegram. */
  ordersDraft: (): Promise<OrdersDraftResponse> =>
    LIVE
      ? request('/api/orders/draft')
      : Promise.resolve(fxOrders as unknown as OrdersDraftResponse),

  margin: (): Promise<MarginResponse> =>
    LIVE ? request('/api/margin') : Promise.resolve(fxMargin as unknown as MarginResponse),

  channels: (): Promise<ChannelsResponse> =>
    LIVE ? request('/api/channels') : Promise.resolve(fxChannels as unknown as ChannelsResponse),

  suppliers: (): Promise<Supplier[]> =>
    LIVE ? request('/api/suppliers') : Promise.resolve(fxSuppliers as unknown as Supplier[]),

  health: (): Promise<HealthResponse> =>
    LIVE ? request('/api/health') : Promise.resolve(fxHealth as unknown as HealthResponse),

  /** Template proposals waiting for a human (spec 6). Reading writes nothing. */
  /** What the cafe took, by day and method. Writes nothing. */
  takings: (days = 30): Promise<TakingsResponse> =>
    LIVE
      ? request(`/api/takings?days=${days}`)
      : Promise.resolve(fxTakings as unknown as TakingsResponse),

  proposals: (): Promise<ProposalsResponse> =>
    LIVE
      ? request('/api/proposals')
      : Promise.resolve(fxProposals as unknown as ProposalsResponse),
}

/* ------------------------------------------------------------ the writes --- */

/**
 * A refused confirmation. The backend writes these messages to be shown to the
 * person who typed the number -- "a 1-day life against a 2-day transit buffer
 * leaves nothing usable on arrival" is the whole explanation -- so they are
 * surfaced verbatim rather than replaced with a generic failure.
 */
export type WriteResult<T> =
  | { kind: 'ok'; data: T }
  | { kind: 'refused'; message: string }
  | { kind: 'offline'; message: string }
  | { kind: 'failed'; status: number | null; message: string }

function refusalMessage(payload: unknown): string | null {
  if (payload === null || typeof payload !== 'object') return null
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (detail !== null && typeof detail === 'object') {
    const m = (detail as { message?: unknown }).message
    if (typeof m === 'string') return m
  }
  // FastAPI's own validation errors arrive as a list of {loc, msg}.
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => (d && typeof d === 'object' ? (d as { msg?: unknown }).msg : null))
      .filter((m): m is string => typeof m === 'string')
    if (parts.length) return parts.join('; ')
  }
  return null
}

async function write<T>(path: string, body: unknown): Promise<WriteResult<T>> {
  if (!LIVE) {
    return {
      kind: 'offline',
      message:
        'This screen is reading recorded fixtures, so there is nothing to write to. ' +
        'Point it at the live API to confirm terms.',
    }
  }
  try {
    const data = await request<T>(path, { method: 'POST', body: JSON.stringify(body) })
    return { kind: 'ok', data }
  } catch (e) {
    if (e instanceof ApiError) {
      const msg = refusalMessage(e.payload)
      // 409 belongs here too: "a template of that name already exists" is the
      // backend declining for a reason the reader can act on, not a transport
      // failure. Rendering it as "the request did not land" would be wrong.
      if (msg !== null && (e.status === 422 || e.status === 404 || e.status === 409)) {
        return { kind: 'refused', message: msg }
      }
      return { kind: 'failed', status: e.status, message: msg ?? e.message }
    }
    return { kind: 'failed', status: null, message: e instanceof Error ? e.message : String(e) }
  }
}

/** Record terms confirmed with a supplier, clearing the invented-terms flag. */
export function confirmSupplierTerms(
  supplierId: number,
  body: SupplierTermsIn,
): Promise<WriteResult<SupplierTermsResponse>> {
  return write(`/api/suppliers/${supplierId}/confirm`, body)
}

/**
 * Confirm one proposal into real composition rows.
 *
 * Addressed by `proposal_id`, never by name: detection names a group after its
 * defining ingredient, so two different recipes can share one. Two pairs do
 * today, and confirming the wrong one attaches the wrong recipe to real drinks.
 * The API refuses an ambiguous name rather than resolving it to the first match.
 */
export function materialiseProposal(
  proposalId: string,
  body: MaterialiseIn,
): Promise<WriteResult<MaterialiseResponse>> {
  return write(`/api/proposals/${encodeURIComponent(proposalId)}/materialise`, body)
}

/** Record a shelf life somebody checked. Changes order size from the next run. */
export function confirmShelfLife(
  ingredientId: number,
  body: ShelfLifeIn,
): Promise<WriteResult<ShelfLifeResponse>> {
  return write(`/api/ingredients/${ingredientId}/shelf-life`, body)
}

/* -------------------------------------------------------------- preview --- */

export type PreviewResult =
  | { kind: 'ok'; data: PreviewResponse }
  /** 409. The slot was edited elsewhere; a preview against a closed row would
   *  honestly answer "0 items affected, no warnings", which reads as *this edit
   *  is harmless*. ARCHITECTURE.md §8H.4. */
  | { kind: 'superseded'; currentComponentId: number | null; message: string }
  /** Fixture mode only, and never dressed up as a real answer. */
  | { kind: 'unpriced'; recordedComponentId: number; recordedQty: Record<string, string> }
  | { kind: 'failed'; status: number | null; message: string }

function sameQty(a: Record<string, string>, b: Record<string, string>): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  for (const k of keys) {
    const x = parseDec(a[k] ?? '')
    const y = parseDec(b[k] ?? '')
    if (x === null || y === null) return false
    if (cmp(x, y) !== 0) return false
  }
  return true
}

const recorded = fxPreview as unknown as PreviewResponse

export async function preview(
  templateId: number,
  componentId: number,
  qtyBySize: Record<string, string>,
): Promise<PreviewResult> {
  if (!LIVE) {
    if (componentId === recorded.component_id && sameQty(qtyBySize, recorded.qty_by_size_after)) {
      return { kind: 'ok', data: recorded }
    }
    return {
      kind: 'unpriced',
      recordedComponentId: recorded.component_id,
      recordedQty: recorded.qty_by_size_after,
    }
  }
  try {
    const data = await request<PreviewResponse>(`/api/templates/${templateId}/preview`, {
      method: 'POST',
      body: JSON.stringify({ component_id: componentId, qty_by_size: qtyBySize }),
    })
    return { kind: 'ok', data }
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      const p = e.payload as { detail?: Record<string, unknown> } | null
      const d = p?.detail ?? {}
      const current = typeof d['current_component_id'] === 'number' ? d['current_component_id'] : null
      const msg =
        typeof d['message'] === 'string'
          ? d['message']
          : 'This slot has been superseded by a later edit.'
      return { kind: 'superseded', currentComponentId: current, message: msg }
    }
    return {
      kind: 'failed',
      status: e instanceof ApiError ? e.status : null,
      message: e instanceof Error ? e.message : String(e),
    }
  }
}

/** Invariant 1-adjacent: there is no write endpoint for composition apply in
 *  the fixture layer, and the API's only two POST routes are composition. The
 *  UI therefore never claims a write happened that did not. */
export async function applyFromToday(
  templateId: number,
  componentId: number,
  qtyBySize: Record<string, string>,
): Promise<{ ok: boolean; message: string }> {
  if (!LIVE) {
    return {
      ok: false,
      message:
        'Fixture mode: nothing was written. The recipe on disk is unchanged. Point VITE_API_BASE at a running API to commit this edit.',
    }
  }
  try {
    await request(`/api/templates/${templateId}/components/${componentId}`, {
      method: 'POST',
      body: JSON.stringify({ qty_by_size: qtyBySize, effective_from: 'today' }),
    })
    return { ok: true, message: 'Applied from today.' }
  } catch (e) {
    return {
      ok: false,
      message: e instanceof Error ? e.message : String(e),
    }
  }
}
