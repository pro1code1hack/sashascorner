/**
 * The Sales dashboard (owner, 2026-09-26: "a good modern dashboard … filterable,
 * display the channels of orders … like a chart graph").
 *
 * Everything here is drawn from what the Sales screen already loaded and
 * filtered (the trading days in the window, after weekday / paid-by / £ filters),
 * plus the monthly P&L for the delivery apps, which exist only per month. No
 * figure is computed that the table below cannot be checked against.
 *
 *   Takings over time   stacked bars by how it was paid (card, cash), per
 *                       day / week / month; click a legend key to
 *                       hide that series; hover a bar for its breakdown
 *   Where it came from  share of takings by channel: card and cash at the
 *                       till, plus Deliveroo + Just Eat from the monthly
 *                       statements, with commission and ads. A month not
 *                       uploaded is "missing", never £0 (invariant 8)
 *   By weekday          average takings per weekday
 *   Best days           the five biggest days in view
 *
 * Colours are the --color-chart-* tokens in styles.css. Cash is under 3:1
 * contrast, so every chart carries a legend with values and the day-by-day
 * table below is the table view.
 */
import { useMemo, useState } from 'react'
import { Segmented, cx } from '../../components/ui'
import { usePL } from '../../lib/finance-api'
import type { SalesDay } from '../../lib/types/finance'
import { count, weekdayIndex } from './filters'
import { Legend, Tip, axisMoney, barPath, niceMax, useWidth } from './chartKit'
import { fd, gbp, mLabel } from './shared'

export const SERIES = [
  { key: 'card', label: 'Card', color: 'var(--color-chart-card)' },
  // One cash figure per day (DECISIONS 26): no till/own split, no own-cash series.
  { key: 'cash', label: 'Cash', color: 'var(--color-chart-cash)' },
] as const
const DELIVERY = { label: 'Delivery apps', color: 'var(--color-chart-delivery)' }
type SeriesKey = (typeof SERIES)[number]['key']
type Grain = 'day' | 'week' | 'month'

const valueOf = (d: SalesDay, k: SeriesKey) =>
  k === 'card' ? (d.card_pence ?? 0) : (d.cash_pence ?? 0)

function mondayOf(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() - weekdayIndex(iso))
  return d.toISOString().slice(0, 10)
}

interface Bucket {
  key: string
  label: string
  long: string
  days: number
  v: Record<SeriesKey, number>
}

function bucketize(rows: readonly SalesDay[], grain: Grain): Bucket[] {
  const m = new Map<string, Bucket>()
  for (const d of [...rows].sort((a, b) => a.date.localeCompare(b.date))) {
    const key = grain === 'day' ? d.date : grain === 'week' ? mondayOf(d.date) : d.date.slice(0, 7)
    let b = m.get(key)
    if (!b) {
      const label = grain === 'month' ? mLabel(key) : fd(key).split(' ').slice(1).join(' ')
      const long = grain === 'day' ? fd(key) : grain === 'week' ? `Week of ${fd(key)}` : mLabel(key)
      b = { key, label, long, days: 0, v: { card: 0, cash: 0 } }
      m.set(key, b)
    }
    b.days += 1
    for (const s of SERIES) b.v[s.key] += valueOf(d, s.key)
  }
  return [...m.values()]
}

/* ------------------------------------------------------------ dashboard --- */

