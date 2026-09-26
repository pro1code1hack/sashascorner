/**
 * Formatting at the edge. DESIGN.md §1 — units and precision, decided once.
 *
 * Nothing here does arithmetic on a float. Money and quantities go through
 * `dec.ts`; the only `number` values touched are percentages, day counts and
 * prep seconds, which arrive from the API already reduced and are formatted,
 * never combined.
 */
import { abs, cmp, fromInt, fromMoney, shiftDown, splitExact, toFixed, trimQty } from './dec'
import type { Dec } from './dec'
import type { Cost } from './types'

export { trimQty }

const ONE_POUND: Dec = fromInt(100)

/**
 * Money under £1 reads in pence — a café thinks in pence below a pound, and
 * `£0.7771` is four leading noise characters. £1 and over reads in pounds.
 */
export function money(v: string | number): string {
  return moneyDec(fromMoney(v))
}

/**
 * Money straight from an exact Dec, with no string round-trip.
 *
 * The sign is emitted BEFORE the currency symbol. Formatting the signed value
 * inside the template gave `£-5.00`, which is not a form anybody writes, and
 * `margin.json` and `stock-tier-a.json` both carry negative money.
 */
export function moneyDec(d: Dec): string {
  const sign = d.u < 0n ? '\u2212' : ''
  const m = abs(d)
  if (cmp(m, ONE_POUND) < 0) return `${sign}${toFixed(m, 2)}p`
  return `${sign}£${groupThousands(toFixed(shiftDown(m, 2), 2))}`
}

/**
 * Always pounds, never switching to pence. `money()` deliberately drops to pence
 * below £1 because a cafe thinks that way in prose -- but that makes it wrong for
 * a *column* of totals, where half the suppliers total zero and the column reads
 * `£125.50 ... 0.00p`. A column that changes unit half-way down cannot be scanned,
 * which is the same rule `pence()` already states in the other direction.
 *
 * Two screens wrote this by hand before it lived here. Use it for any figure that
 * sits in a column with other money.
 */
/**
 * Group the integer part in threes. `1430.05` -> `1,430.05`.
 *
 * Done on the STRING `toFixed` already produced, never by reformatting a number:
 * `Intl.NumberFormat` takes a `number`, and routing exact pence through a float
 * to add commas is the one thing this module exists to avoid. A leading minus (or
 * the U+2212 this module emits) is preserved and not grouped.
 */
function groupThousands(fixed: string): string {
  const sign = /^[-\u2212]/.test(fixed) ? fixed[0] : ''
  const body = sign ? fixed.slice(1) : fixed
  const dot = body.indexOf('.')
  const whole = dot === -1 ? body : body.slice(0, dot)
  const rest = dot === -1 ? '' : body.slice(dot)
  return sign + whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + rest
}

export function poundsOnly(v: string | number): string {
  return poundsOnlyDec(fromMoney(v))
}

/** `poundsOnly` from an exact Dec, with no string round-trip. Sign precedes the
 *  currency symbol, as in `moneyDec`. */
export function poundsOnlyDec(d: Dec): string {
  const sign = d.u < 0n ? '\u2212' : ''
  return `${sign}\u00a3${groupThousands(toFixed(shiftDown(abs(d), 2), 2))}`
}

/** Signed, for a delta in a column. Zero prints as zero, not +0. */
export function poundsOnlySigned(v: string | number): string {
  const d = fromMoney(v)
  const body = poundsOnly(v)
  if (d.u === 0n) return body
  return d.u > 0n ? `+${body}` : body
}

/** Signed, for a delta. A delta of zero is printed as zero, not as +0. */
export function moneySigned(v: string | number): string {
  const d = fromMoney(v)
  const body = money(v)
  if (d.u === 0n) return body
  return d.u > 0n ? `+${body}` : body
}

/**
 * The exact tail. Truncated at 2dp, remainder printed: head and tail
 * reconstitute the value exactly. Always in pence, never switching to pounds,
 * because a column that changes unit half-way down cannot be scanned.
 * DESIGN.md §1 R3.
 */
export function pence(v: string | number): { head: string; tail: string } {
  return splitExact(fromMoney(v), 2)
}

export function penceSigned(v: string | number): { head: string; tail: string } {
  const d = fromMoney(v)
  const { head, tail } = splitExact(d, 2)
  return { head: d.u > 0n ? `+${head}` : head, tail }
}

/** 1 dp, and the sign is always shown for a drift or a delta: an unsigned
 *  drift is ambiguous about direction and direction is the whole finding. */
export function pct(n: number | null, opts?: { sign?: boolean }): string {
  if (n === null || !Number.isFinite(n)) return '—'
  const shown = Math.abs(n).toFixed(1)
  const sign = n < 0 ? '−' : opts?.sign ? '+' : ''
  return `${sign}${shown}%`
}

export function pctRange(lo: number | null, hi: number | null): string {
  if (lo === null || hi === null) return '—'
  if (lo === hi) return pct(lo)
  return `${pct(lo)}–${pct(hi)}`
}

const LONDON = 'Europe/London'

/** "19 Sep" — a count date, which is always within living memory. */
export function dayShort(iso: string | null): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    timeZone: LONDON,
  }).format(new Date(iso))
}

/** "19 Sep 2026" — used where the year could be in question (expiry dates run
 *  to 2028 on ambient stock). */
export function dayFull(iso: string | null): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: LONDON,
  }).format(new Date(iso))
}

export function stamp(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const day = new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: LONDON,
  }).format(d)
  const time = new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: LONDON,
  }).format(d)
  return `${day}, ${time}`
}

/** A run-out `days` figure arrives as an exact decimal string ("5.72"). */
export function daysFrom(text: string | null): string {
  if (text === null) return '—'
  const d = fromMoney(text)
  return toFixed(d, 1)
}

export function plural(n: number, one: string, many?: string): string {
  return n === 1 ? one : (many ?? `${one}s`)
}

/**
 * Invariant 8 at the formatting edge: a missing cost has no figure at all.
 * Callers that want to render a Cost must handle all three states, because
 * this returns a discriminated result rather than a string.
 */
export type CostView =
  | { kind: 'missing'; note: string | null }
  | { kind: 'figure'; head: string; tail: string; isEstimate: boolean; source: string | null }

export function costView(c: Cost | null | undefined): CostView {
  if (!c || c.is_missing || c.pence === null) {
    return { kind: 'missing', note: c?.note ?? null }
  }
  const { head, tail } = pence(c.pence)
  return { kind: 'figure', head, tail, isEstimate: c.is_estimate, source: c.source }
}

/**
 * Money as the v2 design writes it: always pounds, two decimals, U+2212 minus
 * before the symbol (`£12.00`, `−£4.20`). Input is integer pence (number or
 * the API's exact string). An alias of `poundsOnly`, named for the design's
 * `gbp()` so screen builders find it.
 */
export function gbp(pence: string | number): string {
  return poundsOnly(pence)
}

/**
 * Relative time as the design's `ago()` writes it: "just now", "12 min ago",
 * "3 hours ago", "2 days ago". Past only; a future or unparseable stamp falls
 * back to "just now" / "—". `now` is injectable so a caller can tick it.
 */
export function ago(iso: string | null, now: number = Date.now()): string {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 48) return `${h} ${plural(h, 'hour')} ago`
  const d = Math.round(h / 24)
  return `${d} ${plural(d, 'day')} ago`
}
