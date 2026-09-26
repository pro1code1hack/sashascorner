/**
 * Stock view-model: the design's sort, filters and cell words, over server
 * figures only (stock-orders-suppliers.md §1.4: none of the design's toy
 * arithmetic is ported; the only client-side logic is formatting, sorting,
 * filtering and the live count preview).
 */
import { cmp, fromInt, mustDec, parseDec } from '../../lib/dec'
import type { Dec } from '../../lib/dec'
import type { StockRow, TrustLabel } from '../../lib/types/stock'
import { dayMonth, fmtD, humanQty } from './fmt'

export type TierFilter = 'all' | 'A' | 'B' | 'C'
export type StockFilter = 'all' | 'count' | 'out' | 'soon' | 'drift' | 'check' | 'low'

export const SOON_DAYS = 3

/** The server's word, with the fallback for a fixture that predates it. */
export function trustOf(row: StockRow): TrustLabel {
  if (row.trust_label) return row.trust_label
  if (!row.on_hand.has_count_basis) return 'never_counted'
  if (!row.drift.has_observation) return 'not_yet_judged'
  return row.drift.trust_status ?? 'not_yet_judged'
}

const TRUST_ORDER: Record<TrustLabel, number> = {
  excluded: 0,
  never_counted: 1,
  not_yet_judged: 2,
  drifting: 3,
  trusted: 4,
}

export function trustRank(row: StockRow): number {
  return TRUST_ORDER[trustOf(row)]
}

/** Days of cover as an exact decimal, or null (withheld / not moving). */
export function coverDays(row: StockRow): Dec | null {
  const days = row.run_out?.days
  if (days === null || days === undefined) return null
  return parseDec(days)
}

/** Has a real sales rate: the run-out forecast was not withheld and moves. */
export function hasRate(row: StockRow): boolean {
  const rate = row.run_out?.daily_rate_qty
  if (rate === null || rate === undefined) return false
  const d = parseDec(rate)
  return d !== null && d.u > 0n
}

/** Design L1030: C last, then A→B, then trust (least trusted first), then cover asc, null last. */
export function compareRows(a: StockRow, b: StockRow): number {
  const ac = a.tier === 'C' ? 1 : 0
  const bc = b.tier === 'C' ? 1 : 0
  if (ac !== bc) return ac - bc
  if (a.tier !== b.tier) return a.tier < b.tier ? -1 : 1
  const t = trustRank(a) - trustRank(b)
  if (t !== 0) return t
  const ca = coverDays(a)
  const cb = coverDays(b)
  if (ca === null && cb !== null) return 1
  if (ca !== null && cb === null) return -1
  if (ca !== null && cb !== null) {
    const c = cmp(ca, cb)
    if (c !== 0) return c
  }
  return a.name.localeCompare(b.name)
}

const SEVEN: Dec = fromInt(7)

export function matches(row: StockRow, f: StockFilter): boolean {
  switch (f) {
    case 'all':
      return true
    case 'count':
      return (
        row.tier !== 'C' && (hasRate(row) || row.on_hand.has_count_basis) && trustOf(row) !== 'trusted'
      )
    case 'out': {
      if (row.tier === 'C') return false
      if (row.run_out?.is_out_of_stock && row.on_hand.has_count_basis) return true
      const c = coverDays(row)
      return c !== null && cmp(c, SEVEN) <= 0
    }
    case 'soon':
      return soonestLiveBatch(row) !== null && (soonestLiveBatch(row)?.days_left ?? 99) <= SOON_DAYS
    case 'drift': {
      if (row.tier === 'C') return false
      const t = trustOf(row)
      return t === 'drifting' || t === 'excluded'
    }
    case 'check':
      return row.tier === 'C'
    case 'low':
      return row.tier === 'C' && row.checklist?.status === 'LOW'
  }
}

export function soonestLiveBatch(row: StockRow) {
  let best: StockRow['batches'][number] | null = null
  for (const b of row.batches) {
    if (b.days_left === null) continue
    if (mustDec(b.qty_remaining).u <= 0n) continue
    if (best === null || (best.days_left ?? 1e9) > b.days_left) best = b
  }
  return best
}

/* ----------------------------------------------------------- cell words --- */

export function leftCell(row: StockRow): string {
  if (row.tier === 'C') return ''
  // C9: without a count, the figure is a ledger sum; never dress it as "~qty".
  if (!row.on_hand.has_count_basis) return '—'
  return `~${humanQty(row.on_hand.qty, row.unit)}`
}

