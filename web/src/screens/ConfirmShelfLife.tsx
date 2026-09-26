/**
 * Recording a shelf life somebody actually checked.
 *
 * `cafeops doctor`'s second standing warning is a hundred perishables on
 * ESTIMATE shelf lives seeded by a script. It matters because shelf life is not
 * a label: it CAPS ORDER SIZE (invariant 4, spec §5.4). What an order can hold
 * is `shelf_life_days − transit_buffer_days` of forecast trade, so a wrong
 * figure either buys stock that spoils or refuses stock that was needed.
 *
 * Which is why the response here is not a toast. `usable_days_changed_by` is the
 * headline — "a single order may now cover three more days of trade" is the
 * honest answer to "what did I just do", and "saved" is not. The arithmetic is
 * printed with it, because a reader who cannot see where the number came from
 * has to take it on faith.
 *
 * There are two source options and deliberately no third. "Estimate" is what
 * this call leaves behind; the API refuses it outright rather than accept a
 * confirmation that silences a warning without adding knowledge.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { LIVE, confirmShelfLife } from '../lib/api'
import type { WriteResult } from '../lib/api'
import type { ShelfLifeResponse, StockRow } from '../lib/types'
import { plural } from '../lib/format'
import { Badge, Button, Panel } from '../components/ui'
import { Fig, Label } from '../components/prim'
import { Choice, Field, FieldGrid, TextInput } from '../components/confirm/fields'
import { FixtureNotice, Outcome, refusalField } from '../components/confirm/outcome'
import { parseDays } from '../components/confirm/numbers'

type FieldKey = 'open' | 'days' | 'source'

/**
 * Where a refusal is printed. Placement only — the backend's sentence is never
 * reworded. `open` is matched first on purpose: "open life 10d cannot exceed the
 * unopened shelf life 7d" mentions both, and it is the open-life field that is
 * wrong.
 */
const CUES: Record<FieldKey, readonly string[]> = {
  open: ['open life'],
  days: ['shelf life', 'transit buffer', 'at least one day'],
  source: ['source must be', 'as an estimate'],
}

/** What the stored source means in words. `INVOICE` is how a packaging reading
 *  is recorded — the enum is shared with prices, where it means an invoice. */
function sourceLabel(s: string | null): string {
  if (s === null) return 'nothing recorded'
  if (s === 'ESTIMATE') return 'a seeded estimate'
  if (s === 'SUPPLIER_FEED') return 'the supplier’s word'
  if (s === 'INVOICE') return 'read off the packaging'
  return s.toLowerCase()
}

