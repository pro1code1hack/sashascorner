/**
 * Invariant 6 in two components.
 *
 * Confusing a theoretical figure with a counted one is this product's main
 * failure mode, so the distinction is carried three ways at once and none of
 * them is a tooltip or a legend to memorise:
 *
 *   1. POSITION — the theoretical figure is the row's headline, in the on-hand
 *      column, to the left of the strongest vertical rule on the screen. A
 *      counted figure never appears in that column. Ever.
 *   2. TYPE AND WEIGHT — theoretical is large and semibold in full ink; counted
 *      is small and normal in muted ink.
 *   3. A RULE OF THE UI — a counted figure is never printed bare. It only ever
 *      appears inside a phrase that says "counted" and carries its date. So if
 *      a number on this screen has no such phrase attached, it is theoretical.
 */
import { Badge, Note } from './ui'
import { Fig, Label, Qty } from './prim'
import { dayShort, trimQty } from '../lib/format'
import type { OnHand } from '../lib/types'

/** The headline figure: the largest, heaviest number in its row. */
export function Theoretical({ oh }: { oh: OnHand }) {
  const unanchored = !oh.has_count_basis
  const tone = oh.is_negative || unanchored ? 'bad' : 'plain'
  return (
    <span className="whitespace-nowrap">
      <Qty value={oh.qty} unit={oh.unit} size="lg" tone={tone} />
      {!oh.is_theoretical && (
        <>
          {' '}
          <Label>counted figure, not a projection</Label>
        </>
      )}
    </span>
  )
}

/**
 * The count basis, stated as the arithmetic that produced the figure beside it.
 * Showing the equation is what keeps the counted number from ever reading as
 * the headline: it is visibly an input, not a result.
 */
export function CountBasis({ oh }: { oh: OnHand }) {
  if (!oh.has_count_basis) {
    return (
      <div>
        <Badge tone="bad">never counted</Badge>
        <p className="text-[0.6875rem] text-bad leading-[16px] mt-1">
          no physical anchor — this is a bare sum of {oh.movement_count} ledger movements, so
          nothing has ever confirmed it
        </p>
      </div>
    )
  }
  return (
    <div className="leading-[16px]">
      <span className="text-ink-2">
        <Fig size="sm">counted </Fig>
        <Fig size="sm" weight="medium">
          {trimQty(oh.basis_count_qty ?? '0')}
        </Fig>
        <Fig size="sm"> on {dayShort(oh.basis_counted_at)}</Fig>
      </span>
      <div className="text-ink-4">
        <Fig size="sm" className="text-ink-4">
          + ledger {trimQty(oh.movement_sum)} over {oh.movement_count} movements
        </Fig>
      </div>
    </div>
  )
}

/** The standing statement, made once and plainly, at the top of the screen —
 *  not in a tooltip. */
export function TheoreticalStanding({ basis }: { basis: string }) {
  return (
    <div className="space-y-2">
      <Note tone="plain">
        Every on-hand figure below is <strong className="text-ink font-semibold">theoretical</strong>:
        the ledger&rsquo;s projection forward from the last physical count. A count is the only
        truth.
      </Note>
      {/* `plain` rather than the default muted ink: this sits inside a tinted
          panel, where ink-4 falls below a readable contrast. */}
      <Note tone="plain">
        The projection is the large figure, left of the heavy rule. The count it was built from
        sits right of it, named and dated. A number on this screen with no
        &ldquo;counted&nbsp;&hellip;&nbsp;on&nbsp;&hellip;&rdquo; attached to it is theoretical.
      </Note>
      <div className="flex flex-wrap items-center gap-2 pt-1">
        {/* Not `info`: the badge would then be the panel's own wash on the
            panel's own wash, and the edge would vanish. */}
        <Badge tone="plain">API basis: {basis.toLowerCase()}</Badge>
      </div>
    </div>
  )
}
