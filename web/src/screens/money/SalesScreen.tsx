/**
 * Money → Sales: the sales dashboard. One period bar and one filter row scope
 * every section below them:
 *
 *   Till sales      what the till sold, when and through which channel
 *                   (Lightspeed receipt lines: TillInsights)
 *   Takings         how much money came in and how it was paid (daily
 *                   takings: SalesDashboard), then profit by month
 *   Day by day      one row per trading day, read-only
 *
 * The two ledgers are never added together. A filter that one of them cannot
 * apply (a product means nothing to a card total) is named on that section.
 *
 * Card figures come from imports (Mettle via the workbook, Lightspeed/CSV
 * exports), never from typing. The one thing a person adds here is cash: till
 * cash and own cash (cash taken for sales not rung on the till), with a note.
 * That happens in a drawer, not in the table.
 *
 * Filters run on the loaded period: weekday, how it was paid, whether the day
 * has an order count, and a £ range on the day's total. The figures strip and
 * the month subtotals are summed from the rows shown, in integer pence.
 */
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ActiveFilters,
  Button,
  Drawer,
  ErrorBox,
  Field,
  FilterBar,
  FilterSelect,
  Input,
  Loading,
  MoneyInput,
  PageBody,
  PageHeader,
  TBody,
  THead,
  Table,
  Td,
  Textarea,
  Th,
  Tr,
  cx,
} from '../../components/ui'
import type { ActiveFilterChip } from '../../components/ui'
import { poundsToPence, penceToPounds } from '../../components/confirm/numbers'
import { useOperator } from '../../lib/operator'
import { financeApi, financeWrite, useInvalidateFinance, useSales, useSalesInsights } from '../../lib/finance-api'
import type { InsightFilters, SalesDay, SalesDayIn } from '../../lib/types/finance'
import {
  AmountRange,
  Figures,
  PeriodBar,
  WEEKDAY_OPTIONS,
  count,
  inPence,
  inRange,
  isWeekend,
  lastDayOfMonth,
  matchesWeekday,
  shortDate,
} from './filters'
import type { DateRange, PenceRange } from './filters'
import { ProfitSection } from './ProfitSection'
import { SalesDashboard } from './SalesDashboard'
import { CHANNEL_LABEL, SIZE_LABEL, SectionTitle, TillSection, categoryLabel } from './TillInsights'
import type { Drill } from './TillInsights'
import { addDays, fd, gbp, londonToday, mLabel, useFinancePeriod } from './shared'

const PAID_OPTIONS = [
  { value: 'all', label: 'Card or cash' },
  { value: 'card', label: 'Has card' },
  { value: 'cash', label: 'Has cash' },
  { value: 'card_only', label: 'Card only' },
  { value: 'cash_only', label: 'Cash only' },
] as const

const ORDERS_OPTIONS = [
  { value: 'all', label: 'Any orders' },
  { value: 'with', label: 'With order count' },
  { value: 'without', label: 'No order count' },
] as const

const SOURCE_LABEL: Record<string, string> = {
  LEGACY_WORKBOOK: 'finance workbook',
  CSV_UPLOAD: 'CSV export',
  POS_API: 'Lightspeed',
  MANUAL: 'added here',
}
const METHOD_NAME: Record<string, string> = { CARD: 'Card', CASH: 'Till cash', CASH_OFF_TILL: 'Own cash' }

/** The server's long deposit caveat; the screen says it in one line instead. */
const DEPOSIT_CAVEAT = 'Card figures up to March 2026'

/** The weekday filter's value as the insights API's comma list. */
function weekdaysParam(f: string): string | undefined {
  if (f === 'all') return undefined
  if (f === 'weekdays') return '0,1,2,3,4'
  if (f === 'weekends') return '5,6'
  return f
}

const hasCash = (d: SalesDay) => (d.cash_till_pence ?? 0) + (d.cash_off_till_pence ?? 0) > 0

function matchesPaid(d: SalesDay, f: string): boolean {
  const card = (d.card_pence ?? 0) > 0
  const cash = hasCash(d)
  if (f === 'card') return card
  if (f === 'cash') return cash
  if (f === 'card_only') return card && !cash
  if (f === 'cash_only') return cash && !card
  return true
}

