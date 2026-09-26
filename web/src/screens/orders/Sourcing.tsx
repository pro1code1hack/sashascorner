/**
 * Where a line moved off the preferred supplier, and what that was worth.
 *
 * Spec §4.4: sourcing prefers the preferred product unless an alternate is
 * materially cheaper per unit AND the switch does not push another supplier
 * below its minimum. The trade-off is surfaced, never resolved silently — which
 * means the saving forgone by STAYING is shown as honestly as the saving taken
 * by switching.
 *
 * `forgone_saving_pence` is null when no cheaper alternative was rejected. That
 * is not zero: nothing was given up, so there is no figure, and the reason reads
 * in its place.
 */
import { Badge, Note, Panel } from '../../components/ui'
import { plural } from '../../lib/format'
import { Field, MExact } from './figures'
import { prose, type SourcingChoice } from './data'

function rank(c: SourcingChoice): number {
  if (!c.chosen_is_preferred) return 0
  if (c.cheaper_rejected_supplier_id !== null) return 1
  return 2
}

export function Sourcing({ choices }: { choices: SourcingChoice[] }) {
  const ordered = [...choices].sort(
    (a, b) => rank(a) - rank(b) || a.ingredient_name.localeCompare(b.ingredient_name),
  )
  const switched = choices.filter((c) => !c.chosen_is_preferred).length
  const forgone = choices.filter((c) => c.cheaper_rejected_supplier_id !== null).length

  if (choices.length === 0) {
    return (
      <Note tone="muted">
        No ingredient on this run had more than one supplier product on file, so there was no
        sourcing choice to make.
      </Note>
    )
  }

  return (
    <div>
      <p className="max-w-[86ch] text-[0.875rem] text-ink-2">
        {switched === 0 ? (
          <>
            Every line stayed with its preferred supplier. {choices.length}{' '}
            {plural(choices.length, 'ingredient')} had an alternate on file and none of them was
            materially cheaper.
          </>
        ) : (
          <>
            {switched} of {choices.length} {plural(choices.length, 'line')} moved off the
            preferred supplier. The saving that justified each move is in its own sentence — it
            is computed per unit, so it is not a figure this screen can restate without
            re-deriving it.
          </>
        )}
        {forgone > 0 && (
          <>
            {' '}
            {forgone} {plural(forgone, 'line')} stayed put despite a cheaper option, and what
            that cost is shown below.
          </>
        )}
      </p>

      <ul className="mt-4 grid gap-3 xl:grid-cols-2">
        {ordered.map((c) => (
          <li key={c.ingredient_id} className="min-w-0">
            <Panel
              tone={c.chosen_is_preferred ? 'muted' : 'info'}
              title={
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-ink">{c.ingredient_name}</span>
                  {c.chosen_is_preferred ? (
                    <Badge tone="muted">kept preferred</Badge>
                  ) : (
                    <Badge tone="info">switched</Badge>
                  )}
                </span>
              }
              right={<>&rarr; {c.chosen_supplier_name ?? 'supplier unnamed'}</>}
            >
              <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
                <Field label="Chosen unit price">
                  <MExact v={c.chosen_unit_price_pence} missing="no price on file" />
                </Field>
                <Field label="Alternates on file">
                  <span className="fig text-[0.875rem] text-ink">{c.alternative_count}</span>
                </Field>
                <Field
                  label="Saving forgone"
                  tone={c.cheaper_rejected_supplier_id !== null ? 'warn' : undefined}
                >
                  {/* Null is not zero: nothing was given up, so there is no
                      figure and the reason reads in its place. */}
                  {c.cheaper_rejected_supplier_id === null ? (
                    <MExact v={null} missing="no cheaper option rejected" />
                  ) : (
                    <MExact v={c.forgone_saving_pence} tone="warn" missing="not priced" />
                  )}
                </Field>
              </div>

              <p className="mt-3">{prose(c.reason)}</p>
            </Panel>
          </li>
        ))}
      </ul>
    </div>
  )
}
