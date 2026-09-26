/**
 * Money → Profit & loss: worked out from sales and expenses (finance.md 1.5).
 *
 * One column per month with data, then Total. Whole pounds. A month with
 * takings but no running costs entered shows "no costs" where its profit would
 * be: the design showed April 2026 as £1,293 of pure profit. Missing delivery
 * figures read "—" with the reason on hover; they are never zero.
 */
import type { ReactNode } from 'react'
import { ErrorBox, Loading, PageBody, PageHeader, cx } from '../../components/ui'
import { usePL } from '../../lib/finance-api'
import type { PLResponse, PeriodFigures } from '../../lib/types/finance'
import { Caveats, gbp0, pctBp } from './shared'

export function ProfitLossScreen() {
  const q = usePL()
  return (
    <>
      <PageHeader title="Profit & loss" subtitle="worked out from sales and expenses" />
      <PageBody>
        <p className="mb-2.5 text-base text-ink-2">
          Worked out from the sales and expenses logs. Equipment and personal spending are kept out of running costs.
        </p>
        {q.isError ? (
          <ErrorBox error={q.error} what="the profit and loss" />
        ) : !q.data ? (
          <Loading what="Adding it up" />
        ) : q.data.months.length === 0 ? (
          <p className="p-10 text-center text-md text-ink-2">No sales or expenses entered yet.</p>
        ) : (
          <Matrix data={q.data} />
        )}
      </PageBody>
    </>
  )
}

type CellV = { text: string; warn?: boolean; title?: string; quiet?: boolean; est?: boolean }
interface Row {
  key: string
  label: string
  big?: boolean
  cell: (f: PeriodFigures) => CellV
}

const money = (p: number | null, missing: string, title?: string): CellV =>
  p === null ? { text: '—', title: title ?? missing, quiet: true } : { text: gbp0(p) }
const minus = (p: number | null, missing: string): CellV =>
  p === null ? { text: '—', title: missing, quiet: true } : { text: gbp0(p === 0 ? 0 : -p) }
const byCat = (list: { category: string; pence: number }[], name: string) =>
  list.find((c) => c.category === name)?.pence ?? 0

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
    {
      key: 'stockpct',
      label: 'Stock % of takings',
      cell: (f) => noCosts(f) ?? { text: pctBp(f.stock_pct_bp), warn: f.stock_pct_warn },
    },
    { key: 'gp', label: 'Gross profit', big: true, cell: (f) => noCosts(f) ?? money(f.gross_profit_pence, '') },
  )
  for (const name of data.opex_categories) {
    out.push({ key: `opex-${name}`, label: name, cell: (f) => noCosts(f) ?? minus(byCat(f.opex_by_category, name), '') })
  }
  out.push(
    {
      key: 'comm',
      label: 'Delivery app commission',
      cell: (f) => minus(f.delivery_commission_pence, 'not reported: left out, not zero'),
    },
    { key: 'ads', label: 'Delivery app ads', cell: (f) => minus(f.delivery_ads_pence, 'not reported: left out, not zero') },
    {
      key: 'net',
      label: 'Net profit',
      big: true,
      cell: (f) => noCosts(f) ?? { ...money(f.net_profit_pence, ''), warn: f.net_warn },
    },
    { key: 'capital', label: 'Equipment / setup (not in P&L)', cell: (f) => minus(f.capital_pence, '') },
    { key: 'drawings', label: 'Personal spending (not in P&L)', cell: (f) => minus(f.drawings_pence, '') },
  )
  return out
}

function noCosts(f: PeriodFigures): CellV | null {
  return f.expenses_missing
    ? { text: 'no costs', title: `No running costs entered for ${f.label}`, quiet: true }
    : null
}

function Matrix({ data }: { data: PLResponse }) {
  const cols = [...data.columns, data.total]
  const grid = { gridTemplateColumns: `minmax(200px,1.6fr) repeat(${cols.length}, minmax(90px,1fr))` }
  const cellBase = 'whitespace-nowrap px-2 py-1.5'
  const cell = (key: string, content: ReactNode, i: number, extra?: string, title?: string) => (
    <div
      role="cell"
      key={key}
      title={title}
      className={cx(cellBase, 'fig text-right', i === cols.length - 1 && 'bg-canvas', extra)}
    >
      {content}
    </div>
  )
  return (
    <>
      <div className="scroll-x min-w-0 max-w-full">
        <div role="table" aria-label="Profit and loss by month" className="grid min-w-[900px]" style={grid}>
          <div role="row" className="contents">
            <div role="columnheader" className={cx(cellBase, 'sticky left-0 z-[1] bg-surface font-bold text-ink-2')}>
              <span className="sr-only">Line</span>
            </div>
            {cols.map((f, i) =>
              cell(`h-${f.period}`, <span className="font-bold text-ink-2">{f.short_label}</span>, i, undefined, f.label),
            )}
          </div>
          {rows(data).map((r) => (
            <div role="row" key={r.key} className="contents">
              <div
                role="rowheader"
                className={cx(
                  cellBase,
                  'sticky left-0 z-[1] bg-surface',
                  r.big ? 'border-b-2 border-ink text-lg font-bold' : 'border-b-[1.5px] border-dashed border-line text-lg',
                )}
              >
                {r.label}
              </div>
              {cols.map((f, i) => {
                const v = r.cell(f)
                return cell(
                  `${r.key}-${f.period}`,
                  v.text,
                  i,
                  cx(
                    r.big ? 'border-b-2 border-ink text-lg font-bold' : 'border-b-[1.5px] border-dashed border-line text-lg',
                    v.warn ? 'text-alert' : v.quiet ? 'text-sm font-normal text-ink-2' : undefined,
                    v.est && 'italic',
                  ),
                  v.title,
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
      <Caveats items={data.caveats} className="mt-1.5" />
    </>
  )
}
