/**
 * The design's formatters (stock-orders-suppliers.md §0.3), reproduced with
 * exact decimals. Shared by the Stock, Orders and Suppliers screens.
 *
 * Nothing here turns a quantity or money string into a float: every figure
 * goes through lib/dec (BigInt fixed point).
 */
import { abs, cmp, fromInt, isNeg, mustDec, parseDec, rescale, shiftDown, sub, toFixed } from '../../lib/dec'
import type { Dec } from '../../lib/dec'
import type { Unit } from '../../lib/types/stock'

const LONDON = 'Europe/London'
const TEN: Dec = fromInt(10)

function groupInt(text: string): string {
  const neg = text.startsWith('−') || text.startsWith('-')
  const body = neg ? text.slice(1) : text
  const dot = body.indexOf('.')
  const whole = dot === -1 ? body : body.slice(0, dot)
  const rest = dot === -1 ? '' : body.slice(dot)
  return (neg ? '−' : '') + whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + rest
}

/** The design's unit suffix: ' L', none for EACH, else ' ml' / ' g' / ' kg'. */
export function unitSuffix(unit: Unit | string): string {
  switch (unit) {
    case 'L':
      return ' L'
    case 'EACH':
      return ''
    case 'ML':
      return ' ml'
    case 'G':
      return ' g'
    case 'KG':
      return ' kg'
    default:
      return ` ${String(unit).toLowerCase()}`
  }
}

/** Short unit word for inputs: "L", "kg", "ml", "g", "each". */
export function unitWord(unit: Unit | string): string {
  return unit === 'EACH' ? 'each' : unit === 'L' ? 'L' : String(unit).toLowerCase()
}

/**
 * `fmtQ(n, u)`: `—` for nothing; whole number with grouping for EACH, for
 * ml/g at |n| ≥ 10, and for integers at |n| ≥ 10; otherwise 1 dp at |n| ≥ 10,
 * else 2 dp. Then the unit suffix.
 */
export function fmtQ(text: string | null | undefined, unit: Unit | string, opts?: { suffix?: boolean }): string {
  if (text === null || text === undefined) return '—'
  const d = parseDec(text)
  if (d === null) return text
  const a = abs(d)
  const big = cmp(a, TEN) >= 0
  const isInt = d.s === 0 || d.u % 10n ** BigInt(d.s) === 0n
  const whole = unit === 'EACH' || ((unit === 'ML' || unit === 'G') && big) || (isInt && big)
  const shown = whole ? groupInt(toFixed(d, 0)) : big ? toFixed(d, 1) : toFixed(d, 2)
  return shown + (opts?.suffix === false ? '' : unitSuffix(unit))
}

