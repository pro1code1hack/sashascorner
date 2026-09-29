/**
 * Money → Overview: how the month is going (finance.md 1.1).
 *
 * Left, the month statement with the previous month beside it. Right, takings
 * day by day, where the money went, and what needs a look.
 *
 * Fixes over the design: a day with nothing entered is drawn as a dash (the
 * design drew nothing while its caption promised dashes); the last-day label is
 * an ordinal ("31st", not "31th"); "Needs a look" is coral only when something
 * does; a month with takings but no costs entered shows no profit figure rather
 * than an inflated one.
 */
import { ChartTable, ErrorBox, Loading, PageBody, PageHeader, cx } from '../../components/ui'
import { navigate } from '../../lib/router'
import { useOverview } from '../../lib/finance-api'
import type { FinanceOverview, PeriodFigures } from '../../lib/types/finance'
import { Caveats, MonthBar, gbp, gbp0, monthLong, pctBp, useFinancePeriod } from './shared'

export function OverviewScreen() {
  const { period, setPeriod, months } = useFinancePeriod()
  const q = useOverview(period)
  return (
    <>
      <PageHeader title="Money" subtitle="how the month is going" />
      <MonthBar period={period} months={months} onChange={setPeriod} />
      <PageBody>
        {q.isError ? (
          <ErrorBox error={q.error} what="the month" />
        ) : !q.data ? (
          <Loading what="Working out the month" />
        ) : (
          <Overview data={q.data} />
        )}
      </PageBody>
    </>
  )
}

type Value = { kind: 'money'; pence: number } | { kind: 'text'; text: string } | { kind: 'blank' }

interface Line {
  key: string
  label: string
  now: Value
  prev: Value
  big?: boolean
  indent?: boolean
  warn?: boolean
  est?: boolean
}

const money = (p: number | null | undefined, missing: string): Value =>
  p === null || p === undefined ? { kind: 'text', text: missing } : { kind: 'money', pence: p }
const neg = (p: number | null | undefined, missing: string): Value =>
  p === null || p === undefined ? { kind: 'text', text: missing } : { kind: 'money', pence: -p }

const NO_COSTS = 'no costs entered'
const unreported = (x: PeriodFigures) => (x.delivery_gross_pence === null ? 'not uploaded' : 'not reported')

function statement(c: PeriodFigures, p: PeriodFigures | null): Line[] {
  const blank: Value = { kind: 'blank' }
  const pv = (f: (x: PeriodFigures) => Value): Value => (p ? f(p) : blank)
  const lines: Line[] = [
    {
      key: 'takings',
      label: 'Takings',
      now: money(c.revenue_pence, '—'),
      prev: pv((x) => money(x.revenue_pence, '—')),
      big: true,
    },
    {
      key: 'card',
      label: 'Card',
      now: money(c.card_pence, '—'),
      prev: pv((x) => money(x.card_pence, '—')),
      indent: true,
    },
    {
      key: 'cash',
      label: 'Cash',
      now: money(c.cash_pence, '—'),
      prev: pv((x) => money(x.cash_pence, '—')),
      indent: true,
    },
  ]
  if (c.delivery_gross_pence || p?.delivery_gross_pence) {
    lines.push({
      key: 'delivery',
      label: 'Delivery apps (customers paid)',
      now: money(c.delivery_gross_pence, 'not uploaded'),
      prev: pv((x) => money(x.delivery_gross_pence, 'not uploaded')),
      indent: true,
    })
  }
  const stock = (x: PeriodFigures): Value =>
    x.expenses_missing ? { kind: 'text', text: NO_COSTS } : neg(x.stock_bought_pence, '—')
  lines.push(
    { key: 'stock', label: 'Stock bought', now: stock(c), prev: pv(stock) },
    {
      key: 'stockpct',
      label: 'Stock as % of takings',
      now: { kind: 'text', text: pctBp(c.stock_pct_bp) },
      prev: pv((x) => ({ kind: 'text', text: pctBp(x.stock_pct_bp) })),
      indent: true,
      warn: c.stock_pct_warn,
    },
  )
  if (c.written_off_pence || p?.written_off_pence || c.written_off_pence === null) {
    lines.push({
      key: 'wo',
      label: 'Stock written off',
      now: neg(c.written_off_pence, 'not priced'),
      prev: pv((x) => neg(x.written_off_pence, 'not priced')),
      indent: true,
      warn: (c.written_off_pence ?? 0) > 0,
      est: c.written_off_is_estimate,
    })
  }
  lines.push({
    key: 'gp',
    label: 'Gross profit',
    now: money(c.gross_profit_pence, NO_COSTS),
    prev: pv((x) => money(x.gross_profit_pence, NO_COSTS)),
    big: true,
  })
  const hasDelivery = (x: PeriodFigures | null) => !!x && (x.delivery_gross_pence ?? 0) > 0
  if (hasDelivery(c) || hasDelivery(p)) {
    lines.push(
      {
        key: 'comm',
        label: 'Delivery app commission',
        now: neg(c.delivery_commission_pence, unreported(c)),
        prev: pv((x) => neg(x.delivery_commission_pence, unreported(x))),
        indent: true,
      },
      {
        key: 'ads',
        label: 'Delivery app ads',
        now: neg(c.delivery_ads_pence, unreported(c)),
        prev: pv((x) => neg(x.delivery_ads_pence, unreported(x))),
        indent: true,
      },
    )
  }
  const cats: string[] = []
  for (const x of [c, p]) {
    x?.opex_by_category.forEach((o) => {
      if (!cats.includes(o.category)) cats.push(o.category)
    })
  }
  for (const name of cats) {
    const find = (x: PeriodFigures) => x.opex_by_category.find((o) => o.category === name)?.pence ?? 0
    const opex = (x: PeriodFigures): Value =>
      x.expenses_missing ? { kind: 'text', text: NO_COSTS } : { kind: 'money', pence: -find(x) }
    lines.push({ key: `opex-${name}`, label: name, now: opex(c), prev: pv(opex), indent: true })
  }
  lines.push({
    key: 'net',
    label: 'Net profit (before tax)',
    now: money(c.net_profit_pence, NO_COSTS),
    prev: pv((x) => money(x.net_profit_pence, NO_COSTS)),
    big: true,
    warn: c.net_warn,
  })
  return lines
}

