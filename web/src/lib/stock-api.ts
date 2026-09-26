/**
 * Reads and writes for the Stock, Orders and Suppliers screens.
 *
 * Reads go through `request` inside useQuery; writes through `apiWrite`, which
 * returns the server's refusal sentence verbatim (FRONTEND-KIT rule 10).
 * Nothing is optimistic: after a write, the caller invalidates the keys below.
 *
 * Fixture mode is handled by `request` itself (lib/fixtures): each read below is
 * answered from `web/fixtures/` when it was recorded, and says so when not.
 */
import { ApiError, LIVE, apiWrite, request } from './api'
import type { WriteResult } from './api'
import type {
  ChecklistIn,
  ChecklistOut,
  CountIn,
  CountOut,
  DeliveryIn,
  DeliveryOut,
  DraftOrdersResponse,
  OrdersListResponse,
  ParChangeOut,
  ParIn,
  ProductWriteOut,
  PurchaseOrder,
  ReceiveIn,
  ReceiveOut,
  ShopRunIn,
  ShopRunOut,
  ShopRunsResponse,
  StockDetail,
  StockResponse,
  Supplier,
  SupplierCreateIn,
  SupplierPatchIn,
  SupplierProductsResponse,
  SupplierWriteOut,
  TierIn,
  TierOut,
  Unit,
  WriteOffIn,
  WriteOffOut,
} from './types/stock'

/** Query keys. Invalidate after a write; nothing is patched in place. */
export const KEYS = {
  stock: ['stock-v2'] as const,
  stockDetail: (id: number) => ['stock-v2-detail', id] as const,
  draft: ['orders-draft-v2'] as const,
  orders: ['orders-v2'] as const,
  order: (id: number) => ['orders-v2', id] as const,
  shopRuns: ['shop-runs'] as const,
  suppliers: ['suppliers-v2'] as const,
  supplierProducts: (id: number) => ['supplier-products', id] as const,
}

export const stockApi = {
  stock: (): Promise<StockResponse> => request('/api/stock?as_of=today&include_untracked=true'),

  stockDetail: (id: number): Promise<StockDetail> => request(`/api/stock/${id}?as_of=today&history=20`),

  draft: (): Promise<DraftOrdersResponse> => request('/api/orders/draft'),

  orders: (supplierId?: number): Promise<OrdersListResponse> =>
    request(`/api/orders${supplierId === undefined ? '' : `?supplier_id=${supplierId}`}`),

  order: (id: number): Promise<PurchaseOrder> => request(`/api/orders/${id}`),

  shopRuns: (): Promise<ShopRunsResponse> => request('/api/orders/shop-runs?months=8'),

  suppliers: (): Promise<Supplier[]> => request('/api/suppliers'),

  supplierProducts: (id: number): Promise<SupplierProductsResponse> => request(`/api/suppliers/${id}/products`),
}

/* ------------------------------------------------------------ the writes --- */

export const stockWrites = {
  count: (id: number, body: CountIn) => apiWrite<CountOut>(`/api/stock/${id}/counts`, body),
  delivery: (id: number, body: DeliveryIn) =>
    apiWrite<DeliveryOut>(`/api/stock/${id}/deliveries`, body),
  writeOff: (id: number, body: WriteOffIn) =>
    apiWrite<WriteOffOut>(`/api/stock/${id}/write-offs`, body),
  checklist: (id: number, body: ChecklistIn) =>
    apiWrite<ChecklistOut>(`/api/stock/${id}/checklist`, body),
  par: (id: number, body: ParIn) => apiWrite<ParChangeOut>(`/api/stock/${id}/par`, body, 'PUT'),
  tier: (id: number, body: TierIn) => apiWrite<TierOut>(`/api/ingredients/${id}/tier`, body),
}

export const orderWrites = {
  cancel: (poId: number, cancelled_by: string, reason?: string) =>
    apiWrite<PurchaseOrder>(`/api/orders/${poId}/cancel`, { cancelled_by, reason }),
  markSent: (poId: number, sent_by: string) =>
    apiWrite<PurchaseOrder>(`/api/orders/${poId}/mark-sent`, { sent_by }),
  receive: (poId: number, body: ReceiveIn) =>
    apiWrite<ReceiveOut>(`/api/orders/${poId}/receive`, body),
  shopRun: (body: ShopRunIn) => apiWrite<ShopRunOut>('/api/orders/shop-run', body),
  fromDraft: (supplier_id: number, created_by: string) =>
    apiWrite<PurchaseOrder>('/api/orders/from-draft', { supplier_id, created_by }),
  /** A named person's decision, with the packs they settled on (invariant 1). */
  confirm: (poId: number, confirmed_by: string, final_packs: Record<number, number>) =>
    apiWrite<PurchaseOrder>(`/api/orders/${poId}/confirm`, { confirmed_by, final_packs }),
  clearReceipt: (poId: number) => apiWrite<PurchaseOrder>(`/api/orders/${poId}/receipt/clear`, undefined),
  /** Raw image body, like the menu photo upload. Evidence only: no quantity changes. */
  uploadReceipt: async (poId: number, blob: Blob, actor: string | null): Promise<WriteResult<PurchaseOrder>> => {
    if (!LIVE) return { kind: 'offline', message: 'Reading recorded fixtures: there is nothing to upload to.' }
    try {
      const data = await request<PurchaseOrder>(`/api/orders/${poId}/receipt`, {
        method: 'POST',
        body: blob,
        headers: { 'Content-Type': blob.type || 'application/octet-stream', ...(actor ? { 'X-Operator': actor } : {}) },
      })
      return { kind: 'ok', data }
    } catch (e) {
      const detail = e instanceof ApiError && typeof (e.payload as { detail?: unknown })?.detail === 'string'
        ? (e.payload as { detail: string }).detail
        : null
      if (e instanceof ApiError && detail !== null && (e.status === 422 || e.status === 404 || e.status === 413)) {
        return { kind: 'refused', message: detail }
      }
      return { kind: 'failed', status: e instanceof ApiError ? e.status : null, message: detail ?? String(e) }
    }
  },
}

export const supplierWrites = {
  create: (body: SupplierCreateIn) => apiWrite<SupplierWriteOut>('/api/suppliers', body),
  patch: (id: number, body: SupplierPatchIn) =>
    apiWrite<SupplierWriteOut>(`/api/suppliers/${id}`, body, 'PATCH'),
  archive: (id: number, archived_by: string) =>
    apiWrite<SupplierWriteOut>(`/api/suppliers/${id}/archive`, { archived_by }),
  link: (
    supplierId: number,
    body: {
      ingredient_id: number
      sku: string
      pack_size: string
      pack_unit: Unit
      price_pence: number
      changed_by: string
    },
  ) => apiWrite<ProductWriteOut>(`/api/suppliers/${supplierId}/products`, body),
  editProduct: (
    productId: number,
    body: { changed_by: string; sku?: string; pack_size?: string; pack_unit?: Unit; price_pence?: number },
  ) => apiWrite<ProductWriteOut>(`/api/supplier-products/${productId}`, body, 'PATCH'),
  prefer: (productId: number, changed_by: string) =>
    apiWrite<ProductWriteOut>(`/api/supplier-products/${productId}/prefer`, { changed_by }),
  archiveProduct: (productId: number, archived_by: string) =>
    apiWrite<ProductWriteOut>(`/api/supplier-products/${productId}/archive`, { archived_by }),
}
