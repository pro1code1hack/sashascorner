/**
 * The Money tabs' data layer: reads as React Query hooks, writes through
 * `apiWrite` (lib/api), which surfaces a server refusal verbatim.
 *
 * In fixture mode the reads are answered from the finance responses
 * `cafeops api-fixtures` recorded (lib/fixtures) -- real imported numbers, never
 * the design's demo figures -- and anything unrecorded says so.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiWrite, request } from './api'
import type { WriteResult } from './api'
import type {
  CardRow,
  CashRow,
  ChannelUploadOut,
  DeliveryRow,
  DirectorEntry,
  DirectorEntryIn,
  DirectorResponse,
  Expense,
  ExpenseFilters,
  ExpenseIn,
  ExpensesResponse,
  FinanceAlerts,
  FinanceMeta,
  FinanceMonths,
  FinanceOverview,
  FinanceSettings,
  PLResponse,
  Period,
  ReconcileResponse,
  SalesDay,
  SalesDayIn,
  SalesResponse,
} from './types/finance'

/** Fixture mode is `request`'s business (lib/fixtures). */
function read<T>(path: string): Promise<T> {
  return request<T>(path)
}

/** Every finance query key starts here, so one invalidation refreshes the tabs. */
export const FINANCE_KEY = ['finance'] as const

function qs(params: Record<string, string | boolean | undefined>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === false || v === '') continue
    u.set(k, v === true ? 'true' : v)
  }
  const s = u.toString()
  return s ? `?${s}` : ''
}

export const financeApi = {
  months: () => read<FinanceMonths>('/api/finance/months'),
  meta: () => read<FinanceMeta>('/api/finance/meta'),
  overview: (period: Period) => read<FinanceOverview>(`/api/finance/overview${qs({ period })}`),
  pl: () => read<PLResponse>('/api/finance/pl'),
  sales: (period: Period) => read<SalesResponse>(`/api/finance/sales${qs({ period })}`),
  expenses: (period: Period, f: ExpenseFilters) =>
    read<ExpensesResponse>(
      `/api/finance/expenses${qs({
        period,
        q: f.q.trim() || undefined,
        category: f.category,
        kind: f.kind,
        needs_review: f.needsReview,
        no_receipt: f.noReceipt,
      })}`,
    ),
  reconcile: (period: Period) => read<ReconcileResponse>(`/api/finance/reconcile${qs({ period })}`),
  director: () => read<DirectorResponse>('/api/finance/director'),
  alerts: () => read<FinanceAlerts>('/api/finance/alerts'),
}

export const useFinanceMonths = () =>
  useQuery({ queryKey: [...FINANCE_KEY, 'months'], queryFn: financeApi.months })
export const useFinanceMeta = () =>
  useQuery({ queryKey: [...FINANCE_KEY, 'meta'], queryFn: financeApi.meta, staleTime: 60_000 })
export const useOverview = (period: Period | null) =>
  useQuery({
    queryKey: [...FINANCE_KEY, 'overview', period],
    queryFn: () => financeApi.overview(period as Period),
    enabled: period !== null,
  })
export const usePL = () => useQuery({ queryKey: [...FINANCE_KEY, 'pl'], queryFn: financeApi.pl })
export const useSales = (period: Period | null) =>
  useQuery({
    queryKey: [...FINANCE_KEY, 'sales', period],
    queryFn: () => financeApi.sales(period as Period),
    enabled: period !== null,
  })
export const useExpenses = (period: Period | null, f: ExpenseFilters) =>
  useQuery({
    queryKey: [...FINANCE_KEY, 'expenses', period, f],
    queryFn: () => financeApi.expenses(period as Period, f),
    enabled: period !== null,
    placeholderData: (prev) => prev,
  })
export const useReconcile = (period: Period | null) =>
  useQuery({
    queryKey: [...FINANCE_KEY, 'reconcile', period],
    queryFn: () => financeApi.reconcile(period as Period),
    enabled: period !== null,
  })
export const useDirector = () =>
  useQuery({ queryKey: [...FINANCE_KEY, 'director'], queryFn: financeApi.director })
