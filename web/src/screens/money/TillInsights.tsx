/**
 * Money -> Sales: the charts that only the till can answer, plus the small
 * pieces every Sales chart shares. Reads `SalesInsights`
 * (`GET /api/finance/sales/insights`, Lightspeed receipt lines).
 *
 *   When people buy     weekday x hour heatmap of receipts per day open
 *   Best sellers        products by £ or units, with the size split
 *   By category         share of till sales per category
 *   Size mix            units by cup size
 *   Items per receipt   basket size distribution
 *
 * The combined charts (over time, where it came from, by weekday, best days)
 * live in SalesDashboard.tsx, where till lines sit next to the takings ledger
 * without ever being added to it.
 *
 * Every chart here can drill: click a product, category, channel, size or
 * weekday and it becomes a filter in the bar above, which re-scopes the page.
 *
 * Colour jobs (dataviz): one series -> brand; channels -> the validated
 * --color-chart-{instore,deliveroo,justeat,other} order; heatmap and sizes ->
 * the one-hue --color-seq-* ramp. Text never wears a series colour.
 */
import { useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { Pill, Segmented, cx } from '../../components/ui'
import type { SalesInsights } from '../../lib/types/finance'
import { count } from './filters'
import { fd, gbp } from './shared'

export type Drill = 'channel' | 'category' | 'product' | 'size' | 'weekday' | 'paid'

export const NO_CATEGORY = '__none__'
/** Every channel the till can name, in display order. Offered as a filter even
 *  before a channel has any lines, so Deliveroo and Just Eat are always there. */
export const CHANNEL_ORDER = ['EPOS', 'CASH', 'DELIVEROO', 'JUST_EAT', 'WEB', 'OTHER'] as const
export const CHANNEL_LABEL: Record<string, string> = {
  EPOS: 'In store',
  /** Cash taken past the till, typed into the bot or here (DECISIONS 28). */
  CASH: 'Cash (off till)',
  DELIVEROO: 'Deliveroo',
  JUST_EAT: 'Just Eat',
  /** Collected online orders (docs/shop/CONTRACT.md §3.1). */
  WEB: 'Website',
  OTHER: 'Other',
}
export const CHANNEL_COLOR: Record<string, string> = {
  EPOS: 'var(--color-chart-instore)',
  CASH: 'var(--color-chart-cash)',
  DELIVEROO: 'var(--color-chart-deliveroo)',
  JUST_EAT: 'var(--color-chart-justeat)',
  WEB: 'var(--color-seq-3)',
  OTHER: 'var(--color-chart-other)',
}
export const channelColor = (key: string) => CHANNEL_COLOR[key] ?? CHANNEL_COLOR.OTHER ?? 'var(--color-chart-other)'
export const SIZE_LABEL: Record<string, string> = { S: 'Small', M: 'Medium', XL: 'Large', ONE: 'One size' }
const SIZE_COLOR: Record<string, string> = {
  S: 'var(--color-seq-2)',
  M: 'var(--color-seq-3)',
  XL: 'var(--color-seq-5)',
  ONE: 'var(--color-chart-other)',
}
const sizeColor = (key: string) => SIZE_COLOR[key] ?? 'var(--color-chart-other)'
const HEAT = ['var(--color-seq-1)', 'var(--color-seq-2)', 'var(--color-seq-3)', 'var(--color-seq-4)', 'var(--color-seq-5)']
export const WD_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
export const WD_LONG = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays']

export const categoryLabel = (c: string) => (c === NO_CATEGORY ? 'No category' : c)

/** A Decimal-as-text quantity for display: thousands separators on whole numbers. */
export function qtyText(q: string): string {
  return /^-?\d+$/.test(q) ? count(Number(q)) : q
}
/** Layout only (bar lengths, shares): never money arithmetic. */
export const num = (q: string) => Number(q)

export function hourLabel(h: number): string {
  const suffix = h < 12 ? 'am' : 'pm'
  const hh = h % 12 === 0 ? 12 : h % 12
  return `${hh}${suffix}`
}

export function pct(part: number, whole: number): string {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : '—'
}

/* -------------------------------------------------------- shared pieces --- */

export function SectionTitle({
  id,
  title,
  source,
  demo = false,
  range,
  children,
}: {
  id: string
  title: string
  source: string
  demo?: boolean
  range?: string
  children?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b-2 border-ink pb-2">
      <h2 id={id} className="text-2xl font-extrabold tracking-[-.01em]">
        {title}
      </h2>
      <Pill tone="muted">{source}</Pill>
      {demo && <Pill tone="warn">Demo data</Pill>}
      <span className="flex-1" />
      {range && <span className="fig text-base text-ink-2">{range}</span>}
      {children}
    </div>
  )
}

/** "▲ 12% vs Mon 1 Sep – …": direction by glyph and word. A fall is not a crossed
 *  threshold, so it is ink-2 like a rise (design law: red only for a threshold). */
export function Delta({ now, prev, vs }: { now: number | null; prev: number | null | undefined; vs?: string }) {
  if (now === null || prev === null || prev === undefined || prev === 0) return vs ? <span className="text-ink-3">no earlier period to compare</span> : null
  const p = Math.round(((now - prev) / Math.abs(prev)) * 100)
  const glyph = p > 0 ? '▲' : p < 0 ? '▼' : '='
  return (
    <span className="text-ink-2" title={vs ? `Compared with ${vs}` : 'Compared with the period before'}>
      <span aria-hidden="true">{glyph}</span> {p === 0 ? 'level' : `${Math.abs(p)}% ${p > 0 ? 'up' : 'down'}`}
      {vs && <span className="text-ink-3"> vs {vs}</span>}
    </span>
  )
}

export function ChartHead({ id, title, sub, children }: { id: string; title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h3 id={id} className="text-xl font-extrabold tracking-[-.01em]">
          {title}
        </h3>
        <span className="flex-1" />
        {children}
      </div>
      {sub && <p className="mt-0.5 text-sm text-ink-2">{sub}</p>}
    </div>
  )
}

/** A small uppercase label that groups rows inside one chart panel. */
export function GroupLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx('text-xs font-bold uppercase tracking-[.06em] text-ink-3', className)}>{children}</p>
}