export function ConfirmShelfLife({ row, onClose }: { row: StockRow; onClose: () => void }) {
  const qc = useQueryClient()
  const sl = row.shelf_life
  const transit = sl.transit_buffer_days
  const wasEstimate = sl.source === 'ESTIMATE'

  const [days, setDays] = useState(sl.shelf_life_days === null ? '' : String(sl.shelf_life_days))
  const [open, setOpen] = useState(sl.open_life_days === null ? '' : String(sl.open_life_days))
  const [source, setSource] = useState<'supplier' | 'packaging'>('supplier')

  const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({})
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<WriteResult<ShelfLifeResponse> | null>(null)

  const refusedKey =
    result?.kind === 'refused' ? refusalField<FieldKey>(result.message, CUES) : null
  const refusal = result?.kind === 'refused' ? result.message : null
  const errorFor = (k: FieldKey): string | null =>
    (refusedKey === k ? refusal : null) ?? errors[k] ?? null

  /* The cap, as it would stand if this were confirmed. Integer day counts, so
     plain arithmetic is exact — no money and no quantity is touched here. */
  const typed = parseDays(days, 1, 3650)
  const wouldBeUsable = typed.kind === 'value' ? typed.value - transit : null

  async function submit(): Promise<void> {
    if (busy) return
    const next: Partial<Record<FieldKey, string>> = {}

    if (typed.kind === 'bad') next.days = typed.message
    if (typed.kind === 'blank') next.days = 'Required — this is the figure that caps an order.'

    /* Only what cannot be put on the wire is checked here. An open life longer
       than the unopened one, or a shelf life inside the transit buffer, are both
       refused by the service in a sentence written for the person who typed the
       number — duplicating that check locally would replace their words with
       mine, which is the one thing this form must not do. */
    const openP = parseDays(open, 1, 3650)
    if (openP.kind === 'bad') next.open = openP.message

    setErrors(next)
    setResult(null)
    if (Object.keys(next).length > 0) return
    if (typed.kind !== 'value') return

    setBusy(true)
    const r = await confirmShelfLife(row.ingredient_id, {
      shelf_life_days: typed.value,
      open_life_days: openP.kind === 'value' ? openP.value : null,
      source,
    })
    setBusy(false)
    setResult(r)
    if (r.kind === 'ok') {
      void qc.invalidateQueries({ queryKey: ['stock'] })
      void qc.invalidateQueries({ queryKey: ['stock-detail', row.ingredient_id] })
      void qc.invalidateQueries({ queryKey: ['today'] })
      void qc.invalidateQueries({ queryKey: ['orders-draft'] })
    }
  }

  /* ------------------------------------------------------------ recorded --- */

  if (result?.kind === 'ok') {
    const d = result.data
    const moved = d.usable_days_changed_by
    return (
      <div className="mt-3 rounded-card-lg border border-brand/40 bg-surface p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <Label>what this changed</Label>
            {/* The headline is the ORDER CAP, not the shelf life. */}
            {moved === null ? (
              <p className="mt-1 max-w-[62ch] text-[0.875rem] leading-[20px] text-ink">
                {d.name} had no shelf life recorded at all, so there is no before to compare
                against. A single order can now hold{' '}
                <Fig weight="semibold">{d.usable_days_after}</Fig>{' '}
                {plural(d.usable_days_after, 'day')} of trade — and this ingredient is now
                treated as perishable, which it was not before.
              </p>
            ) : moved === 0 ? (
              <p className="mt-1 max-w-[62ch] text-[0.875rem] leading-[20px] text-ink">
                The order cap did not move. A single order still covers{' '}
                <Fig weight="semibold">{d.usable_days_after}</Fig>{' '}
                {plural(d.usable_days_after, 'day')} of trade — the figure is the same, but it is
                now {sourceLabel(d.source_after)} rather than {sourceLabel(d.source_before)}.
              </p>
            ) : (
              <>
                <p className="mt-1 flex flex-wrap items-baseline gap-2">
                  <Fig size="xl" weight="semibold" tone={moved > 0 ? 'ok' : 'warn'}>
                    {moved > 0 ? '+' : '−'}
                    {Math.abs(moved)} {plural(Math.abs(moved), 'day')}
                  </Fig>
                  <span className="text-[0.875rem] text-ink-3">of cover, per order</span>
                </p>
                <p className="mt-1.5 max-w-[62ch] text-[0.875rem] leading-[20px] text-ink-2">
                  A single order of {d.name} may now cover{' '}
                  <Fig weight="medium">{Math.abs(moved)}</Fig>{' '}
                  {moved > 0 ? 'more' : 'fewer'} {plural(Math.abs(moved), 'day')} of trade than it
                  could an hour ago. That is what a shelf life does: it caps the quantity, so this
                  is the consequence, not a saved setting.
                </p>
              </>
            )}
          </div>
          {/* From the RESPONSE, not from `row`: the successful write invalidates
              the stock query, so by the time this renders `row.shelf_life.source`
              already says what it is now rather than what it was. */}
          <Badge tone="ok">
            {d.source_before === 'ESTIMATE' ? 'no longer an estimate' : 'updated'}
          </Badge>
        </div>

        <div className="mt-3.5 grid grid-cols-[minmax(0,1fr)] gap-3 [&>*]:min-w-0">
          <Panel title="The arithmetic behind that">
            <span className="fig">{d.usable_days_after} d usable</span> ={' '}
            <span className="fig">{d.shelf_life_days_after} d</span> unopened shelf life −{' '}
            <span className="fig">{d.transit_buffer_days} d</span> transit buffer. The buffer is
            the time the stock spends getting here, which is life it arrives having already spent;
            the remainder is all an order can ever be sized against.
            {d.shelf_life_days_before !== null && d.shelf_life_days_before !== d.shelf_life_days_after && (
              <>
                {' '}
                It was <span className="fig">{d.shelf_life_days_before} d</span> before, from{' '}
                {sourceLabel(d.source_before)}.
              </>
            )}
            {d.open_life_days_after !== null && (
              <>
                {' '}
                Once opened it keeps for{' '}
                <span className="fig">{d.open_life_days_after} d</span>, which is what depletion
                uses for an open batch.
              </>
            )}
          </Panel>
          <Panel tone="info" title="From the next ordering run">
            Nothing already drafted or delivered has been resized, and no batch already in the
            fridge has changed its expiry. The next time an order is built, the cap above is the
            one it uses — and the line will say so if it is the cap that decided the quantity.
          </Panel>
        </div>

        <div className="mt-3.5">
          <Button onClick={onClose}>Close</Button>
        </div>
      </div>
    )
  }

  /* ---------------------------------------------------------------- form --- */

  const disabled = busy || !LIVE

  return (
    <form
      className="mt-3 rounded-card-lg border border-line-2 bg-surface p-4"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-ink">{row.name} — confirm its shelf life</h3>
          <p className="mt-1 text-[0.75rem] text-ink-4">
            Currently {sl.shelf_life_days === null ? 'nothing recorded' : `${sl.shelf_life_days} d`}
            , from {sourceLabel(sl.source)} · {sl.storage.toLowerCase()} ·{' '}
            {transit === 0 ? 'no transit buffer' : `${transit} d transit buffer`}
          </p>
        </div>
        {wasEstimate && <Badge tone="plain">on a seeded estimate</Badge>}
      </div>

      <div className="mt-3.5 grid grid-cols-[minmax(0,1fr)] gap-3 [&>*]:min-w-0">
        <Panel title="This is an order-size figure, not a label">
          Order quantity is capped at the trade this ingredient can cover before it spoils:{' '}
          <span className="fig">shelf life − transit buffer</span>. Too long and the system buys
          stock that will be thrown away; too short and it refuses stock that was needed. It is
          worth getting from the pack or the supplier rather than from memory.
        </Panel>
        {sl.shelf_life_days === null && (
          <Panel tone="warn" title="Nothing is recorded for this one">
            It is not treated as perishable today, so no order for it is capped and no batch of it
            expires. Recording a shelf life changes that — which is right for food and wrong for a
            cup lid.
          </Panel>
        )}
        {!LIVE && <FixtureNotice what="a shelf life" />}
      </div>

      <div className="mt-4">
        <FieldGrid>
          <Field
            id={`sl-days-${row.ingredient_id}`}
            label="Unopened shelf life"
            hint={
              wouldBeUsable === null ? (
                'Days from delivery to the use-by date, unopened.'
              ) : wouldBeUsable <= 0 ? (
                <>
                  That leaves nothing usable on arrival against the {transit} d transit buffer, so
                  no quantity could ever be ordered. The API will refuse it.
                </>
              ) : (
                <>
                  Caps an order at{' '}
                  <span className="fig text-ink-2">
                    {wouldBeUsable} {plural(wouldBeUsable, 'day')}
                  </span>{' '}
                  of cover ({typed.kind === 'value' ? typed.value : 0} − {transit} transit).
                </>
              )
            }
            guess={
              wasEstimate && sl.shelf_life_days !== null ? `${sl.shelf_life_days} d` : undefined
            }
            error={errorFor('days')}
          >
            <TextInput
              id={`sl-days-${row.ingredient_id}`}
              value={days}
              onChange={setDays}
              numeric
              hint
              width="short"
              suffix="days"
              disabled={disabled}
              invalid={errorFor('days') !== null}
            />
          </Field>

          <Field
            id={`sl-open-${row.ingredient_id}`}
            label="Open life"
            hint="Days it keeps once opened, if the pack says. Leave blank to leave it as it is — it shortens a batch's effective expiry, it does not cap the order."
            guess={wasEstimate && sl.open_life_days !== null ? `${sl.open_life_days} d` : undefined}
            error={errorFor('open')}
          >
            <TextInput
              id={`sl-open-${row.ingredient_id}`}
              value={open}
              onChange={setOpen}
              numeric
              hint
              width="short"
              suffix="days"
              placeholder="optional"
              disabled={disabled}
              invalid={errorFor('open') !== null}
            />
          </Field>

          <div className="sm:col-span-2">
            <Field
              group
              id={`sl-source-${row.ingredient_id}`}
              label="Where the figure came from"
              hint="There is no estimate option: an estimate is what this replaces, and the API refuses a confirmation that would silence the warning without adding knowledge."
              error={errorFor('source')}
            >
              <Choice
                id={`sl-source-${row.ingredient_id}`}
                name={`sl-source-${row.ingredient_id}`}
                value={source}
                onChange={setSource}
                disabled={disabled}
                options={[
                  {
                    value: 'supplier',
                    label: 'The supplier told me',
                    detail: 'From the account manager, the spec sheet or the delivery note.',
                  },
                  {
                    value: 'packaging',
                    label: 'I read it off the pack',
                    detail: 'The date printed on the carton in front of you.',
                  },
                ]}
              />
            </Field>
          </div>
        </FieldGrid>
      </div>

      <div className="mt-4 border-t border-line pt-3.5">
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3 [&>*]:min-w-0">
          <Outcome result={result} refusalPlaced={refusedKey !== null} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" disabled={disabled}>
            {busy ? 'Recording…' : 'Confirm this shelf life'}
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <span className="max-w-[44ch] text-[0.6875rem] leading-[16px] text-ink-4">
            Order sizes change from the next ordering run. Nothing already ordered or in stock is
            touched.
          </span>
        </div>
      </div>
    </form>
  )
}
