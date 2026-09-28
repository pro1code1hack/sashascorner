/**
 * Wire types for the Money tabs: mirrors `cafeops/api/areas/finance_schemas.py`.
 *
 * Money is integer pence (`Pence`). A nullable money field means "not reported /
 * unknown", never zero (invariant 8): render words or "—", never 0. Percentages
 * are basis points (integers). Dates are 'YYYY-MM-DD' Europe/London business days.
 */
export type Pence = number
export type ISODate = string
export type Month = string
export type Period = Month | 'all'

export type ExpenseMethod =
  | 'CARD'
  | 'BANK_TRANSFER'
  | 'DIRECT_DEBIT'
  | 'STANDING_ORDER'
  | 'CASH'
  | 'CASH_WITHDRAWAL'
  | 'OTHER'
export type ExpenseKind = 'OPERATING' | 'CAPITAL' | 'DRAWINGS'
export type DirectorType = 'CAPITAL_INJECTION' | 'LOAN_TO_COMPANY' | 'DRAWINGS' | 'REPAYMENT'
export type Channel = 'DELIVEROO' | 'JUST_EAT'

export interface FinanceMonths {
  months: Month[]
  default_month: Month | null
}

export interface FinanceSettings {
  payout_lag_working_days: number
  card_fee_bp: number
  cash_tolerance_pence: Pence
  payout_tolerance_pence: Pence
  stock_pct_threshold_bp: number
  updated_at: string
  updated_by: string | null
}

export interface ExpenseCategory {
  id: number
  name: string
  group: 'COGS' | 'OPEX'
  sort: number
  active: boolean
}

export interface FinanceMeta {
  categories: ExpenseCategory[]
  methods: ExpenseMethod[]
  kinds: ExpenseKind[]
  director_types: DirectorType[]
  settings: FinanceSettings
}

export interface CategoryAmount {
  category: string
  pence: Pence
}

export interface PeriodFigures {
  period: string
  label: string
  short_label: string
  trading_days: number
  expense_count: number
  card_pence: Pence
  cash_till_pence: Pence
  cash_off_till_pence: Pence
  delivery_gross_pence: Pence | null
  delivery_missing: string[]
  revenue_pence: Pence
  cogs_by_category: CategoryAmount[]
  stock_bought_pence: Pence
  written_off_pence: Pence | null
  written_off_is_estimate: boolean
  cogs_pence: Pence
  stock_pct_bp: number | null
  stock_pct_warn: boolean
  /** null when the month has takings but no running costs entered. */
  gross_profit_pence: Pence | null
  opex_by_category: CategoryAmount[]
  opex_pence: Pence
  delivery_commission_pence: Pence | null
  delivery_ads_pence: Pence | null
  net_profit_pence: Pence | null
  net_warn: boolean
  capital_pence: Pence
  drawings_pence: Pence
  expenses_missing: boolean
  card_is_bank_deposits: boolean
  incomplete: boolean
  caveats: string[]
}

export interface DayBar {
  day: number
  date: ISODate
  /** null = nothing entered that day: draw a dash, never a zero bar. */
  total_pence: Pence | null
}

export interface MonthBar {
  month: Month
  label: string
  revenue_pence: Pence
}

export interface OverviewChart {
  mode: 'days' | 'months'
  days_in_month: number | null
  first_label: string
  last_label: string
  day_bars: DayBar[]
  month_bars: MonthBar[]
}

export interface FinanceOverview {
  current: PeriodFigures
  previous: PeriodFigures | null
  avg_per_day_pence: Pence | null
  chart: OverviewChart
  where_it_went: CategoryAmount[]
  needs_a_look: {
    count: number
    flagged: { expense_id: number; description: string; amount_pence: Pence }[]
    flagged_total: number
    without_receipt: number
  }
  caveats: string[]
}

export interface PLResponse {
  months: Month[]
  columns: PeriodFigures[]
  total: PeriodFigures
  cogs_categories: string[]
  opex_categories: string[]
  threshold_bp: number
  caveats: string[]
}

export interface SalesDay {
  date: ISODate
  weekday: string
  card_pence: Pence | null
  cash_till_pence: Pence | null
  cash_off_till_pence: Pence | null
  total_pence: Pence
  orders: number | null
  orders_source: 'override' | 'pos' | 'payment_export' | null
  avg_ticket_pence: Pence | null
  note: string | null
  basis: 'TILL' | 'BANK_DEPOSIT' | 'MIXED'
  editable: { card: boolean; cash_till: boolean; cash_off_till: boolean }
  sources: { method: string; source: string; source_ref: string | null }[]
}

