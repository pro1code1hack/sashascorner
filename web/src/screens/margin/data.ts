/**
 * Margin screen — the joins and orderings the screen needs, over the shared
 * types in `lib/types.ts`. Nothing here does arithmetic on a float.
 *
 * One thing to know about the payload: the API sends some of these figures as
 * JSON numbers where `MarginRow` types them as strings and vice versa —
 * `margin_pct: 91.577` (typed `string | null`), `units_sold: "0"` (typed
 * `number`), `labour_cost_pence: "42.291667"` (typed `number | null`), the same
 * for `true_margin_pence` and `contribution_pence`. That is reported to the
 * integrator rather than patched over here. It is harmless because every figure
 * goes through `dec()` below, which accepts either spelling and parses the
 * digits as an exact decimal: no value on this screen is ever added, multiplied
 * or rounded as a float.
 */
import { cmp, isNeg, isZero, parseDec, toFixed, type Dec } from '../../lib/dec'
import type { Cost, MarginResponse, MarginRow, RankedRow } from '../../lib/types'

/* ---------------------------------------------------------------- decimals */

/** The one door from the payload to a figure. `String(91.577)` is `"91.577"`,
 *  so a JSON number arrives as its own exact digits and nothing else. */
export function dec(v: string | number | null | undefined): Dec | null {
  if (v === null || v === undefined) return null
  return parseDec(typeof v === 'number' ? String(v) : v)
}

export function isNegative(v: string | number | null | undefined): boolean {
  const d = dec(v)
  return d !== null && isNeg(d)
}

export function isZeroValue(v: string | number | null | undefined): boolean {
  const d = dec(v)
  return d !== null && isZero(d)
}

/** An integer count as sent ("1369"), printed exactly, never parsed to a float. */
export function count(v: string | number | null | undefined): string | null {
  const d = dec(v)
  return d === null ? null : toFixed(d, 0)
}

/* ------------------------------------------------------------------- joins */

/** A row with the two ranks it holds in the two orderings, and the gap. */
export interface Item extends MarginRow {
  margin_rank: number | null
  mpm_rank: number | null
  /** margin_rank − margin_per_minute_rank. Positive: better per minute than by
   *  margin %. The API computes it; it is never re-derived here. */
  rank_delta: number | null
}

export function joinRanks(rows: MarginRow[], ranked: RankedRow[]): Item[] {
  const by = new Map<number, RankedRow>()
  for (const r of ranked) by.set(r.menu_item_id, r)
  return rows.map((row) => {
    const r = by.get(row.menu_item_id)
    return {
      ...row,
      margin_rank: r?.margin_rank ?? null,
      mpm_rank: r?.margin_per_minute_rank ?? null,
      rank_delta: r?.rank_delta ?? null,
    }
  })
}

/* -------------------------------------------------------------- the finding */

export interface Derived {
  /** Items that lose money once labour is subtracted. A classified state. */
  negativeAfterLabour: Item[]
  /** Ranked but unsold in the window: the ranking is per-unit economics. */
  unsoldCount: number
  soldCount: number
  /** How far the two orderings pull apart, in items and in places. */
  betterPerMinute: number
  worsePerMinute: number
  maxMove: number
  /** The payload names the underrated side only; the other is derived from
   *  `ranked`, which carries all 298 deltas. Sorting is not arithmetic. */
  underrated: RankedRow[]
  flattered: RankedRow[]
  prepEstimateCount: number
}

const MOVE = 50

export function derive(full: MarginResponse, items: Item[]): Derived {
  const negativeAfterLabour = items.filter((i) => isNegative(i.true_margin_pct))
  const unsoldCount = items.filter((i) => isZeroValue(i.units_sold)).length
  const deltas = full.ranked
  const flattered = [...deltas].sort((a, b) => a.rank_delta - b.rank_delta).slice(0, 12)
  return {
    negativeAfterLabour,
    unsoldCount,
    soldCount: items.length - unsoldCount,
    betterPerMinute: deltas.filter((r) => r.rank_delta >= MOVE).length,
    worsePerMinute: deltas.filter((r) => r.rank_delta <= -MOVE).length,
    maxMove: deltas.reduce((m, r) => Math.max(m, Math.abs(r.rank_delta)), 0),
    underrated: full.biggest_disagreements,
    flattered,
    prepEstimateCount: items.filter((i) => i.prep_is_estimate).length,
  }
}

/** Every excluded item shares one of a very few reasons; say the reason once. */
export function groupExcluded(pairs: string[][]): { reason: string; labels: string[] }[] {
  const groups = new Map<string, string[]>()
  for (const pair of pairs) {
    const label = pair[0] ?? 'unnamed item'
    const reason = pair[1] ?? 'no reason given'
    const list = groups.get(reason)
    if (list) list.push(label)
    else groups.set(reason, [label])
  }
  return [...groups.entries()].map(([reason, labels]) => ({ reason, labels }))
}

/* ----------------------------------------------------------------- sorting */

export type Ordering = 'margin_pct' | 'margin_per_minute'
export type SortKey =
  | 'name'
  | 'sold'
  | 'price'
  | 'cost'
  | 'prep'
  | 'labour'
  | 'margin'
  | 'after_labour'
  | 'contribution'
  | 'mpm'
  | 'rank_pct'
  | 'rank_mpm'
  | 'delta'
export type Dir = 'asc' | 'desc'

/** Which way round a column reads best on first click. */
export const DEFAULT_DIR: Record<SortKey, Dir> = {
  name: 'asc',
  sold: 'desc',
  price: 'desc',
  cost: 'desc',
  prep: 'desc',
  labour: 'desc',
  margin: 'desc',
  after_labour: 'desc',
  contribution: 'desc',
  mpm: 'desc',
  rank_pct: 'asc',
  rank_mpm: 'asc',
  delta: 'desc',
}

function costPence(c: Cost): string | null {
  return c.is_missing ? null : c.pence
}

/** The comparable value of a column: an exact decimal, or null when unknowable
 *  — and a null sorts to the bottom in either direction, because "unknown" is
 *  not "small". */
function value(it: Item, key: SortKey): Dec | null {
  switch (key) {
    case 'sold':
      return dec(it.units_sold)
    case 'price':
      return dec(it.price_pence)
    case 'cost':
      return dec(costPence(it.cost))
    case 'prep':
      return dec(it.prep_seconds)
    case 'labour':
      return dec(it.labour_cost_pence)
    case 'margin':
      return dec(it.margin_pct)
    case 'after_labour':
      return dec(it.true_margin_pct)
    case 'contribution':
      return dec(it.contribution_pence)
    case 'mpm':
      return dec(it.margin_per_minute_pence)
    case 'rank_pct':
      return dec(it.margin_rank)
    case 'rank_mpm':
      return dec(it.mpm_rank)
    case 'delta':
      return dec(it.rank_delta)
    case 'name':
      return null
  }
}

export function sortItems(items: Item[], key: SortKey, dir: Dir): Item[] {
  if (key === 'name') {
    const sorted = [...items].sort((a, b) => a.label.localeCompare(b.label, 'en'))
    return dir === 'asc' ? sorted : sorted.reverse()
  }
  const known: { it: Item; v: Dec }[] = []
  const unknown: Item[] = []
  for (const it of items) {
    const v = value(it, key)
    if (v === null) unknown.push(it)
    else known.push({ it, v })
  }
  known.sort((a, b) => (dir === 'asc' ? cmp(a.v, b.v) : cmp(b.v, a.v)))
  return [...known.map((k) => k.it), ...unknown]
}
