/**
 * One proposal, closed by default and opened on demand.
 *
 * The closed state has to be enough to decide whether to open it: the group's
 * name, how many menu item rows it would collapse, whether the legacy rows
 * disagree, and the backend's own reason it cannot be confirmed as it stands.
 * None of that is behind the disclosure, because a screen that hides the word
 * "conflict" until you click is a screen that gets clicked past.
 *
 * Opened, the order is the order of the decision: the disagreements first, then
 * what the template would contain, then the items it would replace, then the
 * form. Confirming is at the bottom because it is the last thing you should do.
 */
import { Badge, Card, Chip, Panel } from '../../components/ui'
import { Fig } from '../../components/prim'
import { plural } from '../../lib/format'
import type { MaterialiseResponse, Proposal } from '../../lib/types'
import { Axes, BaseItems, ComponentGrid } from './Detection'
import { Conflicts } from './Conflicts'
import { ConfirmForm, Written } from './Confirm'
import { isHollow, orderSizes, prose, type ConfirmState } from './shape'

function Sub({ children, note }: { children: React.ReactNode; note?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <h3 className="text-[0.75rem] font-semibold text-ink-3">{children}</h3>
      {note !== undefined && <span className="text-[0.6875rem] text-ink-4">{note}</span>}
    </div>
  )
}

function Summary({ p }: { p: Proposal }) {
  const options = p.axes.reduce((n, a) => n + Object.keys(a.options).length, 0)
  const bits: string[] = [
    `${p.base_item_names.length} base ${plural(p.base_item_names.length, 'item')}`,
    `sizes ${orderSizes(p.sizes).join('/')}`,
    `${p.components.length} ${plural(p.components.length, 'component')}`,
  ]
  if (p.axes.length > 0) {
    bits.push(
      `${p.axes.length} ${plural(p.axes.length, 'axis', 'axes')} with ${options} ${plural(
        options,
        'option',
      )}`,
    )
  }
  if (p.category !== null) bits.push(p.category)
  return <span>{bits.join(' · ')}</span>
}

export function ProposalCard({
  p,
  state,
  open,
  onToggle,
  written,
  onWritten,
}: {
  p: Proposal
  state: ConfirmState
  open: boolean
  onToggle: () => void
  /** The result of confirming it in this session, kept after the list refetches. */
  written: MaterialiseResponse | undefined
  onWritten: (r: MaterialiseResponse) => void
}) {
  const conflicts = p.conflicts.length
  const done = state.kind === 'materialised'
  const ambiguous = state.kind === 'ambiguous'
  const hollow = isHollow(p)

  return (
    <Card
      className={conflicts > 0 && !done ? 'border-warn/25' : undefined}
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span>{p.name}</span>
          {done && <Badge tone="ok">already a template</Badge>}
          {!done && conflicts > 0 && (
            <Badge tone="warn">
              {conflicts} {plural(conflicts, 'conflict')}
            </Badge>
          )}
          {!done && conflicts === 0 && !hollow && <Badge tone="muted">no disagreements</Badge>}
          {hollow && <Badge tone="warn">nothing detected</Badge>}
          {ambiguous && <Badge tone="bad">name shared</Badge>}
        </span>
      }
      subtitle={<Summary p={p} />}
      right={
        <div className="text-right">
          <Fig size="lg" weight="semibold">
            {p.menu_item_count}
          </Fig>
          <div className="text-[0.6875rem] text-ink-4">
            menu {plural(p.menu_item_count, 'item')}
          </div>
        </div>
      }
    >
      <div className="grid gap-4">
        {/* The backend's refusal, where it adds something the badge does not.
            Closed, the conflict sentences below carry the specifics and this
            would be the same boilerplate eleven times; once confirmed, the
            write's own report says all of it and more. */}
        {p.blocked_reason !== null && written === undefined && (open || done) && (
          <Panel
            tone={done ? 'muted' : 'warn'}
            title={done ? 'Confirmed already' : 'Refused as it stands'}
          >
            {prose(p.blocked_reason)}
          </Panel>
        )}

        {ambiguous && (
          <Panel tone="bad" title="Two proposals share this name">
            Confirming addresses a proposal by name, and detection produced two groups called{' '}
            <span className="fig">{p.name}</span> — this one covering {p.menu_item_count} menu{' '}
            {plural(p.menu_item_count, 'item')}. There is no way from this screen to say which of
            the two the write would take, so the button is withheld: guessing would attach the
            wrong recipe to real drinks. Settle it in the workbook, or materialise it from the CLI
            where the ambiguity is visible in the candidate list.
          </Panel>
        )}

        {written !== undefined && <Written r={written} />}

        <div>
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            className="text-[0.75rem] text-ink-3 underline decoration-dotted underline-offset-2 transition-colors hover:text-ink"
          >
            {open
              ? 'close this proposal'
              : done
                ? 'see what detection proposed'
                : conflicts > 0
                  ? `see the ${plural(conflicts, 'disagreement')} and what it would write`
                  : 'see what it would write'}
          </button>
        </div>

        {open && (
          <div className="grid gap-5">
            {conflicts > 0 && (
              <div className="grid gap-3">
                <Sub
                  note={
                    done
                      ? 'the lowest was written where this was accepted'
                      : 'the lowest is what an opt-in would write'
                  }
                >
                  Where the legacy rows disagree{done ? 'd' : ''}
                </Sub>
                <Conflicts conflicts={p.conflicts} settled={done} />
              </div>
            )}

            <div className="grid gap-2">
              <Sub note="quantities exactly as detected">
                {done ? 'What detection proposed' : 'The template it would create'}
              </Sub>
              <ComponentGrid p={p} />
            </div>

            {p.axes.length > 0 && (
              <div className="grid gap-2">
                <Sub note="one slot, many options">Variant axis</Sub>
                <Axes p={p} />
              </div>
            )}

            {p.base_item_names.length > 0 && (
              <div className="grid gap-2">
                <Sub
                  note={`${p.menu_item_count} item × size ${plural(p.menu_item_count, 'row')}`}
                >
                  Base items it {done ? 'covers' : 'would cover'}
                </Sub>
                <BaseItems names={p.base_item_names} />
              </div>
            )}

            {done ? (
              <div className="border-t border-line pt-4">
                <p className="max-w-[80ch] text-[0.75rem] leading-[16px] text-ink-4">
                  A template of this name exists, so confirming again is refused by the API. Edit
                  its components on the composition screen instead — that path is effective-dated
                  and shows an impact preview before it commits.
                </p>
              </div>
            ) : ambiguous ? null : (
              <ConfirmForm p={p} state={state} onWritten={onWritten} />
            )}
          </div>
        )}

        {!open && p.conflicts.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {p.conflicts.map((c, i) => (
              <Chip key={`${c.ingredient_name}-${c.size_code ?? 'all'}-${i}`} tone="warn">
                {prose(c.describe)}
              </Chip>
            ))}
          </div>
        )}
      </div>
    </Card>
  )
}