export function lastCountCell(row: StockRow): string {
  if (row.tier === 'C') {
    if (row.checklist?.status === 'LOW') return 'marked low'
    if (row.checklist?.status === 'OK') return 'marked OK'
    return 'checklist'
  }
  if (!row.on_hand.has_count_basis || row.on_hand.basis_count_qty === null) return 'never'
  return `${humanQty(row.on_hand.basis_count_qty, row.unit)} · ${dayMonth(row.on_hand.basis_counted_at)}`
}

export type RunsOut = { text: string; title?: string; alert: boolean; reason: boolean }

export function runsOutCell(row: StockRow): RunsOut {
  if (row.tier === 'C') return { text: '', alert: false, reason: false }
  const ro = row.run_out
  if (!row.on_hand.has_count_basis) {
    return { text: 'count it first', alert: false, reason: true, title: 'No count behind the figure, so no run-out date.' }
  }
  if (!ro || !ro.forecast) return { text: '', alert: false, reason: false }
  if (ro.forecast.is_low_confidence) {
    // Invariant 9: the reason goes where the date would be.
    const why = ro.forecast.reasons[0] ?? 'not enough history to say'
    return { text: why, title: ro.forecast.reasons.join(' '), alert: false, reason: true }
  }
  if (ro.days === null) return { text: 'not enough sales', alert: false, reason: true }
  const d = parseDec(ro.days)
  if (d === null) return { text: '', alert: false, reason: false }
  const whole = Number((d.u / 10n ** BigInt(d.s)).toString())
  if (whole < 1) return { text: 'today', alert: true, reason: false }
  return { text: `${fmtD(ro.on)} · ${whole}d`, alert: whole <= SOON_DAYS, reason: false }
}

export function useByCell(row: StockRow): { text: string; alert: boolean } {
  const b = soonestLiveBatch(row)
  if (!b || !b.effective_expiry) return { text: '', alert: false }
  return { text: dayMonth(b.effective_expiry), alert: (b.days_left ?? 99) <= SOON_DAYS }
}

export const TIER_NOTE: Record<string, string> = {
  A: 'worked out from sales, can earn auto-ordering',
  B: 'worked out, always reviewed',
  C: 'yes/no checklist',
}

/* ------------------------------------------------------------------ sort --- */

export type StockSort = 'attention' | 'az' | 'za' | 'runout' | 'drift' | 'useby' | 'life' | 'trust'

export const STOCK_SORTS: ReadonlyArray<{ value: StockSort; label: string }> = [
  { value: 'attention', label: 'Sort: needs attention first' },
  { value: 'az', label: 'Sort: A–Z' },
  { value: 'za', label: 'Sort: Z–A' },
  { value: 'runout', label: 'Sort: runs out soonest' },
  { value: 'drift', label: 'Sort: biggest drift' },
  { value: 'useby', label: 'Sort: use by soonest' },
  { value: 'life', label: 'Sort: shortest shelf life' },
  { value: 'trust', label: 'Sort: least trusted' },
]

const byName = (a: StockRow, b: StockRow) => a.name.localeCompare(b.name)

/** Ascending on a nullable key; a missing value sorts last either way. */
function nullsLast<T>(key: (r: StockRow) => T | null, order: (x: T, y: T) => number) {
  return (a: StockRow, b: StockRow): number => {
    const ka = key(a)
    const kb = key(b)
    if (ka === null) return kb === null ? byName(a, b) : 1
    if (kb === null) return -1
    return order(ka, kb) || byName(a, b)
  }
}

const num = (x: number, y: number) => x - y

export function stockSorter(key: StockSort): (a: StockRow, b: StockRow) => number {
  switch (key) {
    case 'attention':
      return compareRows
    case 'az':
      return byName
    case 'za':
      return (a, b) => byName(b, a)
    case 'runout':
      return nullsLast(coverDays, cmp)
    case 'drift':
      // Largest absolute drift first.
      return nullsLast((r) => (r.drift.drift_pct === null ? null : -Math.abs(r.drift.drift_pct)), num)
    case 'useby':
      return nullsLast((r) => soonestLiveBatch(r)?.days_left ?? null, num)
    case 'life':
      return nullsLast((r) => r.shelf_life.usable_days ?? r.shelf_life.shelf_life_days, num)
    case 'trust':
      return (a, b) => trustRank(a) - trustRank(b) || byName(a, b)
  }
}

export const TRUST_WORD: Record<TrustLabel, string> = {
  trusted: 'Trusted',
  drifting: 'Drifting',
  excluded: 'Excluded',
  not_yet_judged: 'Not yet judged',
  never_counted: 'Never counted',
}
