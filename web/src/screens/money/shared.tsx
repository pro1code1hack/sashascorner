/**
 * Shared pieces of the six Money tabs: the month selector (one state across
 * Overview, Sales, Expenses and Transactions), the undo line and the finance
 * formatting helpers.
 *
 * Money arrives as integer pence and is formatted here at the edge; typed money
 * is parsed as an exact decimal (`poundsToPence`), never with parseFloat
 * (invariant 11). A missing figure renders as words or "—", never as £0.00.
 */
import { useEffect, useSyncExternalStore } from 'react'
import { cx, FilterChip, StatusLine } from '../../components/ui'
import { gbp } from '../../lib/format'
import { useLocation } from '../../lib/router'
import { useFinanceMonths } from '../../lib/finance-api'
import type { Month, Period } from '../../lib/types/finance'

export { gbp }

/* ------------------------------------------------------------ formatting --- */

/** Whole pounds, U+2212 minus: "£1,235", "−£12". Integer arithmetic only. */
export function gbp0(pence: number): string {
  const sign = pence < 0 ? '−' : ''
  const whole = Math.trunc((Math.abs(pence) + 50) / 100)
  return `${sign}£${String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`
}

/** Basis points as a whole percent: 3609 -> "36%". */
export function pctBp(bp: number | null): string {
  if (bp === null) return '—'
  return `${Math.round(bp / 100)}%`
}

const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTH_LONG = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function parts(iso: string): [number, number, number] {
  const [y = '0', m = '1', d = '1'] = iso.split('-')
  return [Number(y), Number(m), Number(d)]
}

/** "Fri 20 Mar": the design's `fd`, from a business date with no time zone. */
export function fd(iso: string): string {
  const [y, m, d] = parts(iso)
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay()
  return `${WD[wd]} ${d} ${MN[m - 1]}`
}

/** 'YYYY-MM' -> 'Apr 26'. */
export function mLabel(month: Month): string {
  const [y, m] = parts(`${month}-01`)
  return `${MN[m - 1]} ${String(y % 100).padStart(2, '0')}`
}

export function monthLong(month: Month): string {
  const [y, m] = parts(`${month}-01`)
  return `${MONTH_LONG[m - 1]} ${y}`
}

/** Today in Europe/London as YYYY-MM-DD. */
export function londonToday(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London' }).format(new Date())
}

export function addDays(iso: string, n: number): string {
  const [y, m, d] = parts(iso)
  const t = new Date(Date.UTC(y, m - 1, d + n))
  return t.toISOString().slice(0, 10)
}

export const METHOD_LABEL: Record<string, string> = {
  CARD: 'Card',
  BANK_TRANSFER: 'Bank transfer',
  DIRECT_DEBIT: 'Direct debit',
  STANDING_ORDER: 'Standing order',
  CASH: 'Cash',
  CASH_WITHDRAWAL: 'Cash withdrawal',
  OTHER: 'Other',
}

export const KIND_LABEL: Record<string, string> = {
  OPERATING: 'Running',
  CAPITAL: 'Equipment',
  DRAWINGS: 'Personal',
}

/* --------------------------------------------------------- month state --- */

let selected: Period | null = null
const listeners = new Set<() => void>()

function setSelected(p: Period): void {
  selected = p
  listeners.forEach((l) => l())
}

/**
 * The month the four month-scoped tabs share. Starts at `?month=` if the URL
 * carries one (the shell's cash banner links with it), else the latest month
 * with takings.
 */
export function useFinancePeriod(): {
  period: Period | null
  setPeriod: (p: Period) => void
  months: Month[]
} {
  const loc = useLocation()
  const q = useFinanceMonths()
  const current = useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => selected,
  )
  const fromUrl = loc.query.get('month')
  useEffect(() => {
    if (fromUrl && (fromUrl === 'all' || /^\d{4}-\d{2}$/.test(fromUrl)) && fromUrl !== selected) {
      setSelected(fromUrl)
    }
  }, [fromUrl])
  useEffect(() => {
    if (selected === null && !fromUrl && q.data) {
      setSelected(q.data.default_month ?? 'all')
    }
  }, [q.data, fromUrl])
  return { period: current, setPeriod: setSelected, months: q.data?.months ?? [] }
}

/** The design's month bar: "Month" + All + every month with data. Scrolls sideways. */
export function MonthBar({
  period,
  months,
  onChange,
}: {
  period: Period | null
  months: Month[]
  onChange: (p: Period) => void
}) {
  return (
    <div className="flex flex-none items-center gap-2 border-b border-line px-4 py-2.5 sm:px-5">
      <span className="flex-none text-base text-ink-2">Month</span>
      <div role="group" aria-label="Month" className="scroll-x -my-1 flex min-w-0 gap-1.5 py-1">
        <FilterChip active={period === 'all'} onClick={() => onChange('all')}>
          All
        </FilterChip>
        {months.map((m) => (
          <FilterChip key={m} active={period === m} onClick={() => onChange(m)}>
            {mLabel(m)}
          </FilterChip>
        ))}
      </div>
    </div>
  )
}

/**
 * "Deleted … · Undo" for 10 seconds after a money row is removed. Always
 * mounted (a live region that appears with its text is not reliably announced);
 * pass `undo={null}` when there is nothing to undo, `restore: null` to say
 * the undo itself failed.
 */
export function UndoBar({
  undo,
  onDone,
  className,
}: {
  undo: { text: string; restore: (() => void) | null } | null
  onDone: () => void
  className?: string
}) {
  useEffect(() => {
    if (undo === null) return
    const t = window.setTimeout(onDone, 10_000)
    return () => window.clearTimeout(t)
  }, [undo, onDone])
  return (
    <StatusLine
      className={className}
      outcome={
        undo === null
          ? null
          : undo.restore === null
            ? { kind: 'error', text: undo.text }
            : { kind: 'info', text: undo.text, action: { label: 'Undo', onClick: undo.restore } }
      }
    />
  )
}

/** Caveats, verbatim, in the quiet footnote style. */
export function Caveats({ items, className }: { items: readonly string[]; className?: string }) {
  if (items.length === 0) return null
  return (
    <ul className={cx('flex flex-col gap-0.5 text-sm text-ink-2', className)}>
      {items.map((c) => (
        <li key={c}>{c}</li>
      ))}
    </ul>
  )
}
