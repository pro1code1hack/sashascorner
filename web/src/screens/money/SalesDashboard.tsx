/**
 * Money → Sales: the one dashboard (owner, 2026-09-28: "integrate the whole
 * dashboard into one consolidated view where we can filter cash, see Deliveroo,
 * see Just Eat"). Two ledgers sit side by side in every chart and are never
 * added together:
 *
 *   till      what the till rang up (Lightspeed receipt lines, `SalesInsights`)
 *   takings   the money that came in: card and cash per trading day (`SalesDay`),
 *             and the delivery apps' monthly statements (`DeliveryRow`)
 *
 *   Money in            one figures strip: till sales, receipts, avg basket,
 *                       per day open, busiest hour · money taken, card, cash ·
 *                       delivery apps. Each figure names its ledger.
 *   Over time           card + cash as stacked bars, till sales as a line over
 *                       them, per day / week / month; legend keys toggle
 *   Where it came from  rung up through (till channels) · paid by (card, cash)
 *                       · delivery statements (Deliveroo, Just Eat, per month)
 *   When people buy     weekday × hour heatmap (till)
 *   By weekday          average per weekday, till sales or money taken
 *   Best days           the biggest days, same switch
 *   What sold           best sellers, by category, size mix, items per receipt
 *
 * Filters: the "Paid by" filter picks card or cash across the takings ledger
 * (the till does not record how a receipt was paid). The channel filter picks
 * a till channel; for Deliveroo or Just Eat it also swaps card and cash (till
 * money) for that app's statement figures. A month not uploaded is "not
 * uploaded", never £0 (invariant 8).
 */
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Segmented, cx } from '../../components/ui'
import { useReconcile } from '../../lib/finance-api'
import type { DeliveryRow, SalesDay, SalesInsights } from '../../lib/types/finance'
import { Legend, Tip, axisMoney, barPath, niceMax, useWidth } from './chartKit'
import { Figures, count, weekdayIndex } from './filters'
import { fd, gbp, mLabel, pctBp } from './shared'
import {
  Baskets,
  BestSellers,
  Categories,
  CHANNEL_LABEL,
  ChartHead,
  Delta,
  DrillRow,
  GroupLabel,
  SectionTitle,
  SizeMix,
  WD_LONG,
  WD_SHORT,
  WhenHeatmap,
  channelColor,
  hourLabel,
  pct,
  rangeLabel,
} from './TillInsights'
import type { Drill } from './TillInsights'

/* ------------------------------------------------------------- takings --- */

export type Paid = 'all' | 'card' | 'cash'
export const PAID_OPTIONS = [
  { value: 'all', label: 'Card or cash' },
  { value: 'card', label: 'Card' },
  { value: 'cash', label: 'Cash' },
] as const

const CARD = 'var(--color-chart-card)'
const CASH = 'var(--color-chart-cash)'
const TILL = 'var(--color-brand)'

/** The day's money under the Paid-by filter. */
export function takenOf(d: SalesDay, paid: Paid): number {
  if (paid === 'card') return d.card_pence ?? 0
  if (paid === 'cash') return d.cash_pence ?? 0
  return d.total_pence
}

export interface Sums {
  days: number
  card: number
  cash: number
  /** Card + cash, or just the one the Paid-by filter picked. */
  total: number
  orders: number
  /** Takings on days that have an order count: the avg ticket's numerator. */
  totalWithOrders: number
}

export function sumDays(rows: readonly SalesDay[], paid: Paid): Sums {
  const s: Sums = { days: rows.length, card: 0, cash: 0, total: 0, orders: 0, totalWithOrders: 0 }
  for (const d of rows) {
    s.card += d.card_pence ?? 0
    s.cash += d.cash_pence ?? 0
    const t = takenOf(d, paid)
    s.total += t
    if (d.orders !== null && d.orders > 0) {
      s.orders += d.orders
      s.totalWithOrders += t
    }
  }
  return s
}

/** Integer pence, half up, over days that have an order count. */
export function avgTicket(s: Sums): number | null {
  return s.orders > 0 ? Math.floor((s.totalWithOrders * 2 + s.orders) / (s.orders * 2)) : null
}

const isApp = (channel: string) => channel === 'DELIVEROO' || channel === 'JUST_EAT'
const APPS = ['DELIVEROO', 'JUST_EAT'] as const

interface AppFigures {
  channels: string[]
  months: number
  present: DeliveryRow[]
  /** "Deliveroo Sep 26" for every month × app without a statement. */
  missing: string[]
  gross: number
  /** null while any present month lacks commission. */
  kept: number | null
  commission: number | null
  ads: number | null
}

