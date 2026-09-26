/**
 * "Needs attention": the top of the Stock tab. One line per kind of job, each
 * a count, the first few names and what to do about it; clicking a line filters
 * the list to exactly those rows. Everything is derived from the server's rows
 * with the same `matches()` the filter chips use, so the count on a line and the
 * rows it opens can never disagree.
 */
import { cx } from '../../components/ui'
import { gbp } from '../../lib/format'
import type { StockRow, StockSummary } from '../../lib/types/stock'
import { compareRows, matches } from './model'
import type { StockFilter } from './model'

interface Job {
  id: StockFilter
  label: string
  todo: string
  alert: boolean
  extra?: string
}

export function Attention({
  rows,
  summary,
  active,
  onPick,
  onBuy,
}: {
  rows: StockRow[]
  summary: StockSummary | undefined
  active: StockFilter
  onPick: (f: StockFilter) => void
  onBuy: () => void
}) {
  const expiring = summary?.expiring_value_pence
  const jobs: Job[] = [
    { id: 'out', label: 'Running out', todo: 'within a week — check they are on the buy list', alert: true },
    {
      id: 'soon',
      label: 'Use soon',
      todo: 'a batch goes out of date in 3 days — use it first or write it off',
      alert: true,
      extra: expiring ? `${gbp(expiring)} at risk` : undefined,
    },
    { id: 'count', label: 'Count these', todo: 'not trusted yet — a count makes the estimate usable', alert: false },
    { id: 'drift', label: 'Drifting', todo: 'estimate and counts disagree — recount, then check the recipe', alert: false },
    { id: 'low', label: 'Marked low', todo: 'on the checklist — buy by eye', alert: false },
  ]
  const lines = jobs
    .map((j) => ({ job: j, hits: rows.filter((r) => matches(r, j.id)).sort(compareRows) }))
    .filter((l) => l.hits.length > 0)

  if (lines.length === 0) {
    return (
      <p className="border-b border-line-soft px-4 py-3 text-base text-ink-2 sm:px-5">
        Nothing needs attention: nothing running out, nothing going off, every tracked item trusted.
      </p>
    )
  }

  return (
    <section aria-label="Needs attention" className="flex-none border-b border-line-soft px-4 py-3 sm:px-5">
      <div className="mb-1.5 flex items-baseline gap-3">
        <h2 className="text-label font-bold uppercase tracking-[.08em] text-ink-3">Needs attention</h2>
        <button type="button" onClick={onBuy} className="ml-auto text-sm font-semibold text-brand-ink underline-offset-2 hover:underline">
          What to buy →
        </button>
      </div>
      <ul className="grid grid-cols-1 gap-x-6 compact:grid-cols-2">
        {lines.map(({ job, hits }) => {
          const on = active === job.id
          const names = hits.slice(0, 3).map((r) => r.name)
          const more = hits.length - names.length
          return (
            <li key={job.id}>
              <button
                type="button"
                aria-pressed={on}
                onClick={() => onPick(on ? 'all' : job.id)}
                className={cx(
                  'flex w-full min-w-0 items-baseline gap-2.5 rounded-control px-2 py-1.5 text-left text-base transition-[background-color]',
                  on ? 'bg-brand-wash' : 'hover:bg-canvas-2',
                )}
              >
                <span
                  className={cx(
                    'fig w-7 flex-none text-right text-md font-extrabold',
                    job.alert ? 'text-bad-ink' : 'text-ink',
                  )}
                >
                  {hits.length}
                </span>
                <span className="min-w-0 flex-1">
                  <span className={cx('font-bold', on ? 'text-brand-ink' : 'text-ink')}>{job.label}</span>
                  {job.extra && <span className="fig ml-1.5 text-sm font-semibold text-bad-ink">{job.extra}</span>}
                  <span className="block truncate text-sm text-ink-2">
                    {names.join(', ')}
                    {more > 0 ? ` +${more}` : ''} · {job.todo}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