function asDate(iso: string): Date {
  // A bare YYYY-MM-DD is a London calendar day; noon UTC keeps it on that day.
  return /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T12:00:00Z`) : new Date(iso)
}

/** `fmtD`: en-GB "Fri 26 Sept". */
export function fmtD(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: LONDON,
  })
    .format(asDate(iso))
    .replace(',', '')
}

/** `fmtD` without the weekday: "26 Sept". */
export function dayMonth(iso: string | null | undefined): string {
  if (!iso) return '—'
  return fmtD(iso).replace(/^\S+ /, '')
}

/** Today in London as YYYY-MM-DD. */
export function todayIso(): string {
  return londonDate(new Date())
}

export function londonDate(d: Date): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    timeZone: LONDON,
  }).format(d)
  return parts
}

/** YYYY-MM-DD plus n days (calendar arithmetic on the date, not the clock). */
export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

/** Whole days from today (London) to an ISO date or instant. */
export function daysUntil(iso: string): number {
  const target = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso : londonDate(new Date(iso))
  const a = Date.parse(`${todayIso()}T12:00:00Z`)
  const b = Date.parse(`${target}T12:00:00Z`)
  return Math.round((b - a) / 86_400_000)
}

/**
 * `unitPrice(p)`: pounds at 2 dp from £1, 3 dp from £0.10, else 4 dp.
 * Input is exact pence (integer or decimal string).
 */
export function unitPrice(pence: string | number): string {
  const d = typeof pence === 'number' ? fromInt(pence) : mustDec(pence)
  const pounds = shiftDown(d, 2)
  const a = abs(pounds)
  const dp = cmp(a, fromInt(1)) >= 0 ? 2 : cmp(a, { u: 10n, s: 2 }) >= 0 ? 3 : 4
  const sign = isNeg(pounds) ? '−' : ''
  return `${sign}£${toFixed(a, dp)}`
}

/** Drift for display: the design's sign, (counted − expected)/counted, 1 dp. */
export function driftShown(driftPct: number | null | undefined): string {
  if (driftPct === null || driftPct === undefined) return ''
  const v = -driftPct
  const r = Math.round(v * 10) / 10
  if (r === 0) return '0%'
  return `${r > 0 ? '+' : '−'}${Math.abs(r).toFixed(Math.abs(r) % 1 === 0 ? 0 : 1)}%`
}

/**
 * `|a − b| / max(b, 0.001) × 100`, to one decimal, exactly (the count
 * preview). Integer arithmetic on the scaled significands.
 */
export function pctOut(expected: Dec, seen: Dec): string {
  const floor: Dec = { u: 1n, s: 3 }
  const denom = cmp(seen, floor) < 0 ? floor : seen
  const diff = abs(sub(expected, seen))
  const s = Math.max(diff.s, denom.s)
  const n = rescale(diff, s).u
  const m = rescale(denom, s).u
  if (m === 0n) return '0.0'
  // ×1000 for one decimal of a percentage, + half for rounding.
  const q = (n * 1000n + m / 2n) / m
  return `${q / 10n}.${q % 10n}`
}

/** The percentage as a number, for threshold tests only (never displayed). */
export function pctNumber(text: string): number {
  return Number(text)
}

/** Sum of exact pence strings / integers; null if any is null. */
export function sumPence(values: ReadonlyArray<string | number | null>): Dec | null {
  let total: Dec = fromInt(0)
  for (const v of values) {
    if (v === null) return null
    const d = typeof v === 'number' ? fromInt(v) : mustDec(v)
    const s = Math.max(total.s, d.s)
    total = { u: rescale(total, s).u + rescale(d, s).u, s }
  }
  return total
}

/** ISO weekday numbers (Mon=1) and their design labels. */
export const WEEKDAYS: ReadonlyArray<{ iso: number; label: string }> = [
  { iso: 1, label: 'Mon' },
  { iso: 2, label: 'Tue' },
  { iso: 3, label: 'Wed' },
  { iso: 4, label: 'Thu' },
  { iso: 5, label: 'Fri' },
  { iso: 6, label: 'Sat' },
  { iso: 7, label: 'Sun' },
]

/** A Dec as a plain decimal string ("-12.5"), for feeding back into money formatters. */
export function decStr(d: Dec): string {
  if (d.s === 0) return d.u.toString()
  const neg = d.u < 0n
  const raw = (neg ? -d.u : d.u).toString().padStart(d.s + 1, '0')
  return `${neg ? '-' : ''}${raw.slice(0, -d.s)}.${raw.slice(-d.s)}`
}

/* ------------------------------------------------------ human quantities --- */

function trimZeros(text: string): string {
  return text.includes('.') ? text.replace(/0+$/, '').replace(/\.$/, '') : text
}

/** Round to a sensible precision for a person: 2 dp under 10, 1 dp under 100, else whole. */
function sensible(d: Dec, maxDp = 2): string {
  const a = abs(d)
  const dp = cmp(a, TEN) < 0 ? maxDp : cmp(a, fromInt(100)) < 0 ? Math.min(1, maxDp) : 0
  return groupInt(trimZeros(toFixed(d, dp)))
}

/**
 * A quantity in the unit a person would say: 2460 ml → "2.46 L", 0.35 kg →
 * "350 g", 26.755 each → "27". Exact decimals in, rounded words out; the value
 * itself is never changed, only how it is spoken.
 */
export function humanQty(text: string | null | undefined, unit: Unit | string): string {
  if (text === null || text === undefined) return '—'
  const d = parseDec(text)
  if (d === null) return text
  const a = abs(d)
  const THOUSAND = fromInt(1000)
  switch (unit) {
    case 'ML':
      return cmp(a, THOUSAND) >= 0 ? `${sensible(shiftDown(d, 3))} L` : `${sensible(d, 0)} ml`
    case 'L':
      return a.u !== 0n && cmp(a, fromInt(1)) < 0 ? `${sensible(mulPow10(d, 3), 0)} ml` : `${sensible(d)} L`
    case 'G':
      return cmp(a, THOUSAND) >= 0 ? `${sensible(shiftDown(d, 3))} kg` : `${sensible(d, 0)} g`
    case 'KG':
      return a.u !== 0n && cmp(a, fromInt(1)) < 0 ? `${sensible(mulPow10(d, 3), 0)} g` : `${sensible(d)} kg`
    case 'EACH':
      return sensible(d, cmp(a, TEN) < 0 ? 1 : 0)
    default:
      return `${sensible(d)}${unitSuffix(unit)}`
  }
}

function mulPow10(d: Dec, n: number): Dec {
  return d.s >= n ? { u: d.u, s: d.s - n } : { u: d.u * 10n ** BigInt(n - d.s), s: 0 }
}

/** a / b to `dp` places, half-up, as a Dec. Null when b is zero. */
export function divDec(a: Dec, b: Dec, dp = 1): Dec | null {
  if (b.u === 0n) return null
  const s = Math.max(a.s, b.s)
  const x = rescale(a, s).u * 10n ** BigInt(dp)
  const y = rescale(b, s).u
  const neg = x < 0n !== y < 0n
  const ax = x < 0n ? -x : x
  const ay = y < 0n ? -y : y
  const q = (ax * 2n + ay) / (ay * 2n)
  return { u: neg ? -q : q, s: dp }
}

/** The word for one pack, by what it is. Only ever decoration. */
export function packNoun(category: string | null | undefined, unit: Unit | string, n: Dec): string {
  const one = cmp(abs(n), fromInt(1)) === 0
  const c = (category ?? '').toLowerCase()
  let word = 'pack'
  if (c.includes('syrup') || c.includes('bottled')) word = 'bottle'
  else if ((c.includes('dairy') || c.includes('milk')) && (unit === 'L' || unit === 'ML')) word = 'carton'
  else if (c.includes('coffee') || c.includes('tea')) word = 'bag'
  else if (c.includes('packaging')) word = 'box'
  return one ? word : word === 'box' ? 'boxes' : `${word}s`
}

export interface PackLike {
  size_in_unit: string | null
  pack_size: string
  pack_unit: string
}

/**
 * "≈ 3 bottles" for a quantity against a pack, or null when it would say
 * nothing (no pack, a pack of one each, or a pack of another dimension).
 */
export function packEquiv(
  qty: string | null | undefined,
  pack: PackLike | null | undefined,
  category?: string | null,
): { text: string; title: string } | null {
  if (!pack || pack.size_in_unit === null || qty === null || qty === undefined) return null
  const size = parseDec(pack.size_in_unit)
  const q = parseDec(qty)
  if (size === null || q === null || size.u <= 0n || q.u < 0n) return null
  if (pack.pack_unit === 'EACH' && cmp(size, fromInt(1)) === 0) return null
  const n = divDec(q, size, 1)
  if (n === null) return null
  const shown = trimZeros(toFixed(n, cmp(abs(n), TEN) < 0 ? 1 : 0))
  const packLabel = `${humanQty(pack.pack_size, pack.pack_unit)}${pack.pack_unit === 'EACH' ? ' each' : ''}`
  const one = packNoun(category, pack.pack_unit, fromInt(1))
  const lt1 = cmp(abs(n), fromInt(1)) < 0
  const text =
    n.u <= 0n && q.u > 0n
      ? `under a tenth of a ${one}`
      : lt1
        ? `≈ ${shown} of a ${one}`
        : `≈ ${shown} ${packNoun(category, pack.pack_unit, n)}`
  return {
    text,
    title: `Pack of ${packLabel}`,
  }
}