function Figure({ v, warn, prev, est }: { v: Value; warn?: boolean; prev?: boolean; est?: boolean }) {
  if (v.kind === 'blank') return <span />
  if (v.kind === 'text') {
    return (
      <span className={cx('text-right text-sm font-normal', !prev && warn ? 'text-alert' : 'text-ink-2')}>
        {v.text}
      </span>
    )
  }
  return (
    <span
      className={cx(
        'fig text-right',
        prev ? 'text-ink-2' : warn ? 'text-alert' : 'text-ink',
        est && 'italic',
      )}
    >
      {gbp(v.pence)}
    </span>
  )
}

const ROW3 = 'grid grid-cols-[minmax(0,1fr)_minmax(5.25rem,110px)_minmax(5.25rem,110px)] gap-2.5'
const ROW2 = 'grid grid-cols-[minmax(0,1fr)_minmax(5.25rem,130px)] gap-2.5'

function Overview({ data }: { data: FinanceOverview }) {
  const c = data.current
  const p = data.previous
  const ROW = p ? ROW3 : ROW2
  const title = c.period === 'all' ? 'Everything so far' : monthLong(c.period)
  const aside =
    c.capital_pence || c.drawings_pence
      ? ` Not counted as running costs: equipment/setup ${gbp0(c.capital_pence)}, personal ${gbp0(c.drawings_pence)}.`
      : ''
  return (
    <div className="grid gap-7 compact:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <section aria-labelledby="stmt-title" className="min-w-0">
        <h2 id="stmt-title" className="text-2xl font-extrabold tracking-[-.01em]">
          {title}
        </h2>
        <p className="mb-3.5 mt-1 text-base text-ink-2">
          {c.trading_days} trading {c.trading_days === 1 ? 'day' : 'days'} entered · {c.expense_count}{' '}
          {c.expense_count === 1 ? 'expense' : 'expenses'}
        </p>
        <div role="table" aria-label={`Statement for ${title}`}>
          {p && (
            <div role="row" className={cx(ROW, 'text-xs text-ink-2')}>
              <span role="columnheader" className="sr-only">
                Line
              </span>
              <span role="columnheader" className="text-right">
                {c.short_label}
              </span>
              <span role="columnheader" className="text-right">
                {p.short_label}
              </span>
            </div>
          )}
          {statement(c, p).map((l) => (
            <div
              role="row"
              key={l.key}
              className={cx(
                ROW,
                'items-start py-1.5',
                l.big
                  ? 'border-b-2 border-ink text-xl font-bold'
                  : 'border-b-[1.5px] border-dashed border-line text-lg',
              )}
            >
              <span role="rowheader" className={cx('min-w-0', l.indent && 'pl-3.5 text-md')}>
                {l.label}
              </span>
              <span role="cell" className="flex justify-end">
                <Figure v={l.now} warn={l.warn} est={l.est} />
              </span>
              {p && (
                <span role="cell" className="flex justify-end text-base font-normal">
                  <Figure v={l.prev} prev est={l.est} />
                </span>
              )}
            </div>
          ))}
        </div>
        <p className="mt-1.5 text-sm text-ink-2">
          {p ? 'Right column: previous month.' : ''}
          {aside}
        </p>
        <Caveats items={data.caveats} className="mt-2" />
      </section>

      <div className="flex min-w-0 flex-col gap-4.5">
        <TakingsChart data={data} />
        <WhereItWent data={data} />
        <NeedsALook data={data} />
      </div>
    </div>
  )
}

interface ChartBar {
  key: string
  value: number | null
  tip: string
}

