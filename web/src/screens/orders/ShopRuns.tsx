/**
 * Shop runs (§2.4): every time stock was bought at a shop because a delivery
 * would come too late. From the routings logged here and the expenses log.
 *
 * A run with no amount recorded is never counted as £0 (invariant 8): its
 * month's bar is drawn as a dashed outline, and the headline total says how
 * much of it is priced.
 */
import { useQuery } from '@tanstack/react-query'
import { Bars, Empty, ErrorBox, Loading, cx } from '../../components/ui'
import { gbp, plural } from '../../lib/format'
import { KEYS, stockApi } from '../../lib/stock-api'
import { dayMonth } from '../stock/fmt'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function poundsNoPence(p: number): string {
  // "£123" with no pence, as the design labels its bars. Integer pence in,
  // integer arithmetic only.
  const whole = (BigInt(p) + 50n) / 100n
  return `£${whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`
}

const GRID = 'grid grid-cols-[110px_minmax(0,1.6fr)_minmax(0,2fr)_90px] gap-2.5'

export function ShopRuns() {
  const q = useQuery({ queryKey: KEYS.shopRuns, queryFn: stockApi.shopRuns, staleTime: 60_000 })
  if (q.isPending) return <Loading what="Reading shop runs" />
  if (q.isError) return <ErrorBox error={q.error} what="shop runs" />
  const d = q.data
  const n = d.runs_count
  const headline =
    d.total_pence !== null
      ? `${n} shop ${plural(n, 'run')}, ${gbp(d.total_pence)} in total`
      : `${n} shop ${plural(n, 'run')}, ${gbp(d.priced_total_pence)} recorded across ${d.priced_runs} of them`

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-2xl font-extrabold tracking-[-.01em]">{headline}</h2>
      <p className="max-w-[760px] text-base text-ink-2">
        Every time stock is bought at a supermarket or corner shop because a delivery would come too late. Found in the
        expenses log plus shop runs logged here. The count is the case for fixing delivery days: retail almost always
        costs more than the supplier price.
      </p>
      {d.notes.map((note) => (
        <p key={note} className="max-w-[760px] text-sm text-ink-2">
          {note}
        </p>
      ))}
      {n === 0 ? (
        <Empty>No shop runs logged yet.</Empty>
      ) : (
        <>
          <div className="max-w-[760px]">
            <Bars
              label="Shop runs by month"
              height={120}
              bars={d.by_month.map((m) => ({
                key: m.month,
                label: MONTHS[Number(m.month.slice(5, 7)) - 1] ?? m.month,
                // null = a month containing an unpriced run: drawn dashed, never as zero.
                value: m.amount_pence,
                valueLabel: m.amount_pence === null ? `${poundsNoPence(m.priced_amount_pence)}+` : poundsNoPence(m.amount_pence),
              }))}
            />
          </div>
          <div className="scroll-x relative max-w-[900px]">
            <div className="min-w-[560px]">
              <div className={cx(GRID, 'border-b border-line py-1.5 text-sm text-ink-2')}>
                <span>Date</span>
                <span>Where</span>
                <span>Why / note</span>
                <span className="text-right">£</span>
              </div>
              {d.runs.slice(0, 40).map((r, i) => (
                <div key={`${r.occurred_at}-${i}`} className={cx(GRID, 'border-b border-line py-[7px] text-base')}>
                  <span>{dayMonth(r.occurred_at)}</span>
                  <span className="truncate">
                    {r.where}
                    {r.ingredient_name && <span className="text-ink-2"> · {r.ingredient_name}</span>}
                  </span>
                  <span className="truncate text-ink-2" title={r.note}>
                    {r.note}
                  </span>
                  <span className="fig text-right">
                    {r.amount_pence === null ? <span className="text-sm text-ink-2">not recorded</span> : gbp(r.amount_pence)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
