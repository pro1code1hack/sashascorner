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
 * The two halves share one hue and differ in FORM — solid bar versus ticked
 * bar. Colour on this screen is reserved for a STATE the system has classified
 * (trusted / drifting / excluded); expiry and measurement are causes, not
 * states, so a second hue here would say something the data does not. Form also
 * survives greyscale and colour blindness where a hue pair does not.
 */
import { Badge, Note, type Tone } from './ui'
import { Fig, Label, Qty, Marg } from './prim'
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
    <div className="grid grid-cols-[6.5rem_5.5rem_minmax(2rem,1fr)_2.5rem] items-center gap-x-2 py-[3px] max-w-[30rem]">
      <Label>{label}</Label>
      <span className="text-right">
        <Qty value={qty} unit={unit} size="sm" weight={zero ? 'light' : 'medium'} />
      </span>
      <div className="relative h-[8px] rounded-pill bg-line overflow-hidden" aria-hidden>
        {zero ? (
          <div className="absolute inset-y-0 left-0 w-[2px] bg-line-2" />
        ) : (
          <div
            className="absolute inset-y-0 left-0"
            style={{
              width: `${width}%`,
              background:
                texture === 'solid'
                  ? 'var(--color-warn)'
                  : 'repeating-linear-gradient(115deg, var(--color-warn) 0 3px, transparent 3px 6px)',
              borderRight: texture === 'ticked' ? '1px solid var(--color-warn)' : undefined,
            }}
          />
        )}
      </div>
      <Fig size="sm" className={`text-right ${zero ? 'text-ink-4' : 'text-ink-2'}`}>
        {sharePct(share)}
      </Fig>
    </div>
  )
}

export function Attribution({ drift, unit }: { drift: Drift; unit: Unit }) {
  const a = drift.attribution
  if (a === null) {
    return (
      <div className="rounded-control border border-line bg-raised px-3 py-2.5">
        <Note>{drift.auto_order_reason}</Note>
      </div>
    )
  }
  return (
    <div className="rounded-control border border-line bg-raised px-3 py-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 mb-1">
        <Fig size="sm" weight="medium" tone="warn">
          {a.headline}
        </Fig>
        <Label>
          cause {a.cause.toLowerCase()} · {a.loss_pct_of_consumption.toFixed(1)}% of consumption
        </Label>
      </div>
      <Half label="expiry loss" qty={a.expiry_qty} unit={unit} share={a.expiry_share} texture="ticked" />
      <Half
        label="measurement"
        qty={a.measurement_qty}
        unit={unit}
        share={1 - a.expiry_share}
        texture="solid"
      />
      <div className="mt-2 space-y-1">
        <Note tone="plain">{a.action}</Note>
        {a.surplus_note && (
          <Note>
            <span className="text-ink-2 font-semibold">Separately:</span> {a.surplus_note}
          </Note>
        )}
        <Note>
          {drift.auto_order_reason}
          {drift.clean_streak < drift.required_streak && drift.verdict !== 'ELIGIBLE' && (
            <> · clean streak {drift.clean_streak} of {drift.required_streak} needed</>
          )}
        </Note>
      </div>
    </div>
  )
}

/**
 * The four trust states, kept four.
 *
 * `trust_status` is `trusted | drifting | excluded`, and NULL when no drift
 * observation has ever been recorded — on the seeded data that fourth state is
 * a third of the rows. It must not collapse into `trusted`: an absence of
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
export function trustLabel(drift: Drift): { text: string; tone: Tone } {
  switch (drift.trust_status) {
    case 'excluded':
      return { text: 'excluded', tone: 'bad' }
    case 'drifting':
      return { text: 'drifting', tone: 'warn' }
    case 'trusted':
      return { text: 'trusted', tone: 'ok' }
    default:
      return { text: 'not yet judged', tone: 'info' }
  }
}

/** What the trust state means for ordering, said in words rather than left for
 *  the reader to infer from a colour. */
export function GateBadge({ drift }: { drift: Drift }) {
  switch (drift.trust_status) {
    case 'excluded':
      return <Badge tone="bad">auto-ordering refused</Badge>
    case 'drifting':
      return <Badge tone="warn">tuning band · stays manual</Badge>
    case 'trusted':
      return drift.auto_order_enabled ? (
        <Badge tone="ok">auto-ordering earned</Badge>
      ) : (
        <Badge tone="plain">within band · still manual</Badge>
      )
    default:
      return <Badge tone="info">no drift reading either way</Badge>
  }
}

/**
 * The drift verdict as a gutter note, for the row-detail layout where the
 * badge would collide with the figures.
 */
export function DriftMargin({ drift }: { drift: Drift }) {
  const { text, tone } = trustLabel(drift)
  if (tone === 'bad') {
    return (
      <Marg flag>
        auto-ordering
        <br />
        refused
      </Marg>
    )
  }
  if (tone === 'warn') {
    return (
      <Marg flag>
        tuning band
        <br />
        widening
      </Marg>
    )
  }
  return <Marg>{text}</Marg>
}
