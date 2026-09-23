/**
 * The drift panel, split into measurement error versus expiry loss.
 *
 * These two have OPPOSITE fixes — a bad recipe wants the recipe fixed, expired
 * stock wants the order cut — so one undifferentiated drift number tells the
 * owner to do the wrong thing half the time. `drift.attribution` carries the
 * cause, the headline and the action, and all three are rendered.
 *
 * Both halves are printed even when one is zero. `0 L` of measurement error
 * beside `1.665 L` of expiry loss IS the finding; printing only the non-zero
 * half destroys the comparison that makes the split worth having.
 *
 * The two halves share the one accent hue and differ in FORM — solid bar
 * versus ticked bar. A second hue would rebuild the traffic light this design
 * refuses, and form survives greyscale and colour blindness where a hue pair
 * does not (DESIGN.md §2).
 */
import { Fig, Label, Marg, Qty } from './prim'
import type { Drift, Unit } from '../lib/types'

/** `expiry_share` is a float by contract (a proportion, not money) and is used
 *  for a bar width and a formatted percentage. It is never combined into a
 *  money figure — invariant 11 is about money and this is not money. */
function sharePct(share: number): string {
  return `${Math.round(share * 100)}%`
}

function Half({
  label,
  qty,
  unit,
  share,
  texture,
}: {
  label: string
  qty: string
  unit: Unit
  share: number
  texture: 'solid' | 'ticked'
}) {
  const width = Math.max(0, Math.min(1, share)) * 100
  const zero = width === 0
  return (
    <div className="grid max-w-[30rem] grid-cols-[7rem_5.5rem_minmax(0,1fr)_2.5rem] items-center gap-x-2 py-[2px]">
      <Label>{label}</Label>
      <span className="text-right">
        <Qty value={qty} unit={unit} size="sub" weight={zero ? 300 : 500} />
      </span>
      <div className="relative h-[9px] bg-white/45" aria-hidden>
        {zero ? (
          <div className="absolute inset-y-0 left-0 w-[2px] bg-rule-strong" />
        ) : (
          <div
            className="absolute inset-y-0 left-0"
            style={{
              width: `${width}%`,
              background:
                texture === 'solid'
                  ? 'var(--color-flag)'
                  : 'repeating-linear-gradient(115deg, var(--color-flag) 0 3px, #ffffff00 3px 6px)',
              borderRight: texture === 'ticked' ? '1px solid var(--color-flag)' : undefined,
            }}
          />
        )}
      </div>
      <Fig size="micro" className={zero ? 'text-faint text-right' : 'text-right'}>
        {sharePct(share)}
      </Fig>
    </div>
  )
}

export function Attribution({ drift, unit }: { drift: Drift; unit: Unit }) {
  const a = drift.attribution
  if (a === null) {
    return (
      <div className="py-2">
        <p className="text-muted max-w-[68ch] leading-relaxed">{drift.auto_order_reason}</p>
      </div>
    )
  }
  const expiry = a.expiry_share
  const measurement = 1 - a.expiry_share
  return (
    <div className="bg-flag-wash mt-1 px-3 py-2">
      <div className="mb-1">
        <Fig size="sub" weight={500} className="text-flag">
          {a.headline}
        </Fig>
        <Label className="ml-2">
          cause {a.cause.toLowerCase()} · {a.loss_pct_of_consumption.toFixed(1)}% of consumption
        </Label>
      </div>
      <Half
        label="expiry loss"
        qty={a.expiry_qty}
        unit={unit}
        share={expiry}
        texture="ticked"
      />
      <Half
        label="measurement"
        qty={a.measurement_qty}
        unit={unit}
        share={measurement}
        texture="solid"
      />
      <p className="text-ink-soft mt-2 max-w-[74ch] leading-relaxed">{a.action}</p>
      {a.surplus_note && (
        <p className="text-muted mt-1 max-w-[74ch] leading-relaxed">
          <span className="text-ink-soft font-[600]">Separately:</span> {a.surplus_note}
        </p>
      )}
      <p className="text-muted mt-1 leading-relaxed">
        {drift.auto_order_reason}
        {drift.clean_streak < drift.required_streak && drift.verdict !== 'ELIGIBLE' && (
          <>
            {' · '}
            clean streak {drift.clean_streak} of {drift.required_streak} needed
          </>
        )}
      </p>
    </div>
  )
}

/**
 * The four trust states, kept four.
 *
 * `trust_status` is `trusted | drifting | excluded`, and NULL when no drift
 * observation has ever been recorded — on the seeded data that fourth state is
 * the overwhelming majority. It must not collapse into `trusted`: an absence of
 * evidence is not a clean bill of health, and the two need opposite responses
 * (leave it alone / go and count it).
 *
 * The wording matters. These ingredients HAVE been counted — Chocolate powder
 * carries `counted 5 on 25 Jul` in the basis column of the same row. What they
 * lack is a drift OBSERVATION: two counts far enough apart to say whether the
 * ledger tracks reality. Labelling them "never counted" would contradict the
 * basis column two cells to the left, which is precisely the confusion this
 * screen exists to prevent.
 */
export function DriftMargin({ drift }: { drift: Drift }) {
  const t = drift.trust_status
  if (t === 'excluded') {
    return (
      <Marg flag>
        auto-ordering
        <br />
        refused
      </Marg>
    )
  }
  if (t === 'drifting') {
    return (
      <Marg flag>
        tuning band
        <br />
        stays manual
      </Marg>
    )
  }
  if (t === null) {
    return (
      <Marg>
        no evidence
        <br />
        either way
      </Marg>
    )
  }
  return <Marg>{''}</Marg>
}

/** The status word itself, in four visually distinct states. Muted reads as
 *  settled; ink reads as unfinished business; flag reads as a threshold
 *  crossed. Never-judged is ink, so it can never be skimmed as "fine". */
export function trustLabel(drift: Drift): { text: string; className: string } {
  switch (drift.trust_status) {
    case 'excluded':
      return { text: 'excluded', className: 'text-flag font-[500]' }
    case 'drifting':
      return { text: 'drifting', className: 'text-flag' }
    case 'trusted':
      return { text: 'trusted', className: 'text-muted' }
    default:
      return { text: 'not yet judged', className: 'text-ink' }
  }
}
