/**
 * Number rendering for this screen. Money is formatted at the edge from integer
 * pence; a null is the reason it is null, never a zero; a count is grouped but
 * never arithmetic'd here.
 *
 * Money is set in POUNDS all the way down rather than through `lib/format`'s
 * `money()`, which switches to pence under £1. Right for a single unit cost,
 * wrong for a column of channel totals: a column that changes unit half-way down
 * cannot be scanned, and a quiet week's ad spend can easily be under a pound.
 * That rule used to be a local `pounds()` here; it is `poundsOnly()` /
 * `poundsOnlyDec()` in `lib/format` now, and the conversion is still an exact
 * integer shift rather than a division.
 */
import type { Dec } from '../../lib/dec'
import { poundsOnly, poundsOnlyDec } from '../../lib/format'
import { Fig, type Tone } from '../../components/ui'

type Size = 'sm' | 'base' | 'lg' | 'xl'

/** Money from integer pence or an exact decimal string of pence. */
export function M({
  v,
  missing = 'not reported',
  tone = 'plain',
  size = 'base',
}: {
  v: number | string | null
  missing?: string
  tone?: Tone
  size?: Size
}) {
  if (v === null) return <Fig missing={missing} />
  return (
    <Fig tone={tone} size={size}>
      {poundsOnly(v)}
    </Fig>
  )
}

/** Money already summed as an exact Dec, with no string round-trip. */
export function MD({
  v,
  missing = 'not reported',
  tone = 'plain',
  size = 'base',
}: {
  v: Dec | null
  missing?: string
  tone?: Tone
  size?: Size
}) {
  if (v === null) return <Fig missing={missing} />
  return (
    <Fig tone={tone} size={size}>
      {poundsOnlyDec(v)}
    </Fig>
  )
}

/** A count. Grouped at four digits and above; never summed in the browser. */
export function N({
  v,
  missing = 'not reported',
  unit,
  tone = 'plain',
  size = 'base',
}: {
  v: number | null
  missing?: string
  unit?: string
  tone?: Tone
  size?: Size
}) {
  if (v === null) return <Fig missing={missing} />
  return (
    <Fig tone={tone} size={size}>
      {v.toLocaleString('en-GB')}
      {unit && <span className="font-normal text-ink-3"> {unit}</span>}
    </Fig>
  )
}

/** A ratio or percentage already reduced by the API, in basis points. */
export function Ratio({
  text,
  missing = 'not reported',
  tone = 'plain',
  size = 'base',
}: {
  text: string | null
  missing?: string
  tone?: Tone
  size?: Size
}) {
  if (text === null) return <Fig missing={missing} />
  return (
    <Fig tone={tone} size={size}>
      {text}
    </Fig>
  )
}
