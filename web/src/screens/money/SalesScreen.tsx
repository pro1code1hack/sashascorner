/**
 * Money → Sales: one dashboard. A period bar and one filter row scope every
 * section below them:
 *
 *   Money in      till sales, card and cash, and the delivery apps' statements,
 *                 side by side in one figures strip and one set of charts
 *                 (SalesDashboard)
 *   What sold     best sellers, categories, sizes, basket sizes (till lines)
 *   Profit by month   every month, not the filters (ProfitSection)
 *   Day by day    one row per trading day, read-only
 *
 * The two ledgers are never added together. A filter that one of them cannot
 * apply (a product means nothing to a card total) is named under the title.
 *
 * Card figures come from imports (Mettle via the workbook, Lightspeed/CSV
 * exports), never from typing. The one thing a person adds here is cash: one
 * Cash figure per day, with a note (DECISIONS 26 retired the till/own split).
 * That happens in a drawer, not in the table.
 *
 * Filters: period and weekday apply to both ledgers. Channel, category,
 * product and size pick till lines; a Deliveroo or Just Eat channel also swaps
 * card and cash for that app's statements. "Paid by" picks card or cash across
 * the takings; "orders" and the £ range pick trading days. The figures strip,
 * the charts and the table are all summed from the same rows, in integer pence.
 */
import { useEffect, useMemo, useState } from 'react'
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
  StatusLine,
  TBody,
  THead,
  Table,
  Td,
  Textarea,
  Th,
  Tr,
  cx,
} from '../../components/ui'
import type { ActiveFilterChip, Outcome } from '../../components/ui'
import { poundsToPence, penceToPounds } from '../../components/confirm/numbers'
import { useOperator } from '../../lib/operator'
import { financeApi, financeWrite, useInvalidateFinance, useSales, useSalesInsights } from '../../lib/finance-api'
import type { InsightFilters, SalesDay, SalesDayIn } from '../../lib/types/finance'
import {
  AmountRange,
  PeriodBar,
  WEEKDAY_OPTIONS,
  count,
  inPence,
  inRange,
  isWeekend,
  lastDayOfMonth,
  matchesWeekday,
  shortDate,
  windowOf,
} from './filters'
import type { DateRange, PenceRange } from './filters'
import { ProfitSection } from './ProfitSection'
import { PAID_OPTIONS, SalesDashboard, avgTicket, sumDays, takenOf } from './SalesDashboard'
import type { Paid, Sums } from './SalesDashboard'
import { CHANNEL_LABEL, CHANNEL_ORDER, SIZE_LABEL, categoryLabel } from './TillInsights'
import type { Drill } from './TillInsights'
import { addDays, fd, gbp, londonToday, mLabel, useFinancePeriod } from './shared'

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
// CASH_OFF_TILL is a retired method (DECISIONS 26): an old row still reads as Cash.
const METHOD_NAME: Record<string, string> = { CARD: 'Card', CASH: 'Cash', CASH_OFF_TILL: 'Cash' }

/** The server's long deposit caveat; the screen says it in one line instead. */
const DEPOSIT_CAVEAT = 'Card figures up to March 2026'

/** The weekday filter's value as the insights API's comma list. */
function weekdaysParam(f: string): string | undefined {
  if (f === 'all') return undefined
  if (f === 'weekdays') return '0,1,2,3,4'
  if (f === 'weekends') return '5,6'
  return f
}

/** Under a Paid-by filter a day counts only if it has that kind of money. */
const matchesPaid = (d: SalesDay, paid: Paid) => paid === 'all' || takenOf(d, paid) > 0

const dash = <span className="text-ink-3">—</span>

function shiftMonth(month: string, by: number): string {
  const [y = 0, m = 1] = month.split('-').map(Number)
  const d = new Date(Date.UTC(y, m - 1 + by, 1))
  return d.toISOString().slice(0, 7)
}

