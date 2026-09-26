/**
 * Figure rendering for the margin screen.
 *
 * Every number on the screen comes through one of these, so the three rules
 * hold in one place: a figure is tabular, a fractional figure prints the digits
 * past 2dp rather than rounding them away, and a missing value renders as the
 * reason it is missing instead of as a zero.
 */
import { splitExact, toFixed } from '../../lib/dec'
import { poundsOnlyDec } from '../../lib/format'
import { Exact, Fig, EST, type FigSize, type FigWeight } from '../../components/prim'
import type { Tone } from '../../components/ui'
import { dec } from './data'

interface Common {
  size?: FigSize
  weight?: FigWeight
  tone?: Tone
  /** Why there is no number. Never rendered beside one. */
  missing?: string
}

/** A percentage, 1dp, exact. `signed` where the direction is the finding. */
export function Pct({
  v,
  signed = false,
  estimate = false,
  size,
  weight,
  tone,
  missing = 'not computed',
}: Common & { v: string | number | null | undefined; signed?: boolean; estimate?: boolean }) {
  const d = dec(v)
  if (d === null) return <Fig missing={missing} />
  const body = toFixed(d, 1)
  const sign = signed && d.u > 0n ? '+' : ''
  return (
    <Fig size={size} weight={weight} tone={tone} className={estimate ? EST : ''}>
      {sign}
      {body}%
    </Fig>
  )
}

/** Money in pence with its exact tail: the unit never changes down a column. */
export function P({
  v,
  suffix = '',
  estimate = false,
  size,
  weight,
  tone,
  missing = 'not computed',
}: Common & { v: string | number | null | undefined; suffix?: string; estimate?: boolean }) {
  const d = dec(v)
  if (d === null) return <Fig missing={missing} />
  const { head, tail } = splitExact(d, 2)
  const body = <Exact head={head} tail={tail} suffix={`p${suffix}`} size={size} weight={weight} tone={tone} />
  return estimate ? <span className={EST}>{body}</span> : body
}

/**
 * Money in pounds, all the way down.
 *
 * This screen sets prices and window totals in one column each, and
 * `lib/format`'s `money()` deliberately drops below £1 into pence -- right in
 * prose, wrong in a column, because a column that changes unit half-way down
 * cannot be scanned and a 50p babyccino sits directly under a £4.20 milkshake.
 * `poundsOnlyDec` is the shared version of the local helper this screen used to
 * carry; the sign precedes the symbol (−£5.00).
 */
export function Money({
  v,
  size,
  weight,
  tone,
  missing = 'not computed',
}: Common & { v: string | number | null | undefined }) {
  const d = dec(v)
  if (d === null) return <Fig missing={missing} />
  return (
    <Fig size={size} weight={weight} tone={tone}>
      {poundsOnlyDec(d)}
    </Fig>
  )
}

/**
 * A count or a plain quantity as sent — units sold, staff hours, item counts.
 * `dp` is explicit because a count is whole and an hours figure is not, and
 * printing `34` where the value is `34.040278` is a rounding decision, not a
 * formatting one.
 */
export function N({
  v,
  size,
  weight,
  tone,
  missing = 'not recorded',
  unit,
  dp = 0,
}: Common & { v: string | number | null | undefined; unit?: string; dp?: number }) {
  const d = dec(v)
  if (d === null) return <Fig missing={missing} />
  return (
    <Fig size={size} weight={weight} tone={tone}>
      {toFixed(d, dp)}
      {unit && <span className="font-normal text-ink-3"> {unit}</span>}
    </Fig>
  )
}

/** Prep time. Always an estimate in the current data, so marked as one. */
export function Secs({
  v,
  estimate,
  size,
  weight,
  missing = 'prep time not set',
}: Common & { v: number | null; estimate: boolean }) {
  if (v === null) return <Fig missing={missing} />
  return (
    <span className={estimate ? EST : undefined}>
      <Fig size={size} weight={weight}>
        {v}
        <span className="font-normal text-ink-3"> s</span>
      </Fig>
    </span>
  )
}

/**
 * How far an item moves between the two orderings, signed. Positive means it
 * ranks better per minute of the barista's time than by margin %; negative
 * means margin % flatters it.
 *
 * No colour: a move is a fact about the two rankings, not a state the system
 * has classified as good or bad. The arrow carries the direction.
 */
export function Move({
  n,
  size = 'base',
  weight = 'semibold',
}: {
  n: number | null
  size?: FigSize
  weight?: FigWeight
}) {
  if (n === null) return <Fig missing="not ranked" />
  if (n === 0)
    return (
      <Fig size={size} weight={weight} tone="muted">
        0
      </Fig>
    )
  const up = n > 0
  return (
    <span className="whitespace-nowrap">
      <span aria-hidden className="mr-0.5 text-ink-3">
        {up ? '▲' : '▼'}
      </span>
      <Fig size={size} weight={weight}>
        {up ? '+' : '−'}
        {Math.abs(n)}
      </Fig>
    </span>
  )
}

/** `#12` — a position in one of the two orderings. */
export function Rank({
  n,
  active = false,
  size = 'base',
}: {
  n: number | null
  active?: boolean
  size?: FigSize
}) {
  if (n === null) return <Fig missing="not ranked" />
  return (
    <span className="whitespace-nowrap">
      <span className={`text-[0.8em] ${active ? 'text-brand/70' : 'text-ink-4'}`}>#</span>
      <Fig size={size} tone={active ? 'plain' : 'muted'} weight={active ? 'semibold' : undefined}>
        {n}
      </Fig>
    </span>
  )
}

/**
 * The move drawn rather than stated: one track from best (left) to worst
 * (right), a hollow tick for the margin-% position and a filled amber one for
 * the per-minute position, with the distance between them shaded.
 *
 * The percentages here are CSS geometry derived from two integer ranks. No
 * displayed figure comes from this arithmetic.
 */
export function Track({
  from,
  to,
  total,
}: {
  from: number | null
  to: number | null
  total: number
}) {
  if (from === null || to === null || total < 2) return null
  const span = total - 1
  const a = ((from - 1) / span) * 100
  const b = ((to - 1) / span) * 100
  const left = Math.min(a, b)
  const width = Math.abs(b - a)
  return (
    <div
      className="relative h-3 min-w-[6rem] flex-1"
      role="img"
      aria-label={`moves from position ${from} to position ${to} of ${total}`}
    >
      <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-line-2" />
      <div
        className="absolute top-1/2 h-[3px] -translate-y-1/2 rounded-pill bg-brand/30"
        style={{ left: `${left}%`, width: `${width}%` }}
      />
      <span
        className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-pill border border-ink-3 bg-surface"
        style={{ left: `${a}%` }}
      />
      <span
        className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-pill bg-brand"
        style={{ left: `${b}%` }}
      />
    </div>
  )
}

/** The track's key, stated once beside it rather than guessed at per row. */
export function TrackKey() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.6875rem] text-ink-3">
      <span className="inline-flex items-center gap-1">
        <span className="h-2 w-2 rounded-pill border border-ink-3 bg-surface" />
        by margin %
      </span>
      <span className="inline-flex items-center gap-1">
        <span className="h-2.5 w-2.5 rounded-pill bg-brand" />
        by margin per minute
      </span>
      <span className="text-ink-4">best at the left</span>
    </div>
  )
}
