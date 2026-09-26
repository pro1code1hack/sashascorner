/**
 * Money → Sales › Profit: what used to be the Profit & loss screen (owner,
 * 2026-09-26: "consolidated into sales graphs"). Worked out from the sales and
 * expenses logs by `GET /api/finance/pl`, one figure set per month.
 *
 *   Takings and costs   grouped bars per month (takings blue, costs amber;
 *                       palette checked with the dataviz validator: CVD ΔE 26.8,
 *                       contrast ≥ 3:1 on white)
 *   Net profit          bars above/below zero per month; a loss is alert
 *   Full P&L            the month-by-line table, folded away below the charts
 *
 * Invariant 8 throughout: a month with takings but no running costs entered has
 * no cost or profit bar -- a dashed outline and "no costs" -- never a zero;
 * unreported delivery commission and ads are left out and said so.
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { ErrorBox, Loading, cx } from '../../components/ui'
import { usePL } from '../../lib/finance-api'
import type { PLResponse, PeriodFigures } from '../../lib/types/finance'
import { Caveats, gbp0, pctBp } from './shared'

const TAKINGS = 'var(--color-chart-card)'
const COSTS = 'var(--color-chart-costs)'

/** Everything that comes off takings before net profit; null when not known. */
function costsOf(f: PeriodFigures): number | null {
  if (f.expenses_missing) return null
  return f.cogs_pence + f.opex_pence + (f.delivery_commission_pence ?? 0) + (f.delivery_ads_pence ?? 0)
}

export function ProfitSection() {
  const q = usePL()
  return (
    <section aria-labelledby="sales-profit" className="mt-6 flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="sales-profit" className="text-xl font-extrabold tracking-[-.01em]">
          Profit by month
        </h2>
        <span className="text-sm text-ink-2">
          Every month, not the filters above. Before tax; equipment and personal spending kept out.
        </span>
      </div>
      {q.isError ? (
        <ErrorBox error={q.error} what="the profit and loss" />
      ) : !q.data ? (
        <Loading what="Adding it up" />
      ) : q.data.months.length === 0 ? (
        <p className="py-6 text-center text-md text-ink-2">No sales or expenses entered yet.</p>
      ) : (
        <Body data={q.data} />
      )}
    </section>
  )
}

function Body({ data }: { data: PLResponse }) {
  const t = data.total
  const tc = costsOf(t)
  return (
    <>
      <p className="text-base">
        <span className="text-ink-2">All months: </span>
        <strong className="fig">{gbp0(t.revenue_pence)}</strong> taken
        {tc === null ? (
          <span className="text-ink-2"> · costs not complete, so no profit figure</span>
        ) : (
          <>
            {' · '}
            <strong className="fig">{gbp0(tc)}</strong> costs{' · '}
            <strong className={cx('fig', t.net_warn && 'text-alert')}>
              {t.net_profit_pence === null ? 'profit unknown' : `${gbp0(t.net_profit_pence)} net profit`}
            </strong>
          </>
        )}
        {t.stock_pct_bp !== null && (
          <span className={cx('text-ink-2', t.stock_pct_warn && 'font-bold text-alert')}>
            {' · '}stock {pctBp(t.stock_pct_bp)} of takings
          </span>
        )}
      </p>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <TakingsCosts cols={data.columns} />
        <NetProfit cols={data.columns} />
      </div>
      <details className="rounded-card border border-line">
        <summary className="cursor-pointer select-none px-3 py-2.5 text-base font-bold">
          Full profit &amp; loss by month
        </summary>
        <div className="border-t border-line p-3">
          <Matrix data={data} />
        </div>
      </details>
      <Caveats items={data.caveats} />
    </>
  )
}

/* -------------------------------------------------------- takings/costs --- */

const H = 180

