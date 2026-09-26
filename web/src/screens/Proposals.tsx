/**
 * Import review. Spec §6, and the last screen in the §10 build order.
 *
 * The legacy workbook holds 314 item × size rows maintained by hand, and the
 * finding that drives the whole project is that they are not 314 recipes: they
 * are about twenty patterns multiplied out. Detection finds 27 of them, covering
 * 265 of those rows, and — this is the part that needed a screen — writes none of
 * them. Spec §6 is explicit that pattern detection proposes and a human confirms
 * in the UI before any template exists. Until now the only way to confirm one was
 * `cafeops materialise-template` at a shell prompt, which the person who owns the
 * café does not have.
 *
 * Three things this screen refuses to smooth over.
 *
 * **A conflict is the content, not an error.** Eleven of the 27 have legacy rows
 * that disagree about a quantity, and the detector records the disagreement
 * rather than averaging it. Those proposals are refused by default; confirming
 * one anyway takes the LOWEST quantity at each size, which is arbitrary by
 * construction. So the screen shows who disagrees and by how much, marks the
 * quantity that would win before anybody opts in, and reports the result in
 * amber afterwards — never as a clean success.
 *
 * **The 43 singletons are counted, not hidden.** A one-off group is not a
 * pattern, and a manual recipe is the right model for a cake or a bottled drink —
 * which is what they already are. "27 of 70 groups are patterns" is the honest
 * sentence; listing 27 and saying nothing is not.
 *
 * **Reading is safe and says so.** `writes_nothing` comes back on the read, and
 * on a screen whose whole purpose is a destructive-looking button it is worth a
 * line of type.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LIVE, api } from '../lib/api'
import type { MaterialiseResponse } from '../lib/types'
import { plural } from '../lib/format'
import {
  CopyBlock,
  Empty,
  ErrorBox,
  Loading,
  PageHeader,
  Panel,
  SectionLabel,
  StatCard,
  Tabs,
} from '../components/ui'
import { FixtureNotice } from '../components/confirm/outcome'
import { ProposalCard } from './proposals/ProposalCard'
import {
  confirmState,
  coveredBaseItems,
  coveredItems,
  duplicateNames,
  matchesFilter,
  type Filter,
} from './proposals/shape'

/** Bordered cards, never a bare `1fr`: a min-content floor on one figure pushes
 *  the whole page sideways instead of the block that is too wide. */
function CardRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 [&>*]:min-w-0 sm:grid-cols-2 lg:grid-cols-4">
      {children}
    </div>
  )
}