function TakingsChart({ data }: { data: FinanceOverview }) {
  const ch = data.chart
  const days = ch.mode === 'days'
  const bars: ChartBar[] = days
    ? ch.day_bars.map((b) => ({
        key: b.date,
        value: b.total_pence,
        tip: b.total_pence === null ? `${b.day}: nothing entered` : `${b.day}: ${gbp(b.total_pence)}`,
      }))
    : ch.month_bars.map((b) => ({ key: b.month, value: b.revenue_pence, tip: `${b.label}: ${gbp(b.revenue_pence)}` }))
  const max = Math.max(1, ...bars.map((b) => b.value ?? 0))
  const n = bars.length
  const W = 300
  const H = 170
  const gap = n > 20 ? 2 : 6
  const bw = n ? Math.min(40, (W - gap * (n - 1)) / n) : 0
  return (
    <section aria-labelledby="chart-title">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="chart-title" className="text-lg font-extrabold tracking-[-.01em]">
          Takings, day by day
        </h2>
        <span className="text-base text-ink-2">
          {data.avg_per_day_pence === null ? '' : `about ${gbp0(data.avg_per_day_pence)} a day`}
        </span>
      </div>
      {n === 0 ? (
        <p className="mt-2 text-base text-ink-2">Nothing entered yet.</p>
      ) : (
        // Decorative: the figures are in the table after it, not in hover-only <title>s.
        <svg
          aria-hidden="true"
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="mt-2 block h-[170px] w-full border-b border-line"
        >
          {bars.map((b, i) => {
            const x = i * (bw + gap)
            if (b.value === null || b.value === 0) {
              // The design's caption promises dashes; the design drew nothing.
              return (
                <rect key={b.key} x={x} y={H - 2} width={bw} height={2} className="fill-line-strong">
                  <title>{b.tip}</title>
                </rect>
              )
            }
            const h = Math.max(3, (b.value / max) * (H - 4))
            return (
              <rect key={b.key} x={x} y={H - h} width={bw} height={h} className="fill-brand">
                <title>{b.tip}</title>
              </rect>
            )
          })}
        </svg>
      )}
      <div className="mt-1 flex justify-between gap-2 text-sm text-ink-2">
        <span>{ch.first_label}</span>
        {days && <span className="text-center">dashes = no sales entered that day</span>}
        <span>{ch.last_label}</span>
      </div>
      {n > 0 && (
        <ChartTable
          caption={days ? 'Takings for each day of the month' : 'Takings for each month'}
          columns={[days ? 'Day' : 'Month', 'Takings']}
          rows={
            days
              ? ch.day_bars.map((b) => [b.day, b.total_pence === null ? 'nothing entered' : gbp(b.total_pence)])
              : ch.month_bars.map((b) => [b.label, gbp(b.revenue_pence)])
          }
        />
      )}
    </section>
  )
}

function WhereItWent({ data }: { data: FinanceOverview }) {
  const rows = data.where_it_went
  const max = Math.max(1, ...rows.map((r) => r.pence))
  return (
    <section aria-labelledby="where-title">
      <h2 id="where-title" className="mb-1.5 text-lg font-extrabold tracking-[-.01em]">
        Where the money went
      </h2>
      {rows.length === 0 ? (
        <p className="text-base text-ink-2">No expenses entered for this month.</p>
      ) : (
        <div className="flex flex-col">
          {rows.map((r) => (
            <div
              key={r.category}
              className="grid grid-cols-[minmax(6rem,150px)_minmax(0,1fr)_76px] items-center gap-2.5 py-0.5 text-base"
            >
              <span className="truncate" title={r.category}>
                {r.category}
              </span>
              <span className="h-3.5 border border-line-strong" aria-hidden="true">
                <span className="block h-full bg-brand" style={{ width: `${(r.pence / max) * 100}%` }} />
              </span>
              <span className="fig text-right">{gbp0(r.pence)}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function NeedsALook({ data }: { data: FinanceOverview }) {
  const n = data.needs_a_look
  return (
    <section
      aria-labelledby="look-title"
      className={cx(
        'rounded-card border-[1.5px] border-dashed px-3.5 py-3',
        n.count > 0 ? 'border-alert' : 'border-line',
      )}
    >
      <h2 id="look-title" className="mb-1 text-lg font-extrabold tracking-[-.01em]">
        Needs a look · {n.count}
      </h2>
      {n.flagged.map((f) => (
        <button
          key={f.expense_id}
          type="button"
          onClick={() => navigate('/money/expenses', { query: { review: 1 } })}
          className="flex w-full justify-between gap-2.5 py-1 text-left text-base hover:text-brand"
        >
          <span className="min-w-0 truncate">{f.description}</span>
          <span className="fig flex-none text-ink-2">{gbp(f.amount_pence)}</span>
        </button>
      ))}
      {n.flagged_total > n.flagged.length && (
        <p className="py-0.5 text-sm text-ink-2">and {n.flagged_total - n.flagged.length} more flagged</p>
      )}
      <button
        type="button"
        onClick={() => navigate('/money/expenses', { query: { noreceipt: 1 } })}
        className="flex w-full justify-between gap-2.5 py-1 text-left text-base hover:text-brand"
      >
        <span>Expenses without a receipt</span>
        <span className="fig text-ink-2">{n.without_receipt}</span>
      </button>
    </section>
  )
}
