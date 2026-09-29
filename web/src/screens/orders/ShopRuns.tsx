/**
 * Shop runs (§2.4): every time stock was bought at a shop because a delivery
 * would come too late. From the routings logged here and the expenses log.
 *
 * A run with no amount recorded is never counted as £0 (invariant 8): its
 * month's bar is drawn as a dashed outline, and the headline total says how
 * much of it is priced.
 */
import { useQuery } from '@tanstack/react-query'
import { Bars, Empty, ErrorBox, Loading, TBody, THead, Table, Td, Th, Tr } from '../../components/ui'
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

/** Rows shown in the table; the rest are summarised, never silently dropped. */
const SHOWN = 40

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
          <div className="max-w-[900px]">
            <Table label="Shop runs, newest first" minWidth={560}>
              <THead>
                <tr>
                  <Th width={110}>Date</Th>
                  <Th>Where</Th>
                  <Th>Why / note</Th>
                  <Th numeric width={90}>
                    Paid
                  </Th>
                </tr>
              </THead>
              <TBody>
                {d.runs.slice(0, SHOWN).map((r, i) => (
                  <Tr key={`${r.occurred_at}-${i}`}>
                    <Td className="whitespace-nowrap">{dayMonth(r.occurred_at)}</Td>
                    <Td>
                      {r.where}
                      {r.ingredient_name && <span className="text-ink-2"> · {r.ingredient_name}</span>}
                    </Td>
                    {/* The note wraps rather than truncating behind a hover-only title. */}
                    <Td className="text-ink-2">{r.note}</Td>
                    <Td numeric>{r.amount_pence === null ? <span className="text-sm text-ink-2">not recorded</span> : gbp(r.amount_pence)}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
            {d.runs.length > SHOWN && (
              <p className="mt-2 text-sm text-ink-2">
                Showing the latest {SHOWN}; {d.runs.length - SHOWN} older {plural(d.runs.length - SHOWN, 'run')} are counted in the
                total and the chart above.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}