export function Proposals() {
  const q = useQuery({ queryKey: ['proposals'], queryFn: () => api.proposals() })
  const [filter, setFilter] = useState<Filter>('all')
  const [opened, setOpened] = useState<Record<string, boolean>>({})
  const [written, setWritten] = useState<Record<string, MaterialiseResponse>>({})

  if (q.isPending) {
    return (
      <>
        <PageHeader title="Import review" />
        <Loading what="Detecting patterns in the legacy rows" />
      </>
    )
  }
  if (q.isError || !q.data) {
    return (
      <>
        <PageHeader title="Import review" />
        <ErrorBox error={q.error} />
      </>
    )
  }

  const d = q.data
  const all = d.proposals
  const dupes = duplicateNames(all)
  const done = all.filter((p) => p.already_materialised).length
  const waiting = all.length - done
  const groups = d.total + d.singletons_excluded
  const items = coveredItems(all)
  const baseItems = coveredBaseItems(all)

  const counts: Record<Filter, number> = {
    all: all.length,
    conflicts: all.filter((p) => matchesFilter(p, 'conflicts')).length,
    clear: all.filter((p) => matchesFilter(p, 'clear')).length,
    done,
  }
  const shown = all.filter((p) => matchesFilter(p, filter))

  return (
    <>
      <PageHeader
        title="Import review"
        lede={
          <>
            Pattern detection proposes; nothing becomes a template until you confirm it here.
            {d.writes_nothing && ' Reading this screen writes nothing at all.'}
          </>
        }
      />

      {!LIVE && (
        <div className="mb-6">
          <FixtureNotice what="a confirmation" />
        </div>
      )}

      {all.length === 0 ? (
        <Empty title="No proposals, which means no staged legacy rows">
          <p>
            Detection runs over <span className="fig">legacy_staged_recipe</span>, and there is
            nothing in it. Import the workbook first — the import stages the flat rows and
            proposes patterns from them without writing a single template.
          </p>
          <div className="mt-3 text-left">
            <CopyBlock>uv run cafeops import-legacy --commit</CopyBlock>
          </div>
        </Empty>
      ) : (
        <>
          <CardRow>
            <StatCard
              label="Proposals waiting"
              value={waiting}
              sub={
                done === 0
                  ? `${d.total} detected, largest first; none confirmed yet`
                  : `${done} of ${d.total} already confirmed`
              }
            />
            <StatCard
              label="With disagreements"
              value={d.with_conflicts}
              tone={d.with_conflicts > 0 ? 'warn' : 'plain'}
              sub="the legacy rows contradict each other; refused until somebody decides"
            />
            <StatCard
              label="Menu items covered"
              value={items}
              sub={`${baseItems} base items, as ${d.total} ${plural(d.total, 'pattern')}`}
            />
            <StatCard
              label="Left as manual recipes"
              value={d.singletons_excluded}
              sub="one-off groups; a manual recipe is the right model for those"
            />
          </CardRow>

          <div className="mt-5">
            <Panel title="What is being proposed">
              <p className="max-w-[95ch]">
                {items} item × size {plural(items, 'row')} are maintained one at a time today, and
                detection says they are {d.total} {plural(d.total, 'pattern')} — {baseItems} base
                items multiplied out by size and flavour. Confirming one collapses its rows into a
                single template with a per-size component grid and a variant axis, and every cost
                in the app then recalculates from that instead of from the rows.
              </p>
              <p className="mt-2 max-w-[95ch]">
                {d.total} of {groups} groups are patterns. The other {d.singletons_excluded} cover
                one base item each, so there is no pattern to model and nothing to confirm: they
                stay manual recipes, which is what they already are and the right answer for a cake
                or a bottled drink.
              </p>
            </Panel>
          </div>

          <SectionLabel right={`${shown.length} of ${all.length} shown`}>
            The proposals
          </SectionLabel>

          <div className="mb-4 grid gap-2">
            <Tabs<Filter>
              tabs={[
                { key: 'all', label: 'All', count: counts.all },
                { key: 'conflicts', label: 'With disagreements', count: counts.conflicts },
                { key: 'clear', label: 'No disagreement', count: counts.clear },
                { key: 'done', label: 'Confirmed', count: counts.done },
              ]}
              active={filter}
              onChange={setFilter}
            />
            <p className="text-[0.6875rem] text-ink-4">
              Largest first, as the API sorts them — the biggest pattern is the one worth settling
              first.
            </p>
          </div>

          {shown.length === 0 ? (
            <Empty title="Nothing in this view">
              {filter === 'done'
                ? 'No proposal has been confirmed yet. Nothing here has written anything.'
                : filter === 'conflicts'
                  ? 'No proposal has a disagreement in it, which is the good case.'
                  : 'Every remaining proposal has a disagreement to settle first.'}
            </Empty>
          ) : (
            <div className="grid gap-4">
              {shown.map((p, i) => {
                const key = `${p.name}|${p.menu_item_count}|${i}`
                const isOpen = opened[key] ?? i === 0
                return (
                  <ProposalCard
                    key={key}
                    p={p}
                    state={confirmState(p, dupes.has(p.name))}
                    open={isOpen}
                    onToggle={() => setOpened((o) => ({ ...o, [key]: !isOpen }))}
                    written={written[p.name]}
                    onWritten={(r) => setWritten((w) => ({ ...w, [p.name]: r }))}
                  />
                )
              })}
            </div>
          )}
        </>
      )}
    </>
  )
}