const SECTIONS = [
  { id: 'money', label: 'Money in' },
  { id: 'sold', label: 'What sold' },
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
  const [paid, setPaid] = useState<Paid>('all')
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
  // Which section is in view, for `aria-current` on the jump links. The sections
  // only exist once both ledgers have loaded, so the observer is re-attached then.
  const [current, setCurrent] = useState<string>(SECTIONS[0].id)
  const sectionsMounted = Boolean(till.data) && Boolean(q.data)
  useEffect(() => {
    if (!sectionsMounted || typeof IntersectionObserver === 'undefined') return
    const els = SECTIONS.map((s) => document.getElementById(s.id)).filter((el): el is HTMLElement => el !== null)
    const io = new IntersectionObserver(
      (entries) => {
        const hit = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0]
        if (hit) setCurrent(hit.target.id)
      },
      { rootMargin: '-30% 0px -60% 0px', threshold: [0, 0.25, 0.5, 1] },
    )
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [sectionsMounted])
  // Months with takings or till lines, so a till-only install still has a month bar.
  const barMonths = useMemo(() => [...new Set([...months, ...(till.data?.months ?? [])])].sort(), [months, till.data?.months])
  // Every channel the till can name, then anything else it has seen.
  const channelOptions = useMemo(() => [...CHANNEL_ORDER.filter((c) => c !== 'OTHER'), ...(opts?.channels ?? []).filter((c) => !CHANNEL_ORDER.includes(c as (typeof CHANNEL_ORDER)[number]) || c === 'OTHER')], [opts?.channels])
  // Months the window covers: delivery statements exist per month.
  const monthsInView = useMemo(() => {
    const w = windowOf(period, range)
    if (!w) return barMonths
    const out: string[] = []
    for (let m = w.from.slice(0, 7); m <= w.to.slice(0, 7); m = shiftMonth(m, 1)) out.push(m)
    return out
  }, [period, range, barMonths])
  const tillOnly = category !== 'all' || product !== 'all' || size !== 'all'
  const daysOnly = orders !== 'all' || amount.min !== null || amount.max !== null
  const drill = (kind: Drill, value: string) => {
    const toggle = (cur: string, set: (v: string) => void) => set(cur === value ? 'all' : value)
    if (kind === 'channel') toggle(channel, setChannel)
    else if (kind === 'category') toggle(category, setCategory)
    else if (kind === 'product') toggle(product, setProduct)
    else if (kind === 'size') toggle(size, setSize)
    else if (kind === 'paid') setPaid(paid === value ? 'all' : (value as Paid))
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
          inPence(takenOf(d, paid), amount),
      ),
    [all, range, weekday, paid, orders, amount],
  )
  const totals = useMemo(() => sumDays(rows, paid), [rows, paid])
  const keep = (d: SalesDay) => matchesWeekday(d.date, weekday) && matchesPaid(d, paid) && inPence(takenOf(d, paid), amount)
  const prev = useMemo(() => {
    if (range) {
      const len = Math.round((Date.parse(range.to) - Date.parse(range.from)) / 864e5) + 1
      const to = addDays(range.from, -1)
      const from = addDays(range.from, -len)
      return { label: `previous ${len} days`, s: sumDays((all ?? []).filter((d) => d.date >= from && d.date <= to && keep(d)), paid) }
    }
    if (prevMonth && prevQ.data) return { label: mLabel(prevMonth), s: sumDays(prevQ.data.days.filter(keep), paid) }
    return null
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, all, prevMonth, prevQ.data, weekday, paid, amount])
  const inWindow = useMemo(() => (all ?? []).filter((d) => inRange(d.date, range)).length, [all, range])
  const multiMonth = useMemo(() => new Set(rows.map((d) => d.date.slice(0, 7))).size > 1, [rows])
  const hasDeposits = rows.some((d) => d.basis !== 'TILL' && d.card_pence !== null)
  const otherCaveats = (q.data?.caveats ?? []).filter((c) => !c.startsWith(DEPOSIT_CAVEAT))

  // What a filter cannot reach, said once under the title (DECISIONS 24).
  const notes: ReactNode[] = []
  if (tillOnly) notes.push('Category, product and size pick till lines. Card, cash and statements are whole-day and whole-month totals.')
  if (paid !== 'all') notes.push(`Paid by picks ${paid} across card and cash. The till does not record how a receipt was paid, so till lines are unchanged.`)
  if (channel === 'DELIVEROO' || channel === 'JUST_EAT')
    notes.push(`${CHANNEL_LABEL[channel]} pays by monthly statement, so card and cash (till money) are set aside while it is selected.`)
  if (daysOnly) notes.push('Orders and day total pick trading days: they change card and cash, not till lines.')
  if (hasDeposits) notes.push('Card to Mar 2026 is the Mettle deposit on the day it landed (about a day after the sale), not that day\u2019s till takings.')
  for (const c of otherCaveats) notes.push(c)

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
            options={[{ value: 'all', label: 'Any channel' }, ...channelOptions.map((c) => ({ value: c, label: CHANNEL_LABEL[c] ?? c }))]}
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
          <FilterSelect label="Paid by" value={paid} onChange={(v) => setPaid(v as Paid)} options={PAID_OPTIONS} />
          <FilterSelect label="Orders" value={orders} onChange={setOrders} options={ORDERS_OPTIONS} />
          <AmountRange label="Day total" value={amount} onChange={setAmount} />
        </FilterBar>
        <ActiveFilters chips={chips} onClearAll={clearAll} />
      </div>
      <div className="flex min-h-0 flex-1">
        <PageBody flush>
          <div className="px-4 pb-8 sm:px-5 compact:px-6">
            <nav aria-label="Sections" className="sticky top-0 z-10 -mx-4 flex gap-1 overflow-x-auto border-b border-line bg-surface px-4 py-2 sm:-mx-5 sm:px-5 compact:-mx-6 compact:px-6">
              {SECTIONS.map((x) => (
                <a
                  key={x.id}
                  href={`#${x.id}`}
                  onClick={jump(x.id)}
                  aria-current={current === x.id ? 'location' : undefined}
                  className={cx(
                    'flex min-h-8 items-center whitespace-nowrap rounded-full px-3 text-base font-semibold hover:bg-canvas hover:text-ink',
                    current === x.id ? 'bg-brand-wash text-brand-ink' : 'text-ink-2',
                  )}
                >
                  {x.label}
                </a>
              ))}
            </nav>

            {till.isError && <ErrorBox error={till.error} what="till sales" />}
            {q.isError && <ErrorBox error={q.error} what="card and cash" />}
            {(!till.data && !till.isError) || (!q.data && !q.isError) ? (
              <Loading what="Loading sales" />
            ) : (
              <SalesDashboard
                till={till.data ?? null}
                dimmed={till.isPlaceholderData}
                rows={rows}
                totals={totals}
                prev={prev}
                paid={paid}
                channel={channel}
                months={monthsInView}
                onDrill={drill}
                active={{ channel, category, product, size, weekday, paid }}
                notes={notes}
              />
            )}
            {q.data && inWindow === 0 && (
              <div className="mt-4 rounded-card border border-dashed border-line-strong px-4 py-4 text-base text-ink-2">
                <p className="font-bold text-ink">No card or cash entered for this period.</p>
                <p className="mt-1">
                  Card figures are imported by the office; add a day&rsquo;s cash with <b>+ Add cash</b>.
                </p>
              </div>
            )}

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
                  paid={paid}
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
      {/* The dagger's meaning is printed under the table and said here in words,
          not only in a hover title. */}
      <span aria-hidden="true" className="inline-block w-[0.7em] text-left text-ink-3">
        {dagger ? '†' : ''}
      </span>
      {dagger && <span className="sr-only"> (bank deposit date, not till takings)</span>}
    </>
  )
}

