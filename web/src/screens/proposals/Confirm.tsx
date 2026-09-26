/**
 * Confirming one proposal, and reporting what the confirmation did.
 *
 * Two decisions, deliberately two controls:
 *
 *  1. **Who is confirming.** `actor` is not telemetry. Where a conflict is
 *     accepted the backend writes the name into the warning it stores against
 *     the result — "2 conflict(s) ACCEPTED by <actor>" — so the person who chose
 *     an arbitrary quantity is recoverable months later when the milk figures
 *     look wrong.
 *  2. **Whether to accept the lowest quantity.** A separate, explicit opt-in,
 *     never a side effect of pressing the button. One checkbox covering both
 *     would mean typing a name accepted a quantity choice, and those are not the
 *     same consent.
 *
 * The consequence is printed before the click (which quantity, at which size,
 * displacing what) and again after it (`accepted_conflicts`, and the backend's
 * own warning). A write that took an arbitrary quantity is never rendered as a
 * clean success: it is headed as the decision it was, in amber, because the
 * quantities it wrote still need checking against the real recipes.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { LIVE, materialiseProposal } from '../../lib/api'
import type { WriteResult } from '../../lib/api'
import type { MaterialiseResponse, Proposal } from '../../lib/types'
import { plural } from '../../lib/format'
import { Badge, Button, Chip, Panel } from '../../components/ui'
import { Field, TextInput, OptIn } from '../../components/confirm/fields'
import { Outcome } from '../../components/confirm/outcome'
import { Fig, Label } from '../../components/prim'
import { AcceptedLines } from './Conflicts'
import { isHollow, prose, type ConfirmState } from './shape'

/**
 * The opt-in. The kit has no checkbox — until this screen nothing in the app
 * asked for consent to a consequence — so it is built from the same tokens as
 * `components/confirm/fields.tsx` and reported as a missing primitive.
 */

