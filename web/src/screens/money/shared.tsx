/**
 * Shared pieces of the six Money tabs: the month selector (one state across
 * Overview, Sales, Expenses and Reconcile), save status, inline-edit cells and
 * the finance formatting helpers.
 *
 * Money arrives as integer pence and is formatted here at the edge; typed money
 * is parsed as an exact decimal (`poundsToPence`), never with parseFloat
 * (invariant 11). A missing figure renders as words or "—", never as £0.00.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { cx, FilterChip } from '../../components/ui'
import { poundsToPence, penceToPounds } from '../../components/confirm/numbers'
import { gbp } from '../../lib/format'
import { useLocation } from '../../lib/router'
import { useFinanceMonths } from '../../lib/finance-api'
import type { WriteResult } from '../../lib/api'
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

/* ---------------------------------------------------------- save status --- */

export type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'error'; message: string }

/**
 * "Saving…" while a write is in flight, "Saved" after, and on failure coral
 * "Not saved — <reason>" (the design had no error state).
 */
export function useSaveStatus() {
  const [state, setState] = useState<SaveState>({ kind: 'idle' })
  async function run<T>(work: () => Promise<WriteResult<T>>): Promise<WriteResult<T>> {
    setState({ kind: 'saving' })
    const r = await work()
    if (r.kind === 'ok') setState({ kind: 'saved' })
    else setState({ kind: 'error', message: r.message })
    return r
  }
  const fail = (message: string) => setState({ kind: 'error', message })
  return { state, run, fail }
}

export function SaveStatus({ state }: { state: SaveState }) {
  if (state.kind === 'idle') return null
  if (state.kind === 'saving') return <span className="text-base text-ink-3">Saving…</span>
  if (state.kind === 'saved') return <span className="text-base text-ink-3">Saved</span>
  return (
    <span role="alert" className="max-w-[48ch] text-sm text-bad-ink">
      Not saved — {state.message}
    </span>
  )
}

/* --------------------------------------------------------- inline cells --- */

const CELL =
  'h-7 w-full min-w-0 rounded-control border bg-surface px-1.5 text-base outline-none ' +
  // `focus:` not `focus-visible:`: a date input's focus sits on an inner field,
  // and Chrome does not report :focus-visible on the input itself.
  'placeholder:text-ink-3 focus:border-brand focus:ring-3 focus:ring-brand-wash ' +
  'disabled:bg-canvas disabled:text-ink-2'

/**
 * A money input that commits on blur or Enter. Holds the typed string; parses it
 * as an exact decimal. Bad input keeps the red border and the last good value
 * stays saved. `onCommit(null)` means the field was cleared.
 */
export function MoneyCell({
  pence,
  onCommit,
  label,
  placeholder = '—',
  disabled,
  title,
  allowBlank = true,
  est,
}: {
  pence: number | null
  onCommit: (next: number | null) => Promise<boolean>
  label: string
  placeholder?: string
  disabled?: boolean
  title?: string
  allowBlank?: boolean
  est?: boolean
}) {
  const shown = pence === null ? '' : penceToPounds(pence)
  const [text, setText] = useState(shown)
  const [bad, setBad] = useState<string | null>(null)
  const editing = useRef(false)
  useEffect(() => {
    if (!editing.current) setText(shown)
  }, [shown])

  async function commit() {
    editing.current = false
    const parsed = poundsToPence(text)
    if (parsed.kind === 'bad') {
      setBad(parsed.message)
      return
    }
    if (parsed.kind === 'blank' && !allowBlank) {
      setBad('This needs a figure.')
      return
    }
    setBad(null)
    const next = parsed.kind === 'value' ? parsed.value : null
    if (next === pence) {
      setText(shown)
      return
    }
    const ok = await onCommit(next)
    if (!ok) setText(shown)
  }

  return (
    <input
      aria-label={label}
      title={bad ?? title}
      aria-invalid={bad !== null || undefined}
      inputMode="decimal"
      disabled={disabled}
      placeholder={placeholder}
      value={text}
      onFocus={() => (editing.current = true)}
      onChange={(e) => {
        editing.current = true
        setText(e.target.value)
      }}
      onBlur={() => void commit()}
      onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        if (e.key === 'Escape') {
          editing.current = false
          setText(shown)
          setBad(null)
        }
      }}
      className={cx(CELL, 'fig text-right', bad ? 'border-alert' : 'border-line-strong', est && 'italic')}
    />
  )
}