export function SalesDashboard({ rows }: { rows: SalesDay[] }) {
  const span = useMemo(() => {
    if (rows.length === 0) return 0
    const first = rows.reduce((a, d) => (d.date < a ? d.date : a), rows[0]?.date ?? '')
    const last = rows.reduce((a, d) => (d.date > a ? d.date : a), rows[0]?.date ?? '')
    return (Date.parse(last) - Date.parse(first)) / 864e5 + 1
  }, [rows])
  const auto: Grain = span <= 45 ? 'day' : span <= 200 ? 'week' : 'month'
  const [picked, setPicked] = useState<Grain | null>(null)
  const grain = picked ?? auto
  const [hidden, setHidden] = useState<Set<SeriesKey>>(new Set())

  if (rows.length === 0) {
    return <p className="mt-6 rounded-card border border-dashed border-line-strong px-4 py-6 text-center text-base text-ink-2">No trading days in this view.</p>
  }
  return (
    <div className="mt-5 grid gap-x-8 gap-y-7 compact:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
      <section aria-labelledby="takings-h" className="min-w-0">
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-2">
          <h3 id="takings-h" className="text-xl font-extrabold tracking-[-.01em]">
            Takings over time
          </h3>
          <span className="flex-1" />
          <Segmented<Grain>
            label="Group by"
            value={grain}
            onChange={setPicked}
            options={[
              { value: 'day', label: 'Day' },
              { value: 'week', label: 'Week' },
              { value: 'month', label: 'Month' },
            ]}
          />
        </div>
        <Legend
          items={SERIES.map((s) => ({
            key: s.key,
            label: s.label,
            color: s.color,
            value: gbp(rows.reduce((n, d) => n + valueOf(d, s.key), 0)),
            off: hidden.has(s.key),
          }))}
          onToggle={(k) =>
            setHidden((h) => {
              const n = new Set(h)
              if (n.has(k as SeriesKey)) n.delete(k as SeriesKey)
              else if (n.size < SERIES.length - 1) n.add(k as SeriesKey)
              return n
            })
          }
        />
        <TakingsChart buckets={bucketize(rows, grain)} hidden={hidden} grain={grain} />
      </section>

      <ChannelMix rows={rows} />

      <WeekdayChart rows={rows} />
      <BestDays rows={rows} />
    </div>
  )
}

/* -------------------------------------------------------- takings chart --- */

const H = 260
const PAD = { top: 10, right: 8, bottom: 26, left: 48 }

function TakingsChart({ buckets, hidden, grain }: { buckets: Bucket[]; hidden: Set<SeriesKey>; grain: Grain }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const shown = SERIES.filter((s) => !hidden.has(s.key))
  const totals = buckets.map((b) => shown.reduce((n, s) => n + b.v[s.key], 0))
  const { max, step } = niceMax(Math.max(0, ...totals))
  const plotW = Math.max(0, width - PAD.left - PAD.right)
  const plotH = H - PAD.top - PAD.bottom
  const slot = buckets.length ? plotW / buckets.length : 0
  const barW = Math.max(2, Math.min(44, slot * 0.72))
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step)
  const every = Math.max(1, Math.ceil(buckets.length / Math.max(1, Math.floor(plotW / 64))))
  const avg = totals.length ? totals.reduce((a, b) => a + b, 0) / totals.length : 0
  const hb = hover !== null ? buckets[hover] : undefined

  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={H} role="group" aria-label={`Takings per ${grain}, stacked by how it was paid`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--color-line)" strokeWidth={t === 0 ? 1.5 : 1} />
              <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-ink-3 text-label">
                {axisMoney(t)}
              </text>
            </g>
          ))}
          {buckets.map((b, i) => {
            const x = PAD.left + i * slot + (slot - barW) / 2
            let base = 0
            const segs = shown
              .map((s) => ({ s, v: b.v[s.key] }))
              .filter((g) => g.v > 0)
            return (
              <g key={b.key} opacity={hover === null || hover === i ? 1 : 0.45}>
                {segs.map((g, j) => {
                  const y0 = y(base)
                  base += g.v
                  const y1 = y(base)
                  const top = j === segs.length - 1
                  // 2px surface gap between stacked segments; 4px rounded top on the last one.
                  const h = Math.max(0, y0 - y1 - (j > 0 ? 2 : 0))
                  return <path key={g.s.key} d={barPath(x, y1, barW, h, top ? 4 : 0)} fill={g.s.color} />
                })}
              </g>
            )
          })}
          {avg > 0 && buckets.length > 2 && (
            <g>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(avg)} y2={y(avg)} stroke="var(--color-ink-2)" strokeWidth={1.5} strokeDasharray="4 4" />
              <text x={width - PAD.right} y={y(avg) - 5} textAnchor="end" className="fill-ink-2 text-label font-bold">
                avg {gbp(Math.round(avg))}
              </text>
            </g>
          )}
          {buckets.map((b, i) =>
            i % every === 0 ? (
              <text key={b.key} x={PAD.left + i * slot + slot / 2} y={H - 8} textAnchor="middle" className="fill-ink-3 text-label">
                {b.label}
              </text>
            ) : null,
          )}
          {/* Hit targets: the full column, bigger than the bar. */}
          {buckets.map((b, i) => (
            <rect
              key={b.key}
              x={PAD.left + i * slot}
              y={PAD.top}
              width={slot}
              height={plotH}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              tabIndex={0}
              role="img"
              aria-label={`${b.long}: ${gbp(totals[i] ?? 0)}`}
            />
          ))}
        </svg>
      )}
      {hb && hover !== null && (
        <Tip x={PAD.left + hover * slot + slot / 2} width={width}>
          <div className="mb-1 font-bold text-ink">{hb.long}</div>
          {shown.map((s) => (
            <div key={s.key} className="flex items-center gap-2">
              <span className="size-2.5 rounded-[2px]" style={{ background: s.color }} />
              <span className="flex-1 text-ink-2">{s.label}</span>
              <span className="fig">{gbp(hb.v[s.key])}</span>
            </div>
          ))}
          <div className="mt-1 flex justify-between gap-3 border-t border-line pt-1 font-bold">
            <span>Total</span>
            <span className="fig">{gbp(totals[hover] ?? 0)}</span>
          </div>
          {grain !== 'day' && <div className="text-xs text-ink-3">{count(hb.days)} trading {hb.days === 1 ? 'day' : 'days'}</div>}
        </Tip>
      )}
    </div>
  )
}


