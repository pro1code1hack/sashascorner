/**
 * Online ordering admin: the data layer for the Online orders screens
 * (docs/shop/CONTRACT.md §5). Every call goes to `/api/shop-admin/*`, authed like
 * everything else, so `request()` / `apiWrite()` apply: a 401 returns to Login and a
 * refusal is shown as the server wrote it.
 *
 * Reads are `useQuery` over `request()`. The shop screens read the LIVE API only:
 * there are no recorded fixtures for them, so in fixture mode `shopGet` rejects and
 * the screens show an `Empty` saying so (contract §7).
 *
 * Photo uploads send the raw image body, as the menu photo upload does; the server
 * decides the type from the bytes.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, LIVE, apiWrite, request, type WriteResult } from './api'
import type {
  BannerAdmin,
  BannerIn,
  CatalogueAdmin,
  CategoryAdmin,
  CategoryPatch,
  GroupAdmin,
  GroupIn,
  ModifierRef,
  OrderAdmin,
  OrdersPage,
  OrdersScope,
  OrderStatus,
  ProductAdmin,
  ProductByMenuItem,
  ProductPatch,
  ShopSettings,
  ShopSettingsPatch,
  ShopSummary,
  UpsellAdmin,
  UpsellIn,
} from './types/shop'

export const SHOP_KEY = ['shop'] as const
export const SHOP_KEYS = {
  summary: [...SHOP_KEY, 'summary'] as const,
  orders: (q: string) => [...SHOP_KEY, 'orders', q] as const,
  order: (id: number) => [...SHOP_KEY, 'order', id] as const,
  settings: [...SHOP_KEY, 'settings'] as const,
  catalogue: [...SHOP_KEY, 'catalogue'] as const,
  modifiers: [...SHOP_KEY, 'modifiers'] as const,
}

/** The board and the live list refresh this often (contract §7). */
export const LIVE_POLL_MS = 15_000
/** The most `GET /orders` hands over in one page (`PageSizeQ`, le=200). */
export const ORDERS_PAGE_MAX = 200

const BASE = '/api/shop-admin'

/** A GET against the shop admin. Throws ApiError like `request()`. */
export function shopGet<T>(path: string): Promise<T> {
  if (!LIVE) {
    const msg = 'The online ordering screens read the live API. Run the back office with VITE_LIVE=1.'
    return Promise.reject(new ApiError(503, { detail: msg }, msg))
  }
  return request<T>(`${BASE}${path}`)
}

/** A write against the shop admin; never throws (see `WriteResult`). */
export function shopWrite<T>(
  path: string,
  body: unknown,
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST',
): Promise<WriteResult<T>> {
  return apiWrite<T>(`${BASE}${path}`, body, method)
}

/** Raw-body image upload (`POST …/photo`), the menu photo pattern. */
export async function shopUpload<T>(path: string, blob: Blob, by: string | null): Promise<WriteResult<T>> {
  if (!LIVE) return { kind: 'offline', message: 'Uploads need the live API (VITE_LIVE=1).' }
  try {
    const data = await request<T>(`${BASE}${path}`, {
      method: 'POST',
      body: blob,
      headers: { 'Content-Type': blob.type || 'application/octet-stream', ...(by ? { 'X-Operator': by } : {}) },
    })
    return { kind: 'ok', data }
  } catch (e) {
    if (e instanceof ApiError) {
      const p = e.payload as { detail?: unknown } | null
      const message = p && typeof p.detail === 'string' ? p.detail : `The upload failed (${e.status}).`
      return e.status === 413 || e.status === 422 || e.status === 409
        ? { kind: 'refused', message, status: e.status }
        : { kind: 'failed', status: e.status, message }
    }
    return { kind: 'failed', status: null, message: e instanceof Error ? e.message : String(e) }
  }
}

/* --------------------------------------------------------------- reads --- */

/** Counts and the on/off state; polled on the board. */
export function useShopSummary(poll = false) {
  return useQuery({
    queryKey: SHOP_KEYS.summary,
    queryFn: () => shopGet<ShopSummary>('/summary'),
    enabled: LIVE,
    staleTime: 10 * 1000,
    refetchInterval: poll ? LIVE_POLL_MS : false,
    // Only the board polls (poll=true), and it rings from a background tab too, so
    // it keeps polling when hidden; nothing else refetches from a hidden tab.
    refetchIntervalInBackground: poll,
    retry: false,
  })
}

