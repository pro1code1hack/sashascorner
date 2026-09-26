/**
 * Figures for the Recipes, Menu items and Ingredients screens.
 *
 * Every money and quantity value here is a `Dec` (lib/dec): exact BigInt
 * arithmetic, never a float. A percentage is computed from Decs with integer
 * division and only then turned into a display number. `0.1 + 0.2` in a recipe
 * editor is unacceptable (CLAUDE.md §10.10).
 */
import { abs, cmp, fromInt, fromMoney, parseDec, rescale, shiftDown, sub, toFixed, trimQty } from '../../../lib/dec'
import type { Dec } from '../../../lib/dec'
import { gbp } from '../../../lib/format'
import type { Cost, SizeCode, Unit } from '../../../lib/types/menu'

export { gbp }

export const SIZE_ORDER: SizeCode[] = ['S', 'M', 'XL', 'ONE']
export const sizeLabel = (s: SizeCode | null | undefined): string => (s === 'ONE' || !s ? 'One' : s)

/** Design units: L, ml, kg, g, unit. */
export function unitWord(u: Unit | null | undefined): string {
  switch (u) {
    case 'L':
      return 'L'
    case 'ML':
      return 'ml'
    case 'KG':
      return 'kg'
    case 'G':
      return 'g'
    case 'EACH':
      return 'unit'
    default:
      return ''
  }
}

/** Units a quantity of this ingredient may be typed in: same dimension only. */
export function compatibleUnits(u: Unit): Unit[] {
  if (u === 'L' || u === 'ML') return ['L', 'ML']
  if (u === 'KG' || u === 'G') return ['KG', 'G']
  return ['EACH']
}

/** Money as the design writes it, but never for a missing cost. */
export function costText(c: Cost | null | undefined): string {
  if (!c || c.pence === null || c.is_missing) return '—'
  return gbp(c.pence)
}

/** Pence per unit the design's way: £1.23 / £0.734 / £0.0450 (2/3/4 dp). */
export function unitPrice(pence: string | number): string {
  const d = fromMoney(pence)
  const pounds = shiftDown(abs(d), 2)
  const dp = cmp(pounds, fromInt(1)) >= 0 ? 2 : cmp(pounds, { u: 1n, s: 1 }) >= 0 ? 3 : 4
  return `${d.u < 0n ? '−' : ''}£${toFixed(pounds, dp)}`
}

/** A Dec as the plain decimal string `gbp()` and `fromMoney()` accept ("-12.5"). */
export function decStr(d: Dec): string {
  return `${d.u < 0n ? '-' : ''}${toFixed(abs(d), Math.max(d.s, 0))}`
}

/** Signed money for a delta: "+£0.02", "−£0.10", zero as "£0.00". */
export function gbpSigned(pence: string | number): string {
  const d = fromMoney(pence)
  const body = gbp(toFixed(abs(d), 6).replace('−', ''))
  if (d.u === 0n) return body
  return d.u > 0n ? `+${body}` : `−${body}`
}

/** Three decimals of pounds for a per-item cost delta: "+£0.014". */
export function gbpDelta3(pence: string): string {
  const d = fromMoney(pence)
  const pounds = toFixed(shiftDown(abs(d), 2), 3)
  if (d.u === 0n) return '£0.000'
  return `${d.u > 0n ? '+' : '−'}£${pounds}`
}

/**
 * (num / den) × 100 as a display number, from exact decimals. Integer division
 * at 4 extra places, so the float is made only from the finished result.
 */
export function ratioPct(num: Dec, den: Dec): number | null {
  if (den.u === 0n) return null
  const s = Math.max(num.s, den.s)
  const n = rescale(num, s).u
  const d = rescale(den, s).u
  const scaled = (n * 1_000_000n) / d // percent × 10^4
  return Number(scaled) / 10_000
}

/** Margin % = (price − cost) / price. Null when either is unknown or price is 0. */
export function marginPct(pricePence: number | null, cost: Cost | null | undefined): number | null {
  if (pricePence === null || pricePence <= 0 || !cost || cost.pence === null) return null
  const p = fromInt(pricePence)
  return ratioPct(sub(p, fromMoney(cost.pence)), p)
}

/** Staff time in pence: prep seconds × hourly rate / 3600, exact to 6 dp. */
export function labourPence(prepSeconds: number | null, ratePence: number | null): Dec | null {
  if (!prepSeconds || prepSeconds <= 0 || !ratePence || ratePence <= 0) return null
  const scaled = (BigInt(prepSeconds) * BigInt(ratePence) * 1_000_000n) / 3600n
  return { u: scaled, s: 6 }
}

/** (price − cost) per minute of prep, in pence. */
export function perMinute(pricePence: number, cost: Cost | null | undefined, prepSeconds: number | null): Dec | null {
  if (!prepSeconds || prepSeconds <= 0 || !cost || cost.pence === null) return null
  const contribution = sub(fromInt(pricePence), fromMoney(cost.pence))
  const scaledNum = rescale(contribution, 6).u * 60n
  return { u: scaledNum / BigInt(prepSeconds), s: 6 }
}

export function pctText(n: number | null, dp = 0): string {
  if (n === null || !Number.isFinite(n)) return '—'
  const v = dp === 0 ? Math.round(n).toString() : n.toFixed(dp)
  return `${v.replace('-', '−')}%`
}

/** A quantity string as typed, trimmed of trailing zeros, for display. */
export const qtyText = (t: string | null | undefined): string => (t ? trimQty(t) : '')

/** Accepts what a person types while editing a quantity: digits and one dot. */
export const QTY_INPUT = /^\d*\.?\d*$/
/** Accepts what a person types while editing pounds: up to 2 decimals. */
export const MONEY_INPUT = /^\d*(\.\d{0,2})?$/

/** A typed quantity → the wire string, or null when blank/invalid. */
export function qtyOut(text: string): string | null {
  const t = text.trim()
  if (t === '' || t === '.') return null
  const d = parseDec(t.startsWith('.') ? `0${t}` : t.endsWith('.') ? t.slice(0, -1) : t)
  if (d === null || d.u < 0n) return null
  return toFixed(d, d.s)
}

/** Two quantity strings are the same number ("0.30" = "0.3"). */
export function sameQty(a: string | null | undefined, b: string | null | undefined): boolean {
  const x = a ? parseDec(a) : null
  const y = b ? parseDec(b) : null
  if (x === null || y === null) return (x === null) === (y === null)
  return cmp(x, y) === 0
}

/** Pounds typed → integer pence, exact. Null when blank or not money. */
export function poundsToPence(text: string): number | null {
  const t = text.trim().replace(/^£/, '')
  if (t === '') return null
  const d = parseDec(t.endsWith('.') ? t.slice(0, -1) : t.startsWith('.') ? `0${t}` : t)
  if (d === null || d.u < 0n || d.s > 2) return null
  return Number(rescale(d, 2).u)
}

/** Integer pence → the pounds string an input is filled with: 390 → "3.90". */
export function penceToPounds(pence: number | null | undefined): string {
  if (pence === null || pence === undefined) return ''
  return toFixed(shiftDown(fromInt(pence), 2), 2)
}

export function dayLabel(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: 'Europe/London',
  }).format(new Date(iso))
}
