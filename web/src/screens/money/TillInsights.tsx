/**
 * Money -> Sales, the "Till sales" half: what the till sold, when, and through
 * which channel. Reads `GET /api/finance/sales/insights` (Lightspeed receipt
 * lines). A separate ledger from the daily takings below it: the two are never
 * added, and each section names its source.
 *
 * Every chart here can drill: click a product, category, channel, size or
 * weekday and it becomes a filter in the bar above, which re-scopes the page.
 *
 * Colour jobs (dataviz): one series -> brand; channels -> the validated
 * --color-chart-{instore,deliveroo,justeat,other} order; heatmap and sizes ->
 * the one-hue --color-seq-* ramp. Text never wears a series colour.
 */
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Pill, Segmented, cx } from '../../components/ui'
import type { SalesInsights } from '../../lib/types/finance'
import { Tip, axisMoney, barPath, niceMax, useWidth } from './chartKit'
import { Figures, count, weekdayIndex } from './filters'
import { fd, gbp, mLabel } from './shared'

export type Drill = 'channel' | 'category' | 'product' | 'size' | 'weekday'

export const NO_CATEGORY = '__none__'
export const CHANNEL_LABEL: Record<string, string> = {
  EPOS: 'In store',
  DELIVEROO: 'Deliveroo',
  JUST_EAT: 'Just Eat',
  OTHER: 'Other',
}
const CHANNEL_COLOR: Record<string, string> = {
  EPOS: 'var(--color-chart-instore)',
  DELIVEROO: 'var(--color-chart-deliveroo)',
  JUST_EAT: 'var(--color-chart-justeat)',
  OTHER: 'var(--color-chart-other)',
}
export const SIZE_LABEL: Record<string, string> = { S: 'Small', M: 'Medium', XL: 'Large', ONE: 'One size' }
const SIZE_COLOR: Record<string, string> = {
  S: 'var(--color-seq-2)',
  M: 'var(--color-seq-3)',
  XL: 'var(--color-seq-5)',
  ONE: 'var(--color-chart-other)',
}
const HEAT = ['var(--color-seq-1)', 'var(--color-seq-2)', 'var(--color-seq-3)', 'var(--color-seq-4)', 'var(--color-seq-5)']
const WD_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const WD_LONG = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays']

export const categoryLabel = (c: string) => (c === NO_CATEGORY ? 'No category' : c)

/** A Decimal-as-text quantity for display: thousands separators on whole numbers. */
function qtyText(q: string): string {
  return /^-?\d+$/.test(q) ? count(Number(q)) : q
}
/** Layout only (bar lengths, shares): never money arithmetic. */
const num = (q: string) => Number(q)

function hourLabel(h: number): string {
  const suffix = h < 12 ? 'am' : 'pm'
  const hh = h % 12 === 0 ? 12 : h % 12
  return `${hh}${suffix}`
}

function pct(part: number, whole: number): string {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : '—'
}

/* ------------------------------------------------------------- section --- */

