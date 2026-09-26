/**
 * Number rendering for this screen. Three rules, all from DESIGN.md §1:
 *
 *  - Money arrives as integer pence or an exact decimal STRING of pence and is
 *    formatted here. Nothing on this screen does float arithmetic; the two
 *    figures it derives (the placeholder-terms subtotal, the premium across
 *    runs) are summed as exact integers by `lib/dec.ts`.
 *  - Where a fractional figure gets summed later — a unit price, a retail
 *    premium — the digits past 2dp are PRINTED in muted ink in the same column
 *    (R3), not rounded away and not hidden in a tooltip.
 *  - A missing value is null, never 0, and renders as the reason it is missing.
 */
import type { ReactNode } from 'react'
import { money, pence, trimQty } from '../../lib/format'
import { Fig, type Tone } from '../../components/ui'

/** Money, rounded, units declared. For integer pence: totals, minimums, fees. */
export function M({
  v,
  tone = 'plain',
  size = 'base',
  missing,
}: {
  v: number | string | null
  tone?: Tone
  size?: 'sm' | 'base' | 'lg' | 'xl'
  missing?: string
}) {
  if (v === null) return <Fig missing={missing ?? 'not priced'} />
  return (
    <Fig tone={tone} size={size} className="whitespace-nowrap">
      {money(v)}
    </Fig>
  )
}

/** Money with its exact tail, always in pence so the column keeps one unit.
 *  For unit prices and premiums — the figures that get compared and summed. */
export function MExact({
  v,
  tone = 'plain',
  size = 'base',
  suffix,
  missing,
}: {
  v: string | number | null
  tone?: Tone
  size?: 'sm' | 'base' | 'lg' | 'xl'
  /** e.g. `/L`. Part of the figure, never a separate column. */
  suffix?: string
  missing?: string
}) {
  if (v === null) return <Fig missing={missing ?? 'not priced'} />
  const { head, tail } = pence(v)
  return (
    <Fig tone={tone} size={size} className="whitespace-nowrap">
      {head}
      {tail && <span className="text-ink-4">{tail}</span>}p{suffix}
    </Fig>
  )
}

/** A quantity as sent, trailing zeros trimmed, unit always attached. */
export function Q({
  qty,
  unit,
  tone = 'plain',
  size = 'base',
  missing,
}: {
  qty: string | null
  unit?: string
  tone?: Tone
  size?: 'sm' | 'base' | 'lg' | 'xl'
  missing?: string
}) {
  if (qty === null) return <Fig missing={missing ?? 'withheld'} />
  return (
    <Fig tone={tone} size={size} className="whitespace-nowrap">
      {trimQty(qty)}
      {unit && <span className="text-ink-3 font-normal"> {unit}</span>}
    </Fig>
  )
}

export function Days({ n, tone = 'plain' }: { n: number | null; tone?: Tone }) {
  if (n === null) return <Fig missing="not computed" />
  return (
    <Fig tone={tone} className="whitespace-nowrap">
      {n}
      <span className="text-ink-3 font-normal"> d</span>
    </Fig>
  )
}

/**
 * A label above a figure, used inside panels rather than table headers.
 *
 * Sentence case, in the same small grey ink as a column head. Tracked-out
 * capitals are banned by the design law: they cost legibility at 11px and they
 * are the cliché that made the last build read as a dashboard template.
 *
 * Named `Field` rather than `Cell` because the kit now owns a `Cell` (a name
 * over a quieter second line, for the first column of a table) and two things
 * called the same thing in one screen is how the wrong one gets imported.
 */
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

const LONDON = 'Europe/London'

/** "Tue 29 Sep" — a delivery date is read as a weekday first, because whether
 *  the supplier delivers that day is the thing in question. */
export function DayName({ iso }: { iso: string | null }) {
  if (!iso) return <Fig missing="no date" />
  const d = new Date(`${iso}T12:00:00Z`)
  const text = new Intl.DateTimeFormat('en-GB', {
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    timeZone: LONDON,
  }).format(d)
  return <Fig>{text}</Fig>
}
