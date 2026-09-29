/**
 * Filter pieces shared by the read-only Money tables (Sales, Expenses,
 * Transactions): a period bar (month chips + a custom date range), a £ range,
 * a totals strip, and the small date helpers they need.
 *
 * Money typed into a filter is parsed as an exact decimal (poundsToPence),
 * never with parseFloat (invariant 11).
 */
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { FilterChip, cx } from '../../components/ui'
import { poundsToPence } from '../../components/confirm/numbers'
import type { ISODate, Month, Period } from '../../lib/types/finance'
import { mLabel } from './shared'

/* ----------------------------------------------------------------- dates --- */

export interface DateRange {
  from: ISODate
  to: ISODate
}

function parts(iso: string): [number, number, number] {
  const [y = '0', m = '1', d = '1'] = iso.split('-')
  return [Number(y), Number(m), Number(d)]
}

/** 0 = Monday ... 6 = Sunday, from a business date with no time zone. */
export function weekdayIndex(iso: ISODate): number {
  const [y, m, d] = parts(iso)
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7
}

export const isWeekend = (iso: ISODate) => weekdayIndex(iso) >= 5

export function lastDayOfMonth(month: Month): ISODate {
  const [y, m] = parts(`${month}-01`)
  const d = new Date(Date.UTC(y, m, 0)).getUTCDate()
  return `${month}-${String(d).padStart(2, '0')}`
}

export function inRange(iso: ISODate, r: DateRange | null): boolean {
  return r === null || (iso >= r.from && iso <= r.to)
}

/** The date window a period + optional custom range stands for; null = everything. */
export function windowOf(period: Period | null, range: DateRange | null): DateRange | null {
  if (range) return range
  if (!period || period === 'all') return null
  return { from: `${period}-01`, to: lastDayOfMonth(period) }
}

export const WEEKDAY_OPTIONS = [
  { value: 'all', label: 'Any day' },
  { value: 'weekdays', label: 'Mon–Fri' },
  { value: 'weekends', label: 'Sat–Sun' },
  { value: '0', label: 'Mondays' },
  { value: '1', label: 'Tuesdays' },
  { value: '2', label: 'Wednesdays' },
  { value: '3', label: 'Thursdays' },
  { value: '4', label: 'Fridays' },
  { value: '5', label: 'Saturdays' },
  { value: '6', label: 'Sundays' },
] as const

export function matchesWeekday(iso: ISODate, filter: string): boolean {
  if (filter === 'all') return true
  const w = weekdayIndex(iso)
  if (filter === 'weekdays') return w < 5
  if (filter === 'weekends') return w >= 5
  return String(w) === filter
}