export interface OrdersQuery {
  status: OrdersScope
  from?: string
  to?: string
  q?: string
  page?: number
  page_size?: number
}

export function ordersQueryString(p: OrdersQuery): string {
  const q = new URLSearchParams()
  q.set('status', p.status)
  if (p.from) q.set('from', p.from)
  if (p.to) q.set('to', p.to)
  if (p.q) q.set('q', p.q)
  q.set('page', String(p.page ?? 1))
  q.set('page_size', String(p.page_size ?? 50))
  return q.toString()
}

export function useShopOrders(p: OrdersQuery, poll = false) {
  const qs = ordersQueryString(p)
  return useQuery({
    queryKey: SHOP_KEYS.orders(qs),
    queryFn: () => shopGet<OrdersPage>(`/orders?${qs}`),
    enabled: LIVE,
    staleTime: 5 * 1000,
    refetchInterval: poll ? LIVE_POLL_MS : false,
    // Only the board polls (poll=true), and it rings from a background tab too, so
    // it keeps polling when hidden; nothing else refetches from a hidden tab.
    refetchIntervalInBackground: poll,
    placeholderData: (prev) => prev,
    retry: false,
  })
}

/**
 * Every order matching a filter, in the server's order, pulled page by page at
 * the largest page the API allows. For the CSV download; not for rendering.
 */
export async function fetchAllOrders(p: Omit<OrdersQuery, 'page' | 'page_size'>): Promise<OrderAdmin[]> {
  const out: OrderAdmin[] = []
  for (let page = 1; ; page += 1) {
    const qs = ordersQueryString({ ...p, page, page_size: ORDERS_PAGE_MAX })
    const got = await shopGet<OrdersPage>(`/orders?${qs}`)
    out.push(...got.items)
    if (got.items.length < ORDERS_PAGE_MAX || out.length >= got.total) break
  }
  return out
}

export function useShopOrder(id: number, poll = false) {
  return useQuery({
    queryKey: SHOP_KEYS.order(id),
    queryFn: () => shopGet<OrderAdmin>(`/orders/${id}`),
    enabled: LIVE,
    staleTime: 5 * 1000,
    refetchInterval: poll ? LIVE_POLL_MS : false,
    // Nothing on the order page rings: a hidden tab stops polling and catches up when it is shown.
    refetchIntervalInBackground: false,
    retry: false,
  })
}

export function useShopSettings() {
  return useQuery({
    queryKey: SHOP_KEYS.settings,
    queryFn: () => shopGet<ShopSettings>('/settings'),
    enabled: LIVE,
    staleTime: 30 * 1000,
    retry: false,
  })
}

/** The whole shop catalogue; the server syncs categories and products first. */
export function useShopCatalogue() {
  return useQuery({
    queryKey: SHOP_KEYS.catalogue,
    queryFn: () => shopGet<CatalogueAdmin>('/catalogue'),
    enabled: LIVE,
    staleTime: 30 * 1000,
    retry: false,
  })
}

/** The shop product behind an ops menu item (any size id), for the Menu item page. */
export function useShopProductByMenuItem(menuItemId: number) {
  return useQuery({
    queryKey: [...SHOP_KEY, 'product-by-menu-item', menuItemId],
    queryFn: () => shopGet<ProductByMenuItem>(`/products/by-menu-item/${menuItemId}`),
    enabled: LIVE,
    staleTime: 30 * 1000,
    retry: false,
  })
}