function appFigures(rows: DeliveryRow[], months: string[], channel: string): AppFigures {
  const channels = isApp(channel) ? [channel] : [...APPS]
  const present = rows.filter((r) => r.present && months.includes(r.month) && channels.includes(r.channel))
  const missing: string[] = []
  for (const ch of channels)
    for (const m of months) if (!present.some((r) => r.channel === ch && r.month === m)) missing.push(`${CHANNEL_LABEL[ch] ?? ch} ${mLabel(m)}`)
  const sumOf = (pick: (r: DeliveryRow) => number | null) =>
    present.length > 0 && present.every((r) => pick(r) !== null) ? present.reduce((n, r) => n + (pick(r) ?? 0), 0) : null
  return {
    channels,
    months: months.length,
    present,
    missing,
    gross: present.reduce((n, r) => n + (r.gross_pence ?? 0), 0),
    kept: sumOf((r) => r.kept_pence),
    commission: sumOf((r) => r.commission_pence),
    ads: sumOf((r) => r.ads_pence),
  }
}

/* ------------------------------------------------------------ dashboard --- */

export function SalesDashboard({
  till,
  dimmed,
  rows,
  totals,
  prev,
  paid,
  channel,
  months,
  onDrill,
  active,
  notes,
}: {
  /** Till lines for the window; null when the till could not be read. */
  till: SalesInsights | null
  /** A filter change is refetching the till: hold the frame, dimmed. */
  dimmed: boolean
  /** Trading days in view, after the filters. */
  rows: SalesDay[]
  totals: Sums
  prev: { label: string; s: Sums } | null
  paid: Paid
  channel: string
  /** Months the period covers: delivery statements exist per month. */
  months: string[]
  onDrill: (k: Drill, v: string) => void
  active: Partial<Record<Drill, string>>
  notes: ReactNode[]
}) {
  const rec = useReconcile('all')
  const apps = useMemo(() => appFigures(rec.data?.delivery ?? [], months, channel), [rec.data, months, channel])
  const hasTill = till !== null && till.totals.receipts > 0
  const showTakings = !isApp(channel)
  const showApps = channel === 'all' || isApp(channel)
  const hasTakings = showTakings && rows.length > 0
  const t = till?.totals
  const busiest = useMemo(() => (till ? till.by_hour.reduce<(typeof till.by_hour)[number] | null>((a, h) => (a === null || h.receipts > a.receipts ? h : a), null) : null), [till])
  const prevLabel = till ? rangeLabel(till.prev_since, till.prev_until) : ''
  const vs = (now: number, before: number | undefined) => (prev ? <Delta now={now} prev={before} vs={prev.label} /> : undefined)

  const figures: { label: string; value: ReactNode; sub?: ReactNode; strong?: boolean }[] = []
  if (till && t && hasTill) {
    figures.push(
      { label: 'Till sales', value: gbp(t.gross_pence), strong: true, sub: <Delta now={t.gross_pence} prev={till.previous?.gross_pence} vs={prevLabel} /> },
      { label: 'Receipts', value: count(t.receipts), sub: <Delta now={t.receipts} prev={till.previous?.receipts} /> },
      { label: 'Avg basket', value: t.avg_basket_pence === null ? '—' : gbp(t.avg_basket_pence), sub: <Delta now={t.avg_basket_pence} prev={till.previous?.avg_basket_pence} /> },
      { label: 'Per day open', value: t.per_day_pence === null ? '—' : gbp(t.per_day_pence), sub: `${count(t.trading_days)} ${t.trading_days === 1 ? 'day' : 'days'} open` },
      {
        label: 'Busiest hour',
        value: busiest ? `${hourLabel(busiest.hour)}–${hourLabel(busiest.hour + 1)}` : '—',
        sub: busiest ? `${count(busiest.receipts)} receipts · ${pct(busiest.gross_pence, t.gross_pence)} of sales` : undefined,
      },
    )
  }
  if (hasTakings) {
    const label = paid === 'card' ? 'Card taken' : paid === 'cash' ? 'Cash taken' : 'Money taken'
    figures.push({
      label,
      value: gbp(totals.total),
      strong: figures.length === 0,
      sub: (
        <>
          {count(totals.days)} {totals.days === 1 ? 'day' : 'days'}
          {prev && prev.s.total > 0 ? <> · {vs(totals.total, prev.s.total)}</> : null}
        </>
      ),
    })
    if (paid === 'all') {
      figures.push(
        { label: 'Card', value: gbp(totals.card), sub: vs(totals.card, prev?.s.card) ?? 'through the till' },
        { label: 'Cash', value: gbp(totals.cash), sub: vs(totals.cash, prev?.s.cash) ?? 'through the till' },
      )
    }
  }
  if (showApps && months.length > 0) {
    const label = isApp(channel) ? (CHANNEL_LABEL[channel] ?? channel) : 'Delivery apps'
    if (apps.present.length === 0) {
      figures.push({ label, value: <span className="text-lg font-bold text-ink-2">not uploaded</span>, sub: `${count(apps.missing.length)} ${apps.missing.length === 1 ? 'statement' : 'statements'} missing, not zero` })
    } else {
      figures.push({
        label,
        value: gbp(apps.gross),
        strong: figures.length === 0,
        sub:
          apps.kept !== null ? (
            <>
              {gbp(apps.kept)} kept after commission{apps.ads !== null ? ' and ads' : ''}
              {apps.missing.length > 0 ? ` · ${count(apps.missing.length)} not uploaded` : ''}
            </>
          ) : (
            `gross from ${count(apps.present.length)} ${apps.present.length === 1 ? 'statement' : 'statements'}${apps.missing.length > 0 ? ` · ${count(apps.missing.length)} not uploaded` : ''}`
          ),
      })
    }
  }

  const empty = !hasTill && !hasTakings && apps.present.length === 0

  return (
    <>
      <section id="money" aria-labelledby="money-h" className="scroll-mt-4 pt-5">
        <SectionTitle
          id="money-h"
          title="Money in"
          source="Till lines · card and cash · app statements"
          demo={till?.is_demo ?? false}
          range={till ? rangeLabel(till.since, till.until) : undefined}
        />
        {(notes.length > 0 || (till && till.caveats.length > 0)) && (
          <ul className="mt-2 flex flex-col gap-0.5 text-sm text-ink-2">
            {notes.map((n, i) => (
              <li key={i} className="font-semibold">
                {n}
              </li>
            ))}
            {till?.caveats.map((c) => <li key={c}>{c}</li>)}
          </ul>
        )}
        <div className={cx('transition-opacity', dimmed && 'opacity-60')} aria-busy={dimmed || undefined}>
          {empty ? (
            <p className="mt-4 rounded-card border border-dashed border-line-strong px-4 py-6 text-center text-base text-ink-2">
              Nothing in this view: no till sales, no card or cash, no statements. Widen the period or clear a filter.
            </p>
          ) : (
            <>
              <Figures className="mt-2" items={figures} />
              {!hasTill && showTakings && <p className="mt-2 text-sm text-ink-2">No till sales in this view{till === null ? ' (the till could not be read)' : ''}: the charts below show card and cash.</p>}
              {!hasTakings && showTakings && <p className="mt-2 text-sm text-ink-2">No card or cash entered for these days.</p>}
              <div className="mt-5 grid gap-x-8 gap-y-8 compact:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
                <OverTime till={hasTill ? till : null} rows={rows} paid={paid} showTakings={showTakings} />
                <WhereFrom till={hasTill ? till : null} totals={totals} paid={paid} channel={channel} apps={apps} showTakings={showTakings} showApps={showApps} onDrill={onDrill} active={active} />
                {hasTill && till && <WhenHeatmap data={till} onDrill={onDrill} />}
                <WeekdaysAndBestDays till={hasTill ? till : null} rows={rows} paid={paid} showTakings={showTakings} onDrill={onDrill} active={active.weekday} />
              </div>
            </>
          )}
        </div>
      </section>

      <section id="sold" aria-labelledby="sold-h" className="scroll-mt-14 pt-12">
        <SectionTitle id="sold-h" title="What sold" source="Lightspeed till lines" demo={till?.is_demo ?? false} />
        <div className={cx('transition-opacity', dimmed && 'opacity-60')} aria-busy={dimmed || undefined}>
          {!hasTill || !till ? (
            <p className="mt-4 rounded-card border border-dashed border-line-strong px-4 py-6 text-center text-base text-ink-2">
              {till === null ? 'The till could not be read.' : 'No till lines in this view, so nothing to rank. Widen the period or clear a filter.'}
            </p>
          ) : (
            <div className="mt-5 grid gap-x-8 gap-y-8 compact:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
              <BestSellers data={till} onDrill={onDrill} active={active.product} />
              <div className="flex min-w-0 flex-col gap-8">
                <Categories data={till} onDrill={onDrill} active={active.category} />
                <SizeMix data={till} onDrill={onDrill} active={active.size} />
                <Baskets data={till} />
              </div>
            </div>
          )}
        </div>
      </section>
    </>
  )
}