function Figures({ r }: { r: MaterialiseResponse }) {
  const rows: { label: string; value: React.ReactNode }[] = [
    {
      label: 'Template',
      value:
        r.template_id === null ? (
          <Fig missing="no id returned" />
        ) : (
          <Fig weight="semibold">#{r.template_id}</Fig>
        ),
    },
    { label: 'Sizes', value: <Fig weight="semibold">{r.sizes.join(' / ') || '—'}</Fig> },
    {
      label: 'Components',
      value: (
        <span>
          <Fig weight="semibold">{r.components}</Fig>{' '}
          <Label>incl. {r.axis_filled_slots} axis-filled</Label>
        </span>
      ),
    },
    {
      label: 'Axes',
      value: (
        <span>
          <Fig weight="semibold">{r.axes}</Fig>{' '}
          <Label>
            with {r.options} {plural(r.options, 'option')}
          </Label>
        </span>
      ),
    },
    { label: 'Menu items re-pointed', value: <Fig weight="semibold">{r.items_repointed}</Fig> },
    { label: 'Manual lines closed', value: <Fig weight="semibold">{r.manual_lines_closed}</Fig> },
    {
      label: 'Conflicts accepted',
      value: (
        <Fig weight="semibold" tone={r.accepted_conflicts > 0 ? 'warn' : 'plain'}>
          {r.accepted_conflicts}
        </Fig>
      ),
    },
  ]
  return (
    <dl className="mt-3 grid grid-cols-[minmax(0,1fr)] gap-x-5 gap-y-2.5 [&>*]:min-w-0 sm:grid-cols-2 lg:grid-cols-3">
      {rows.map((row) => (
        <div key={row.label} className="min-w-0">
          <dt className="text-[0.6875rem] text-ink-4">{row.label}</dt>
          <dd className="mt-0.5">{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function NameList({
  title,
  why,
  names,
}: {
  title: string
  why: string
  names: readonly string[]
}) {
  if (names.length === 0) return null
  return (
    <div>
      <div className="text-[0.75rem] font-medium text-ink-2">
        {title} ({names.length})
      </div>
      <p className="mt-0.5 text-[0.75rem] leading-[16px] text-ink-4">{why}</p>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {names.map((n) => (
          <Chip key={n} tone="muted">
            {n}
          </Chip>
        ))}
      </div>
    </div>
  )
}

/**
 * What the write did. `summary` and every warning are the backend's sentences,
 * printed as written; the lists are rendered in full because the warnings that
 * describe them truncate at six names.
 */
export function Written({ r }: { r: MaterialiseResponse }) {
  const arbitrary = r.accepted_conflicts > 0
  return (
    <div className="grid gap-3">
      <Panel
        tone={arbitrary ? 'warn' : 'ok'}
        title={
          arbitrary
            ? 'Written — with an arbitrary quantity recorded'
            : 'Written. This is a template now.'
        }
        right={<Badge tone={arbitrary ? 'warn' : 'ok'}>composition changed</Badge>}
      >
        <p className="max-w-[95ch]">{prose(r.summary)}</p>
        {arbitrary && (
          <p className="mt-2 max-w-[95ch] text-warn-ink">
            {r.accepted_conflicts} {plural(r.accepted_conflicts, 'disagreement')} were settled by
            taking the lowest quantity, which is arbitrary by construction. Those quantities need
            checking against the real recipes before the costs they produce are trusted.
          </p>
        )}
        <Figures r={r} />
      </Panel>

      {r.warnings.length > 0 && (
        <div className="grid gap-1">
          <Label>the write&rsquo;s own words</Label>
          {r.warnings.map((w) => (
            <p key={w} className="max-w-[95ch] text-[0.75rem] leading-[16px] text-warn-ink">
              {prose(w)}
            </p>
          ))}
        </div>
      )}

      {(r.items_unresolved_option.length > 0 ||
        r.missing_ingredients.length > 0 ||
        r.items_skipped_other_template.length > 0 ||
        r.items_not_found.length > 0) && (
        <div className="grid gap-4 rounded-control border border-line bg-sunk p-3">
          <NameList
            title="Items with no variant option matched"
            why="Re-pointed, but nothing was chosen in that axis, so they resolve without it."
            names={r.items_unresolved_option}
          />
          <NameList
            title="Ingredient names not in the ingredient table"
            why="Those slots were skipped rather than invented, so the template is missing them."
            names={r.missing_ingredients}
          />
          <NameList
            title="Items left on another template"
            why="Never stolen: re-pointing would change what their past sales are deemed to have consumed (invariant 3)."
            names={r.items_skipped_other_template}
          />
          <NameList
            title="Proposal rows with no menu item"
            why="The (item, size) pair is in the legacy staging but has no menu_item row to re-point."
            names={r.items_not_found}
          />
        </div>
      )}
    </div>
  )
}

export function ConfirmForm({
  p,
  state,
  onWritten,
}: {
  p: Proposal
  state: ConfirmState
  onWritten: (r: MaterialiseResponse) => void
}) {
  const qc = useQueryClient()
  const [actor, setActor] = useState('')
  const [accept, setAccept] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actorError, setActorError] = useState<string | null>(null)
  const [acceptError, setAcceptError] = useState<string | null>(null)
  const [result, setResult] = useState<WriteResult<MaterialiseResponse> | null>(null)

  const id = `mat-${p.name.replace(/[^a-z0-9]+/gi, '-')}-${p.menu_item_count}`
  const conflicted = state.kind === 'conflicts'
  const hollow = isHollow(p)

  async function submit(): Promise<void> {
    if (busy) return
    const name = actor.trim()
    let stop = false
    if (name === '') {
      setActorError(
        'Required. The write is recorded against a person, and where a conflict is accepted the backend names them in the warning it stores.',
      )
      stop = true
    } else setActorError(null)
    if (conflicted && !accept) {
      setAcceptError(
        'This proposal cannot be confirmed as it stands. Either accept the lowest quantities above, or settle the disagreement in the workbook first.',
      )
      stop = true
    } else setAcceptError(null)
    setResult(null)
    if (stop) return

    setBusy(true)
    // By id, never by name: two proposals share a name, and both are strings, so
    // passing the wrong one type-checks and writes the wrong recipe.
    const r = await materialiseProposal(p.proposal_id, { actor: name, allow_conflicts: accept })
    setBusy(false)
    setResult(r)
    if (r.kind === 'ok') {
      onWritten(r.data)
      void qc.invalidateQueries({ queryKey: ['proposals'] })
    }
  }

  return (
    <div className="grid gap-3 border-t border-line pt-4">
      <div>
        <div className="text-[0.875rem] font-medium text-ink">Confirm this pattern</div>
        <p className="mt-1 max-w-[80ch] text-[0.75rem] leading-[16px] text-ink-4">
          Writing it creates the template, its sizes, components, axes and options, re-points{' '}
          {p.menu_item_count} menu {plural(p.menu_item_count, 'item')} at it and closes their
          manual recipe lines — from today, never backdated. One transaction; there is no
          half-written outcome.
        </p>
      </div>

      {hollow && (
        <Panel tone="warn" title="Nothing was detected to put in this template">
          Detection found no shared component and no axis for this group. Confirming it would
          create an empty template and take {p.menu_item_count} menu{' '}
          {plural(p.menu_item_count, 'item')} off the manual recipes they currently resolve
          through, leaving them resolving to no ingredients at all. A one-off is better left as a
          manual recipe.
        </Panel>
      )}

      {conflicted && (
        <div>
          <OptIn id={`${id}-accept`} checked={accept} onChange={setAccept} disabled={!LIVE || busy}>
            <span className="block text-[0.875rem] leading-[16px] text-ink">
              Accept the lowest quantity at {state.count === 1 ? 'the' : 'each of the'}{' '}
              {state.count === 1 ? '' : `${state.count} `}
              {plural(state.count, 'disagreement')}
            </span>
            <span className="mt-1 block text-[0.75rem] leading-[16px] text-ink-3">
              The choice is arbitrary by construction — the legacy rows disagreed and the lowest
              is simply the lowest. It is recorded against your name, and the resulting quantities
              have to be verified against the real recipes.
            </span>
            <AcceptedLines conflicts={p.conflicts} />
          </OptIn>
          {acceptError !== null && (
            <p role="alert" className="mt-1.5 text-[0.75rem] leading-[16px] text-bad-ink">
              {acceptError}
            </p>
          )}
        </div>
      )}

      <div className="max-w-[24rem]">
        <Field
          id={`${id}-actor`}
          label="Who is confirming"
          hint="A name, not a role. It is stored with the write and quoted in any warning it produces."
          error={actorError}
        >
          <TextInput
            id={`${id}-actor`}
            value={actor}
            onChange={setActor}
            placeholder="e.g. Sasha"
            disabled={!LIVE || busy}
            invalid={actorError !== null}
            hint
          />
        </Field>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant={conflicted ? 'default' : 'primary'}
          onClick={() => void submit()}
          disabled={!LIVE || busy}
        >
          {busy ? 'Writing…' : 'Confirm and write the template'}
        </Button>
        {!LIVE && <Label>fixtures — nothing to write to</Label>}
        {LIVE && <Label>nothing is written until you press it</Label>}
      </div>

      <Outcome result={result} />
    </div>
  )
}