function TakingsCosts({ cols }: { cols: PeriodFigures[] }) {
  const [hover, setHover] = useState<string | null>(null)
  const max = Math.max(1, ...cols.flatMap((f) => [f.revenue_pence, costsOf(f) ?? 0]))
  const y = (p: number) => Math.max(2, (p / max) * (H - 8))
  const hovered = cols.find((f) => f.period === hover) ?? null
  return (
    <figure className="min-w-0" aria-label="Takings and costs by month">
      <figcaption className="mb-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-2">
        <span className="font-bold text-ink">Takings and costs</span>
        <Key color={TAKINGS} label="Takings" />
        <Key color={COSTS} label="Costs" />
        <Key dashed label="Costs not entered" />
      </figcaption>
      <div className="relative">
        <div className="flex items-end gap-2 border-b border-line" style={{ height: H }} onMouseLeave={() => setHover(null)}>
          {cols.map((f) => {
            const c = costsOf(f)
            return (
              <div
                key={f.period}
                className={cx('flex min-w-0 flex-1 items-end justify-center gap-[2px] rounded-t', hover === f.period && 'bg-canvas')}
                style={{ height: H }}
                onMouseEnter={() => setHover(f.period)}
                tabIndex={0}
                onFocus={() => setHover(f.period)}
                onBlur={() => setHover(null)}
                aria-label={`${f.label}: takings ${gbp0(f.revenue_pence)}, ${c === null ? 'no costs entered' : `costs ${gbp0(c)}`}`}
              >
                <div className="w-full max-w-5 rounded-t" style={{ height: y(f.revenue_pence), background: TAKINGS }} />
                {c === null ? (
                  <div
                    className="w-full max-w-5 border-[1.5px] border-b-0 border-dashed border-line-strong"
                    style={{ height: H * 0.3 }}
                  />
                ) : (
                  <div className="w-full max-w-5 rounded-t" style={{ height: y(c), background: COSTS }} />
                )}
              </div>
            )
          })}
        </div>
        {hovered && <Tip f={hovered} left={cols.indexOf(hovered) >= cols.length / 2} />}
      </div>
      <Axis cols={cols} />
    </figure>
  )
}

/* ----------------------------------------------------------- net profit --- */

function NetProfit({ cols }: { cols: PeriodFigures[] }) {
  const [hover, setHover] = useState<string | null>(null)
  const vals = cols.map((f) => (f.expenses_missing ? null : f.net_profit_pence))
  const max = Math.max(1, ...vals.map((v) => (v === null ? 0 : Math.abs(v))))
  const half = H / 2
  const hovered = cols.find((f) => f.period === hover) ?? null
  return (
    <figure className="min-w-0" aria-label="Net profit by month">
      <figcaption className="mb-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-2">
        <span className="font-bold text-ink">Net profit</span>
        <span>above the line a profit, below it a loss</span>
      </figcaption>
      <div className="relative">
        <div className="relative flex gap-2" style={{ height: H }} onMouseLeave={() => setHover(null)}>
          <div className="absolute inset-x-0 border-t border-ink-3" style={{ top: half }} aria-hidden="true" />
          {cols.map((f, i) => {
            const v = vals[i] ?? null
            const h = v === null ? 0 : Math.max(2, (Math.abs(v) / max) * (half - 6))
            return (
              <div
                key={f.period}
                className={cx('relative min-w-0 flex-1 rounded', hover === f.period && 'bg-canvas')}
                onMouseEnter={() => setHover(f.period)}
                tabIndex={0}
                onFocus={() => setHover(f.period)}
                onBlur={() => setHover(null)}
                aria-label={`${f.label}: ${v === null ? 'no costs entered, so no profit figure' : `net profit ${gbp0(v)}`}`}
              >
                {v === null ? (
                  <div className="absolute inset-x-1/4 border-[1.5px] border-dashed border-line-strong" style={{ top: half - 12, height: 24 }} />
                ) : (
                  <div
                    className={cx('absolute inset-x-0 mx-auto max-w-7', v >= 0 ? 'rounded-t' : 'rounded-b', v < 0 || f.net_warn ? 'bg-alert' : 'bg-brand')}
                    style={v >= 0 ? { bottom: half, height: h } : { top: half, height: h }}
                  />
                )}
              </div>
            )
          })}
        </div>
        {hovered && <Tip f={hovered} left={cols.indexOf(hovered) >= cols.length / 2} />}
      </div>
      <Axis cols={cols} />
    </figure>
  )
}

