/**
 * Money → Sales: one row per trading day (finance.md 1.2).
 *
 * Every row is editable in place and commits on blur or Enter. A figure that
 * came from an export (CSV/POS) wins over anything typed and is read-only here.
 * Card figures up to March 2026 are bank deposits from the workbook, not till
 * takings; the caveat says so above the table.
 *
 * "Square cash" is "Till cash" (DECISIONS 4). "Own cash" is cash taken for
 * sales that were not rung on the till.
 */
import { useCallback, useState } from 'react'
import {
  Button,
  ErrorBox,
  Loading,
  PageBody,
  PageHeader,
  TBody,
  THead,
  Table,
  Td,
  Th,
  TotalRow,
  Tr,
} from '../../components/ui'
import { useOperator } from '../../lib/operator'
import { financeWrite, useInvalidateFinance, useSales } from '../../lib/finance-api'
import type { SalesDay, SalesDayIn } from '../../lib/types/finance'
import {
  Caveats,
  DateCell,
  MonthBar,
  MoneyCell,
  RemoveButton,
  SaveStatus,
  TextCell,
  UndoBar,
  addDays,
  committed,
  fd,
  gbp,
  londonToday,
  useFinancePeriod,
  useSaveStatus,
} from './shared'

const FROM_EXPORT = 'From an export: correct it at the source and re-import.'

export function SalesScreen() {
  const { period, setPeriod, months } = useFinancePeriod()
  const q = useSales(period)
  const save = useSaveStatus()
  const refresh = useInvalidateFinance()
  const [operator] = useOperator()
  const [undo, setUndo] = useState<{ text: string; restore: () => void } | null>(null)
  const clearUndo = useCallback(() => setUndo(null), [])

  const patch = (date: string, body: SalesDayIn) =>
    committed(save, () => financeWrite.patchDay(date, { ...body, operator }), refresh)

  async function addDay() {
    const days = q.data?.days ?? []
    const last = days[days.length - 1]
    let date: string
    if (last) date = addDays(last.date, 1)
    else if (period && period !== 'all') date = `${period}-01`
    else date = londonToday()
    const ok = await committed(save, () => financeWrite.createDay({ date, operator }), refresh)
    if (ok && period !== 'all' && date.slice(0, 7) !== period) setPeriod(date.slice(0, 7))
  }

  async function remove(d: SalesDay) {
    const ok = await committed(save, () => financeWrite.deleteDay(d.date), refresh)
    if (!ok) return
    setUndo({
      text: `Deleted ${fd(d.date)}`,
      restore: () =>
        void committed(
          save,
          () =>
            financeWrite.createDay({
              date: d.date,
              card_pence: d.card_pence,
              cash_till_pence: d.cash_till_pence,
              cash_off_till_pence: d.cash_off_till_pence,
              orders_override: d.orders_source === 'override' ? d.orders : null,
              note: d.note,
              operator,
            }),
          async () => {
            setUndo(null)
            await refresh()
          },
        ),
    })
  }

  return (
    <>
      <PageHeader
        title="Sales"
        subtitle="one row per trading day"
        saved={<SaveStatus state={save.state} />}
        actions={
          <Button variant="primary" onClick={() => void addDay()} disabled={!q.data}>
            + Add a day
          </Button>
        }
      />
      <MonthBar period={period} months={months} onChange={setPeriod} />
      <PageBody flush>
        {q.isError ? (
          <ErrorBox error={q.error} what="sales" />
        ) : !q.data ? (
          <Loading what="Loading sales" />
        ) : (
          <div className="px-4 pb-6 pt-2 sm:px-5 compact:px-6">
            {undo && (
              <div className="mb-2 flex">
                <UndoBar text={undo.text} onUndo={undo.restore} onDone={clearUndo} />
              </div>
            )}
            <Caveats items={q.data.caveats} className="mb-2" />
            <Table header="sentence" stickyHeader minWidth={940} label="Sales by day">
              <THead>
                <tr>
                  <Th width={128}>Date</Th>
                  <Th width={48}>Day</Th>
                  <Th numeric>Card £</Th>
                  <Th numeric>Till cash £</Th>
                  <Th numeric>Own cash £</Th>
                  <Th numeric>Total</Th>
                  <Th numeric width={72}>
                    Orders
                  </Th>
                  <Th numeric width={86}>
                    Avg ticket
                  </Th>
                  <Th>Note</Th>
                  <Th width={32} className="relative">
                    <span className="sr-only">Delete</span>
                  </Th>
                </tr>
              </THead>
              <TBody>
                {q.data.days.map((d) => (
                  <Tr key={d.date}>
                    <Td>
                      <DateCell
                        value={d.date}
                        label={`Date of ${fd(d.date)}`}
                        disabled={!d.editable.card || !d.editable.cash_till || !d.editable.cash_off_till}
                        onCommit={(next) => patch(d.date, { date: next })}
                      />
                    </Td>
                    <Td className="text-sm text-ink-2">{d.weekday}</Td>
                    <Td>
                      <MoneyCell
                        pence={d.card_pence}
                        label={`Card on ${fd(d.date)}`}
                        disabled={!d.editable.card}
                        title={
                          !d.editable.card
                            ? FROM_EXPORT
                            : d.basis !== 'TILL'
                              ? 'A bank deposit on this date, from the workbook'
                              : undefined
                        }
                        onCommit={(v) => patch(d.date, { card_pence: v })}
                      />
                    </Td>
                    <Td>
                      <MoneyCell
                        pence={d.cash_till_pence}
                        label={`Till cash on ${fd(d.date)}`}
                        disabled={!d.editable.cash_till}
                        title={!d.editable.cash_till ? FROM_EXPORT : undefined}
                        onCommit={(v) => patch(d.date, { cash_till_pence: v })}
                      />
                    </Td>
                    <Td>
                      <MoneyCell
                        pence={d.cash_off_till_pence}
                        label={`Own cash on ${fd(d.date)}`}
                        disabled={!d.editable.cash_off_till}
                        title={!d.editable.cash_off_till ? FROM_EXPORT : undefined}
                        onCommit={(v) => patch(d.date, { cash_off_till_pence: v })}
                      />
                    </Td>
                    <Td numeric>{gbp(d.total_pence)}</Td>
                    <Td>
                      <OrdersCell day={d} onCommit={(v) => patch(d.date, { orders_override: v })} />
                    </Td>
                    <Td numeric className="text-ink-2">
                      {d.avg_ticket_pence === null ? '—' : gbp(d.avg_ticket_pence)}
                    </Td>
                    <Td>
                      <TextCell
                        value={d.note}
                        label={`Note for ${fd(d.date)}`}
                        onCommit={(v) => patch(d.date, { note: v })}
                      />
                    </Td>
                    <Td remove>
                      <RemoveButton label={`Delete ${fd(d.date)}`} onClick={() => void remove(d)} />
                    </Td>
                  </Tr>
                ))}
                {q.data.days.length === 0 && (
                  <tr>
                    <td colSpan={10} className="p-10 text-center text-md text-ink-2">
                      No days entered for this month.
                    </td>
                  </tr>
                )}
                <TotalRow>
                  <Td>
                    {q.data.totals.days} {q.data.totals.days === 1 ? 'day' : 'days'}
                  </Td>
                  <Td />
                  <Td numeric>{gbp(q.data.totals.card_pence)}</Td>
                  <Td numeric>{gbp(q.data.totals.cash_till_pence)}</Td>
                  <Td numeric>{gbp(q.data.totals.cash_off_till_pence)}</Td>
                  <Td numeric>{gbp(q.data.totals.total_pence)}</Td>
                  <Td numeric>{q.data.totals.orders ?? ''}</Td>
                  <Td />
                  <Td />
                  <Td />
                </TotalRow>
              </TBody>
            </Table>
          </div>
        )}
      </PageBody>
    </>
  )
}