/* ------------------------------------------------------------ over time --- */

type Grain = 'day' | 'week' | 'month'
type Series = 'till' | 'card' | 'cash'

interface Bucket {
  key: string
  label: string
  long: string
  /** Till sales; null when the till rang up nothing in the bucket. */
  till: number | null
  receipts: number
  /** Days the till was open. */
  open: number
  card: number
  cash: number
  /** Trading days with a takings row. */
  days: number
}

function mondayOf(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() - weekdayIndex(iso))
  return d.toISOString().slice(0, 10)
}

function bucketKey(date: string, grain: Grain): string {
  return grain === 'day' ? date : grain === 'week' ? mondayOf(date) : date.slice(0, 7)
}

function bucketize(tillDays: SalesInsights['by_day'], rows: readonly SalesDay[], grain: Grain): Bucket[] {
  const m = new Map<string, Bucket>()
  const at = (date: string) => {
    const key = bucketKey(date, grain)
    let b = m.get(key)
    if (!b) {
      const label = grain === 'month' ? mLabel(key) : fd(key).split(' ').slice(1).join(' ')
      const long = grain === 'day' ? fd(key) : grain === 'week' ? `Week of ${fd(key)}` : mLabel(key)
      b = { key, label, long, till: null, receipts: 0, open: 0, card: 0, cash: 0, days: 0 }
      m.set(key, b)
    }
    return b
  }
  for (const d of tillDays) {
    const b = at(d.date)
    if (d.gross_pence !== null) {
      b.till = (b.till ?? 0) + d.gross_pence
      b.receipts += d.receipts
      b.open += 1
    }
  }
  for (const d of rows) {
    const b = at(d.date)
    b.card += d.card_pence ?? 0
    b.cash += d.cash_pence ?? 0
    b.days += 1
  }
  return [...m.values()].sort((a, b) => a.key.localeCompare(b.key))
}

