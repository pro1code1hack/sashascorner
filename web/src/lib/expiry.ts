/**
 * The one aggregate this frontend derives: what expiry has cost since each
 * ingredient's last count.
 *
 * It is exact fixed-point arithmetic (expired quantity × unit cost, both exact
 * decimal strings), never float — invariant 11.
 *
 * Invariant 8: a row whose unit cost is MISSING is excluded from the total, and
 * the caller is handed the exclusion count so the exclusion can be printed next
 * to the figure rather than quietly swallowed. A missing cost is null, never 0,
 * so treating it as zero here would understate the loss and read as reassurance.
 */
import { ZERO, add, isZero, mul, mustDec, type Dec } from './dec'
import type { StockRow } from './types'

export interface ExpiryLoss {
  total: Dec
  /** Ingredients that contributed a figure. */
  costed: number
  /** Ingredients with expired stock but no price, so no value could be put on
   *  it. These are excluded from `total`. */
  uncosted: string[]
  /** Ingredients with expired stock at all. */
  affected: number
}

export function expiryLossSinceCount(rows: readonly StockRow[]): ExpiryLoss {
  let total = ZERO
  let costed = 0
  let affected = 0
  const uncosted: string[] = []
  for (const r of rows) {
    const a = r.drift.attribution
    if (a === null) continue
    const expired = mustDec(a.expired_qty)
    if (isZero(expired)) continue
    affected += 1
    const c = r.unit_cost
    if (!c || c.is_missing || c.pence === null) {
      uncosted.push(r.name)
      continue
    }
    total = add(total, mul(expired, mustDec(c.pence)))
    costed += 1
  }
  return { total, costed, uncosted, affected }
}