/* -------------------------------------------------------------- pieces --- */

function Axis({ cols }: { cols: PeriodFigures[] }) {
  return (
    <div className="mt-1 flex gap-2">
      {cols.map((f) => (
        <div key={f.period} className="min-w-0 flex-1 truncate text-center text-xs text-ink-2" title={f.label}>
          {f.short_label}
        </div>
      ))}
    </div>
  )
}

function Key({ color, label, dashed }: { color?: string; label: string; dashed?: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className={cx('inline-block size-2.5 rounded-sm', dashed && 'border-[1.5px] border-dashed border-line-strong')}
        style={color ? { background: color } : undefined}
      />
      {label}
    </span>
  )
}

function Tip({ f, left }: { f: PeriodFigures; left: boolean }) {
  const c = costsOf(f)
  const row = (label: string, value: ReactNode, strong?: boolean) => (
    <div className="flex justify-between gap-4">
      <span className="text-ink-2">{label}</span>
      <span className={cx('fig', strong && 'font-bold')}>{value}</span>
    </div>
  )
  return (
    <div
      className={cx(
        'pointer-events-none absolute top-0 z-10 w-56 rounded-card border border-line bg-surface px-3 py-2 text-sm shadow-login',
        left ? 'left-0' : 'right-0',
      )}
    >
      <div className="mb-1 font-bold">{f.label}</div>
      {row('Takings', gbp0(f.revenue_pence), true)}
      {f.expenses_missing ? (
        <div className="text-ink-2">No running costs entered for this month.</div>
      ) : (
        <>
          {row('Stock', gbp0(f.cogs_pence))}
          {row('Running costs', gbp0(f.opex_pence))}
          {row('App commission', f.delivery_commission_pence === null ? 'not reported' : gbp0(f.delivery_commission_pence))}
          {row('App ads', f.delivery_ads_pence === null ? 'not reported' : gbp0(f.delivery_ads_pence))}
          {row('Costs', c === null ? '—' : gbp0(c), true)}
          {row('Net profit', f.net_profit_pence === null ? '—' : gbp0(f.net_profit_pence), true)}
        </>
      )}
      <div className="mt-1 text-xs text-ink-2">{f.trading_days} trading days</div>
    </div>
  )
}

/* --------------------------------------------------------- full matrix --- */
/* Moved from the retired ProfitLossScreen: one column per month, then Total. */

type CellV = { text: string; warn?: boolean; title?: string; quiet?: boolean; est?: boolean }
interface Row {
  key: string
  label: string
  big?: boolean
  cell: (f: PeriodFigures) => CellV
}

const money = (p: number | null, missing: string): CellV =>
  p === null ? { text: '—', title: missing, quiet: true } : { text: gbp0(p) }
const minus = (p: number | null, missing: string): CellV =>
  p === null ? { text: '—', title: missing, quiet: true } : { text: gbp0(p === 0 ? 0 : -p) }
const byCat = (list: { category: string; pence: number }[], name: string) =>
  list.find((c) => c.category === name)?.pence ?? 0

function noCosts(f: PeriodFigures): CellV | null {
  return f.expenses_missing ? { text: 'no costs', title: `No running costs entered for ${f.label}`, quiet: true } : null
}

