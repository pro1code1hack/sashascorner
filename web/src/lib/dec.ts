/**
 * Exact fixed-point decimals on BigInt.
 *
 * Invariant 11: all money is integer pence, or an exact decimal STRING of pence
 * where the figure is derived and fractional ("77.709059"). A float touching
 * money is a bug. Quantities arrive as strings and stay strings (spec §10.10);
 * `0.1 + 0.2` in a recipe editor is unacceptable.
 *
 * So: nothing in this file uses `number` for a value. A Dec is an integer
 * significand `u` and a decimal scale `s`, meaning u / 10^s. Every operation is
 * integer arithmetic on BigInt and therefore exact. There is no path from a
 * JSON string to a float anywhere in the app's money or quantity handling.
 */

export interface Dec {
  readonly u: bigint
  readonly s: number
}

const DECIMAL = /^[+-]?(\d+)(\.(\d+))?$/

/** Parse an exact decimal string. Returns null for anything else — including
 *  the empty string, `NaN`, exponent notation and a bare sign. */
export function parseDec(text: string): Dec | null {
  const t = text.trim()
  const m = DECIMAL.exec(t)
  if (!m) return null
  const neg = t.startsWith('-')
  const whole = m[1] ?? '0'
  const frac = m[3] ?? ''
  const u = BigInt(whole + frac)
  return { u: neg ? -u : u, s: frac.length }
}

/** Parse an exact decimal that the API contract guarantees. Throws on a
 *  malformed payload rather than silently rendering a wrong figure. */
export function mustDec(text: string): Dec {
  const d = parseDec(text)
  if (d === null) throw new Error(`not an exact decimal: ${JSON.stringify(text)}`)
  return d
}

/** Integer pence straight from the database (a JSON integer, not a float). */
export function fromInt(n: number): Dec {
  if (!Number.isInteger(n)) throw new Error(`integer pence expected, got ${n}`)
  return { u: BigInt(n), s: 0 }
}

/** Money as it arrives: an integer or an exact decimal string of pence. */
export function fromMoney(v: string | number): Dec {
  return typeof v === 'number' ? fromInt(v) : mustDec(v)
}

export const ZERO: Dec = { u: 0n, s: 0 }

function pow10(n: number): bigint {
  let r = 1n
  for (let i = 0; i < n; i++) r *= 10n
  return r
}

/** Raise the scale without changing the value. Never lowers it — that would
 *  round, and rounding happens only at the formatting edge. */
export function rescale(d: Dec, s: number): Dec {
  if (s <= d.s) return d
  return { u: d.u * pow10(s - d.s), s }
}

function align(a: Dec, b: Dec): [bigint, bigint, number] {
  const s = Math.max(a.s, b.s)
  return [rescale(a, s).u, rescale(b, s).u, s]
}

export function add(a: Dec, b: Dec): Dec {
  const [x, y, s] = align(a, b)
  return { u: x + y, s }
}

export function sub(a: Dec, b: Dec): Dec {
  const [x, y, s] = align(a, b)
  return { u: x - y, s }
}

export function mul(a: Dec, b: Dec): Dec {
  return { u: a.u * b.u, s: a.s + b.s }
}

export function neg(a: Dec): Dec {
  return { u: -a.u, s: a.s }
}

export function abs(a: Dec): Dec {
  return a.u < 0n ? neg(a) : a
}

/** Exact division by a power of ten — pence to pounds, and nothing else. */
export function shiftDown(a: Dec, places: number): Dec {
  return { u: a.u, s: a.s + places }
}

export function cmp(a: Dec, b: Dec): -1 | 0 | 1 {
  const [x, y] = align(a, b)
  return x < y ? -1 : x > y ? 1 : 0
}

export function isZero(a: Dec): boolean {
  return a.u === 0n
}

export function isNeg(a: Dec): boolean {
  return a.u < 0n
}

export function sum(items: readonly Dec[]): Dec {
  return items.reduce(add, ZERO)
}

function digits(u: bigint): string {
  return (u < 0n ? -u : u).toString()
}

/**
 * Round half-away-from-zero to `dp` decimal places and render.
 * Used wherever a figure is shown rounded; the exact-tail renderer below is
 * used where the rounding itself would be a lie (DESIGN.md §1 R3).
 */
export function toFixed(d: Dec, dp: number): string {
  const neg_ = d.u < 0n
  let u = neg_ ? -d.u : d.u
  if (dp < d.s) {
    const drop = pow10(d.s - dp)
    const q = u / drop
    const r = u % drop
    u = r * 2n >= drop ? q + 1n : q
  } else if (dp > d.s) {
    u = u * pow10(dp - d.s)
  }
  let str = u.toString()
  if (dp > 0) {
    str = str.padStart(dp + 1, '0')
    str = `${str.slice(0, -dp)}.${str.slice(-dp)}`
  }
  return (neg_ && u !== 0n ? '−' : '') + str
}

/**
 * Split a figure into the digits shown at full contrast and the exact
 * remainder. TRUNCATED, not rounded: head + tail must reconstitute the value
 * exactly, otherwise printing the tail would be a second lie on top of the
 * first. `77.709059` at 2dp → head `77.70`, tail `9059`.
 */
export function splitExact(d: Dec, dp: number): { head: string; tail: string } {
  const neg_ = d.u < 0n
  const raw = digits(d.u)
  const intPart = d.s === 0 ? raw : raw.padStart(d.s + 1, '0').slice(0, -d.s)
  const fracPart = d.s === 0 ? '' : raw.padStart(d.s + 1, '0').slice(-d.s)
  const shown = fracPart.slice(0, dp).padEnd(dp, '0')
  const tail = fracPart.slice(dp).replace(/0+$/, '')
  const head = (neg_ ? '−' : '') + (dp > 0 ? `${intPart}.${shown}` : intPart)
  return { head, tail }
}

/** A quantity as sent, with trailing decimal zeros trimmed. The string from
 *  the API is authoritative; we never re-derive it. */
export function trimQty(text: string): string {
  const d = parseDec(text)
  if (d === null) return text
  if (d.s === 0) return text.replace(/^\+/, '').replace('-', '−')
  const neg_ = d.u < 0n
  const raw = digits(d.u).padStart(d.s + 1, '0')
  let frac = raw.slice(-d.s).replace(/0+$/, '')
  const whole = raw.slice(0, -d.s)
  frac = frac.length > 0 ? `.${frac}` : ''
  return (neg_ && d.u !== 0n ? '−' : '') + whole + frac
}

/**
 * One part's share of a whole, as a percentage string to one decimal.
 *
 * Integer arithmetic only: `part * 1000 / whole`, rounded half-up, then shifted.
 * Money must never reach a float to be turned into a percentage -- and `0.1 + 0.2`
 * is the reason this module exists.
 *
 * Returns null when the whole is zero or either side is missing: a share of
 * nothing is not 0%, it is undefined, and rendering 0% would read as "this
 * contributed nothing".
 *
 * Shares round to a tenth and so need not sum to exactly 100; say so wherever a
 * column of them is shown.
 */
export function sharePct(part: number | null, whole: number | null): string | null {
  if (part === null || whole === null || whole === 0) return null
  const p = BigInt(Math.trunc(part))
  const w = BigInt(Math.trunc(whole))
  const sign = (p < 0n) !== (w < 0n) ? '-' : ''
  const a = p < 0n ? -p : p
  const b = w < 0n ? -w : w
  // x10 for the decimal place, x100 for percent, +half for half-up rounding.
  const scaled = (a * 1000n + b / 2n) / b
  return `${sign}${scaled / 10n}.${scaled % 10n}%`
}