/** Orders: a typed override, or the count from the till/export shown as a placeholder. */
function OrdersCell({ day, onCommit }: { day: SalesDay; onCommit: (v: number | null) => Promise<boolean> }) {
  const typed = day.orders_source === 'override' ? String(day.orders) : ''
  const [text, setText] = useState(typed)
  const [bad, setBad] = useState(false)
  const [prev, setPrev] = useState(typed)
  if (prev !== typed) {
    setPrev(typed)
    setText(typed)
  }
  return (
    <input
      aria-label={`Orders on ${fd(day.date)}`}
      inputMode="numeric"
      placeholder={day.orders !== null && day.orders_source !== 'override' ? String(day.orders) : '—'}
      title={
        day.orders_source === 'pos'
          ? 'Counted from till receipts. Type a number to override.'
          : day.orders_source === 'payment_export'
            ? 'From the payment export. Type a number to override.'
            : undefined
      }
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      onBlur={async () => {
        const t = text.trim()
        if (t !== '' && !/^\d{1,5}$/.test(t)) {
          setBad(true)
          return
        }
        setBad(false)
        if (t === typed) return
        const ok = await onCommit(t === '' ? null : Number(t))
        if (!ok) setText(typed)
      }}
      className={`fig h-7 w-full min-w-0 rounded-control border bg-surface px-1.5 text-right text-base outline-none placeholder:text-ink-3 focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-brand-wash ${bad ? 'border-alert' : 'border-line-strong'}`}
    />
  )
}
