/**
 * "Needs attention" as its own Stock tab (owner, 2026-09-26: "moved as a
 * separate screen next to On the shelf, What to buy … sorted, clear and
 * scannable").
 *
 * One card per job, most urgent first. Each card says how many, what to do in
 * one line, and carries the one action that does it (count them, see the buy
 * list); its rows show only the figure that job is about, and open the
 * ingredient's page. A jump strip at the top lists the jobs with their counts.
 *
 * Rows come from the same `matches()` the On-the-shelf chips use, so a count
 * here and the list it filters can never disagree. Estimates stay italic,
 * counts upright and dated (invariant 6); an unpriced batch says so (8).
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Button, TierBadge, cx } from '../../components/ui'
import { gbp, plural } from '../../lib/format'
import { href } from '../../lib/router'
import type { StockRow, StockSummary } from '../../lib/types/stock'
import { StockTrust } from './StockList'
import { dayMonth, driftShown, fmtQ } from './fmt'
import { compareRows, lastCountCell, leftCell, matches, runsOutCell, soonestLiveBatch } from './model'
import type { StockFilter } from './model'

type JobId = Exclude<StockFilter, 'all' | 'check'>

interface Job {
  id: JobId
  title: string
  todo: string
  urgent: boolean
}

const JOBS: readonly Job[] = [
  { id: 'soon', title: 'Use soon', todo: 'A batch goes out of date within 3 days. Use it first, or write it off on its page.', urgent: true },
  { id: 'out', title: 'Running out', todo: 'Runs out within a week. Check it is on the buy list.', urgent: true },
  { id: 'drift', title: 'Drifting', todo: 'The estimate and the counts disagree. Recount, then check the recipe.', urgent: false },
  { id: 'count', title: 'Count these', todo: 'Not trusted yet. One count makes the estimate usable.', urgent: false },
  { id: 'low', title: 'Marked low', todo: 'On the checklist and marked low. Buy by eye.', urgent: false },
]

const PREVIEW = 8

export function NeedsAttention({
  rows,
  summary,
  onCount,
  onBuy,
  onShowInList,
}: {
  rows: StockRow[]
  summary: StockSummary | undefined
  onCount: (ids: number[]) => void
  onBuy: () => void
  onShowInList: (f: StockFilter) => void
}) {
  const lines = JOBS.map((job) => ({ job, hits: rows.filter((r) => matches(r, job.id)).sort(compareRows) })).filter(
    (l) => l.hits.length > 0,
  )

  if (lines.length === 0) {
    return (
      <div className="flex-1 bg-canvas px-4 py-6 sm:px-5">
        <div className="rounded-card-lg bg-surface p-5 text-base text-ink-2 shadow-raised">
          Nothing needs attention: nothing running out, nothing going off, every tracked item trusted.
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-canvas">
      <nav
        aria-label="Jump to"
        className="sticky top-0 z-10 flex flex-wrap gap-1.5 border-b border-line-soft bg-canvas px-4 py-2.5 sm:px-5"
      >
        {lines.map(({ job, hits }) => (
          <a
            key={job.id}
            href={`#att-${job.id}`}
            onClick={(e) => {
              // Hash routing owns location.hash: scroll instead of navigating.
              // Smooth only when motion is welcome; focus follows so a keyboard
              // user lands on the card they jumped to.
              e.preventDefault()
              const el = document.getElementById(`att-${job.id}`)
              if (!el) return
              const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
              el.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' })
              el.focus({ preventScroll: true })
            }}
            className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line-control bg-surface px-3 text-base text-ink no-underline hover:bg-canvas-2"
          >
            <span className={cx('fig font-extrabold', job.urgent ? 'text-bad-ink' : 'text-ink')}>{hits.length}</span>
            {job.title}
          </a>
        ))}
      </nav>
      <div className="flex flex-col gap-4 px-4 pb-10 pt-4 sm:px-5">
        {lines.map(({ job, hits }) => (
          <JobCard
            key={job.id}
            job={job}
            hits={hits}
            extra={job.id === 'soon' ? atRisk(summary) : undefined}
            action={
              job.id === 'count' || job.id === 'drift' ? (
                <Button variant="primary" size="sm" onClick={() => onCount(hits.map((r) => r.ingredient_id))}>
                  {job.id === 'drift' ? 'Recount' : 'Count'} {hits.length === 1 ? 'it' : `these ${hits.length}`}
                </Button>
              ) : job.id === 'out' || job.id === 'low' ? (
                <Button variant="primary" size="sm" onClick={onBuy}>
                  See what to buy
                </Button>
              ) : undefined
            }
            onShowInList={() => onShowInList(job.id)}
          />
        ))}
      </div>
    </div>
  )
}

function atRisk(summary: StockSummary | undefined): ReactNode {
  const v = summary?.expiring_value_pence
  if (v === undefined) return undefined
  return v === null ? 'value unknown (unpriced batch)' : <span className="fig">{gbp(v)} at risk</span>
}

function JobCard({
  job,
  hits,
  extra,
  action,
  onShowInList,
}: {
  job: Job
  hits: StockRow[]
  extra?: ReactNode
  action?: ReactNode
  onShowInList: () => void
}) {
  const [all, setAll] = useState(false)
  const shown = all ? hits : hits.slice(0, PREVIEW)
  return (
    <section
      id={`att-${job.id}`}
      tabIndex={-1}
      aria-labelledby={`att-h-${job.id}`}
      className="scroll-mt-14 overflow-hidden rounded-card-lg bg-surface shadow-raised focus:outline-none"
    >
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-3">
        <span className={cx('fig w-10 text-3xl font-extrabold', job.urgent ? 'text-bad-ink' : 'text-ink')}>{hits.length}</span>
        <div className="min-w-0 flex-1">
          <h2 id={`att-h-${job.id}`} className="text-lg font-extrabold">
            {job.title}
            {extra && <span className="ml-2 text-base font-bold text-bad-ink">{extra}</span>}
          </h2>
          <p className="text-sm text-ink-2">{job.todo}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onShowInList}>
            Show in list
          </Button>
          {action}
        </div>
      </header>
      <ul>
        {shown.map((r) => (
          <li key={r.ingredient_id} className="border-b border-line-row last:border-b-0">
            <a
              href={href(`/stock/${r.ingredient_id}`)}
              className="grid grid-cols-[44px_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2.5 text-ink no-underline hover:bg-canvas-2"
            >
              <span className="grid size-11 place-items-center rounded-control bg-line-soft">
                <TierBadge tier={r.tier} />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-md font-bold">{r.name}</span>
                <span className="block truncate text-sm text-ink-2">
                  {[r.category ?? 'Uncategorised', r.pack?.supplier_name].filter(Boolean).join(' · ')}
                </span>
                {job.id === 'out' && <Reason r={r} />}
              </span>
              <Figure job={job.id} r={r} />
            </a>
          </li>
        ))}
      </ul>
      {hits.length > PREVIEW && (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          className="w-full border-t border-line px-4 py-2 text-left text-sm font-bold text-brand-ink hover:bg-canvas-2"
        >
          {all ? 'Show fewer' : `Show all ${hits.length}`}
        </button>
      )}
    </section>
  )
}

/** Why there is no run-out date, in full: the sentence the forecast gave. */
function Reason({ r }: { r: StockRow }) {
  const ro = runsOutCell(r)
  if (!ro.reason) return null
  return <span className="block text-sm text-ink-2">{ro.title ?? ro.text}</span>
}

