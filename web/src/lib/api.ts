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
import { cmp, parseDec } from './dec'
import type {
  MetaResponse,
  PreviewResponse,
  StockDetailResponse,
  StockResponse,
  TemplateDetailResponse,
  TemplateSummary,
} from './types'

export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')
export const LIVE = API_BASE !== ''

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

export interface TodaySummary {
  local_date: string
  expiry_write_offs_due: number
  expiry_write_offs_value_pence: string
  short_dated: unknown[]
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