function spanDays(dates: string[]): number {
  if (dates.length === 0) return 0
  const sorted = [...dates].sort()
  return (Date.parse(sorted[sorted.length - 1] ?? '') - Date.parse(sorted[0] ?? '')) / 864e5 + 1
}

const H = 260
const PAD = { top: 12, right: 8, bottom: 26, left: 48 }

function OverTime({ till, rows, paid, showTakings }: { till: SalesInsights | null; rows: SalesDay[]; paid: Paid; showTakings: boolean }) {
  const tillDays = till?.by_day ?? []
  const span = useMemo(() => spanDays([...tillDays.map((d) => d.date), ...rows.map((d) => d.date)]), [tillDays, rows])
  const auto: Grain = span <= 45 ? 'day' : span <= 200 ? 'week' : 'month'
  const [picked, setPicked] = useState<Grain | null>(null)
  const grain = picked ?? auto
  const buckets = useMemo(() => bucketize(tillDays, showTakings ? rows : [], grain), [tillDays, rows, grain, showTakings])
  const [hidden, setHidden] = useState<Set<Series>>(new Set())
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)

  const offered: { key: Series; label: string; color: string; shape: 'line' | 'bar'; value: number }[] = []
  if (till) offered.push({ key: 'till', label: 'Till sales', color: TILL, shape: 'line', value: till.totals.gross_pence })
  if (showTakings && rows.length > 0 && paid !== 'cash') offered.push({ key: 'card', label: 'Card', color: CARD, shape: 'bar', value: rows.reduce((n, d) => n + (d.card_pence ?? 0), 0) })
  if (showTakings && rows.length > 0 && paid !== 'card') offered.push({ key: 'cash', label: 'Cash', color: CASH, shape: 'bar', value: rows.reduce((n, d) => n + (d.cash_pence ?? 0), 0) })
  const on = (k: Series) => offered.some((s) => s.key === k) && !hidden.has(k)
  const bars = (['card', 'cash'] as const).filter(on)
  const lineOn = on('till')
  const taken = (b: Bucket) => bars.reduce((n, k) => n + b[k], 0)

  const peak = Math.max(0, ...buckets.map((b) => Math.max(lineOn ? (b.till ?? 0) : 0, taken(b))))
  const { max, step } = niceMax(peak)
  const plotW = Math.max(0, width - PAD.left - PAD.right)
  const plotH = H - PAD.top - PAD.bottom
  const slot = buckets.length ? plotW / buckets.length : 0
  const barW = Math.max(2, Math.min(44, slot * 0.72))
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH
  const cx0 = (i: number) => PAD.left + i * slot + slot / 2
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step)
  const every = Math.max(1, Math.ceil(buckets.length / Math.max(1, Math.floor(plotW / 64))))
  const closed = buckets.filter((b) => b.till === null && taken(b) === 0).length

  // The dashed average: till sales per bucket with sales, else money taken per bucket with a row.
  const avgOf = lineOn ? buckets.filter((b) => b.till !== null).map((b) => b.till ?? 0) : buckets.filter((b) => b.days > 0).map(taken)
  const avg = avgOf.length ? avgOf.reduce((a, b) => a + b, 0) / avgOf.length : 0
  const avgWhat = lineOn ? 'till' : 'taken'

  // Line segments between consecutive buckets the till was open; a gap where it was not.
  const segments: string[] = []
  if (lineOn) {
    let cur: string[] = []
    buckets.forEach((b, i) => {
      if (b.till === null) {
        if (cur.length) segments.push(cur.join(' '))
        cur = []
      } else cur.push(`${cx0(i)},${y(b.till)}`)
    })
    if (cur.length) segments.push(cur.join(' '))
  }
  const hb = hover !== null ? buckets[hover] : undefined

  const what = offered.length === 1 ? (offered[0]?.label ?? 'Sales') : 'Till sales over card and cash'
  return (
    <section aria-labelledby="sot-h" className="min-w-0">
      <ChartHead
        id="sot-h"
        title="Over time"
        sub={
          closed > 0 && grain === 'day'
            ? `${count(closed)} ${closed === 1 ? 'day' : 'days'} with nothing rung up or taken are marked on the baseline, not drawn as £0.`
            : `${what}, per ${grain}.`
        }
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
      {offered.length > 1 && (
        <Legend
          items={offered.map((s) => ({ key: s.key, label: s.label, color: s.color, shape: s.shape, value: gbp(s.value), off: hidden.has(s.key) }))}
          onToggle={(k) =>
            setHidden((h) => {
              const n = new Set(h)
              const key = k as Series
              if (n.has(key)) n.delete(key)
              else if (n.size < offered.length - 1) n.add(key)
              return n
            })
          }
        />
      )}
      <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={H} role="group" aria-label={`${what} per ${grain}`}>
            {ticks.map((tk) => (
              <g key={tk}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(tk)} y2={y(tk)} stroke="var(--color-line)" strokeWidth={tk === 0 ? 1.5 : 1} />
                <text x={PAD.left - 8} y={y(tk)} dy="0.32em" textAnchor="end" className="fill-ink-3 text-label">
                  {axisMoney(tk)}
                </text>
              </g>
            ))}
            {buckets.map((b, i) => {
              const x = PAD.left + i * slot + (slot - barW) / 2
              const segs = bars.map((k) => ({ k, v: b[k], color: k === 'card' ? CARD : CASH })).filter((g) => g.v > 0)
              if (segs.length === 0 && b.till === null) {
                return <line key={b.key} x1={x} x2={x + barW} y1={y(0) - 1} y2={y(0) - 1} stroke="var(--color-line-strong)" strokeWidth={2} strokeLinecap="round" />
              }
              let base = 0
              return (
                <g key={b.key} opacity={hover === null || hover === i ? 1 : 0.45}>
                  {segs.map((g, j) => {
                    const y0 = y(base)
                    base += g.v
                    const y1 = y(base)
                    const top = j === segs.length - 1
                    // 2px surface gap between stacked segments; 4px rounded top on the last one.
                    const h = Math.max(0, y0 - y1 - (j > 0 ? 2 : 0))
                    return <path key={g.k} d={barPath(x, y1, barW, h, top ? 4 : 0)} fill={g.color} />
                  })}
                </g>
              )
            })}
            {lineOn && (
              <g>
                {segments.map((pts) => (
                  <polyline key={pts} points={pts} fill="none" stroke={TILL} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                ))}
                {buckets.map((b, i) =>
                  b.till === null ? null : (
                    <circle key={b.key} cx={cx0(i)} cy={y(b.till)} r={hover === i ? 4.5 : 3} fill="var(--color-surface)" stroke={TILL} strokeWidth={2} />
                  ),
                )}
              </g>
            )}
            {avg > 0 && buckets.length > 2 && (
              <g>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(avg)} y2={y(avg)} stroke="var(--color-ink-2)" strokeWidth={1} strokeDasharray="4 4" />
                <text x={width - PAD.right} y={y(avg) - 5} textAnchor="end" className="fill-ink-2 text-label font-bold">
                  avg {gbp(Math.round(avg))} {avgWhat}
                </text>
              </g>
            )}
            {buckets.map((b, i) =>
              i % every === 0 ? (
                <text key={b.key} x={cx0(i)} y={H - 8} textAnchor="middle" className="fill-ink-3 text-label">
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
                aria-label={`${b.long}: ${[
                  lineOn ? (b.till === null ? 'nothing rung up' : `till ${gbp(b.till)}`) : null,
                  bars.length ? `taken ${gbp(taken(b))}` : null,
                ]
                  .filter(Boolean)
                  .join(', ')}`}
              />
            ))}
          </svg>
        )}
        {hb && hover !== null && (
          <Tip x={cx0(hover)} width={width}>
            <div className="mb-1 font-bold text-ink">{hb.long}</div>
            {lineOn && (
              <>
                <div className="flex items-center gap-2">
                  <span className="h-[3px] w-2.5 rounded-full" style={{ background: TILL }} />
                  <span className="flex-1 text-ink-2">Till sales</span>
                  <b className="fig">{hb.till === null ? 'nothing rung up' : gbp(hb.till)}</b>
                </div>
                {hb.receipts > 0 && (
                  <div className="flex justify-between gap-3 pl-[18px]">
                    <span className="text-ink-2">Receipts</span>
                    <span className="fig">{count(hb.receipts)}</span>
                  </div>
                )}
              </>
            )}
            {bars.map((k) => (
              <div key={k} className="flex items-center gap-2">
                <span className="size-2.5 rounded-[2px]" style={{ background: k === 'card' ? CARD : CASH }} />
                <span className="flex-1 text-ink-2">{k === 'card' ? 'Card' : 'Cash'}</span>
                <span className="fig">{hb.days > 0 ? gbp(hb[k]) : <span className="text-ink-3">—</span>}</span>
              </div>
            ))}
            {bars.length > 1 && hb.days > 0 && (
              <div className="mt-1 flex justify-between gap-3 border-t border-line pt-1 font-bold">
                <span>Taken</span>
                <span className="fig">{gbp(taken(hb))}</span>
              </div>
            )}
            {grain !== 'day' && (
              <div className="text-xs text-ink-3">
                {lineOn ? `${count(hb.open)} ${hb.open === 1 ? 'day' : 'days'} open` : `${count(hb.days)} trading ${hb.days === 1 ? 'day' : 'days'}`}
              </div>
            )}
          </Tip>
        )}
      </div>
    </section>
  )
}