/* ---------------------------------------------------------- channel mix --- */

function ChannelMix({ rows }: { rows: SalesDay[] }) {
  const pl = usePL()
  const till = SERIES.map((s) => ({ ...s, pence: rows.reduce((n, d) => n + valueOf(d, s.key), 0) }))
  const months = new Set(rows.map((d) => d.date.slice(0, 7)))
  const cols = (pl.data?.columns ?? []).filter((c) => months.has(c.period))
  const missing = cols.flatMap((c) => c.delivery_missing)
  const gross = cols.reduce((n, c) => n + (c.delivery_gross_pence ?? 0), 0)
  const commission = cols.some((c) => c.delivery_commission_pence !== null) ? cols.reduce((n, c) => n + (c.delivery_commission_pence ?? 0), 0) : null
  const ads = cols.some((c) => c.delivery_ads_pence !== null) ? cols.reduce((n, c) => n + (c.delivery_ads_pence ?? 0), 0) : null
  const parts = [...till.map((t) => ({ key: t.key, label: t.label, color: t.color, pence: t.pence })), { key: 'delivery', label: DELIVERY.label, color: DELIVERY.color, pence: gross }]
  const total = parts.reduce((n, p) => n + p.pence, 0)
  // Both apps missing for every month in view: unknown, not £0 (invariant 8).
  const deliveryUnknown = cols.length > 0 && gross === 0 && missing.length >= cols.length * 2
  const pct = (p: number) => (total > 0 ? `${Math.round((p / total) * 100)}%` : '—')

  return (
    <section aria-labelledby="mix-h" className="min-w-0">
      <h3 id="mix-h" className="mb-1 text-xl font-extrabold tracking-[-.01em]">
        Where it came from
      </h3>
      <p className="mb-3 text-sm text-ink-2">
        Till takings for the days in view; delivery apps from their monthly statements for {months.size === 1 ? 'that month' : `those ${months.size} months`}.
      </p>
      <div className="mb-3 flex h-3.5 w-full gap-[2px] overflow-hidden rounded-full" aria-hidden="true">
        {parts
          .filter((p) => p.pence > 0)
          .map((p) => (
            <span key={p.key} style={{ flexGrow: p.pence, background: p.color }} title={`${p.label} ${pct(p.pence)}`} />
          ))}
      </div>
      <div role="table" aria-label="Takings by channel">
        {parts.map((p) => (
          <div role="row" key={p.key} className="grid grid-cols-[minmax(0,1fr)_auto_3.2rem] items-center gap-3 border-b-[1.5px] border-dashed border-line py-1.5 text-md">
            <span role="rowheader" className="flex items-center gap-2">
              <span aria-hidden="true" className="size-3 rounded-[3px]" style={{ background: p.color }} />
              {p.label}
            </span>
            <span role="cell" className="fig text-right">
              {p.key === 'delivery' && deliveryUnknown ? <span className="text-sm text-ink-2">not uploaded</span> : gbp(p.pence)}
            </span>
            <span role="cell" className="fig text-right text-sm text-ink-2">
              {p.key === 'delivery' && deliveryUnknown ? '—' : pct(p.pence)}
            </span>
          </div>
        ))}
        <div role="row" className="grid grid-cols-[minmax(0,1fr)_auto_3.2rem] gap-3 border-b-2 border-ink py-1.5 text-lg font-bold">
          <span role="rowheader">Total</span>
          <span role="cell" className="fig text-right">
            {gbp(total)}
          </span>
          <span />
        </div>
      </div>
      {gross > 0 && (
        <p className="mt-2 text-sm text-ink-2">
          Delivery apps: {gbp(gross)} gross
          {commission !== null ? ` − ${gbp(commission)} commission` : ', commission not reported'}
          {ads !== null ? ` − ${gbp(ads)} ads` : ''}
          {commission !== null ? ` = ${gbp(gross - commission - (ads ?? 0))} kept` : ''}.
        </p>
      )}
      {missing.length > 0 && <p className="mt-1 text-sm text-ink-2">Not uploaded (missing, not zero): {missing.join(', ')}.</p>}
      <p className="mt-1 text-xs text-ink-3">Per-order channels (till vs Deliveroo vs Just Eat for each order) arrive once Lightspeed is connected.</p>
    </section>
  )
}

