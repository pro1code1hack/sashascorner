/**
 * Reading the proposals payload. No arithmetic on quantities, ever.
 *
 * Quantities arrive as exact decimal STRINGS ("0.18", "0.036") and leave this
 * file as the same strings. The only thing computed from them is an ORDER, and
 * that goes through `lib/dec.ts` on BigInt: `0.1 + 0.2` deciding which quantity
 * the backend is about to write into 66 drinks is not acceptable.
 *
 * The one judgement here is `groupQuantities`. A conflict is a map of menu item
 * name -> the quantity that item uses, and what a human needs in order to
 * adjudicate is not 27 rows but the two or three quantities in play and who is
 * on each side. So the items are partitioned by value — by value, not by
 * spelling, so "0.180" and "0.18" are one side of the argument and not two —
 * and the lowest is marked, because the lowest is the one `allow_conflicts`
 * writes.
 */
import { cmp, parseDec } from '../../lib/dec'
import type { Proposal, ProposalAxis, ProposalConflict } from '../../lib/types'

/** The backend writes plain ASCII. Typography only; not a word is changed. */
export function prose(s: string): string {
  return s.replace(/ -- /g, ' — ')
}

/** The workbook's size order, not the alphabet's. Unknown codes sort last. */
const SIZE_ORDER = ['S', 'M', 'XL', 'One']

function sizeRank(code: string): number {
  const i = SIZE_ORDER.indexOf(code)
  return i === -1 ? SIZE_ORDER.length : i
}

export function orderSizes(sizes: readonly string[]): string[] {
  return [...sizes].sort((a, b) => sizeRank(a) - sizeRank(b) || a.localeCompare(b))
}

/** Two quantity strings that mean the same number. Exact, on BigInt. */
function sameValue(a: string, b: string): boolean {
  const x = parseDec(a)
  const y = parseDec(b)
  if (x === null || y === null) return a === b
  return cmp(x, y) === 0
}

export interface QtyGroup {
  /** Exactly as the API sent it, for the side of the argument this is. */
  qty: string
  /** Menu item names using it, alphabetical. */
  items: string[]
  /** The quantity `allow_conflicts` would write. Only ever set when `ordered`. */
  lowest: boolean
}

export interface QtySides {
  groups: QtyGroup[]
  /** False when some quantity did not parse as an exact decimal, in which case
   *  nothing is marked as the one that would be written — guessing which side
   *  wins is worse than saying we cannot tell. */
  ordered: boolean
}

export function groupQuantities(c: ProposalConflict): QtySides {
  const raw: { qty: string; items: string[] }[] = []
  for (const [item, qty] of Object.entries(c.quantities)) {
    const hit = raw.find((g) => sameValue(g.qty, qty))
    if (hit) hit.items.push(item)
    else raw.push({ qty, items: [item] })
  }
  for (const g of raw) g.items.sort((a, b) => a.localeCompare(b))

  const ordered = raw.every((g) => parseDec(g.qty) !== null)
  if (ordered) {
    raw.sort((a, b) => {
      const x = parseDec(a.qty)
      const y = parseDec(b.qty)
      return x === null || y === null ? 0 : cmp(x, y)
    })
  } else {
    raw.sort((a, b) => a.qty.localeCompare(b.qty))
  }
  return { groups: raw.map((g, i) => ({ ...g, lowest: ordered && i === 0 })), ordered }
}

/** The quantity that would be written at this disagreement, or null when the
 *  strings could not be ordered. */
export function acceptedQty(c: ProposalConflict): string | null {
  const { groups, ordered } = groupQuantities(c)
  if (!ordered) return null
  return groups[0]?.qty ?? null
}

/** How a conflict names itself in a heading: the ingredient and the size. */
export function conflictWhere(c: ProposalConflict): string {
  const size = c.size_code === null ? 'every size' : `size ${c.size_code}`
  return `${c.ingredient_name} at ${size}`
}

/** A component with no ingredient is a slot an axis fills. Every one of the 18
 *  in the payload matches an axis by role; if one ever did not, the caller
 *  renders the role alone rather than inventing an axis name. */
export function axisForRole(p: Proposal, role: string): ProposalAxis | undefined {
  return p.axes.find((a) => a.role === role)
}

export function roleLabel(role: string): string {
  return role.replace(/_/g, ' ').toLowerCase()
}

/** Item x size rows the 27 patterns account for. Plain integer counts. */
export function coveredItems(proposals: readonly Proposal[]): number {
  return proposals.reduce((n, p) => n + p.menu_item_count, 0)
}

export function coveredBaseItems(proposals: readonly Proposal[]): number {
  return proposals.reduce((n, p) => n + p.base_item_names.length, 0)
}

/**
 * Names shared by more than one proposal.
 *
 * Two do in the live payload — "Flavoured Matcha (Matcha powder)" (18 items and
 * 5) and "Flavoured Tea (Loose-leaf tea (black))" (6 and 4).
 *
 * The write is now aimed by `proposal_id`, a hash of the structural signature,
 * so a shared name no longer makes a confirmation unaimable — and the API
 * refuses an ambiguous NAME rather than resolving it to whichever came first.
 * The flag is still surfaced, because two cards reading the same is worth
 * saying out loud even when the button works.
 */
export function duplicateNames(proposals: readonly Proposal[]): Set<string> {
  // The API now decides this and sends `name_is_ambiguous`, because it is the
  // side that can see every proposal and the side that refuses the write. This
  // reads the flag rather than re-deriving it, so the screen and the backend
  // cannot disagree about which names are safe to aim at.
  return new Set(proposals.filter((p) => p.name_is_ambiguous).map((p) => p.name))
}

export type ConfirmState =
  /** Confirmable as it stands. */
  | { kind: 'ready' }
  /** Confirmable only with the lowest-quantity opt-in. */
  | { kind: 'conflicts'; count: number }
  /** A template of this name exists; confirming again is a 409. */
  | { kind: 'materialised' }
  /** Two proposals share this name, so the write cannot be aimed. */
  | { kind: 'ambiguous' }

export function confirmState(p: Proposal, ambiguous: boolean): ConfirmState {
  if (p.already_materialised) return { kind: 'materialised' }
  if (ambiguous) return { kind: 'ambiguous' }
  if (p.conflicts.length > 0) return { kind: 'conflicts', count: p.conflicts.length }
  return { kind: 'ready' }
}

/**
 * A proposal detection found no shared components in — four of the 27 (Cake,
 * Juice, Panini, Water), whose legacy rows carry no ingredient lines at all.
 * Confirming one would write an empty template AND re-point its items off the
 * manual recipes they resolve through today, so they would cost and deplete
 * nothing. The API refuses it; this flag is what lets the card say so first.
 */
export function isHollow(p: Proposal): boolean {
  // The API sends this and now REFUSES the write (422), for the reason above.
  return p.is_hollow
}

export type Filter = 'all' | 'conflicts' | 'clear' | 'done'

export function matchesFilter(p: Proposal, f: Filter): boolean {
  switch (f) {
    case 'all':
      return true
    case 'conflicts':
      return p.conflicts.length > 0 && !p.already_materialised
    case 'clear':
      return p.conflicts.length === 0 && !p.already_materialised
    case 'done':
      return p.already_materialised
  }
}