/* ---------------------------------------------------- where it came from --- */

function ShareBar({ parts }: { parts: { key: string; value: number; color: string }[] }) {
  const live = parts.filter((p) => p.value > 0)
  if (live.length === 0) return <div className="h-3 w-full rounded-full border border-dashed border-line-strong" aria-hidden="true" />
  return (
    <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full" aria-hidden="true">
      {live.map((p) => (
        <span key={p.key} style={{ flexGrow: p.value, background: p.color }} />
      ))}
    </div>
  )
}

function WhereFrom({
  till,
  totals,
  paid,
  channel,
  apps,
  showTakings,
  showApps,
  onDrill,
  active,
}: {
  till: SalesInsights | null
  totals: Sums
  paid: Paid
  channel: string
  apps: AppFigures
  showTakings: boolean
  showApps: boolean
  onDrill: (k: Drill, v: string) => void
  active: Partial<Record<Drill, string>>
}) {
  const tillTotal = till ? till.by_channel.reduce((n, c) => n + c.gross_pence, 0) : 0
  const takenTotal = totals.card + totals.cash
  const payParts = [
    { key: 'card', label: 'Card', color: CARD, value: totals.card },
    { key: 'cash', label: 'Cash', color: CASH, value: totals.cash },
  ]
  const appRows = apps.channels.map((ch) => {
    const rows = apps.present.filter((r) => r.channel === ch)
    const missing = apps.months - rows.length
    const gross = rows.reduce((n, r) => n + (r.gross_pence ?? 0), 0)
    const kept = rows.length > 0 && rows.every((r) => r.kept_pence !== null) ? rows.reduce((n, r) => n + (r.kept_pence ?? 0), 0) : null
    return { ch, rows, missing, gross, kept }
  })

  return (
    <section aria-labelledby="from-h" className="min-w-0">
      <ChartHead id="from-h" title="Where it came from" sub="Click a row to see only that." />
      <div className="flex flex-col gap-5">
        {till && (
          <div>
            <GroupLabel className="mb-2">Rung up through</GroupLabel>
            <ShareBar parts={till.by_channel.map((c) => ({ key: c.key, value: c.gross_pence, color: channelColor(c.key) }))} />
            <ul className="mt-2 flex flex-col">
              {till.by_channel.map((c) => (
                <li key={c.key}>
                  <DrillRow active={active.channel === c.key} onClick={() => onDrill('channel', c.key)} label={`Only ${CHANNEL_LABEL[c.key] ?? c.key}`}>
                    <span aria-hidden="true" className="size-3 flex-none rounded-[3px]" style={{ background: channelColor(c.key) }} />
                    <span className="flex-1 text-ink">{CHANNEL_LABEL[c.key] ?? c.key}</span>
                    <span className="fig text-ink-2">{count(c.receipts)} receipts</span>
                    <span className="fig w-20 text-right font-bold">{gbp(c.gross_pence)}</span>
                    <span className="fig w-10 text-right text-ink-2">{pct(c.gross_pence, tillTotal)}</span>
                  </DrillRow>
                </li>
              ))}
            </ul>
            {till.options.channels.length <= 1 && channel === 'all' && (
              <p className="mt-2 text-sm text-ink-2">Deliveroo and Just Eat orders reach the till once the apps are linked to Lightspeed. Until then their statements are below.</p>
            )}
          </div>
        )}

        {showTakings && (
          <div>
            <GroupLabel className="mb-2">Paid by</GroupLabel>
            <ShareBar parts={payParts} />
            <ul className="mt-2 flex flex-col">
              {payParts.map((p) => (
                <li key={p.key}>
                  <DrillRow active={paid === p.key} onClick={() => onDrill('paid', p.key)} label={`Only ${p.label.toLowerCase()}`}>
                    <span aria-hidden="true" className="size-3 flex-none rounded-[3px]" style={{ background: p.color }} />
                    <span className="flex-1 text-ink">{p.label}</span>
                    <span className="fig w-20 text-right font-bold">{gbp(p.value)}</span>
                    <span className="fig w-10 text-right text-ink-2">{pct(p.value, takenTotal)}</span>
                  </DrillRow>
                </li>
              ))}
            </ul>
            {totals.days === 0 && <p className="mt-2 text-sm text-ink-2">No card or cash entered for these days.</p>}
          </div>
        )}

        {showApps && apps.months > 0 && (
          <div>
            <GroupLabel className="mb-2">Delivery statements · {apps.months === 1 ? 'the month' : `${count(apps.months)} months`} in view</GroupLabel>
            <ShareBar parts={appRows.map((a) => ({ key: a.ch, value: a.gross, color: channelColor(a.ch) }))} />
            <ul className="mt-2 flex flex-col">
              {appRows.map((a) => (
                <li key={a.ch}>
                  <DrillRow active={channel === a.ch} onClick={() => onDrill('channel', a.ch)} label={`Only ${CHANNEL_LABEL[a.ch] ?? a.ch}`}>
                    <span aria-hidden="true" className="size-3 flex-none rounded-[3px]" style={{ background: channelColor(a.ch) }} />
                    <span className="min-w-0 flex-1 py-1.5">
                      <span className="block text-ink">{CHANNEL_LABEL[a.ch] ?? a.ch}</span>
                      <span className="fig block text-sm text-ink-2">
                        {a.rows.length === 0
                          ? 'not uploaded: missing, not zero'
                          : a.kept === null
                            ? `${count(a.rows.length)} ${a.rows.length === 1 ? 'statement' : 'statements'}, commission not reported`
                            : `${gbp(a.kept)} kept after commission and ads${a.missing > 0 ? ` · ${count(a.missing)} not uploaded` : ''}`}
                      </span>
                    </span>
                    <span className="fig w-20 text-right font-bold">{a.rows.length === 0 ? <span className="text-ink-3">—</span> : gbp(a.gross)}</span>
                    <span className="fig w-10 text-right text-ink-2">{a.rows.length === 0 ? '' : pct(a.gross, apps.gross)}</span>
                  </DrillRow>
                </li>
              ))}
            </ul>
            {apps.present.some((r) => r.kept_bp !== null) && (
              <p className="mt-2 text-sm text-ink-2">
                Kept: {apps.present.filter((r) => r.kept_bp !== null).map((r) => `${r.channel_label} ${mLabel(r.month)} ${pctBp(r.kept_bp)}`).join(' · ')}.
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

/* -------------------------------------------------- weekday + best days --- */

type Ledger = 'till' | 'taken'

function WeekdaysAndBestDays({
  till,
  rows,
  paid,
  showTakings,
  onDrill,
  active,
}: {
  till: SalesInsights | null
  rows: SalesDay[]
  paid: Paid
  showTakings: boolean
  onDrill: (k: Drill, v: string) => void
  active?: string
}) {
  const canTill = till !== null
  const canTaken = showTakings && rows.length > 0
  const [picked, setPicked] = useState<Ledger | null>(null)
  const ledger: Ledger = picked && ((picked === 'till' && canTill) || (picked === 'taken' && canTaken)) ? picked : canTill ? 'till' : 'taken'
  const takenLabel = paid === 'card' ? 'Card taken' : paid === 'cash' ? 'Cash taken' : 'Money taken'

  const byWeekday = WD_SHORT.map((_, w) => {
    if (ledger === 'till' && till) {
      const r = till.by_weekday.find((x) => x.weekday === w)
      return { n: r?.trading_days ?? 0, avg: r && r.trading_days > 0 ? Math.round(r.gross_pence / r.trading_days) : null }
    }
    const days = rows.filter((d) => weekdayIndex(d.date) === w)
    const sum = days.reduce((n, d) => n + takenOf(d, paid), 0)
    return { n: days.length, avg: days.length ? Math.round(sum / days.length) : null }
  })
  const top = Math.max(1, ...byWeekday.map((b) => b.avg ?? 0))
  const best = byWeekday.reduce((bi, b, i) => ((b.avg ?? -1) > (byWeekday[bi]?.avg ?? -1) ? i : bi), 0)

  const bestDays =
    ledger === 'till' && till
      ? till.by_day.filter((d) => d.gross_pence !== null).map((d) => ({ date: d.date, value: d.gross_pence ?? 0, sub: `${count(d.receipts)} receipts` }))
      : rows.map((d) => ({ date: d.date, value: takenOf(d, paid), sub: d.orders !== null ? `${count(d.orders)} orders` : '' }))
  const topDays = [...bestDays].sort((a, b) => b.value - a.value || a.date.localeCompare(b.date)).slice(0, 5)
  const maxDay = topDays[0]?.value ?? 1

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <section aria-labelledby="wd-h" className="min-w-0">
        <ChartHead id="wd-h" title="By weekday" sub={`Average ${ledger === 'till' ? 'till sales' : takenLabel.toLowerCase()} per day open. Click a day to see only that weekday.`}>
          {canTill && canTaken && (
            <Segmented<Ledger>
              label="Show"
              value={ledger}
              onChange={setPicked}
              options={[
                { value: 'till', label: 'Till sales' },
                { value: 'taken', label: takenLabel },
              ]}
            />
          )}
        </ChartHead>
        <div className="flex h-44 items-end gap-2">
          {byWeekday.map((b, i) => (
            <button
              key={WD_SHORT[i]}
              type="button"
              onClick={() => onDrill('weekday', String(i))}
              aria-pressed={active === String(i)}
              aria-label={`${WD_LONG[i]}: ${b.avg === null ? 'not open' : `${gbp(b.avg)} a day over ${b.n} ${b.n === 1 ? 'day' : 'days'}`}`}
              className={cx('group flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1 rounded-control pt-1 hover:bg-canvas-2', active === String(i) && 'bg-brand-wash')}
            >
              <span className={cx('fig text-xs', i === best ? 'font-bold text-ink' : 'text-ink-2')}>{b.avg === null ? '—' : axisMoney(b.avg)}</span>
              <span className="w-full max-w-6 rounded-t-[4px]" style={{ height: `${((b.avg ?? 0) / top) * 100}%`, minHeight: b.avg ? 2 : 0, background: ledger === 'till' ? TILL : CARD }} />
              <span className={cx('text-sm', i === best ? 'font-bold text-ink' : 'text-ink-2')}>{WD_SHORT[i]}</span>
            </button>
          ))}
        </div>
      </section>

      <section aria-labelledby="best-h" className="min-w-0">
        <ChartHead id="best-h" title="Best days" sub={`The biggest days in view, by ${ledger === 'till' ? 'till sales' : takenLabel.toLowerCase()}.`} />
        {topDays.length === 0 ? (
          <p className="text-sm text-ink-2">No days to rank.</p>
        ) : (
          <ol>
            {topDays.map((d, i) => (
              <li key={d.date} className="grid grid-cols-[1.5rem_minmax(0,7rem)_minmax(0,1fr)_auto] items-center gap-3 border-b border-line-row py-1.5 text-base">
                <span className="fig text-sm text-ink-3">{i + 1}</span>
                <span className="truncate">
                  {fd(d.date)}
                  {d.sub && <span className="block truncate text-sm text-ink-2">{d.sub}</span>}
                </span>
                <span className="h-2 rounded-full bg-canvas" aria-hidden="true">
                  <span className="block h-2 rounded-full" style={{ width: `${(d.value / maxDay) * 100}%`, background: ledger === 'till' ? TILL : CARD }} />
                </span>
                <span className="fig text-right font-bold">{gbp(d.value)}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}
