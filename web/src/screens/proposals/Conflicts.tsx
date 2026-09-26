/**
 * The disagreements. This is the screen's reason to exist.
 *
 * Where two legacy rows in the same group use different quantities for the same
 * ingredient at the same size, detection records the disagreement instead of
 * averaging it away — "27 items differ in Whole milk qty at size M (0.18,
 * 0.24)" is two real sub-patterns (iced and hot) that the workbook lumped into
 * one name. The sentence is the backend's and is printed as written; what this
 * adds is the thing the sentence cannot carry: which items are on each side.
 *
 * The quantity `allow_conflicts` would write is marked before anybody opts in,
 * because "accept the lowest" is only an informed decision if you can see what
 * the lowest is and what it displaces. 0.18 written into 27 drinks that include
 * 13 hot lattes made with 0.24 is a 25% understatement of milk consumption on
 * every one of them, and the drift screen would eventually say so — from the
 * wrong end.
 */
import { Chip, Panel } from '../../components/ui'
import { Label, Qty } from '../../components/prim'
import { plural } from '../../lib/format'
import type { ProposalConflict } from '../../lib/types'
import {
  acceptedQty,
  conflictWhere,
  groupQuantities,
  prose,
  roleLabel,
  type QtyGroup,
} from './shape'

/** Item names as chips, with the tail behind a disclosure so a 27-item side
 *  still reads as a side rather than a paragraph. */
function ItemChips({ items, limit = 8 }: { items: readonly string[]; limit?: number }) {
  const head = items.slice(0, limit)
  const tail = items.slice(limit)
  return (
    <div className="mt-2">
      <div className="flex flex-wrap gap-1">
        {head.map((n) => (
          <Chip key={n} tone="muted">
            {n}
          </Chip>
        ))}
      </div>
      {tail.length > 0 && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[0.6875rem] text-ink-4 hover:text-ink-2">
            and {tail.length} more
          </summary>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {tail.map((n) => (
              <Chip key={n} tone="muted">
                {n}
              </Chip>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

function Side({ g, ordered, settled }: { g: QtyGroup; ordered: boolean; settled: boolean }) {
  return (
    <div
      className={`rounded-control border px-3 py-2.5 ${
        g.lowest ? 'border-warn/30 bg-warn-wash' : 'border-line bg-sunk'
      }`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
        <Qty value={g.qty} size="lg" weight="semibold" tone={g.lowest ? 'warn' : 'plain'} />
        {ordered && (
          <Label tone={g.lowest ? 'warn' : 'muted'}>
            {g.lowest ? (settled ? 'the one written' : 'the one an opt-in writes') : 'dropped'}
          </Label>
        )}
      </div>
      <Label>
        {g.items.length} {plural(g.items.length, 'item')} use{g.items.length === 1 ? 's' : ''} it
      </Label>
      <ItemChips items={g.items} />
    </div>
  )
}

function One({ c, settled }: { c: ProposalConflict; settled: boolean }) {
  const { groups, ordered } = groupQuantities(c)
  const items = Object.keys(c.quantities).length
  return (
    <div className="grid gap-3">
      <Panel
        tone="warn"
        title={conflictWhere(c)}
        right={`${roleLabel(c.role)} · ${items} ${plural(items, 'item')}`}
      >
        {prose(c.describe)}
      </Panel>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3 [&>*]:min-w-0 sm:grid-cols-[repeat(auto-fit,minmax(15rem,1fr))]">
        {groups.map((g) => (
          <Side key={g.qty} g={g} ordered={ordered} settled={settled} />
        ))}
      </div>
      {!ordered && (
        <p className="text-[0.75rem] leading-[16px] text-warn-ink">
          One of these quantities is not an exact decimal, so which one counts as the lowest
          cannot be read off the payload. Nothing is marked, and this proposal should be settled
          against the workbook rather than confirmed from here.
        </p>
      )}
    </div>
  )
}

/** Every disagreement in one proposal, in full. */
export function Conflicts({
  conflicts,
  settled = false,
}: {
  conflicts: readonly ProposalConflict[]
  /** The proposal is already a template, so the lowest was taken, not "would be". */
  settled?: boolean
}) {
  if (conflicts.length === 0) return null
  return (
    <div className="grid gap-5">
      {conflicts.map((c, i) => (
        <One key={`${c.role}-${c.ingredient_name}-${c.size_code ?? 'all'}-${i}`} c={c} settled={settled} />
      ))}
    </div>
  )
}

/**
 * The consequence of opting in, spelled out per disagreement and above the
 * checkbox rather than after the write. `acceptedQty` is exactly what the
 * backend takes: the lowest, per size, independently.
 */
export function AcceptedLines({ conflicts }: { conflicts: readonly ProposalConflict[] }) {
  return (
    <ul className="mt-1.5 grid gap-1">
      {conflicts.map((c, i) => {
        const q = acceptedQty(c)
        return (
          <li
            key={`${c.ingredient_name}-${c.size_code ?? 'all'}-${i}`}
            className="text-[0.75rem] leading-[16px] text-ink-3"
          >
            {conflictWhere(c)} would be written as{' '}
            {q === null ? (
              <span className="text-warn-ink">
                an unreadable quantity — settle this one against the workbook instead
              </span>
            ) : (
              <Qty value={q} size="sm" weight="medium" tone="warn" />
            )}
          </li>
        )
      })}
    </ul>
  )
}
