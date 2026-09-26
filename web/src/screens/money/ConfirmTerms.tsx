/**
 * Recording the terms a supplier actually gave you.
 *
 * Until this form existed the only remedy for `cafeops doctor`'s standing
 * warning — six of eight suppliers on INVENTED lead times, delivery days,
 * cutoffs, minimums and fees — was to edit a Python seed file in the
 * repository, which the person who rings Booker cannot do.
 *
 * Three decisions are deliberate and none of them is cosmetic:
 *
 *  1. Every term is on the form, together. `terms_are_placeholders` is ONE fact
 *     covering all six numbers because the cover window is computed from several
 *     at once; confirming the minimum alone would clear the warning on an order
 *     whose lead time is still fiction. The form says so rather than leaving it
 *     to be inferred from the absence of a save-one-field button.
 *  2. The controls are pre-filled with the seeded guesses, because editing six
 *     figures beats retyping them — and every pre-filled figure is labelled as
 *     the guess it is, so nobody confirms the seeder's invention back at itself
 *     by accident.
 *  3. Money is pounds here and integer pence on the wire, converted through
 *     `lib/dec.ts`. See `components/confirm/numbers.ts` for why `parseFloat`
 *     is not an option.
 *
 * The response is not a toast. `changed` says which terms actually moved, and
 * "confirming Booker changed the minimum but not the lead time" is the part
 * worth reading; `was_placeholder` says whether this is the call that cleared
 * the warning.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { LIVE, confirmSupplierTerms } from '../../lib/api'
import type { Supplier, SupplierTermsResponse } from '../../lib/types'
import type { WriteResult } from '../../lib/api'
import { poundsOnly } from '../../lib/format'
import { Badge, Button, Panel } from '../../components/ui'
import { DayToggles, Field, FieldGrid, TextInput } from '../../components/confirm/fields'
import { FixtureNotice, Outcome, refusalField } from '../../components/confirm/outcome'
import {
  parseCutoff,
  parseDays,
  penceToPounds,
  poundsToPence,
} from '../../components/confirm/numbers'
import { channelLabel, shortTime, weekdayNames } from './figures'

/** The six fields, as the refusal router knows them. */
type FieldKey = 'lead' | 'days' | 'min' | 'fee' | 'cutoff' | 'free'

/**
 * Which field a backend refusal is printed under. Placement only — the sentence
 * itself is never touched. `services/confirm_terms.py` is the source of these
 * phrasings.
 */
const CUES: Record<FieldKey, readonly string[]> = {
  days: ['delivery weekday', 'weekdays', 'delivery days'],
  lead: ['lead time'],
  min: ['minimum order'],
  fee: ['delivery fee'],
  free: ['free-delivery threshold'],
  cutoff: ['cutoff'],
}

type Errors = Partial<Record<FieldKey, string>>

/** "minimum order: 4500 -> 5000" as the backend wrote it, arrow set properly. */
function changeLine(s: string): string {
  return s.replace(' -> ', ' → ')
}