export interface SalesResponse {
  period: Period
  days: SalesDay[]
  totals: {
    days: number
    card_pence: Pence
    cash_till_pence: Pence
    cash_off_till_pence: Pence
    total_pence: Pence
    orders: number | null
  }
  caveats: string[]
}

export interface SalesDayIn {
  date?: ISODate
  card_pence?: Pence | null
  cash_till_pence?: Pence | null
  cash_off_till_pence?: Pence | null
  orders_override?: number | null
  note?: string | null
  operator?: string | null
}

export interface Expense {
  id: number
  date: ISODate
  category_id: number
  category: string
  group: 'COGS' | 'OPEX'
  description: string
  amount_pence: Pence
  method: ExpenseMethod | null
  kind: ExpenseKind
  has_receipt: boolean
  notes: string | null
  needs_review: boolean
  supplier_id: number | null
  director_entry_id: number | null
  source: string
  source_ref: string | null
  updated_at: string
  updated_by: string | null
}

export interface ExpensesResponse {
  period: Period
  expenses: Expense[]
  count: number
  total_pence: Pence
  caveats: string[]
}

export interface ExpenseIn {
  date?: ISODate
  category_id?: number
  description?: string
  amount_pence?: Pence
  method?: ExpenseMethod | null
  kind?: ExpenseKind
  has_receipt?: boolean
  notes?: string | null
  needs_review?: boolean
  operator?: string | null
}

export interface ExpenseFilters {
  q: string
  category: string
  kind: string
  needsReview: boolean
  noReceipt: boolean
}

export interface CardRow {
  sold_on: ISODate
  card_pence: Pence
  due_on: ISODate
  expected_pence: Pence
  /** null = not recorded. Never assumed to have arrived. */
  arrived_pence: Pence | null
  arrived_on: ISODate | null
  diff_pence: Pence | null
  status: 'not_yet_due' | 'not_recorded' | 'ok' | 'mismatch' | 'bank_basis'
  note: string | null
}

export interface DeliveryRow {
  month: Month
  label: string
  channel: Channel
  channel_label: string
  present: boolean
  gross_pence: Pence | null
  commission_pence: Pence | null
  ads_pence: Pence | null
  kept_pence: Pence | null
  kept_bp: number | null
  source: string | null
  note: string | null
}

export interface CashRow {
  date: ISODate
  till_pence: Pence | null
  counted_pence: Pence | null
  counted_by: string | null
  diff_pence: Pence | null
  status: 'not_counted' | 'no_till_figure' | 'spot_on' | 'within' | 'out'
  explanation: string | null
  explained_by: string | null
}

export interface ReconcileResponse {
  period: Period
  payout_lag_working_days: number
  card_fee_bp: number
  cash_tolerance_pence: Pence
  payout_tolerance_pence: Pence
  card: {
    rows: CardRow[]
    expected_total_pence: Pence
    arrived_total_pence: Pence
    mismatched_days: number
    not_recorded_days: number
    not_yet_due_days: number
    bank_basis_days: number
  }
  delivery: DeliveryRow[]
  cash: {
    rows: CashRow[]
    days_with_cash: number
    net_diff_pence: Pence
    days_out: number
    days_out_unexplained: number
  }
  caveats: string[]
}

export interface ChannelUploadOut {
  inserted: number
  updated: number
  rejected: string[]
  unmapped: string[]
  notes: string[]
}

export interface DirectorEntry {
  id: number
  date: ISODate
  type: DirectorType
  description: string
  in_pence: Pence
  out_pence: Pence
  /** Running "company owes you" after this row; capital injections do not move it. */
  balance_pence: Pence
  counts_toward_owed: boolean
  notes: string | null
  /** Set: mirrored from an expense, read-only here apart from the note. */
  expense_id: number | null
  source: string
  source_ref: string | null
}

export interface DirectorResponse {
  entries: DirectorEntry[]
  put_in_pence: Pence
  taken_out_pence: Pence
  capital_in_pence: Pence
  loans_in_pence: Pence
  /** + the company owes you, − you owe the company. Capital excluded. */
  loan_balance_pence: Pence
  workbook_balance_pence: Pence
  mirrored_count: number
  caveats: string[]
}

