/**
 * Data layer for the Recipes, Menu items and Ingredients screens.
 *
 * Reads: `useQuery` hooks over `request()` (auth attached by lib/api). In fixture
 * mode (no `VITE_LIVE`) `request()` answers them from `web/fixtures/` (lib/fixtures),
 * loaded lazily so the live bundle does not carry them.
 *
 * Writes: every recipe, price and line edit is a PREVIEW (writes nothing) and an
 * APPLY (from today). `send()` returns a discriminated result so a screen can tell
 * "the server refused, here is its sentence" (422/404) from "the recipe moved under
 * you, reload" (409) from "fixture mode, nothing to write to".
 *
 * This file also carries the two contract fixes the spec found in the legacy client
 * (recipes-menu-ingredients.md §A1): apply goes to `/apply` (the old
 * `applyFromToday` posted to a route that does not exist), and a 409's `detail` is
 * read whether it is a string (what the API sends) or `{message}`.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, LIVE, API_BASE, request } from './api'
import type {
  ChangesetApplied,
  ChangesetPreview,
  IngredientDetail,
  IngredientPriceBody,
  IngredientPricePreview,
  IngredientsResponse,
  IngredientWrite,
  LineIn,
  LinesPreview,
  MenuItemDetail,
  MenuItemsResponse,
  MenuWrite,
  PhotoResult,
  PricesPreview,
  ProposalPreview,
  RecipeEditor,
  RecipesRail,
  Season,
  Swap,
  SwapPreview,
  TemplateOp,
} from './types/menu'

/* ------------------------------------------------------------ results --- */

export type SendResult<T> =
  | { kind: 'ok'; data: T }
  /** 422/404/413: the server declined, for a reason to show verbatim. */
  | { kind: 'refused'; message: string; status: number }
  /** 409: the world moved (stale recipe, retroactive date, still in use). */
  | { kind: 'conflict'; message: string }
  | { kind: 'offline'; message: string }
  | { kind: 'failed'; message: string }

function detailMessage(payload: unknown): string | null {
  if (payload === null || typeof payload !== 'object') return typeof payload === 'string' ? payload : null
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (detail !== null && typeof detail === 'object' && !Array.isArray(detail)) {
    const m = (detail as { message?: unknown }).message
    if (typeof m === 'string') return m
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (!d || typeof d !== 'object') return null
        const msg = (d as { msg?: unknown }).msg
        const loc = (d as { loc?: unknown }).loc
        const where = Array.isArray(loc) ? loc.filter((x) => typeof x === 'string' && x !== 'body').slice(-1)[0] : null
        return typeof msg === 'string' ? (where ? `${where}: ${msg}` : msg) : null
      })
      .filter((m): m is string => m !== null)
    if (parts.length) return parts.join('; ')
  }
  return null
}

const OFFLINE =
  'This screen is reading recorded fixtures, so nothing can be previewed or written. Point it at the live API to make changes.'

export async function send<T>(path: string, body?: unknown, init?: RequestInit): Promise<SendResult<T>> {
  if (!LIVE) return { kind: 'offline', message: OFFLINE }
  try {
    const data = await request<T>(path, {
      method: 'POST',
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      ...init,
    })
    return { kind: 'ok', data }
  } catch (e) {
    if (e instanceof ApiError) {
      const msg = detailMessage(e.payload) ?? `The server answered ${e.status}.`
      if (e.status === 409) return { kind: 'conflict', message: msg }
      if (e.status === 422 || e.status === 404 || e.status === 413) {
        return { kind: 'refused', message: msg, status: e.status }
      }
      return { kind: 'failed', message: msg }
    }
    return { kind: 'failed', message: e instanceof Error ? e.message : String(e) }
  }
}

/* -------------------------------------------------------------- reads --- */

/** Fixture mode is `request`'s business (lib/fixtures). */
function read<T>(path: string): Promise<T> {
  return request<T>(path)
}

export const MENU_KEYS = {
  recipes: ['menu', 'recipes'] as const,
  editor: (id: number) => ['menu', 'editor', id] as const,
  seasons: ['menu', 'seasons'] as const,
  swaps: ['menu', 'swaps'] as const,
  menuItems: ['menu', 'items'] as const,
  menuItem: (id: number) => ['menu', 'item', id] as const,
  ingredients: ['menu', 'ingredients'] as const,
  ingredient: (id: number) => ['menu', 'ingredient', id] as const,
}

export function useRecipesRail() {
  return useQuery({ queryKey: MENU_KEYS.recipes, queryFn: () => read<RecipesRail>('/api/recipes') })
}
export function useRecipeEditor(id: number | null) {
  return useQuery({
    queryKey: MENU_KEYS.editor(id ?? 0),
    queryFn: () => read<RecipeEditor>(`/api/templates/${id}/editor`),
    enabled: id !== null,
  })
}
export function useSeasons() {
  return useQuery({ queryKey: MENU_KEYS.seasons, queryFn: () => read<Season[]>('/api/seasons') })
}
export function useMenuItems() {
  return useQuery({ queryKey: MENU_KEYS.menuItems, queryFn: () => read<MenuItemsResponse>('/api/menu-items') })
}
export function useMenuItem(id: number | null) {
  return useQuery({
    queryKey: MENU_KEYS.menuItem(id ?? 0),
    queryFn: () => read<MenuItemDetail>(`/api/menu-items/${id}`),
    enabled: id !== null,
  })
}
export function useIngredients() {
  return useQuery({
    queryKey: MENU_KEYS.ingredients,
    queryFn: () => read<IngredientsResponse>('/api/ingredients'),
  })
}
export function useIngredient(id: number | null) {
  return useQuery({
    queryKey: MENU_KEYS.ingredient(id ?? 0),
    queryFn: () => read<IngredientDetail>(`/api/ingredients/${id}`),
    enabled: id !== null,
  })
}