/** The one figure each job is about, right-aligned. */
function Figure({ job, r }: { job: JobId; r: StockRow }) {
  const cell = (main: ReactNode, sub?: ReactNode, alert = false) => (
    <span className="flex flex-col items-end text-right leading-tight">
      <span className={cx('fig text-base', alert && 'font-bold text-bad-ink')}>{main}</span>
      {sub && <span className="fig text-xs text-ink-2">{sub}</span>}
    </span>
  )
  switch (job) {
    case 'soon': {
      const b = soonestLiveBatch(r)
      if (!b) return cell('—')
      return cell(
        `use by ${dayMonth(b.effective_expiry ?? b.expires_at)}`,
        <>
          {fmtQ(b.qty_remaining, b.unit)} left · {b.days_left === 0 ? 'today' : `${b.days_left} ${plural(b.days_left ?? 0, 'day')}`}
          {b.value_pence === null ? ' · unpriced' : ` · ${gbp(b.value_pence)}`}
        </>,
        true,
      )
    }
    case 'out': {
      const ro = runsOutCell(r)
      // A withheld forecast says so where the date would be (invariant 9); the
      // reason itself is printed under the name (Reason), never only in a tooltip.
      const main = ro.reason ? 'no forecast yet' : ro.text ? `runs out ${ro.text}` : 'out now'
      return cell(main, <em>{leftCell(r)} left (est.)</em>, ro.alert)
    }
    case 'drift':
      return (
        <span className="flex items-center gap-3">
          <span className="fig text-base font-bold">{driftShown(r.drift.drift_pct)}</span>
          <StockTrust row={r} />
        </span>
      )
    case 'count':
      return cell(r.on_hand.has_count_basis ? `last count ${lastCountCell(r)}` : 'never counted', undefined)
    case 'low':
      return cell('marked low', r.checklist ? `by ${r.checklist.responded_by}, ${dayMonth(r.checklist.responded_at)}` : undefined)
  }
}