export function ConfirmTerms({
  supplier,
  onClose,
}: {
  supplier: Supplier
  onClose: () => void
}) {
  const qc = useQueryClient()
  const invented = supplier.terms_are_placeholders

  const [lead, setLead] = useState(String(supplier.lead_time_days))
  const [days, setDays] = useState<number[]>([...supplier.delivery_weekdays])
  const [min, setMin] = useState(penceToPounds(supplier.min_order_pence))
  const [fee, setFee] = useState(penceToPounds(supplier.delivery_fee_pence))
  const [cutoff, setCutoff] = useState(shortTime(supplier.cutoff_time) ?? '')
  const [free, setFree] = useState(penceToPounds(supplier.free_delivery_threshold_pence))

  const [errors, setErrors] = useState<Errors>({})
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<WriteResult<SupplierTermsResponse> | null>(null)

  /* A refused write lands on the field it is about; one that matches nothing is
     printed whole beside the button by <Outcome>. */
  const refusedKey =
    result?.kind === 'refused' ? refusalField<FieldKey>(result.message, CUES) : null
  const refusal = result?.kind === 'refused' ? result.message : null
  const errorFor = (k: FieldKey): string | null =>
    (refusedKey === k ? refusal : null) ?? errors[k] ?? null

  async function submit(): Promise<void> {
    if (busy) return
    const next: Errors = {}

    const leadP = parseDays(lead, 0, 60)
    if (leadP.kind === 'bad') next.lead = leadP.message
    if (leadP.kind === 'blank') next.lead = 'Required. Write 0 if they deliver the same day.'

    if (days.length === 0) {
      next.days =
        'Pick at least one day. A supplier with no delivery day can never satisfy a cover window; a walk-in supplier delivers every day, so pick all seven.'
    }

    const minP = poundsToPence(min)
    if (minP.kind === 'bad') next.min = minP.message
    if (minP.kind === 'blank') next.min = 'Required. Write 0 if there is no minimum.'

    const feeP = poundsToPence(fee)
    if (feeP.kind === 'bad') next.fee = feeP.message
    if (feeP.kind === 'blank') next.fee = 'Required. Write 0 if delivery is always free.'

    const cutP = parseCutoff(cutoff)
    if (cutP.kind === 'bad') next.cutoff = cutP.message

    const freeP = poundsToPence(free)
    if (freeP.kind === 'bad') next.free = freeP.message

    setErrors(next)
    setResult(null)
    if (Object.keys(next).length > 0) return
    if (leadP.kind !== 'value' || minP.kind !== 'value' || feeP.kind !== 'value') return

    setBusy(true)
    const r = await confirmSupplierTerms(supplier.supplier_id, {
      lead_time_days: leadP.value,
      delivery_weekdays: days,
      min_order_pence: minP.value,
      delivery_fee_pence: feeP.value,
      cutoff_time: cutP.kind === 'value' ? cutP.value : null,
      free_delivery_threshold_pence: freeP.kind === 'value' ? freeP.value : null,
    })
    setBusy(false)
    setResult(r)
    if (r.kind === 'ok') {
      // The table above this form, and the draft order sized against these very
      // terms, are both now stale.
      void qc.invalidateQueries({ queryKey: ['suppliers'] })
      void qc.invalidateQueries({ queryKey: ['orders-draft'] })
      void qc.invalidateQueries({ queryKey: ['today'] })
    }
  }

  /* ------------------------------------------------------------ recorded --- */

  if (result?.kind === 'ok') {
    const d = result.data
    return (
      <div className="mt-5 rounded-card-lg border border-brand/40 bg-surface p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="font-semibold text-ink">{d.name} — terms recorded</h3>
            <p className="mt-1 max-w-[70ch] text-[0.875rem] leading-[20px] text-ink-3">
              {d.was_placeholder
                ? 'These figures are now somebody’s word rather than the seeder’s invention, and the invented-terms warning is cleared for this supplier.'
                : 'This supplier’s terms were already confirmed; they have been updated.'}
            </p>
          </div>
          <Badge tone={d.was_placeholder ? 'ok' : 'info'}>
            {d.was_placeholder ? 'warning cleared' : 'updated'}
          </Badge>
        </div>

        <div className="mt-4">
          <p className="text-[0.75rem] font-medium text-ink-2">What actually moved</p>
          {d.changed.length === 0 ? (
            <p className="mt-1.5 max-w-[70ch] text-[0.875rem] leading-[20px] text-ink-3">
              Nothing. Every figure you confirmed matches what was already stored — which is worth
              knowing on its own: the seeder&rsquo;s guesses were right, and now they are recorded
              as confirmed rather than invented. No quantity will change.
            </p>
          ) : (
            <>
              <ul className="mt-1.5 grid gap-1">
                {d.changed.map((c) => (
                  <li key={c} className="fig text-[0.875rem] leading-[16px] text-ink">
                    {changeLine(c)}
                  </li>
                ))}
              </ul>
              <p className="mt-1.5 text-[0.6875rem] text-ink-4">
                Money is shown here in pence, as it is stored. Unlisted terms did not move.
              </p>
            </>
          )}
        </div>

        <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3 [&>*]:min-w-0">
          <Panel tone="info" title="Quantities change from the next ordering run">
            Nothing already drafted has been resized. The cover window is
            <span className="fig"> lead time + days to the next delivery + safety days</span>, so
            the terms you just recorded are applied the next time an order is built. The draft on
            this screen has been re-read; a draft already sitting in Telegram was sized against the
            old figures.
          </Panel>
          <Panel title="Still a draft, either way">
            This confirmation is not an order and it spends nothing. Every order still stops at a
            human in Telegram (invariant 1).
          </Panel>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button onClick={onClose}>Close</Button>
          <span className="text-[0.6875rem] text-ink-4">
            The table above now reads {d.supplier.terms_are_placeholders ? 'unconfirmed' : 'confirmed'} for{' '}
            {d.name}.
          </span>
        </div>
      </div>
    )
  }

  /* ---------------------------------------------------------------- form --- */

  const disabled = busy || !LIVE

  return (
    <form
      className="mt-5 rounded-card-lg border border-line-2 bg-surface p-4 sm:p-5"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-ink">Confirm {supplier.name}&rsquo;s terms</h3>
          <p className="mt-1 text-[0.75rem] text-ink-4">
            Ordered via {channelLabel(supplier.order_channel)} · currently{' '}
            {invented ? 'never confirmed with anyone' : 'confirmed'}
          </p>
        </div>
        <Badge tone={invented ? 'warn' : 'plain'}>
          {invented ? 'every figure below is a guess' : 'editing confirmed terms'}
        </Badge>
      </div>

      <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3 [&>*]:min-w-0">
        <Panel tone={invented ? 'warn' : 'plain'} title="All six terms, in one go — on purpose">
          The ordering maths builds one cover window out of the lead time, the delivery days and
          the cutoff together, and decides whether to place the order at all from the minimum, the
          fee and the free-delivery threshold. There is no partial confirmation, because clearing
          the warning on a minimum while the lead time is still invented would mark an order
          confirmed that is still half fiction. Ring them once and record all six.
        </Panel>
        {!LIVE && <FixtureNotice what="supplier terms" />}
      </div>

      <div className="mt-5">
        <FieldGrid>
          <Field
            id="ct-lead"
            label="Lead time"
            hint="Days from placing the order to it arriving. 0 means same day."
            guess={invented ? `${supplier.lead_time_days} d` : undefined}
            error={errorFor('lead')}
          >
            <TextInput
              id="ct-lead"
              value={lead}
              onChange={setLead}
              numeric
              hint
              width="short"
              suffix="days"
              disabled={disabled}
              invalid={errorFor('lead') !== null}
            />
          </Field>

          <Field
            id="ct-cutoff"
            label="Cutoff time"
            hint="Order after this and it ships the following delivery day. Leave blank if they have no cutoff."
            guess={invented ? (shortTime(supplier.cutoff_time) ?? 'no cutoff') : undefined}
            error={errorFor('cutoff')}
          >
            <TextInput
              id="ct-cutoff"
              value={cutoff}
              onChange={setCutoff}
              placeholder="HH:MM — blank for none"
              hint
              width="short"
              disabled={disabled}
              invalid={errorFor('cutoff') !== null}
            />
          </Field>

          <div className="sm:col-span-2">
            <Field
              group
              id="ct-days"
              label="Delivery days"
              hint="At least one. A supplier with no delivery day can never satisfy a cover window; a walk-in supplier delivers every day, so pick all seven."
              guess={invented ? weekdayNames(supplier.delivery_weekdays) : undefined}
              error={errorFor('days')}
            >
              <DayToggles
                id="ct-days"
                selected={days}
                onChange={setDays}
                disabled={disabled}
                invalid={errorFor('days') !== null}
              />
            </Field>
          </div>

          <Field
            id="ct-min"
            label="Minimum order"
            hint="Below this they will not deliver at all. Write 0 if there is no minimum."
            guess={invented ? poundsOnly(supplier.min_order_pence) : undefined}
            error={errorFor('min')}
          >
            <TextInput
              id="ct-min"
              value={min}
              onChange={setMin}
              numeric
              hint
              width="short"
              prefix="£"
              placeholder="0.00"
              disabled={disabled}
              invalid={errorFor('min') !== null}
            />
          </Field>

          <Field
            id="ct-fee"
            label="Delivery fee"
            hint="Charged per delivery. Write 0 if delivery is always free."
            guess={invented ? poundsOnly(supplier.delivery_fee_pence) : undefined}
            error={errorFor('fee')}
          >
            <TextInput
              id="ct-fee"
              value={fee}
              onChange={setFee}
              numeric
              hint
              width="short"
              prefix="£"
              placeholder="0.00"
              disabled={disabled}
              invalid={errorFor('fee') !== null}
            />
          </Field>

          <Field
            id="ct-free"
            label="Free delivery from"
            hint="Spend this much and the fee is waived. Leave blank if they never waive it — blank is not £0."
            guess={
              invented
                ? supplier.free_delivery_threshold_pence === null
                  ? 'no threshold'
                  : poundsOnly(supplier.free_delivery_threshold_pence)
                : undefined
            }
            error={errorFor('free')}
          >
            <TextInput
              id="ct-free"
              value={free}
              onChange={setFree}
              numeric
              hint
              width="short"
              prefix="£"
              placeholder="blank for none"
              disabled={disabled}
              invalid={errorFor('free') !== null}
            />
          </Field>

        </FieldGrid>
      </div>

      <div className="mt-5 border-t border-line pt-4">
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3 [&>*]:min-w-0">
          <Outcome result={result} refusalPlaced={refusedKey !== null} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" disabled={disabled}>
            {busy ? 'Recording…' : 'Confirm all six terms'}
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <span className="max-w-[46ch] text-[0.6875rem] leading-[16px] text-ink-4">
            Quantities change from the next ordering run, not retrospectively. This spends nothing
            and orders nothing.
          </span>
        </div>
      </div>
    </form>
  )
}