export function TillSection({
  data,
  dimmed,
  onDrill,
  active,
  note,
}: {
  note?: ReactNode
  data: SalesInsights
  /** A filter change is refetching: hold the frame, dimmed. */
  dimmed: boolean
  onDrill: (kind: Drill, value: string) => void
  active: Partial<Record<Drill, string>>
}) {
  const t = data.totals
  const prevLabel = `${fd(data.prev_since)} – ${fd(data.prev_until)}`
  const busiest = useMemo(() => data.by_hour.reduce<(typeof data.by_hour)[number] | null>((a, h) => (a === null || h.receipts > a.receipts ? h : a), null), [data])

  return (
    <section id="till" aria-labelledby="till-h" className="scroll-mt-4 pt-5">
      <SectionTitle
        id="till-h"
        title="Till sales"
        source="Lightspeed till lines"
        demo={data.is_demo}
        range={`${fd(data.since)} – ${fd(data.until)}`}
      />
      {note && <p className="mt-2 text-sm font-semibold text-ink-2">{note}</p>}
      {data.caveats.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5 text-sm text-ink-2">
          {data.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
      <div className={cx('transition-opacity', dimmed && 'opacity-60')} aria-busy={dimmed || undefined}>
        {t.receipts === 0 ? (
          <p className="mt-4 rounded-card border border-dashed border-line-strong px-4 py-6 text-center text-base text-ink-2">
            No till sales in this view. Widen the period or clear a filter.
          </p>
        ) : (
          <>
            <Figures
              className="mt-2"
              items={[
                { label: 'Sales', value: gbp(t.gross_pence), strong: true, sub: <Delta now={t.gross_pence} prev={data.previous?.gross_pence} vs={prevLabel} /> },
                { label: 'Receipts', value: count(t.receipts), sub: <Delta now={t.receipts} prev={data.previous?.receipts} /> },
                {
                  label: 'Avg basket',
                  value: t.avg_basket_pence === null ? '—' : gbp(t.avg_basket_pence),
                  sub: <Delta now={t.avg_basket_pence} prev={data.previous?.avg_basket_pence} />,
                },
                { label: 'Items per basket', value: t.items_per_basket ?? '—', sub: `${qtyText(t.items)} items` },
                {
                  label: 'Per trading day',
                  value: t.per_day_pence === null ? '—' : gbp(t.per_day_pence),
                  sub: `${count(t.trading_days)} ${t.trading_days === 1 ? 'day' : 'days'} open`,
                },
                {
                  label: 'Busiest hour',
                  value: busiest ? `${hourLabel(busiest.hour)}–${hourLabel(busiest.hour + 1)}` : '—',
                  sub: busiest ? `${count(busiest.receipts)} receipts · ${pct(busiest.gross_pence, t.gross_pence)} of sales` : undefined,
                },
              ]}
            />
            <div className="mt-5 grid gap-x-8 gap-y-8 compact:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
              <SalesOverTime data={data} />
              <Channels data={data} onDrill={onDrill} active={active.channel} />
              <WhenHeatmap data={data} onDrill={onDrill} />
              <ByWeekday data={data} onDrill={onDrill} active={active.weekday} />
              <BestSellers data={data} onDrill={onDrill} active={active.product} />
              <div className="flex min-w-0 flex-col gap-8">
                <Categories data={data} onDrill={onDrill} active={active.category} />
                <SizeMix data={data} onDrill={onDrill} active={active.size} />
                <Baskets data={data} />
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  )
}

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

/** "▲ 12% vs Mon 1 Sep – …": direction by glyph and word, never colour alone. */
function Delta({ now, prev, vs }: { now: number | null; prev: number | null | undefined; vs?: string }) {
  if (now === null || prev === null || prev === undefined || prev === 0) return vs ? <span className="text-ink-3">no earlier period to compare</span> : null
  const p = Math.round(((now - prev) / Math.abs(prev)) * 100)
  const glyph = p > 0 ? '▲' : p < 0 ? '▼' : '='
  return (
    <span className={p < 0 ? 'text-bad-ink' : 'text-ink-2'} title={vs ? `Compared with ${vs}` : 'Compared with the period before'}>
      <span aria-hidden="true">{glyph}</span> {p === 0 ? 'level' : `${Math.abs(p)}% ${p > 0 ? 'up' : 'down'}`}
      {vs && <span className="text-ink-3"> vs {vs}</span>}
    </span>
  )
}

function ChartHead({ id, title, sub, children }: { id: string; title: string; sub?: ReactNode; children?: ReactNode }) {
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

/* ------------------------------------------------------ sales over time --- */

type Grain = 'day' | 'week' | 'month'

interface Bucket {
  key: string
  label: string
  long: string
  gross: number | null
  receipts: number
  open: number
}

function mondayOf(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() - weekdayIndex(iso))
  return d.toISOString().slice(0, 10)
}

function bucketize(days: SalesInsights['by_day'], grain: Grain): Bucket[] {
  const m = new Map<string, Bucket>()
  for (const d of days) {
    const key = grain === 'day' ? d.date : grain === 'week' ? mondayOf(d.date) : d.date.slice(0, 7)
    let b = m.get(key)
    if (!b) {
      const label = grain === 'month' ? mLabel(key) : fd(key).split(' ').slice(1).join(' ')
      const long = grain === 'day' ? fd(key) : grain === 'week' ? `Week of ${fd(key)}` : mLabel(key)
      b = { key, label, long, gross: null, receipts: 0, open: 0 }
      m.set(key, b)
    }
    if (d.gross_pence !== null) {
      b.gross = (b.gross ?? 0) + d.gross_pence
      b.receipts += d.receipts
      b.open += 1
    }
  }
  return [...m.values()]
}

const H = 240
const PAD = { top: 12, right: 8, bottom: 26, left: 48 }

function SalesOverTime({ data }: { data: SalesInsights }) {
  const span = data.by_day.length
  const auto: Grain = span <= 45 ? 'day' : span <= 200 ? 'week' : 'month'
  const [picked, setPicked] = useState<Grain | null>(null)
  const grain = picked ?? auto
  const buckets = useMemo(() => bucketize(data.by_day, grain), [data.by_day, grain])
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)

  const vals = buckets.map((b) => b.gross ?? 0)
  const { max, step } = niceMax(Math.max(0, ...vals))
  const plotW = Math.max(0, width - PAD.left - PAD.right)
  const plotH = H - PAD.top - PAD.bottom
  const slot = buckets.length ? plotW / buckets.length : 0
  const barW = Math.max(2, Math.min(24, slot - 2))
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step)
  const every = Math.max(1, Math.ceil(buckets.length / Math.max(1, Math.floor(plotW / 64))))
  const withSales = buckets.filter((b) => b.gross !== null)
  const avg = withSales.length ? withSales.reduce((a, b) => a + (b.gross ?? 0), 0) / withSales.length : 0
  const closed = buckets.filter((b) => b.gross === null).length
  const hb = hover !== null ? buckets[hover] : undefined

  return (
    <section aria-labelledby="sot-h" className="min-w-0">
      <ChartHead
        id="sot-h"
        title="Sales over time"
        sub={closed > 0 && grain === 'day' ? `${count(closed)} ${closed === 1 ? 'day' : 'days'} with nothing rung up are marked on the baseline, not drawn as £0.` : `Till sales per ${grain}.`}
      >
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
      </ChartHead>
      <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={H} role="group" aria-label={`Till sales per ${grain}`}>
            {ticks.map((tk) => (
              <g key={tk}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(tk)} y2={y(tk)} stroke="var(--color-line)" strokeWidth={1} />
                <text x={PAD.left - 8} y={y(tk)} dy="0.32em" textAnchor="end" className="fill-ink-3 text-label">
                  {axisMoney(tk)}
                </text>
              </g>
            ))}
            {buckets.map((b, i) => {
              const x = PAD.left + i * slot + (slot - barW) / 2
              if (b.gross === null) {
                return <line key={b.key} x1={x} x2={x + barW} y1={y(0) - 1} y2={y(0) - 1} stroke="var(--color-line-strong)" strokeWidth={2} strokeLinecap="round" />
              }
              const top = y(b.gross)
              return (
                <path
                  key={b.key}
                  d={barPath(x, top, barW, Math.max(1, y(0) - top))}
                  fill="var(--color-brand)"
                  opacity={hover === null || hover === i ? 1 : 0.45}
                />
              )
            })}
            {avg > 0 && buckets.length > 2 && (
              <g>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(avg)} y2={y(avg)} stroke="var(--color-ink-2)" strokeWidth={1} strokeDasharray="4 4" />
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
                aria-label={`${b.long}: ${b.gross === null ? 'nothing rung up' : `${gbp(b.gross)}, ${b.receipts} receipts`}`}
              />
            ))}
          </svg>
        )}
        {hb && hover !== null && (
          <Tip x={PAD.left + hover * slot + slot / 2} width={width}>
            <div className="mb-1 font-bold text-ink">{hb.long}</div>
            {hb.gross === null ? (
              <div className="text-ink-2">Nothing rung up</div>
            ) : (
              <>
                <div className="flex justify-between gap-3">
                  <span className="text-ink-2">Sales</span>
                  <b className="fig">{gbp(hb.gross)}</b>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-ink-2">Receipts</span>
                  <span className="fig">{count(hb.receipts)}</span>
                </div>
                {hb.receipts > 0 && (
                  <div className="flex justify-between gap-3">
                    <span className="text-ink-2">Avg basket</span>
                    <span className="fig">{gbp(Math.round(hb.gross / hb.receipts))}</span>
                  </div>
                )}
                {grain !== 'day' && <div className="text-xs text-ink-3">{count(hb.open)} trading {hb.open === 1 ? 'day' : 'days'}</div>}
              </>
            )}
          </Tip>
        )}
      </div>
    </section>
  )
}