/** A text input that commits on blur or Enter. Blank commits null. */
export function TextCell({
  value,
  onCommit,
  label,
  placeholder = '—',
  disabled,
  alert,
  required,
  inputRef,
}: {
  value: string | null
  onCommit: (next: string | null) => Promise<boolean>
  label: string
  placeholder?: string
  disabled?: boolean
  alert?: boolean
  required?: boolean
  inputRef?: (el: HTMLInputElement | null) => void
}) {
  const shown = value ?? ''
  const [text, setText] = useState(shown)
  const [bad, setBad] = useState(false)
  const editing = useRef(false)
  useEffect(() => {
    if (!editing.current) setText(shown)
  }, [shown])
  async function commit() {
    editing.current = false
    const next = text.trim() === '' ? null : text.trim()
    if (next === null && required) {
      setBad(true)
      return
    }
    setBad(false)
    if ((next ?? '') === shown.trim()) return
    const ok = await onCommit(next)
    if (!ok) setText(shown)
  }
  return (
    <input
      ref={inputRef}
      aria-label={label}
      title={text || undefined}
      disabled={disabled}
      placeholder={placeholder}
      value={text}
      onFocus={() => (editing.current = true)}
      onChange={(e) => {
        editing.current = true
        setText(e.target.value)
      }}
      onBlur={() => void commit()}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        if (e.key === 'Escape') {
          editing.current = false
          setText(shown)
        }
      }}
      className={cx(CELL, bad || alert ? 'border-alert' : 'border-line-strong')}
    />
  )
}

export function DateCell({
  value,
  onCommit,
  label,
  disabled,
}: {
  value: string
  onCommit: (next: string) => Promise<boolean>
  label: string
  disabled?: boolean
}) {
  const [text, setText] = useState(value)
  useEffect(() => setText(value), [value])
  return (
    <input
      type="date"
      aria-label={label}
      disabled={disabled}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={async () => {
        if (!text || text === value) {
          setText(value)
          return
        }
        const ok = await onCommit(text)
        if (!ok) setText(value)
      }}
      className={cx(CELL, 'border-line-strong px-1 text-sm')}
    />
  )
}

export function SelectCell({
  value,
  onChange,
  label,
  children,
  disabled,
  title,
}: {
  value: string
  onChange: (next: string) => void
  label: string
  children: ReactNode
  disabled?: boolean
  title?: string
}) {
  return (
    <select
      aria-label={label}
      title={title}
      disabled={disabled}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cx(CELL, 'border-line-strong px-0.5 text-sm')}
    >
      {children}
    </select>
  )
}

/** The bare × delete of the design, as a real button with a name. */
export function RemoveButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="flex size-7 items-center justify-center rounded-control text-lg text-ink-3 hover:bg-canvas hover:text-ink disabled:opacity-30"
    >
      <span aria-hidden="true">×</span>
    </button>
  )
}

/** "Deleted · Undo" for 5 seconds after a money row is removed. */
export function UndoBar({ text, onUndo, onDone }: { text: string; onUndo: () => void; onDone: () => void }) {
  useEffect(() => {
    const t = window.setTimeout(onDone, 5000)
    return () => window.clearTimeout(t)
  }, [onDone])
  return (
    <div role="status" className="flex items-center gap-3 rounded-button bg-ink px-3.5 py-2 text-base text-white">
      <span>{text}</span>
      <button type="button" onClick={onUndo} className="font-bold text-white underline underline-offset-2">
        Undo
      </button>
    </div>
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

/** Turn a write result into the boolean the inline cells want, reporting failures. */
export async function committed<T>(
  save: ReturnType<typeof useSaveStatus>,
  work: () => Promise<WriteResult<T>>,
  after?: (data: T) => void | Promise<void>,
): Promise<boolean> {
  const r = await save.run(work)
  if (r.kind === 'ok') {
    await after?.(r.data)
    return true
  }
  return false
}