/** "Sat 4 Apr" -> used as a short label for a range chip. */
export function shortDate(iso: ISODate): string {
  const [, m, d] = parts(iso)
  const MN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${d} ${MN[m - 1]} ${iso.slice(2, 4)}`
}

/* ------------------------------------------------------------ period bar --- */

const DATE_INPUT =
  'fig h-[34px] rounded-full border border-line-control bg-surface px-3 text-sm outline-none ' +
  'focus:edge-brand'

/**
 * "Period" + All + every month + Custom. Custom shows two date inputs; a
 * custom range overrides the month selection until it is cleared.
 */
export function PeriodBar({
  period,
  months,
  onPeriod,
  range,
  onRange,
  trailing,
}: {
  period: Period | null
  months: Month[]
  onPeriod: (p: Period) => void
  range: DateRange | null
  onRange: (r: DateRange | null) => void
  trailing?: ReactNode
}) {
  const [custom, setCustom] = useState(range !== null)
  const base = windowOf(period, null)
  const [from, setFrom] = useState(range?.from ?? base?.from ?? '')
  const [to, setTo] = useState(range?.to ?? base?.to ?? '')
  useEffect(() => {
    if (range) {
      setFrom(range.from)
      setTo(range.to)
    }
  }, [range])

  function apply(f: string, t: string) {
    setFrom(f)
    setTo(t)
    if (f && t) onRange(f <= t ? { from: f, to: t } : { from: t, to: f })
  }

  return (
    <div className="flex flex-none flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-4 py-2.5 sm:px-5">
      <div className="flex min-w-0 max-w-full items-center gap-2">
        <span className="flex-none text-base text-ink-2">Period</span>
        <div role="group" aria-label="Period" className="scroll-x -my-1 flex min-w-0 gap-1.5 py-1">
          <FilterChip
            active={!custom && period === 'all'}
            onClick={() => {
              setCustom(false)
              onRange(null)
              onPeriod('all')
            }}
          >
            All
          </FilterChip>
          {months.map((m) => (
            <FilterChip
              key={m}
              active={!custom && period === m}
              onClick={() => {
                setCustom(false)
                onRange(null)
                onPeriod(m)
              }}
            >
              {mLabel(m)}
            </FilterChip>
          ))}
          <FilterChip
            active={custom}
            onClick={() => {
              if (custom) {
                setCustom(false)
                onRange(null)
                return
              }
              setCustom(true)
              const w = windowOf(period, null)
              if (w) apply(w.from, w.to)
            }}
          >
            Custom…
          </FilterChip>
        </div>
      </div>
      {custom && (
        <div className="flex flex-wrap items-center gap-1.5">
          <input
            type="date"
            aria-label="From"
            value={from}
            onChange={(e) => apply(e.target.value, to)}
            className={DATE_INPUT}
          />
          <span className="text-ink-3">to</span>
          <input type="date" aria-label="To" value={to} onChange={(e) => apply(from, e.target.value)} className={DATE_INPUT} />
        </div>
      )}
      {trailing && <div className="ml-auto flex flex-wrap items-center gap-2">{trailing}</div>}
    </div>
  )
}

/* -------------------------------------------------------------- £ range --- */

export interface PenceRange {
  min: number | null
  max: number | null
}

export function inPence(v: number, r: PenceRange): boolean {
  return (r.min === null || v >= r.min) && (r.max === null || v <= r.max)
}

/** Two small £ inputs. Commits on blur/Enter; bad input keeps a red border. */
export function AmountRange({
  value,
  onChange,
  label = 'Amount',
}: {
  value: PenceRange
  onChange: (r: PenceRange) => void
  label?: string
}) {
  return (
    <div role="group" aria-label={label} className="flex items-center gap-1">
      <PoundBox placeholder="£ min" label={`${label} from`} pence={value.min} onCommit={(min) => onChange({ ...value, min })} />
      <span className="text-ink-3">–</span>
      <PoundBox placeholder="£ max" label={`${label} to`} pence={value.max} onCommit={(max) => onChange({ ...value, max })} />
    </div>
  )
}

function PoundBox({
  pence,
  onCommit,
  placeholder,
  label,
}: {
  pence: number | null
  onCommit: (p: number | null) => void
  placeholder: string
  label: string
}) {
  const [text, setText] = useState(pence === null ? '' : shownPounds(pence))
  const [bad, setBad] = useState(false)
  useEffect(() => setText(pence === null ? '' : shownPounds(pence)), [pence])
  function commit() {
    const p = poundsToPence(text)
    if (p.kind === 'bad') return setBad(true)
    setBad(false)
    const next = p.kind === 'value' ? p.value : null
    if (next !== pence) onCommit(next)
  }
  return (
    <input
      aria-label={label}
      aria-invalid={bad || undefined}
      inputMode="decimal"
      placeholder={placeholder}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && commit()}
      className={cx(
        'fig h-[34px] w-[84px] rounded-full border bg-surface px-3 text-right text-base outline-none placeholder:text-left placeholder:text-ink-3',
        'focus:edge-brand',
        bad ? 'border-alert' : pence !== null ? 'border-brand-line bg-brand-wash font-bold text-brand-ink' : 'border-line-control',
      )}
    />
  )
}

/** Integer pence -> "12.5" style text for an input, without float arithmetic. */
function shownPounds(p: number): string {
  const whole = Math.trunc(p / 100)
  const frac = p % 100
  return frac === 0 ? String(whole) : `${whole}.${String(frac).padStart(2, '0')}`
}

/* --------------------------------------------------------- totals strip --- */

/**
 * The period's figures in one quiet line: label over figure, hairline
 * dividers. Not a card grid.
 */
/** Moved to the kit (components/ui/page.tsx); re-exported so existing imports keep working. */
export { Figures } from '../../components/ui'

/** Integer count with thousands separators. */
export function count(n: number): string {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}