/* ------------------------------------------------------------- channels --- */

function Channels({ data, onDrill, active }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void; active?: string }) {
  const total = data.by_channel.reduce((n, c) => n + c.gross_pence, 0)
  return (
    <section aria-labelledby="ch-h" className="min-w-0">
      <ChartHead id="ch-h" title="By channel" sub="Where till sales were rung up. Click one to see only that channel." />
      <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full" aria-hidden="true">
        {data.by_channel.map((c) => (
          <span key={c.key} style={{ flexGrow: Math.max(0, c.gross_pence), background: CHANNEL_COLOR[c.key] ?? CHANNEL_COLOR.OTHER }} />
        ))}
      </div>
      <ul className="mt-3 flex flex-col">
        {data.by_channel.map((c) => (
          <li key={c.key}>
            <DrillRow active={active === c.key} onClick={() => onDrill('channel', c.key)} label={`Only ${CHANNEL_LABEL[c.key] ?? c.key}`}>
              <span aria-hidden="true" className="size-3 flex-none rounded-[3px]" style={{ background: CHANNEL_COLOR[c.key] ?? CHANNEL_COLOR.OTHER }} />
              <span className="flex-1 text-ink">{CHANNEL_LABEL[c.key] ?? c.key}</span>
              <span className="fig text-ink-2">{count(c.receipts)} receipts</span>
              <span className="fig w-20 text-right font-bold">{gbp(c.gross_pence)}</span>
              <span className="fig w-10 text-right text-ink-2">{pct(c.gross_pence, total)}</span>
            </DrillRow>
          </li>
        ))}
      </ul>
      {data.options.channels.length <= 1 && (
        <p className="mt-2 text-sm text-ink-2">
          Deliveroo and Just Eat show here once their orders reach the till. Their monthly statement totals are in <a href="#takings" className="font-semibold text-brand-ink">Takings</a>.
        </p>
      )}
    </section>
  )
}