function rows(data: PLResponse): Row[] {
  const out: Row[] = [
    { key: 'card', label: 'Card sales', cell: (f) => money(f.card_pence, '') },
    { key: 'cash', label: 'Cash sales', cell: (f) => money(f.cash_till_pence + f.cash_off_till_pence, '') },
    {
      key: 'delivery',
      label: 'Delivery apps (gross)',
      cell: (f) =>
        f.delivery_gross_pence === null
          ? { text: '—', title: f.delivery_missing.join('; ') || 'not uploaded', quiet: true }
          : {
              text: gbp0(f.delivery_gross_pence),
              title: f.delivery_missing.length ? `Also missing: ${f.delivery_missing.join('; ')}` : undefined,
            },
    },
    { key: 'takings', label: 'Takings', big: true, cell: (f) => money(f.revenue_pence, '') },
  ]
  for (const name of data.cogs_categories) {
    out.push({ key: `cogs-${name}`, label: name, cell: (f) => noCosts(f) ?? minus(byCat(f.cogs_by_category, name), '') })
  }
  out.push(
    {
      key: 'wo',
      label: 'Stock written off',
      cell: (f) => ({
        ...minus(f.written_off_pence, 'some write-offs have no cost'),
        warn: (f.written_off_pence ?? 0) > 0,
        est: f.written_off_is_estimate,
      }),
    },
    { key: 'stockpct', label: 'Stock % of takings', cell: (f) => noCosts(f) ?? { text: pctBp(f.stock_pct_bp), warn: f.stock_pct_warn } },
    { key: 'gp', label: 'Gross profit', big: true, cell: (f) => noCosts(f) ?? money(f.gross_profit_pence, '') },
  )
  for (const name of data.opex_categories) {
    out.push({ key: `opex-${name}`, label: name, cell: (f) => noCosts(f) ?? minus(byCat(f.opex_by_category, name), '') })
  }
  out.push(
    { key: 'comm', label: 'Delivery app commission', cell: (f) => minus(f.delivery_commission_pence, 'not reported: left out, not zero') },
    { key: 'ads', label: 'Delivery app ads', cell: (f) => minus(f.delivery_ads_pence, 'not reported: left out, not zero') },
    { key: 'net', label: 'Net profit', big: true, cell: (f) => noCosts(f) ?? { ...money(f.net_profit_pence, ''), warn: f.net_warn } },
    { key: 'capital', label: 'Equipment / setup (not in P&L)', cell: (f) => minus(f.capital_pence, '') },
    { key: 'drawings', label: 'Personal spending (not in P&L)', cell: (f) => minus(f.drawings_pence, '') },
  )
  return out
}

function Matrix({ data }: { data: PLResponse }) {
  const cols = [...data.columns, data.total]
  const grid = { gridTemplateColumns: `minmax(200px,1.6fr) repeat(${cols.length}, minmax(90px,1fr))` }
  const base = 'whitespace-nowrap px-2 py-1.5'
  const line = (big?: boolean) => (big ? 'border-b-2 border-ink text-lg font-bold' : 'border-b-[1.5px] border-dashed border-line text-lg')
  return (
    <>
      <div className="scroll-x min-w-0 max-w-full">
        <div role="table" aria-label="Profit and loss by month" className="grid min-w-[900px]" style={grid}>
          <div role="row" className="contents">
            <div role="columnheader" className={cx(base, 'sticky left-0 z-[1] bg-surface')}>
              <span className="sr-only">Line</span>
            </div>
            {cols.map((f, i) => (
              <div
                role="columnheader"
                key={f.period}
                title={f.label}
                className={cx(base, 'text-right font-bold text-ink-2', i === cols.length - 1 && 'bg-canvas')}
              >
                {f.short_label}
              </div>
            ))}
          </div>
          {rows(data).map((r) => (
            <div role="row" key={r.key} className="contents">
              <div role="rowheader" className={cx(base, 'sticky left-0 z-[1] bg-surface', line(r.big))}>
                {r.label}
              </div>
              {cols.map((f, i) => {
                const v = r.cell(f)
                return (
                  <div
                    role="cell"
                    key={f.period}
                    title={v.title}
                    className={cx(
                      base,
                      'fig text-right',
                      i === cols.length - 1 && 'bg-canvas',
                      line(r.big),
                      v.warn ? 'text-alert' : v.quiet ? 'text-sm font-normal text-ink-2' : undefined,
                      v.est && 'italic',
                    )}
                  >
                    {v.text}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>
      <p className="mt-2.5 text-sm text-ink-2">
        Stock costs above {pctBp(data.threshold_bp)} of takings are marked. Before tax. “—” means not reported or not
        uploaded: missing, not zero.
      </p>
    </>
  )
}
