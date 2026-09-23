/**
 * Invariant 6 in two components.
 *
 * Confusing a theoretical figure with a counted one is this product's main
 * failure mode, so the distinction is carried three ways at once and none of
 * them is a tooltip or a legend to memorise (DESIGN.md §5):
 *
 *   1. POSITION — the theoretical figure is the row's headline, in the on-hand
 *      column, to the left of the strongest vertical rule on the screen. A
 *      counted figure never appears in that column. Ever.
 *   2. TYPE AND WEIGHT — theoretical is large mono 500 in ink; counted is small
 *      mono 400 in muted.
 *   3. A RULE OF THE UI — a counted figure is never printed bare. It only ever
 *      appears inside a phrase that says "counted" and carries its date. So if
 *      a number on this screen has no such phrase attached, it is theoretical.
 */
import { Fig, Label, Qty } from './prim'
import { dayShort, trimQty } from '../lib/format'
import type { OnHand } from '../lib/types'

/** The headline figure. Larger on a phone, where it is the whole row. */
export function Theoretical({ oh }: { oh: OnHand }) {
  const unanchored = !oh.has_count_basis
  return (
    <span className="whitespace-nowrap">
      <Qty
        value={oh.qty}
        unit={oh.unit}
        sizeClass="text-[length:var(--text-fig-lg)] md:text-[length:var(--text-fig)]"
        weight={500}
        className={oh.is_negative || unanchored ? 'text-flag' : ''}
      />
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
      <div className="text-flag">
        <Fig size="sub" weight={500}>
          never counted
        </Fig>
        <div className="text-[length:var(--text-micro)] leading-snug">
          no physical anchor — this is a bare sum of {oh.movement_count} ledger movements,
          so nothing has ever confirmed it
        </div>
      </div>
    )
  }
  return (
    <div className="leading-snug">
      <span className="text-muted">
        <Fig size="sub">counted </Fig>
        <Fig size="sub" weight={500}>
          {trimQty(oh.basis_count_qty ?? '0')}
        </Fig>
        <Fig size="sub"> on {dayShort(oh.basis_counted_at)}</Fig>
      </span>
      <div className="text-faint">
        <Fig size="sub">
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
    <p className="max-w-[62ch] leading-relaxed">
      Every on-hand figure below is <strong className="font-[600]">theoretical</strong>: the
      ledger&rsquo;s projection forward from the last physical count. A count is the only
      truth. The projection sits left of the heavy rule; the count it was built from sits
      right of it, named and dated. A number on this screen with no
      &ldquo;counted&nbsp;&hellip;&nbsp;on&nbsp;&hellip;&rdquo; attached to it is theoretical.
      <span className="text-muted"> API basis: {basis.toLowerCase()}.</span>
    </p>
  )
}