/* -------------------------------------------------------- weekday chart --- */

const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function WeekdayChart({ rows }: { rows: SalesDay[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const by = WD.map((_, i) => {
    const days = rows.filter((d) => weekdayIndex(d.date) === i)
    const sum = days.reduce((n, d) => n + d.total_pence, 0)
    return { n: days.length, avg: days.length ? Math.round(sum / days.length) : null }
  })
  const top = Math.max(1, ...by.map((b) => b.avg ?? 0))
  const best = by.reduce((bi, b, i) => ((b.avg ?? -1) > (by[bi]?.avg ?? -1) ? i : bi), 0)
  return (
    <section aria-labelledby="wd-h" className="min-w-0">
      <h3 id="wd-h" className="mb-1 text-xl font-extrabold tracking-[-.01em]">
        By weekday
      </h3>
      <p className="mb-3 text-sm text-ink-2">Average takings on each weekday in view.</p>
      <div className="flex h-44 items-end gap-2" onMouseLeave={() => setHover(null)}>
        {by.map((b, i) => (
          <div
            key={WD[i]}
            className="relative flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1"
            onMouseEnter={() => setHover(i)}
            tabIndex={0}
            role="img"
            onFocus={() => setHover(i)}
            onBlur={() => setHover(null)}
            aria-label={`${WD[i]}: ${b.avg === null ? 'no days' : `average ${gbp(b.avg)} over ${b.n} days`}`}
          >
            <span className={cx('fig text-xs', i === best ? 'font-bold text-ink' : 'text-ink-2')}>{b.avg === null ? '—' : axisMoney(b.avg)}</span>
            <span
              className="w-full max-w-[44px] rounded-t-[4px] bg-brand"
              style={{ height: `${((b.avg ?? 0) / top) * 100}%`, minHeight: b.avg ? 2 : 0, opacity: hover === null || hover === i ? 1 : 0.45 }}
            />
            <span className={cx('text-sm', i >= 5 ? 'font-bold text-ink' : 'text-ink-2')}>{WD[i]}</span>
            {hover === i && b.avg !== null && (
              <span role="tooltip" className="pointer-events-none absolute bottom-full z-10 mb-1 whitespace-nowrap rounded-card border border-line bg-surface px-2.5 py-1.5 text-sm shadow-login">
                <b className="fig">{gbp(b.avg)}</b> avg · {b.n} {b.n === 1 ? 'day' : 'days'}
              </span>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

/* ------------------------------------------------------------ best days --- */

function BestDays({ rows }: { rows: SalesDay[] }) {
  const top = [...rows].sort((a, b) => b.total_pence - a.total_pence).slice(0, 5)
  const max = top[0]?.total_pence ?? 1
  return (
    <section aria-labelledby="best-h" className="min-w-0">
      <h3 id="best-h" className="mb-1 text-xl font-extrabold tracking-[-.01em]">
        Best days
      </h3>
      <p className="mb-3 text-sm text-ink-2">The biggest days in view.</p>
      <ol>
        {top.map((d, i) => (
          <li key={d.date} className="grid grid-cols-[1.5rem_minmax(0,7rem)_minmax(0,1fr)_auto] items-center gap-3 border-b-[1.5px] border-dashed border-line py-1.5 text-md">
            <span className="fig text-sm text-ink-3">{i + 1}</span>
            <span className="truncate">{fd(d.date)}</span>
            <span className="h-2 rounded-full bg-line-soft" aria-hidden="true">
              <span className="block h-2 rounded-full bg-brand" style={{ width: `${(d.total_pence / max) * 100}%` }} />
            </span>
            <span className="fig text-right font-bold">{gbp(d.total_pence)}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}