/** A clickable list row that applies a filter. Keyboard and screen-reader friendly. */
export function DrillRow({ children, onClick, active, label }: { children: ReactNode; onClick: () => void; active: boolean; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={active ? 'Showing only this. Click again to clear.' : label}
      className={cx(
        'flex min-h-10 w-full items-center gap-3 border-b border-line-row px-1.5 text-left text-base hover:bg-canvas-2',
        active && 'bg-brand-wash hover:bg-brand-wash',
      )}
    >
      {children}
    </button>
  )
}

/* -------------------------------------------------------------- heatmap --- */

export function WhenHeatmap({ data, onDrill }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void }) {
  const [hover, setHover] = useState<{ w: number; h: number } | null>(null)
  const hours = data.by_hour.map((h) => h.hour)
  const openDays = data.by_weekday.map((w) => w.trading_days)
  const cell = useMemo(() => {
    const m = new Map<string, { receipts: number; gross: number }>()
    for (const c of data.heat) m.set(`${c.weekday}-${c.hour}`, { receipts: c.receipts, gross: c.gross_pence })
    return m
  }, [data.heat])
  // Average receipts in that hour, per day that weekday was open.
  const avgOf = (w: number, h: number) => {
    const c = cell.get(`${w}-${h}`)
    const n = openDays[w] ?? 0
    return c && n > 0 ? c.receipts / n : 0
  }
  const max = Math.max(0, ...WD_SHORT.flatMap((_, w) => hours.map((h) => avgOf(w, h))))
  const bin = (v: number) => (v <= 0 || max <= 0 ? -1 : Math.min(HEAT.length - 1, Math.floor((v / max) * HEAT.length - 1e-9)))
  const rows = WD_SHORT.map((_, w) => w).filter((w) => (openDays[w] ?? 0) > 0)
  const hv = hover ? { avg: avgOf(hover.w, hover.h), c: cell.get(`${hover.w}-${hover.h}`) } : null

  // One tab stop for the whole grid (WCAG 2.4.3): the arrow keys move between
  // cells, Home/End run along a row, and the weekday header is column 0 so
  // Enter on it drills. Every other cell is tabIndex -1. The grid is itself the
  // accessible table (row and column headers, a labelled gridcell per hour), so
  // it needs no sr-only twin.
  const [pos, setPos] = useState({ r: 0, c: 1 })
  const cells = useRef(new Map<string, HTMLElement>())
  const keyOf = (r: number, c: number) => `${r}-${c}`
  const focusR = Math.min(pos.r, Math.max(0, rows.length - 1))
  const focusC = Math.min(pos.c, hours.length)
  const tab = (r: number, c: number) => (focusR === r && focusC === c ? 0 : -1)
  const setRef = (r: number, c: number) => (el: HTMLElement | null) => {
    if (el) cells.current.set(keyOf(r, c), el)
    else cells.current.delete(keyOf(r, c))
  }
  const move = (r: number, c: number) => {
    setPos({ r, c })
    cells.current.get(keyOf(r, c))?.focus()
  }
  const onKey = (e: KeyboardEvent, r: number, c: number) => {
    const lastR = rows.length - 1
    const lastC = hours.length
    let nr = r
    let nc = c
    if (e.key === 'ArrowRight') nc = Math.min(lastC, c + 1)
    else if (e.key === 'ArrowLeft') nc = Math.max(0, c - 1)
    else if (e.key === 'ArrowDown') nr = Math.min(lastR, r + 1)
    else if (e.key === 'ArrowUp') nr = Math.max(0, r - 1)
    else if (e.key === 'Home') nc = 0
    else if (e.key === 'End') nc = lastC
    else return
    e.preventDefault()
    move(nr, nc)
  }

  return (
    <section aria-labelledby="heat-h" className="min-w-0">
      <ChartHead id="heat-h" title="When people buy" sub="Average receipts in each hour, per day open. Click a day to see only that weekday." />
      <p id="heat-keys" className="sr-only">
        Use the arrow keys to move between hours and days; Home and End run along a row.
      </p>
      <div className="scroll-x">
        <div
          role="grid"
          aria-label="Average receipts per hour, by weekday"
          aria-describedby="heat-keys"
          className="grid min-w-[420px] gap-[2px]"
          style={{ gridTemplateColumns: `44px repeat(${hours.length}, minmax(0, 1fr))` }}
          onMouseLeave={() => setHover(null)}
        >
          <div role="row" className="contents">
            <span role="columnheader">
              <span className="sr-only">Weekday</span>
            </span>
            {hours.map((h) => (
              <span key={h} role="columnheader" className="fig pb-1 text-center text-label text-ink-3">
                {hourLabel(h)}
              </span>
            ))}
          </div>
          {rows.map((w, r) => (
            <div key={w} role="row" className="contents">
              {/* A rowheader wrapping a real button: the drill is a visible link, not a bare label. */}
              <span role="rowheader" className="flex items-center">
                <button
                  type="button"
                  ref={setRef(r, 0)}
                  tabIndex={tab(r, 0)}
                  onKeyDown={(e) => onKey(e, r, 0)}
                  onClick={() => onDrill('weekday', String(w))}
                  className="h-8 w-full rounded-control pr-2 text-left text-sm font-semibold text-brand-ink underline decoration-brand-line underline-offset-2 hover:bg-canvas-2 hover:decoration-brand"
                >
                  {WD_SHORT[w]}
                  <span className="sr-only">: show only {WD_LONG[w]}</span>
                </button>
              </span>
              {hours.map((h, i) => {
                const c = i + 1
                const v = avgOf(w, h)
                const b = bin(v)
                const on = hover?.w === w && hover.h === h
                return (
                  <span
                    key={h}
                    role="gridcell"
                    ref={setRef(r, c)}
                    tabIndex={tab(r, c)}
                    onKeyDown={(e) => onKey(e, r, c)}
                    aria-label={`${WD_LONG[w]} ${hourLabel(h)}: ${v.toFixed(1)} receipts on average`}
                    onMouseEnter={() => setHover({ w, h })}
                    onFocus={() => {
                      setPos({ r, c })
                      setHover({ w, h })
                    }}
                    onBlur={() => setHover(null)}
                    // An empty hour is a hairline outline, not a paler fill: the
                    // lightest bin and "nothing" must not read as the same thing.
                    className={cx('h-8 rounded-[4px] outline-offset-1', b < 0 && 'border border-line bg-surface', on && 'ring-2 ring-ink')}
                    style={b < 0 ? undefined : { background: HEAT[b] }}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-2">
        <span className="flex items-center gap-1.5" aria-hidden="true">
          Fewer
          {HEAT.map((c) => (
            <span key={c} className="h-3 w-5 rounded-[3px]" style={{ background: c }} />
          ))}
          More
          <span className="ml-1 h-3 w-5 rounded-[3px] border border-line bg-surface" /> none
        </span>
        <span className="fig min-h-5">
          {hover && hv ? (
            <>
              <b className="text-ink">{WD_SHORT[hover.w]} {hourLabel(hover.h)}</b> · {hv.avg.toFixed(1)} receipts a day
              {hv.c ? ` · ${gbp(hv.c.gross)} in all` : ''}
            </>
          ) : (
            `Darkest: about ${max.toFixed(1)} receipts an hour`
          )}
        </span>
      </div>
    </section>
  )
}

/* ---------------------------------------------------------- best sellers --- */

type Metric = 'gross' | 'qty'

export function BestSellers({ data, onDrill, active }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void; active?: string }) {
  const [metric, setMetric] = useState<Metric>('gross')
  const [all, setAll] = useState(false)
  const sorted = useMemo(
    () => [...data.products].sort((a, b) => (metric === 'gross' ? b.gross_pence - a.gross_pence : num(b.qty) - num(a.qty)) || a.name.localeCompare(b.name)),
    [data.products, metric],
  )
  const shown = all ? sorted : sorted.slice(0, 8)
  const value = (p: (typeof sorted)[number]) => (metric === 'gross' ? p.gross_pence : num(p.qty))
  const max = Math.max(1, ...sorted.map(value))
  const total = sorted.reduce((n, p) => n + value(p), 0)

  return (
    <section aria-labelledby="till-best-h" className="min-w-0">
      <ChartHead id="till-best-h" title="Best sellers" sub={`${count(sorted.length)} ${sorted.length === 1 ? 'product' : 'products'} sold, every size together. Click one to see only it.`}>
        <Segmented<Metric>
          label="Rank by"
          value={metric}
          onChange={setMetric}
          options={[
            { value: 'gross', label: 'Sales £' },
            { value: 'qty', label: 'Units' },
          ]}
        />
      </ChartHead>
      <ol className="flex flex-col">
        {shown.map((p, i) => {
          const v = value(p)
          const sizes = Object.entries(p.sizes)
          return (
            <li key={p.name}>
              <DrillRow active={active === p.name} onClick={() => onDrill('product', p.name)} label={`Only ${p.name}`}>
                <span className="fig w-5 flex-none text-right text-sm text-ink-3">{i + 1}</span>
                <span className="min-w-0 flex-1 py-1.5">
                  <span className="flex items-baseline gap-2">
                    <span className="truncate font-semibold text-ink">{p.name}</span>
                    {p.category && <span className="hidden truncate text-sm text-ink-3 sm:inline">{p.category}</span>}
                  </span>
                  <span className="mt-1 block h-2 rounded-full bg-canvas">
                    <span className="block h-2 rounded-full bg-brand" style={{ width: `${(v / max) * 100}%` }} />
                  </span>
                  {sizes.length > 1 && (
                    <span className="fig mt-0.5 block text-sm text-ink-2">
                      {sizes.map(([s, q]) => `${s === 'ONE' ? 'One' : s} ${qtyText(q)}`).join(' · ')}
                    </span>
                  )}
                </span>
                <span className="w-24 flex-none text-right">
                  <span className="fig block font-bold text-ink">{metric === 'gross' ? gbp(p.gross_pence) : qtyText(p.qty)}</span>
                  <span className="fig block text-sm text-ink-2">
                    {metric === 'gross' ? `${qtyText(p.qty)} sold` : gbp(p.gross_pence)} · {pct(v, total)}
                  </span>
                </span>
              </DrillRow>
            </li>
          )
        })}
      </ol>
      {sorted.length > 8 && (
        <button type="button" onClick={() => setAll((a) => !a)} className="mt-2 min-h-8 text-base font-semibold text-brand-ink hover:underline">
          {all ? 'Show top 8' : `Show all ${count(sorted.length)}`}
        </button>
      )}
    </section>
  )
}

/* ------------------------------------------------------------ categories --- */

export function Categories({ data, onDrill, active }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void; active?: string }) {
  const max = Math.max(1, ...data.by_category.map((c) => c.gross_pence))
  const total = data.by_category.reduce((n, c) => n + c.gross_pence, 0)
  return (
    <section aria-labelledby="cat-h" className="min-w-0">
      <ChartHead id="cat-h" title="By category" />
      <ul className="flex flex-col">
        {data.by_category.map((c) => (
          <li key={c.key}>
            <DrillRow active={active === c.key} onClick={() => onDrill('category', c.key)} label={`Only ${categoryLabel(c.key)}`}>
              <span className="min-w-0 flex-1 py-1.5">
                <span className={cx('block truncate', c.key === NO_CATEGORY ? 'text-ink-2' : 'text-ink')}>{categoryLabel(c.key)}</span>
                <span className="mt-1 block h-2 rounded-full bg-canvas">
                  <span className="block h-2 rounded-full bg-brand" style={{ width: `${(c.gross_pence / max) * 100}%` }} />
                </span>
              </span>
              <span className="fig w-20 flex-none text-right font-bold">{gbp(c.gross_pence)}</span>
              <span className="fig w-10 flex-none text-right text-ink-2">{pct(c.gross_pence, total)}</span>
            </DrillRow>
          </li>
        ))}
      </ul>
    </section>
  )
}

/* -------------------------------------------------------------- size mix --- */

export function SizeMix({ data, onDrill, active }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void; active?: string }) {
  const total = data.by_size.reduce((n, s) => n + num(s.qty), 0)
  if (data.by_size.length === 0) return null
  return (
    <section aria-labelledby="size-h" className="min-w-0">
      <ChartHead id="size-h" title="Size mix" sub="Units sold by cup size." />
      <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full" aria-hidden="true">
        {data.by_size.map((s) => (
          <span key={s.key} style={{ flexGrow: Math.max(0, num(s.qty)), background: sizeColor(s.key) }} />
        ))}
      </div>
      <ul className="mt-3 flex flex-col">
        {data.by_size.map((s) => (
          <li key={s.key}>
            <DrillRow active={active === s.key} onClick={() => onDrill('size', s.key)} label={`Only ${SIZE_LABEL[s.key] ?? s.key}`}>
              <span aria-hidden="true" className="size-3 flex-none rounded-[3px]" style={{ background: sizeColor(s.key) }} />
              <span className="flex-1 text-ink">{SIZE_LABEL[s.key] ?? s.key}</span>
              <span className="fig text-ink-2">{gbp(s.gross_pence)}</span>
              <span className="fig w-14 text-right font-bold">{qtyText(s.qty)}</span>
              <span className="fig w-10 text-right text-ink-2">{pct(num(s.qty), total)}</span>
            </DrillRow>
          </li>
        ))}
      </ul>
    </section>
  )
}

/* --------------------------------------------------------------- baskets --- */

export function Baskets({ data }: { data: SalesInsights }) {
  const total = data.baskets.reduce((n, b) => n + b.receipts, 0)
  const max = Math.max(1, ...data.baskets.map((b) => b.receipts))
  if (total === 0) return null
  return (
    <section aria-labelledby="basket-h" className="min-w-0">
      <ChartHead id="basket-h" title="Items per receipt" sub="How many things people buy at once." />
      <div className="flex h-32 items-end gap-3">
        {data.baskets.map((b) => (
          <div key={b.items} className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1" role="img" aria-label={`${b.items} ${b.items === '1' ? 'item' : 'items'}: ${b.receipts} receipts, ${pct(b.receipts, total)}`}>
            <span className="fig text-xs font-bold text-ink">{pct(b.receipts, total)}</span>
            <span className="w-full max-w-6 rounded-t-[4px] bg-brand" style={{ height: `${(b.receipts / max) * 100}%`, minHeight: b.receipts ? 2 : 0 }} />
            <span className="fig text-sm text-ink-2">{b.items}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

/** "Tue 1 Sep – Wed 30 Sep" for a section title. */
export const rangeLabel = (since: string, until: string) => `${fd(since)} – ${fd(until)}`