/** A clickable list row that applies a filter. Keyboard and screen-reader friendly. */
function DrillRow({ children, onClick, active, label }: { children: ReactNode; onClick: () => void; active: boolean; label: string }) {
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

function WhenHeatmap({ data, onDrill }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void }) {
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

  return (
    <section aria-labelledby="heat-h" className="min-w-0">
      <ChartHead id="heat-h" title="When people buy" sub="Average receipts in each hour, per day open. Click a day to see only that weekday." />
      <div className="scroll-x">
        <div
          role="table"
          aria-label="Average receipts per hour, by weekday"
          className="grid min-w-[420px] gap-[2px]"
          style={{ gridTemplateColumns: `44px repeat(${hours.length}, minmax(0, 1fr))` }}
          onMouseLeave={() => setHover(null)}
        >
          <div role="row" className="contents">
            <span role="columnheader" />
            {hours.map((h) => (
              <span key={h} role="columnheader" className="fig pb-1 text-center text-label text-ink-3">
                {hourLabel(h)}
              </span>
            ))}
          </div>
          {rows.map((w) => (
            <div key={w} role="row" className="contents">
              <button
                type="button"
                role="rowheader"
                onClick={() => onDrill('weekday', String(w))}
                title={`Only ${WD_LONG[w]}`}
                className="h-8 rounded-control pr-2 text-left text-sm font-semibold text-ink-2 hover:bg-canvas-2 hover:text-ink"
              >
                {WD_SHORT[w]}
              </button>
              {hours.map((h) => {
                const v = avgOf(w, h)
                const b = bin(v)
                const on = hover?.w === w && hover.h === h
                return (
                  <span
                    key={h}
                    role="cell"
                    tabIndex={0}
                    aria-label={`${WD_LONG[w]} ${hourLabel(h)}: ${v.toFixed(1)} receipts on average`}
                    onMouseEnter={() => setHover({ w, h })}
                    onFocus={() => setHover({ w, h })}
                    onBlur={() => setHover(null)}
                    className={cx('h-8 rounded-[4px] outline-offset-1', on && 'ring-2 ring-ink')}
                    style={{ background: b < 0 ? 'var(--color-canvas)' : HEAT[b] }}
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

/* ------------------------------------------------------------ by weekday --- */

function ByWeekday({ data, onDrill, active }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void; active?: string }) {
  const rows = data.by_weekday.map((w) => ({ ...w, avg: w.trading_days > 0 ? Math.round(w.gross_pence / w.trading_days) : null }))
  const top = Math.max(1, ...rows.map((r) => r.avg ?? 0))
  const best = rows.reduce((a, r, i) => ((r.avg ?? -1) > (rows[a]?.avg ?? -1) ? i : a), 0)
  return (
    <section aria-labelledby="wd-till-h" className="min-w-0">
      <ChartHead id="wd-till-h" title="By weekday" sub="Average sales per day open. Click a day to see only that weekday." />
      <div className="flex h-48 items-end gap-2">
        {rows.map((r, i) => (
          <button
            key={r.weekday}
            type="button"
            onClick={() => onDrill('weekday', String(r.weekday))}
            aria-pressed={active === String(r.weekday)}
            aria-label={`${WD_LONG[r.weekday]}: ${r.avg === null ? 'not open' : `${gbp(r.avg)} a day over ${r.trading_days} days`}`}
            className={cx('group flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1 rounded-control pt-1 hover:bg-canvas-2', active === String(r.weekday) && 'bg-brand-wash')}
          >
            <span className={cx('fig text-xs', i === best ? 'font-bold text-ink' : 'text-ink-2')}>{r.avg === null ? '—' : axisMoney(r.avg)}</span>
            <span
              className="w-full max-w-6 rounded-t-[4px] bg-brand"
              style={{ height: `${((r.avg ?? 0) / top) * 100}%`, minHeight: r.avg ? 2 : 0 }}
            />
            <span className={cx('text-sm', i === best ? 'font-bold text-ink' : 'text-ink-2')}>{WD_SHORT[r.weekday]}</span>
          </button>
        ))}
      </div>
    </section>
  )
}

/* ---------------------------------------------------------- best sellers --- */

type Metric = 'gross' | 'qty'

function BestSellers({ data, onDrill, active }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void; active?: string }) {
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

function Categories({ data, onDrill, active }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void; active?: string }) {
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
                <span className={cx('block truncate', c.key === NO_CATEGORY ? 'italic text-ink-2' : 'text-ink')}>{categoryLabel(c.key)}</span>
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

function SizeMix({ data, onDrill, active }: { data: SalesInsights; onDrill: (k: Drill, v: string) => void; active?: string }) {
  const total = data.by_size.reduce((n, s) => n + num(s.qty), 0)
  if (data.by_size.length === 0) return null
  return (
    <section aria-labelledby="size-h" className="min-w-0">
      <ChartHead id="size-h" title="Size mix" sub="Units sold by cup size." />
      <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full" aria-hidden="true">
        {data.by_size.map((s) => (
          <span key={s.key} style={{ flexGrow: Math.max(0, num(s.qty)), background: SIZE_COLOR[s.key] ?? SIZE_COLOR.ONE }} />
        ))}
      </div>
      <ul className="mt-3 flex flex-col">
        {data.by_size.map((s) => (
          <li key={s.key}>
            <DrillRow active={active === s.key} onClick={() => onDrill('size', s.key)} label={`Only ${SIZE_LABEL[s.key] ?? s.key}`}>
              <span aria-hidden="true" className="size-3 flex-none rounded-[3px]" style={{ background: SIZE_COLOR[s.key] ?? SIZE_COLOR.ONE }} />
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

function Baskets({ data }: { data: SalesInsights }) {
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