/** For the shell's cash banner. Polls gently; the banner is not urgent. */
export const useFinanceAlerts = () =>
  useQuery({
    queryKey: [...FINANCE_KEY, 'alerts'],
    queryFn: financeApi.alerts,
    refetchInterval: 5 * 60_000,
  })

/** Refresh every finance read after a write: totals cascade across tabs. */
export function useInvalidateFinance(): () => Promise<void> {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: FINANCE_KEY })
}

/* ---------------------------------------------------------------- writes --- */

const enc = encodeURIComponent

export const financeWrite = {
  createDay: (body: SalesDayIn & { date: string }): Promise<WriteResult<SalesDay>> =>
    apiWrite('/api/finance/sales', body, 'POST'),
  patchDay: (date: string, body: SalesDayIn): Promise<WriteResult<SalesDay>> =>
    apiWrite(`/api/finance/sales/${enc(date)}`, body, 'PATCH'),
  deleteDay: (date: string): Promise<WriteResult<{ deleted: string }>> =>
    apiWrite(`/api/finance/sales/${enc(date)}`, undefined, 'DELETE'),

  createExpense: (body: ExpenseIn): Promise<WriteResult<Expense>> =>
    apiWrite('/api/finance/expenses', body, 'POST'),
  patchExpense: (id: number, body: ExpenseIn): Promise<WriteResult<Expense>> =>
    apiWrite(`/api/finance/expenses/${id}`, body, 'PATCH'),
  deleteExpense: (
    id: number,
    operator: string | null,
  ): Promise<WriteResult<{ deleted: number; undo_token: string }>> =>
    apiWrite(
      `/api/finance/expenses/${id}${operator ? `?operator=${enc(operator)}` : ''}`,
      undefined,
      'DELETE',
    ),
  restoreExpense: (id: number, token: string, operator: string | null): Promise<WriteResult<Expense>> =>
    apiWrite(`/api/finance/expenses/${id}/restore`, { undo_token: token, operator }, 'POST'),

  settings: (body: {
    payout_lag_working_days?: number
    card_fee_bp?: number
    operator?: string | null
  }): Promise<WriteResult<FinanceSettings>> => apiWrite('/api/finance/settings', body, 'PATCH'),
  payout: (
    soldOn: string,
    arrived_pence: number | null,
    operator: string | null,
  ): Promise<WriteResult<CardRow>> =>
    apiWrite(`/api/finance/payouts/${enc(soldOn)}`, { arrived_pence, operator }, 'PUT'),
  statement: (
    month: string,
    channel: string,
    body: {
      gross_pence: number | null
      commission_pence: number | null
      ads_pence: number | null
      operator?: string | null
    },
  ): Promise<WriteResult<DeliveryRow>> =>
    apiWrite(`/api/finance/channel-statements/${enc(month)}/${enc(channel)}`, body, 'PUT'),
  upload: (channel: string, filename: string, text: string): Promise<WriteResult<ChannelUploadOut>> =>
    apiWrite('/api/finance/channel-statements/upload', { channel, filename, text }, 'POST'),
  cashCount: (
    date: string,
    counted_pence: number | null,
    counted_by: string | null,
  ): Promise<WriteResult<CashRow>> =>
    apiWrite(`/api/finance/cash-counts/${enc(date)}`, { counted_pence, counted_by }, 'PUT'),
  explainCash: (date: string, explanation: string, explained_by: string): Promise<WriteResult<CashRow>> =>
    apiWrite(`/api/finance/cash-counts/${enc(date)}/explain`, { explanation, explained_by }, 'POST'),

  createDirector: (body: DirectorEntryIn): Promise<WriteResult<DirectorEntry>> =>
    apiWrite('/api/finance/director', body, 'POST'),
  patchDirector: (id: number, body: DirectorEntryIn): Promise<WriteResult<DirectorEntry>> =>
    apiWrite(`/api/finance/director/${id}`, body, 'PATCH'),
  deleteDirector: (id: number, operator: string | null): Promise<WriteResult<{ deleted: string }>> =>
    apiWrite(
      `/api/finance/director/${id}${operator ? `?operator=${enc(operator)}` : ''}`,
      undefined,
      'DELETE',
    ),
}
