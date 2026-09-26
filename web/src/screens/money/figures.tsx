/**
 * Money rendering for this screen, plus the three helpers it needs.
 *
 * One rule this screen adds to the shared ones: a column of order totals is
 * rendered in POUNDS all the way down, by `<P>`, even below £1. `lib/format`'s
 * `money()` switches to pence under a pound — right for a single unit cost,
 * wrong for a column, because a column that changes unit half-way down cannot
 * be scanned, and half these suppliers total zero. That rule used to live here
 * as a local `pounds()`; it is `poundsOnly()` / `poundsOnlyDec()` in
 * `lib/format` now, shared with the two other screens that needed it, and the
 * conversion is still an exact integer shift rather than a division.
 *
 * Everything else holds: integer pence in, formatted here, never arithmetic'd as
 * a float, and a null renders as the reason it is null.
 */
import type { Dec } from '../../lib/dec'
import { poundsOnly, poundsOnlyDec } from '../../lib/format'
import { Fig, type Tone } from '../../components/ui'
import type { ReactNode } from 'react'

type Size = 'sm' | 'base' | 'lg' | 'xl'

/** Money in pounds, always. For any column of totals, minimums or fees. */
export function P({
  v,
  missing = 'not set',
  tone = 'plain',
  size = 'base',
}: {
  v: string | number | null
  missing?: string
  tone?: Tone
  size?: Size
}) {
  if (v === null) return <Fig missing={missing} />
  return (
    <Fig tone={tone} size={size}>
      {poundsOnly(v)}
    </Fig>
  )
}

/** Pounds from a Dec summed here, with no string round-trip. Same unit rule as
 *  `<P>`: a figure that sits in a row beside other totals keeps their unit. */
export function PD({
  v,
  missing = 'not computed',
  tone = 'plain',
  size = 'base',
}: {
  v: Dec | null
  missing?: string
  tone?: Tone
  size?: Size
}) {
  if (v === null) return <Fig missing={missing} />
  return (
    <Fig tone={tone} size={size}>
      {poundsOnlyDec(v)}
    </Fig>
  )
}

/** A count. Grouped at four digits; never summed in the browser. */
export function N({
  v,
  missing = 'not computed',
  unit,
  tone = 'plain',
  size = 'base',
}: {
  v: number | null
  missing?: string
  unit?: string
  tone?: Tone
  size?: Size
}) {
  if (v === null) return <Fig missing={missing} />
  return (
    <Fig tone={tone} size={size}>
      {v.toLocaleString('en-GB')}
      {unit && <span className="font-normal text-ink-3"> {unit}</span>}
    </Fig>
  )
}

export function Days({ n, tone = 'plain' }: { n: number | null; tone?: Tone }) {
  if (n === null) return <Fig missing="not set" />
  return (
    <Fig tone={tone}>
      {n}
      <span className="font-normal text-ink-3"> d</span>
    </Fig>
  )
}

/** A label above a figure, inside a panel rather than a table head. Sentence
 *  case: tracked-out capitals cost legibility at 11px and the design law bans
 *  them outright. */
export function Field({
  label,
  children,
  tone,
}: {
  label: ReactNode
  children: ReactNode
  tone?: 'warn'
}) {
  return (
    <div className="min-w-0">
      <div
        className={`text-[0.6875rem] font-medium ${tone === 'warn' ? 'text-warn-ink' : 'text-ink-4'}`}
      >
        {label}
      </div>
      <div className="mt-1">{children}</div>
    </div>
  )
}

/* ------------------------------------------------------------- helpers --- */

const DAYS = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/** ISO 1..7, in week order. EMPTY means any day — walk-in retail — and that has
 *  to be said rather than printed as nothing. */
export function weekdayNames(iso: readonly number[]): string {
  if (iso.length === 0) return 'any day (walk-in)'
  return [...iso]
    .sort((a, b) => a - b)
    .map((d) => DAYS[d] ?? String(d))
    .join(' ')
}

export function shortTime(t: string | null): string | null {
  if (!t) return null
  const parts = t.split(':')
  if (parts.length < 2) return t
  return `${parts[0]}:${parts[1]}`
}

/** The API writes its em dashes as `--`; its prose is otherwise verbatim,
 *  because no number in this app is ever read out of a sentence. */
export function prose(s: string): string {
  return s.replace(/ -- /g, ' — ')
}

export function channelLabel(c: string): string {
  return c.replace(/_/g, ' ').toLowerCase()
}

/**
 * Two money fields on `/api/today` are declared `string` in `lib/api.ts` but the
 * API schema types them `str | None`: `expiry_write_offs_value_pence` and
 * `expiring_value_pence` are BOTH null when any batch in the set has no unit
 * cost, because a partial total would understate the waste it exists to warn
 * about (invariant 8). `lib/` is shared and not ours to correct, so the null is
 * absorbed here rather than rendered as `0`.
 */
export function maybePence(v: string | null | undefined): string | null {
  return v === null || v === undefined || v === '' ? null : v
}