export interface DirectorEntryIn {
  date?: ISODate
  type?: DirectorType
  description?: string
  in_pence?: Pence
  out_pence?: Pence
  notes?: string | null
  operator?: string | null
}

export interface FinanceAlerts {
  cash: null | {
    date: ISODate
    /** Signed: counted − till. */
    diff_pence: Pence
    direction: 'short' | 'over'
    message: string
    open_count: number
    tolerance_pence: Pence
  }
  takings_last_imported_at: string | null
}

/* ------------------------------------------------ transactions (read-only) --- */

export interface Receipt {
  receipt_id: string
  date: ISODate
  weekday: string
  /** HH:MM, Europe/London. */
  time: string
  channel: string
  lines: number
  /** Item count as a decimal string. */
  items: string
  summary: string
  gross_pence: Pence
  voided: boolean
  refund: boolean
}

export interface ReceiptsResponse {
  rows: Receipt[]
  page: number
  page_size: number
  total_rows: number
  gross_pence: Pence
  voided_count: number
  first_date: ISODate | null
  last_date: ISODate | null
  caveats: string[]
}

export interface TakingsRow {
  id: number
  date: ISODate
  weekday: string
  method: string
  source: string
  basis: 'TILL' | 'BANK_DEPOSIT'
  gross_pence: Pence
  refunds_pence: Pence | null
  fees_pence: Pence | null
  discounts_pence: Pence | null
  /** null when any deduction was not reported. */
  net_pence: Pence | null
  transactions: number | null
  /** false = another source wins for this day and method; shown, not added. */
  used: boolean
  source_ref: string | null
  notes: string | null
}

export interface TakingsLedgerResponse {
  rows: TakingsRow[]
  page: number
  page_size: number
  total_rows: number
  used_gross_pence: Pence
  by_method_pence: Record<string, Pence>
  shadowed_count: number
  caveats: string[]
}

export interface ReceiptFilters {
  from?: ISODate
  to?: ISODate
  channel?: string
  q?: string
  min_pence?: number
  max_pence?: number
  include_voided?: boolean
  page: number
  page_size: number
}

export interface TakingsFilters {
  from?: ISODate
  to?: ISODate
  method?: string
  source?: string
  used_only?: boolean
  min_pence?: number
  max_pence?: number
  page: number
  page_size: number
}

/* ------------------------------------------------ sales insights (till) --- */
// GET /api/finance/sales/insights: Lightspeed till lines, sliced for the Sales
// dashboard. A different ledger from the takings above; never added to them.

/** A Decimal quantity as text (CLAUDE.md 10.10). */
export type QtyStr = string

export interface InsightTotals {
  gross_pence: Pence
  receipts: number
  items: QtyStr
  trading_days: number
  refund_receipts: number
  avg_basket_pence: Pence | null
  items_per_basket: QtyStr | null
  per_day_pence: Pence | null
}

export interface InsightShare {
  /** Channel, category ('__none__' = none set) or size. */
  key: string
  gross_pence: Pence
  qty: QtyStr
  receipts: number
}

export interface SalesInsights {
  since: ISODate
  until: ISODate
  prev_since: ISODate
  prev_until: ISODate
  first_sale_date: ISODate | null
  last_sale_date: ISODate | null
  months: Month[]
  is_demo: boolean
  totals: InsightTotals
  previous: InsightTotals | null
  /** gross null: nothing rung up that day (closed, or not synced). Never 0. */
  by_day: { date: ISODate; gross_pence: Pence | null; receipts: number }[]
  by_hour: { hour: number; gross_pence: Pence; receipts: number }[]
  heat: { weekday: number; hour: number; receipts: number; gross_pence: Pence }[]
  by_weekday: { weekday: number; gross_pence: Pence; receipts: number; trading_days: number }[]
  by_channel: InsightShare[]
  by_category: InsightShare[]
  by_size: InsightShare[]
  products: {
    name: string
    category: string | null
    gross_pence: Pence
    qty: QtyStr
    receipts: number
    sizes: Record<string, QtyStr>
  }[]
  baskets: { items: string; receipts: number }[]
  options: { channels: string[]; categories: string[]; products: string[]; sizes: string[] }
  caveats: string[]
}

export interface InsightFilters {
  from?: ISODate
  to?: ISODate
  whole?: boolean
  channel?: string
  category?: string
  product?: string
  size?: string
  /** Comma list, 0 Monday .. 6 Sunday. */
  weekdays?: string
}