/** Under a Paid-by filter the table shows just that column; Total would repeat it. */
const showCol = (paid: Paid, col: 'card' | 'cash' | 'total') => paid === 'all' || paid === col

function Totals({ s, paid, label, className }: { s: Sums; paid: Paid; label: ReactNode; className: string }) {
  const avg = avgTicket(s)
  return (
    <tr className={className}>
      <td className="whitespace-nowrap py-2 pl-2 pr-2">{label}</td>
      {showCol(paid, 'card') && (
        <td className="fig px-2 text-right">
          {gbp(s.card)}
          <span className="inline-block w-[0.7em]" />
        </td>
      )}
      {showCol(paid, 'cash') && (
        <td className="fig px-2 text-right">
          {gbp(s.cash)}
          <span className="inline-block w-[0.7em]" />
        </td>
      )}
      {showCol(paid, 'total') && <td className="fig px-2 text-right">{gbp(s.total)}</td>}
      <td className="fig px-2 text-right">{s.orders > 0 ? count(s.orders) : '—'}</td>
      <td className="fig px-2 text-right">{avg === null ? '—' : gbp(avg)}</td>
      <td />
    </tr>
  )
}

function SalesTable({
  rows,
  paid,
  multiMonth,
  totals,
  selected,
  onOpen,
}: {
  rows: SalesDay[]
  paid: Paid
  multiMonth: boolean
  totals: Sums
  selected: string | null
  onOpen: (date: string) => void
}) {
  const body: ReactNode[] = []
  const cols = 4 + ['card', 'cash', 'total'].filter((c) => showCol(paid, c as 'card' | 'cash' | 'total')).length
  const flush = (month: string, list: SalesDay[]) => {
    if (!multiMonth || list.length === 0) return
    const s = sumDays(list, paid)
    body.push(
      <Totals
        key={`sub-${month}`}
        s={s}
        paid={paid}
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
        {showCol(paid, 'card') && (
          <Td numeric strong={paid === 'card'}>
            <Money pence={d.card_pence} dagger={d.basis !== 'TILL' && d.card_pence !== null} />
          </Td>
        )}
        {showCol(paid, 'cash') && (
          <Td numeric strong={paid === 'cash'}>
            <Money pence={d.cash_pence} />
          </Td>
        )}
        {showCol(paid, 'total') && (
          <Td numeric strong>
            {gbp(d.total_pence)}
          </Td>
        )}
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
      <Table header="upper" stickyHeader minWidth={680} label="Sales by day">
        <THead>
          <tr>
            <Th width={112} className="pl-2!">
              Day
            </Th>
            {showCol(paid, 'card') && <Th numeric>Card</Th>}
            {showCol(paid, 'cash') && <Th numeric>Cash</Th>}
            {showCol(paid, 'total') && <Th numeric>Total</Th>}
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
              <td colSpan={cols} className="p-10 text-center text-md text-ink-2">
                No days match these filters.
              </td>
            </tr>
          ) : (
            <Totals
              s={totals}
              paid={paid}
              className="border-t-2 border-ink text-md font-extrabold [&>td]:py-2.5"
              label={`${count(totals.days)} ${totals.days === 1 ? 'day' : 'days'}`}
            />
          )}
        </TBody>
      </Table>
      {rows.some((d) => d.basis !== 'TILL' && d.card_pence !== null) && showCol(paid, 'card') && (
        <p className="mt-2 text-sm text-ink-2">† Card is the bank deposit on that date, not the till&rsquo;s takings for the day.</p>
      )}
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
  const [cash, setCash] = useState(penceToPounds(day?.cash_pence ?? null))
  const [note, setNote] = useState(day?.note ?? '')
  // Field problems sit on their field (aria-describedby via Field); what the
  // server said goes in the always-mounted StatusLine.
  const [fieldErr, setFieldErr] = useState<{ cash?: string; date?: string }>({})
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const setError = (text: string) => setOutcome({ kind: 'error', text })
  const [busy, setBusy] = useState(false)
  const cashLocked = day !== null && !day.editable.cash

  async function submit() {
    const c = poundsToPence(cash)
    const cashP = c.kind === 'value' ? c.value : null
    const problems: typeof fieldErr = {}
    if (c.kind === 'bad') problems.cash = c.message
    else if ((cashP ?? 0) < 0) problems.cash = 'Cash cannot be negative.'
    else if (day === null && cashP === null) problems.cash = 'Enter the cash taken.'
    if (!date) problems.date = 'Pick a date.'
    setFieldErr(problems)
    if (Object.keys(problems).length > 0) return
    const noteV = note.trim() || null
    setOutcome(null)
    setBusy(true)
    try {
      let target = day
      if (target === null) {
        // The day may already have a row (card imported): add the cash to it.
        const monthData = await financeApi.sales(date.slice(0, 7))
        target = monthData.days.find((d) => d.date === date) ?? null
      }
      if (target === null) {
        const r = await financeWrite.createDay({
          date,
          cash_pence: cashP,
          note: noteV,
          operator,
        })
        if (r.kind !== 'ok') return setError(r.message)
      } else {
        const body: SalesDayIn = {}
        if (target.editable.cash && cashP !== target.cash_pence && (day !== null || cashP !== null))
          body.cash_pence = cashP
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
      context={day ? 'Sales day' : 'Cash taken on one day'}
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
                ['Cash', day.cash_pence],
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
          <Field label="Date" error={fieldErr.date}>
            <Input type="date" required value={date} max={londonToday()} onChange={(e) => setDate(e.target.value)} />
          </Field>
        )}
        <Field label="Cash" hint={cashLocked ? 'From an export' : 'Cash taken today'} error={fieldErr.cash}>
          <MoneyInput value={cash} disabled={cashLocked} placeholder="0.00" onChange={(e) => setCash(e.target.value)} />
        </Field>
        <Field label="Note" hint="Optional">
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <p className="text-sm text-ink-2">
          All the cash taken that day, in one figure. Card takings are imported from Mettle and Lightspeed, not typed.
          {day ? ' Empty the box to clear it.' : ' If that day already has cash, what you enter here replaces it.'}
        </p>
        <StatusLine outcome={outcome} />
      </section>
    </Drawer>
  )
}