interface Sums {
  days: number
  card: number
  till: number
  own: number
  total: number
  orders: number
  /** Takings on days that have an order count: the avg ticket's numerator. */
  totalWithOrders: number
}

function sum(rows: readonly SalesDay[]): Sums {
  const s: Sums = { days: rows.length, card: 0, till: 0, own: 0, total: 0, orders: 0, totalWithOrders: 0 }
  for (const d of rows) {
    s.card += d.card_pence ?? 0
    s.till += d.cash_till_pence ?? 0
    s.own += d.cash_off_till_pence ?? 0
    s.total += d.total_pence
    if (d.orders !== null && d.orders > 0) {
      s.orders += d.orders
      s.totalWithOrders += d.total_pence
    }
  }
  return s
}

/** Integer pence, half up, over days that have an order count. */
function avgTicket(s: Sums): number | null {
  return s.orders > 0 ? Math.floor((s.totalWithOrders * 2 + s.orders) / (s.orders * 2)) : null
}

const dash = <span className="text-ink-3">—</span>

function shiftMonth(month: string, by: number): string {
  const [y = 0, m = 1] = month.split('-').map(Number)
  const d = new Date(Date.UTC(y, m - 1 + by, 1))
  return d.toISOString().slice(0, 7)
}

const SECTIONS = [
  { id: 'till', label: 'Till sales' },
  { id: 'takings', label: 'Takings' },
  { id: 'profit', label: 'Profit by month' },
  { id: 'days', label: 'Day by day' },
] as const

/**
 * In-page jump without touching the hash (the hash is this app's router), and
 * without scrollIntoView, which also scrolls overflow-hidden ancestors and
 * slides the page header away. Only the nearest scrolling pane moves.
 */
const jump = (id: string) => (e: React.MouseEvent) => {
  e.preventDefault()
  const el = document.getElementById(id)
  let pane = el?.parentElement ?? null
  while (pane && !/(auto|scroll)/.test(getComputedStyle(pane).overflowY)) pane = pane.parentElement
  if (!el || !pane) return
  const nav = pane.querySelector('nav[aria-label="Sections"]')
  const top = el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - (nav?.clientHeight ?? 0) - 8
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  pane.scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' })
}

type DrawerState = { kind: 'add' } | { kind: 'day'; date: string } | null

