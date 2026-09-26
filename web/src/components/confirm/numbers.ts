/**
 * Parsing at the edge, for the two confirmation forms.
 *
 * The UI asks for pounds because that is what an invoice and an account manager
 * say; the wire carries integer pence (invariant 11). `parseFloat(x) * 100` is
 * exactly the bug the rest of this codebase is built to avoid — `45.10` is not
 * representable and `4509.999999999999` truncates to 4509 — so the conversion
 * goes through `lib/dec.ts` and is an exact integer rescale: a Dec with scale 2
 * has significand `value * 100` already, so pence is that significand and no
 * division or multiplication of a float happens anywhere.
 *
 * Day counts and times are not money. They are still never parsed with
 * `parseInt`, which accepts "7 days" and "7.9" and silently keeps the 7.
 */
import { fromInt, parseDec, rescale, shiftDown, toFixed } from '../../lib/dec'

/** Blank is a state, not an error: several of these fields mean "none" empty. */
export type Parsed<T> =
  | { kind: 'value'; value: T }
  | { kind: 'blank' }
  | { kind: 'bad'; message: string }

/** £1,000,000. Anything larger is a typo in a café's supplier terms. */
const MAX_PENCE = 100_000_000n

/**
 * Pounds as typed → integer pence. Rejects a third decimal place rather than
 * rounding it away: somebody who typed £4.555 meant something, and guessing
 * which of 455 or 456 they meant is not ours to do.
 */
export function poundsToPence(text: string): Parsed<number> {
  const t = text.trim().replace(/^£/, '').trim()
  if (t === '') return { kind: 'blank' }
  const d = parseDec(t)
  if (d === null) {
    return {
      kind: 'bad',
      message: 'Pounds and pence, written plainly: 45 or 45.50. No commas, no currency symbol.',
    }
  }
  if (d.u < 0n) return { kind: 'bad', message: 'This cannot be negative.' }
  if (d.s > 2) {
    return {
      kind: 'bad',
      message: 'Money stops at two decimal places — the figure is stored as whole pence.',
    }
  }
  // Scale 2 means the significand IS the value in pence. Exact, by construction.
  const pence = rescale(d, 2).u
  if (pence > MAX_PENCE) {
    return { kind: 'bad', message: 'That is over £1,000,000 — check the figure.' }
  }
  return { kind: 'value', value: Number(pence) }
}

/** Integer pence → the pounds string an input is pre-filled with. */
export function penceToPounds(pence: number | null): string {
  if (pence === null) return ''
  return toFixed(shiftDown(fromInt(pence), 2), 2)
}

/** A whole number of days. No decimals, no units, no leading plus. */
export function parseDays(text: string, min: number, max: number): Parsed<number> {
  const t = text.trim()
  if (t === '') return { kind: 'blank' }
  if (!/^\d+$/.test(t)) {
    return { kind: 'bad', message: 'A whole number of days, digits only.' }
  }
  const n = Number(t)
  if (n < min || n > max) {
    return { kind: 'bad', message: `Between ${min} and ${max} days.` }
  }
  return { kind: 'value', value: n }
}

/** 24-hour HH:MM, which is what the API stores. Blank means no cutoff at all. */
export function parseCutoff(text: string): Parsed<string> {
  const t = text.trim()
  if (t === '') return { kind: 'blank' }
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(t)) {
    return {
      kind: 'bad',
      message: 'Twenty-four hour time as HH:MM — 16:00, not 4pm.',
    }
  }
  return { kind: 'value', value: t }
}

/** Mon…Sun, ISO 1..7, for the day toggles. Index 0 is unused. */
export const ISO_DAYS: readonly { iso: number; short: string; long: string }[] = [
  { iso: 1, short: 'Mon', long: 'Monday' },
  { iso: 2, short: 'Tue', long: 'Tuesday' },
  { iso: 3, short: 'Wed', long: 'Wednesday' },
  { iso: 4, short: 'Thu', long: 'Thursday' },
  { iso: 5, short: 'Fri', long: 'Friday' },
  { iso: 6, short: 'Sat', long: 'Saturday' },
  { iso: 7, short: 'Sun', long: 'Sunday' },
]