/** After any write in this area, every read here may be stale: costs cascade. */
export function useInvalidateMenu() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: ['menu'] })
}

/* ------------------------------------------------------------- writes --- */

export const recipeApi = {
  preview: (id: number, body: { base_version: string; ops: TemplateOp[]; loaded_hourly_rate_pence?: number }) =>
    send<ChangesetPreview>(`/api/templates/${id}/changeset/preview`, body),
  apply: (id: number, body: { base_version: string; ops: TemplateOp[]; actor: string }) =>
    send<ChangesetApplied>(`/api/templates/${id}/changeset/apply`, body),
  swapPreview: (id: number, body: Record<string, unknown>) => send<SwapPreview>(`/api/swaps/${id}/preview`, body),
  swapApply: (id: number, body: Record<string, unknown>) =>
    send<{ swap: Swap; diff: string[] }>(`/api/swaps/${id}/apply`, body),
  proposalPreview: (id: string, allowConflicts: boolean) =>
    send<ProposalPreview>(`/api/proposals/${encodeURIComponent(id)}/preview`, { allow_conflicts: allowConflicts }),
  /** By proposal_id ONLY: names are not unique (ARCHITECTURE 8Q). */
  proposalConfirm: (id: string, actor: string, allowConflicts: boolean) =>
    send<{ template_id: number | null; summary: string; warnings: string[] }>(
      `/api/proposals/${encodeURIComponent(id)}/confirm`,
      { actor, allow_conflicts: allowConflicts },
    ),
}

export const menuApi = {
  create: (body: {
    name: string
    category: string | null
    note: string | null
    sizes: { size_code: string | null; price_pence: number; lines: LineIn[] }[]
    actor: string
  }) => send<MenuWrite>('/api/menu-items', body),
  group: (id: number, body: { actor: string; name?: string; category?: string | null; note?: string | null; active?: boolean }) =>
    send<MenuWrite>(`/api/menu-items/${id}/group`, body),
  addSize: (id: number, body: { actor: string; size_code: string | null; price_pence: number; copy_from_menu_item_id?: number }) =>
    send<MenuWrite>(`/api/menu-items/${id}/sizes`, body),
  removeSize: (id: number, actor: string) => send<MenuWrite>(`/api/menu-items/${id}/remove-size`, { actor }),
  duplicate: (id: number, actor: string) => send<MenuWrite>(`/api/menu-items/${id}/duplicate`, { actor }),
  linesPreview: (id: number, lines: LineIn[], also: number[]) =>
    send<LinesPreview>(`/api/menu-items/${id}/lines/preview`, { lines, also_menu_item_ids: also }),
  linesApply: (id: number, lines: LineIn[], also: number[], actor: string) =>
    send<{ diff: string[] }>(`/api/menu-items/${id}/lines/apply`, { lines, also_menu_item_ids: also, actor }),
  pricesPreview: (prices: { menu_item_id: number; price_pence: number }[]) =>
    send<PricesPreview>('/api/menu-items/prices/preview', { prices }),
  pricesApply: (prices: { menu_item_id: number; price_pence: number }[], actor: string) =>
    send<{ diff: string[]; pos_actions: string[] }>('/api/menu-items/prices/apply', { prices, actor }),
  category: (name: string, kind: 'DRINKS' | 'FOOD' | 'OTHER') =>
    send<{ name: string }>('/api/menu-categories', { name, kind }),
  clearPhoto: (id: number) => send<PhotoResult>(`/api/menu-items/${id}/photo/clear`, {}),
  /** Raw body; the server decides the type from the bytes, not this header. */
  uploadPhoto: (id: number, blob: Blob, actor: string | null) =>
    send<PhotoResult>(`/api/menu-items/${id}/photo`, undefined, {
      body: blob,
      headers: { 'Content-Type': blob.type || 'application/octet-stream', ...(actor ? { 'X-Operator': actor } : {}) },
    }),
}

export const ingredientApi = {
  create: (body: Record<string, unknown>) => send<IngredientWrite>('/api/ingredients', body),
  meta: (id: number, body: Record<string, unknown>) => send<IngredientWrite>(`/api/ingredients/${id}/meta`, body),
  retire: (id: number, actor: string) => send<IngredientWrite>(`/api/ingredients/${id}/retire`, { actor }),
  pricePreview: (id: number, body: IngredientPriceBody) =>
    send<IngredientPricePreview>(`/api/ingredients/${id}/price/preview`, body),
  priceApply: (id: number, body: IngredientPriceBody & { actor: string }) =>
    send<{ rollup_items_recosted: number }>(`/api/ingredients/${id}/price/apply`, body),
}

/** Photo URLs are same-origin paths; in split-origin dev they need the API base. */
export function mediaSrc(url: string | null): string | null {
  if (url === null) return null
  return url.startsWith('/') ? `${API_BASE}${url}` : url
}