export function useShopModifiers() {
  return useQuery({
    queryKey: SHOP_KEYS.modifiers,
    queryFn: () => shopGet<ModifierRef[]>('/modifiers'),
    enabled: LIVE,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
}

/** Refetch everything under Online orders after a write (the badge included). */
export function useInvalidateShop(): () => Promise<void> {
  const qc = useQueryClient()
  return async () => {
    await qc.invalidateQueries({ queryKey: SHOP_KEY })
    await qc.invalidateQueries({ queryKey: ['shell'] })
  }
}

/* -------------------------------------------------------------- writes --- */

export const orderWrites = {
  status: (id: number, status: OrderStatus, by: string, reason?: string) =>
    shopWrite<OrderAdmin>(`/orders/${id}/status`, { status, by, ...(reason ? { reason } : {}) }),
  note: (id: number, staffNote: string, by: string) => shopWrite<OrderAdmin>(`/orders/${id}/note`, { staff_note: staffNote, by }),
  paid: (id: number, by: string) => shopWrite<OrderAdmin>(`/orders/${id}/paid`, { by }),
  /** A PAID online order back through the provider (contract §10.F); the server refuses the rest with its sentence. */
  refund: (id: number, by: string, reason?: string) => shopWrite<OrderAdmin>(`/orders/${id}/refund`, { by, ...(reason ? { reason } : {}) }),
}

export const settingsWrites = {
  update: (patch: ShopSettingsPatch) => shopWrite<ShopSettings>('/settings', patch, 'PUT'),
}

export const catalogueWrites = {
  category: (id: number, patch: CategoryPatch) => shopWrite<CategoryAdmin>(`/categories/${id}`, patch, 'PUT'),
  categoriesOrder: (ids: number[]) => shopWrite<unknown>('/categories/order', { ids }),
  categoryPhoto: (id: number, blob: Blob, by: string | null) => shopUpload<CategoryAdmin>(`/categories/${id}/photo`, blob, by),
  categoryPhotoClear: (id: number) => shopWrite<CategoryAdmin>(`/categories/${id}/photo/clear`, {}),

  product: (id: number, patch: ProductPatch) => shopWrite<ProductAdmin>(`/products/${id}`, patch, 'PUT'),
  productsOrder: (categorySlug: string, ids: number[]) => shopWrite<unknown>('/products/order', { category_slug: categorySlug, ids }),
  productPhoto: (id: number, blob: Blob, by: string | null) => shopUpload<ProductAdmin>(`/products/${id}/photo`, blob, by),
  productPhotoClear: (id: number) => shopWrite<ProductAdmin>(`/products/${id}/photo/clear`, {}),
  productsBulk: (ids: number[], patch: { available?: boolean; visible?: boolean }) =>
    shopWrite<unknown>('/products/bulk', { ids, ...patch }),
  productByMenuItem: (menuItemId: number, patch: ProductPatch) =>
    shopWrite<ProductByMenuItem>(`/products/by-menu-item/${menuItemId}`, patch, 'PUT'),

  groupCreate: (body: GroupIn) => shopWrite<GroupAdmin>('/option-groups', body),
  groupUpdate: (id: number, body: GroupIn) => shopWrite<GroupAdmin>(`/option-groups/${id}`, body, 'PUT'),
  groupDelete: (id: number) => shopWrite<unknown>(`/option-groups/${id}`, undefined, 'DELETE'),
  groupsOrder: (ids: number[]) => shopWrite<unknown>('/option-groups/order', { ids }),
  optionPhoto: (id: number, blob: Blob, by: string | null) => shopUpload<unknown>(`/options/${id}/photo`, blob, by),
  optionPhotoClear: (id: number) => shopWrite<unknown>(`/options/${id}/photo/clear`, {}),

  upsellCreate: (body: UpsellIn) => shopWrite<UpsellAdmin>('/upsells', body),
  upsellUpdate: (id: number, body: Partial<UpsellIn>) => shopWrite<UpsellAdmin>(`/upsells/${id}`, body, 'PUT'),
  upsellDelete: (id: number) => shopWrite<unknown>(`/upsells/${id}`, undefined, 'DELETE'),

  bannerCreate: (body: BannerIn) => shopWrite<BannerAdmin>('/banners', body),
  bannerUpdate: (id: number, body: Partial<BannerIn>) => shopWrite<BannerAdmin>(`/banners/${id}`, body, 'PUT'),
  bannerDelete: (id: number) => shopWrite<unknown>(`/banners/${id}`, undefined, 'DELETE'),
  bannerPhoto: (id: number, blob: Blob, by: string | null) => shopUpload<BannerAdmin>(`/banners/${id}/photo`, blob, by),
  bannerPhotoClear: (id: number) => shopWrite<BannerAdmin>(`/banners/${id}/photo/clear`, {}),
  bannersOrder: (ids: number[]) => shopWrite<unknown>('/banners/order', { ids }),
}