export function SalesScreen() {
  const { period, setPeriod, months } = useFinancePeriod()
  const [range, setRange] = useState<DateRange | null>(null)
  const q = useSales(range ? 'all' : period)
  const [weekday, setWeekday] = useState('all')
  const [paid, setPaid] = useState('all')
  const [orders, setOrders] = useState('all')
  const [amount, setAmount] = useState<PenceRange>({ min: null, max: null })
  const [channel, setChannel] = useState('all')
  const [category, setCategory] = useState('all')
  const [product, setProduct] = useState('all')
  const [size, setSize] = useState('all')
  const [drawer, setDrawer] = useState<DrawerState>(null)

  // Till lines: the same period and weekday, plus the till-only filters.
  const tillFilters: InsightFilters | null =
    range === null && period === null
      ? null
      : {
          ...(range
            ? { from: range.from, to: range.to }
            : period === 'all'
              ? { whole: true }
              : { from: `${period}-01`, to: lastDayOfMonth(period as string) }),
          weekdays: weekdaysParam(weekday),
          channel: channel === 'all' ? undefined : channel,
          category: category === 'all' ? undefined : category,
          product: product === 'all' ? undefined : product,
          size: size === 'all' ? undefined : size,
        }
  const till = useSalesInsights(tillFilters)
  const opts = till.data?.options
  // Months with takings or till lines, so a till-only install still has a month bar.
  const barMonths = useMemo(() => [...new Set([...months, ...(till.data?.months ?? [])])].sort(), [months, till.data?.months])
  const tillOnly = channel !== 'all' || category !== 'all' || product !== 'all' || size !== 'all'
  const takingsOnly = paid !== 'all' || orders !== 'all' || amount.min !== null || amount.max !== null
  const drill = (kind: Drill, value: string) => {
    const toggle = (cur: string, set: (v: string) => void) => set(cur === value ? 'all' : value)
    if (kind === 'channel') toggle(channel, setChannel)
    else if (kind === 'category') toggle(category, setCategory)
    else if (kind === 'product') toggle(product, setProduct)
    else if (kind === 'size') toggle(size, setSize)
    else toggle(weekday, setWeekday)
  }
  // The period before, for "vs last month" on the figures: the month before a
  // month, or the same number of days before a custom range.
  const prevMonth = !range && period && period !== 'all' ? shiftMonth(period, -1) : null
  const prevQ = useSales(prevMonth !== null && months.includes(prevMonth) ? prevMonth : null)

  const all = q.data?.days
  const rows = useMemo(
    () =>
      (all ?? []).filter(
        (d) =>
          inRange(d.date, range) &&
          matchesWeekday(d.date, weekday) &&
          matchesPaid(d, paid) &&
          (orders === 'all' || (orders === 'with') === (d.orders !== null)) &&
          inPence(d.total_pence, amount),
      ),
    [all, range, weekday, paid, orders, amount],
  )
  const totals = useMemo(() => sum(rows), [rows])
  const keep = (d: SalesDay) => matchesWeekday(d.date, weekday) && matchesPaid(d, paid) && inPence(d.total_pence, amount)
  const prev = useMemo(() => {
    if (range) {
      const len = Math.round((Date.parse(range.to) - Date.parse(range.from)) / 864e5) + 1
      const to = addDays(range.from, -1)
      const from = addDays(range.from, -len)
      return { label: `previous ${len} days`, s: sum((all ?? []).filter((d) => d.date >= from && d.date <= to && keep(d))) }
    }
    if (prevMonth && prevQ.data) return { label: mLabel(prevMonth), s: sum(prevQ.data.days.filter(keep)) }
    return null
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, all, prevMonth, prevQ.data, weekday, paid, amount])
  const vs = (now: number, before: number | undefined) => {
    if (!prev || before === undefined || before === 0) return undefined
    const pct = Math.round(((now - before) / before) * 100)
    return (
      <span className={pct < 0 ? 'text-bad-ink' : 'text-ink-2'}>
        {pct > 0 ? '▲' : pct < 0 ? '▼' : '='} {Math.abs(pct)}% vs {prev.label}
      </span>
    )
  }
  const inWindow = useMemo(() => (all ?? []).filter((d) => inRange(d.date, range)).length, [all, range])
  const multiMonth = useMemo(() => new Set(rows.map((d) => d.date.slice(0, 7))).size > 1, [rows])
  const hasDeposits = rows.some((d) => d.basis !== 'TILL' && d.card_pence !== null)
  const otherCaveats = (q.data?.caveats ?? []).filter((c) => !c.startsWith(DEPOSIT_CAVEAT))
  const avg = avgTicket(totals)

  const chips: ActiveFilterChip[] = []
  if (range)
    chips.push({ key: 'range', label: `${shortDate(range.from)} – ${shortDate(range.to)}`, onRemove: () => setRange(null) })
  if (weekday !== 'all')
    chips.push({
      key: 'wd',
      label: WEEKDAY_OPTIONS.find((o) => o.value === weekday)?.label ?? weekday,
      onRemove: () => setWeekday('all'),
    })
  if (paid !== 'all')
    chips.push({ key: 'paid', label: PAID_OPTIONS.find((o) => o.value === paid)?.label ?? paid, onRemove: () => setPaid('all') })
  if (orders !== 'all')
    chips.push({
      key: 'orders',
      label: ORDERS_OPTIONS.find((o) => o.value === orders)?.label ?? orders,
      onRemove: () => setOrders('all'),
    })
  if (amount.min !== null || amount.max !== null)
    chips.push({
      key: 'amount',
      label: `Day total ${amount.min !== null ? gbp(amount.min) : '£0.00'} – ${amount.max !== null ? gbp(amount.max) : 'any'}`,
      onRemove: () => setAmount({ min: null, max: null }),
    })
  const tillChip = (key: string, label: string, clear: () => void) => chips.push({ key, label, onRemove: clear })
  if (channel !== 'all') tillChip('channel', CHANNEL_LABEL[channel] ?? channel, () => setChannel('all'))
  if (category !== 'all') tillChip('category', categoryLabel(category), () => setCategory('all'))
  if (product !== 'all') tillChip('product', product, () => setProduct('all'))
  if (size !== 'all') tillChip('size', SIZE_LABEL[size] ?? size, () => setSize('all'))
  const clearAll = () => {
    setRange(null)
    setWeekday('all')
    setPaid('all')
    setOrders('all')
    setAmount({ min: null, max: null })
    setChannel('all')
    setCategory('all')
    setProduct('all')
    setSize('all')
  }

  const openDay = drawer?.kind === 'day' ? ((all ?? []).find((d) => d.date === drawer.date) ?? null) : null

  return (
    <>
      <PageHeader
        title="Sales"
        subtitle="what sold, when, and where the money came from"
        actions={
          <Button variant="primary" onClick={() => setDrawer({ kind: 'add' })}>
            + Add cash
          </Button>
        }
      />
      <PeriodBar period={period} months={barMonths} onPeriod={setPeriod} range={range} onRange={setRange} />
      <div className="flex flex-none flex-col gap-2 border-b border-line px-4 py-2.5 sm:px-5">
        <FilterBar
          label="Filter sales"
          fold
          activeCount={chips.length}
          trailing={
            q.data && (
              <span className="fig text-base text-ink-2">
                {count(rows.length)} of {count(inWindow)} days
              </span>
            )
          }
        >
          <FilterSelect label="Weekday" value={weekday} onChange={setWeekday} options={WEEKDAY_OPTIONS} />
          <FilterSelect
            label="Channel"
            value={channel}
            onChange={setChannel}
            options={[{ value: 'all', label: 'Any channel' }, ...(opts?.channels ?? []).map((c) => ({ value: c, label: CHANNEL_LABEL[c] ?? c }))]}
          />
          <FilterSelect
            label="Category"
            value={category}
            onChange={(v) => {
              setCategory(v)
              setProduct('all')
            }}
            options={[{ value: 'all', label: 'Any category' }, ...(opts?.categories ?? []).map((c) => ({ value: c, label: categoryLabel(c) }))]}
          />
          <FilterSelect
            label="Product"
            value={product}
            onChange={setProduct}
            options={[{ value: 'all', label: 'Any product' }, ...(opts?.products ?? []).map((p) => ({ value: p, label: p }))]}
          />
          <FilterSelect
            label="Size"
            value={size}
            onChange={setSize}
            options={[{ value: 'all', label: 'Any size' }, ...(opts?.sizes ?? []).map((z) => ({ value: z, label: SIZE_LABEL[z] ?? z }))]}
          />
          <FilterSelect label="Paid by" value={paid} onChange={setPaid} options={PAID_OPTIONS} />
          <FilterSelect label="Orders" value={orders} onChange={setOrders} options={ORDERS_OPTIONS} />
          <AmountRange label="Day total" value={amount} onChange={setAmount} />
        </FilterBar>
        <ActiveFilters chips={chips} onClearAll={clearAll} />
      </div>
      <div className="flex min-h-0 flex-1">
        <PageBody flush>
          <div className="px-4 pb-8 sm:px-5 compact:px-6">
            <nav aria-label="Sections" className="sticky top-0 z-10 -mx-4 flex gap-1 overflow-x-auto border-b border-line bg-surface/95 px-4 py-2 backdrop-blur-[2px] sm:-mx-5 sm:px-5 compact:-mx-6 compact:px-6">
              {SECTIONS.map((x) => (
                <a key={x.id} href={`#${x.id}`} onClick={jump(x.id)} className="flex min-h-8 items-center whitespace-nowrap rounded-full px-3 text-base font-semibold text-ink-2 hover:bg-canvas hover:text-ink">
                  {x.label}
                </a>
              ))}
            </nav>

            {till.isError ? (
              <ErrorBox error={till.error} what="till sales" />
            ) : !till.data ? (
              <Loading what="Loading till sales" />
            ) : (
              <TillSection
                data={till.data}
                dimmed={till.isPlaceholderData}
                onDrill={drill}
                active={{ channel, category, product, size, weekday }}
                note={takingsOnly ? 'Paid by, orders and day total apply to takings only, not to these till lines.' : undefined}
              />
            )}

            <section id="takings" aria-labelledby="takings-title" className="scroll-mt-14 pt-12">
              <SectionTitle id="takings-title" title="Takings" source="Card and cash, imported or added here" />
              {tillOnly && (
                <p className="mt-2 text-sm font-semibold text-ink-2">
                  Channel, category, product and size apply to till sales only. Takings are whole-day totals.
                </p>
              )}
              {q.isError ? (
                <ErrorBox error={q.error} what="sales" />
              ) : !q.data ? (
                <Loading what="Loading takings" />
              ) : inWindow === 0 ? (
                <div className="mt-4 rounded-card border border-dashed border-line-strong px-4 py-6 text-base text-ink-2">
                  <p className="font-bold text-ink">No takings entered for this period.</p>
                  <p className="mt-1">
                    Card and cash totals come from the finance workbook or a payment export. Import them with{' '}
                    <code className="rounded-xs bg-canvas px-1 py-0.5 font-mono text-sm text-ink">cafeops import-finance --commit</code>, or add a
                    day&rsquo;s cash with <b>+ Add cash</b>.
                  </p>
                </div>
              ) : (
                <>
                    <Figures
                      items={[
                        {
                          label: 'Total',
                          value: gbp(totals.total),
                          strong: true,
                          sub: (
                            <>
                              {count(totals.days)} {totals.days === 1 ? 'day' : 'days'}
                              {prev && vs(totals.total, prev.s.total) ? <> · {vs(totals.total, prev.s.total)}</> : null}
                            </>
                          ),
                        },
                        { label: 'Card', value: gbp(totals.card), sub: vs(totals.card, prev?.s.card) },
                        {
                          label: 'Per trading day',
                          value: totals.days ? gbp(Math.round(totals.total / totals.days)) : '—',
                          sub: prev && prev.s.days ? vs(totals.total / Math.max(1, totals.days), prev.s.total / prev.s.days) : undefined,
                        },
                        {
                          label: 'Cash',
                          value: gbp(totals.till + totals.own),
                          sub: totals.own > 0 ? `${gbp(totals.till)} till · ${gbp(totals.own)} own` : undefined,
                        },
                        { label: 'Orders', value: totals.orders > 0 ? count(totals.orders) : '—' },
                        {
                          label: 'Avg ticket',
                          value: avg === null ? '—' : gbp(avg),
                          sub: totals.orders > 0 && totals.totalWithOrders !== totals.total ? 'days with orders only' : undefined,
                        },
                      ]}
                    />
                    {(hasDeposits || otherCaveats.length > 0) && (
                      <div className="flex flex-col gap-0.5 pt-2 text-sm text-ink-2">
                        {hasDeposits && (
                          <p>
                            <span className="text-ink-3">†</span> Card to Mar 2026 is the Mettle deposit on the day it landed (about a day
                            after the sale), not that day&rsquo;s till takings.
                          </p>
                        )}
                        {otherCaveats.length > 0 && (
                          <details>
                            <summary className="cursor-pointer select-none">
                              {otherCaveats.length} {otherCaveats.length === 1 ? 'note' : 'notes'} on these figures
                            </summary>
                            <ul className="mt-1 flex flex-col gap-0.5 pl-3">
                              {otherCaveats.map((c) => (
                                <li key={c}>{c}</li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </div>
                    )}
                  <SalesDashboard rows={rows} />
                </>
              )}
            </section>

            <section id="profit" className="scroll-mt-14 pt-12">
              <ProfitSection />
            </section>

            <section id="days" aria-labelledby="days-h" className="scroll-mt-14 pt-12">
              <h2 id="days-h" className="border-b-2 border-ink pb-2 text-2xl font-extrabold tracking-[-.01em]">
                Day by day
              </h2>
              {q.data && (
                <SalesTable
                  rows={rows}
                  multiMonth={multiMonth}
                  totals={totals}
                  selected={openDay?.date ?? null}
                  onOpen={(d) => setDrawer({ kind: 'day', date: d })}
                />
              )}
            </section>
          </div>
        </PageBody>
        {drawer?.kind === 'add' && <CashDrawer day={null} onClose={() => setDrawer(null)} />}
        {openDay && <CashDrawer key={openDay.date} day={openDay} onClose={() => setDrawer(null)} />}
      </div>
    </>
  )
}

/* ----------------------------------------------------------------- table --- */

function Money({ pence, dagger }: { pence: number | null; dagger?: boolean }) {
  if (pence === null) return dash
  return (
    <>
      {gbp(pence)}
      <span
        aria-hidden={!dagger}
        title={dagger ? 'Bank deposit date, not till takings' : undefined}
        className="inline-block w-[0.7em] text-left text-ink-3"
      >
        {dagger ? '†' : ''}
      </span>
    </>
  )
}

function Totals({ s, label, className }: { s: Sums; label: ReactNode; className: string }) {
  const avg = avgTicket(s)
  return (
    <tr className={className}>
      <td className="whitespace-nowrap py-2 pl-2 pr-2">{label}</td>
      <td className="fig px-2 text-right">
        {gbp(s.card)}
        <span className="inline-block w-[0.7em]" />
      </td>
      <td className="fig px-2 text-right">
        {gbp(s.till)}
        <span className="inline-block w-[0.7em]" />
      </td>
      <td className="fig px-2 text-right">
        {gbp(s.own)}
        <span className="inline-block w-[0.7em]" />
      </td>
      <td className="fig px-2 text-right">{gbp(s.total)}</td>
      <td className="fig px-2 text-right">{s.orders > 0 ? count(s.orders) : '—'}</td>
      <td className="fig px-2 text-right">{avg === null ? '—' : gbp(avg)}</td>
      <td />
    </tr>
  )
}

function SalesTable({
  rows,
  multiMonth,
  totals,
  selected,
  onOpen,
}: {
  rows: SalesDay[]
  multiMonth: boolean
  totals: Sums
  selected: string | null
  onOpen: (date: string) => void
}) {
  const body: ReactNode[] = []
  const flush = (month: string, list: SalesDay[]) => {
    if (!multiMonth || list.length === 0) return
    const s = sum(list)
    body.push(
      <Totals
        key={`sub-${month}`}
        s={s}
        className="border-b-2 border-line-strong bg-canvas-2 text-base font-bold"
        label={
          <>
            {mLabel(month)}{' '}
            <span className="font-normal text-ink-2">
              · {s.days} {s.days === 1 ? 'day' : 'days'}
            </span>
          </>
        }
      />,
    )
  }
  let month = ''
  let bucket: SalesDay[] = []
  let prev: string | null = null
  for (const d of rows) {
    const m = d.date.slice(0, 7)
    if (m !== month) {
      flush(month, bucket)
      month = m
      bucket = []
      prev = null
    }
    bucket.push(d)
    const weekend = isWeekend(d.date)
    // A heavier rule where a new week starts (the first row after a Sunday).
    const newWeek = prev !== null && d.weekday === 'Mon'
    prev = d.date
    const [, mon] = fd(d.date).split(' ').slice(1)
    body.push(
      <Tr
        key={d.date}
        onClick={() => onOpen(d.date)}
        selected={selected === d.date}
        label={`Open ${fd(d.date)}`}
        className={cx(weekend && selected !== d.date && 'bg-canvas-2', newWeek && 'border-t-2 border-t-line-strong')}
      >
        <Td className="whitespace-nowrap pl-2!">
          <span className={cx('inline-block w-9', weekend ? 'font-bold text-ink' : 'text-ink-2')}>{d.weekday}</span>
          <span className="fig inline-block w-5 text-right">{Number(d.date.slice(8, 10))}</span>{' '}
          <span className="text-ink-2">{mon}</span>
        </Td>
        <Td numeric>
          <Money pence={d.card_pence} dagger={d.basis !== 'TILL' && d.card_pence !== null} />
        </Td>
        <Td numeric>
          <Money pence={d.cash_till_pence} />
        </Td>
        <Td numeric>
          <Money pence={d.cash_off_till_pence} />
        </Td>
        <Td numeric strong>
          {gbp(d.total_pence)}
        </Td>
        <Td
          numeric
          title={
            d.orders_source === 'pos'
              ? 'Counted from Lightspeed receipts'
              : d.orders_source === 'payment_export'
                ? 'From the payment export'
                : undefined
          }
        >
          {d.orders === null ? dash : count(d.orders)}
        </Td>
        <Td numeric className="text-ink-2">
          {d.avg_ticket_pence === null ? dash : gbp(d.avg_ticket_pence)}
        </Td>
        <Td secondary title={d.note ?? undefined}>
          {d.note ?? ''}
        </Td>
      </Tr>,
    )
  }
  flush(month, bucket)

  return (
    <div className="pt-3">
      <Table header="upper" stickyHeader minWidth={760} label="Sales by day">
        <THead>
          <tr>
            <Th width={112} className="pl-2!">
              Day
            </Th>
            <Th numeric>Card</Th>
            <Th numeric>Till cash</Th>
            <Th numeric>Own cash</Th>
            <Th numeric>Total</Th>
            <Th numeric width={70}>
              Orders
            </Th>
            <Th numeric width={90}>
              Avg ticket
            </Th>
            <Th width="22%">Note</Th>
          </tr>
        </THead>
        <TBody>
          {body}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={8} className="p-10 text-center text-md text-ink-2">
                No days match these filters.
              </td>
            </tr>
          ) : (
            <Totals
              s={totals}
              className="border-t-2 border-ink text-md font-extrabold [&>td]:py-2.5"
              label={`${count(totals.days)} ${totals.days === 1 ? 'day' : 'days'}`}
            />
          )}
        </TBody>
      </Table>
    </div>
  )
}

/* ---------------------------------------------------------------- drawer --- */

/**
 * `day === null`: "Add cash" for any date (creates the day if it has no row).
 * Otherwise the day's figures, where they came from, and its cash to edit.
 */
function CashDrawer({ day, onClose }: { day: SalesDay | null; onClose: () => void }) {
  const [operator] = useOperator()
  const refresh = useInvalidateFinance()
  const [date, setDate] = useState(day?.date ?? londonToday())
  const [till, setTill] = useState(penceToPounds(day?.cash_till_pence ?? null))
  const [own, setOwn] = useState(penceToPounds(day?.cash_off_till_pence ?? null))
  const [note, setNote] = useState(day?.note ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const tillLocked = day !== null && !day.editable.cash_till
  const ownLocked = day !== null && !day.editable.cash_off_till

  async function submit() {
    const t = poundsToPence(till)
    const o = poundsToPence(own)
    if (t.kind === 'bad') return setError(`Till cash: ${t.message}`)
    if (o.kind === 'bad') return setError(`Own cash: ${o.message}`)
    const tillP = t.kind === 'value' ? t.value : null
    const ownP = o.kind === 'value' ? o.value : null
    if ((tillP ?? 0) < 0 || (ownP ?? 0) < 0) return setError('Cash cannot be negative.')
    if (!date) return setError('Pick a date.')
    const noteV = note.trim() || null
    setError(null)
    setBusy(true)
    try {
      let target = day
      if (target === null) {
        if (tillP === null && ownP === null) return setError('Enter till cash, own cash, or both.')
        // The day may already have a row (card imported): add the cash to it.
        const monthData = await financeApi.sales(date.slice(0, 7))
        target = monthData.days.find((d) => d.date === date) ?? null
      }
      if (target === null) {
        const r = await financeWrite.createDay({
          date,
          cash_till_pence: tillP,
          cash_off_till_pence: ownP,
          note: noteV,
          operator,
        })
        if (r.kind !== 'ok') return setError(r.message)
      } else {
        const body: SalesDayIn = {}
        if (target.editable.cash_till && tillP !== target.cash_till_pence && (day !== null || tillP !== null))
          body.cash_till_pence = tillP
        if (target.editable.cash_off_till && ownP !== target.cash_off_till_pence && (day !== null || ownP !== null))
          body.cash_off_till_pence = ownP
        if (day === null) {
          // Adding to an existing day keeps its note; a typed one is appended.
          if (noteV !== null && noteV !== target.note) body.note = target.note ? `${target.note} · ${noteV}` : noteV
        } else if (noteV !== target.note) body.note = noteV
        if (Object.keys(body).length > 0) {
          const r = await financeWrite.patchDay(target.date, { ...body, operator })
          if (r.kind !== 'ok') return setError(r.message)
        }
      }
      await refresh()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer
      open
      onClose={onClose}
      title={day ? `${fd(day.date)} ${day.date.slice(0, 4)}` : 'Add cash'}
      context={day ? 'Sales day' : 'Till cash and own cash for one day'}
      footer={
        <>
          <Button variant="ghost" className="flex-1" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" className="flex-1" onClick={() => void submit()} disabled={busy}>
            {busy ? 'Saving…' : day ? 'Save cash' : 'Add cash'}
          </Button>
        </>
      }
    >
      {day && (
        <section className="flex flex-col gap-2">
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 text-base">
            {(
              [
                ['Card', day.card_pence],
                ['Till cash', day.cash_till_pence],
                ['Own cash', day.cash_off_till_pence],
              ] as const
            ).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="border-b border-line-row py-1.5 text-ink-2">{k}</dt>
                <dd className="fig border-b border-line-row py-1.5 text-right">{v === null ? '—' : gbp(v)}</dd>
              </div>
            ))}
            <dt className="py-1.5 font-bold">Total</dt>
            <dd className="fig py-1.5 text-right font-extrabold">{gbp(day.total_pence)}</dd>
            <dt className="border-t border-line-row py-1.5 text-ink-2">Orders</dt>
            <dd className="fig border-t border-line-row py-1.5 text-right">
              {day.orders === null ? '—' : count(day.orders)}
            </dd>
            <dt className="py-1.5 text-ink-2">Avg ticket</dt>
            <dd className="fig py-1.5 text-right">{day.avg_ticket_pence === null ? '—' : gbp(day.avg_ticket_pence)}</dd>
          </dl>
          {(day.sources.length > 0 || day.orders_source === 'pos') && (
            <ul className="flex flex-col gap-0.5 text-sm text-ink-2">
              {day.sources.map((s) => (
                <li key={s.method}>
                  {METHOD_NAME[s.method] ?? s.method}: {SOURCE_LABEL[s.source] ?? s.source}
                  {s.source_ref ? <span className="text-ink-3"> · {s.source_ref}</span> : null}
                </li>
              ))}
              {day.basis !== 'TILL' && <li>Card is a Mettle bank deposit on this date, not till takings.</li>}
              {day.orders_source === 'pos' && <li>Orders counted from Lightspeed receipts.</li>}
            </ul>
          )}
        </section>
      )}
      <section className="flex flex-col gap-3">
        {day && <h3 className="text-md font-extrabold">Cash</h3>}
        {!day && (
          <Field label="Date">
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Till cash" hint={tillLocked ? 'From an export' : 'Rung on the till'}>
            <MoneyInput value={till} disabled={tillLocked} placeholder="0.00" onChange={(e) => setTill(e.target.value)} />
          </Field>
          <Field label="Own cash" hint={ownLocked ? 'From an export' : 'Not rung on the till'}>
            <MoneyInput value={own} disabled={ownLocked} placeholder="0.00" onChange={(e) => setOwn(e.target.value)} />
          </Field>
        </div>
        <Field label="Note" hint="Optional">
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <p className="text-sm text-ink-2">
          Card takings are imported from Mettle and Lightspeed, not typed.
          {day
            ? ' Empty a cash box to clear it.'
            : ' If that day already has cash, what you enter here replaces it.'}
        </p>
        {error && (
          <p role="alert" className="text-sm text-bad-ink">
            {error}
          </p>
        )}
      </section>
    </Drawer>
  )
}
