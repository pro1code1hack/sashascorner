/**
 * Money → Transactions: what the back office recorded, read-only.
 *
 * Two ledgers, never summed together:
 * - Receipts: Lightspeed receipts (lines grouped by receipt), newest first.
 * - Takings: one row per day, method and source (workbook, CSV export,
 *   Lightspeed, typed cash). When two sources report the same day and method,
 *   only the winner counts; the other is shown faded, never added.
 *
 * Filtering and paging happen on the server; money is integer pence.
 */
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ActiveFilters,
  ErrorBox,
  FilterBar,
  FilterSelect,
  FilterToggle,
  Loading,
  PageBody,
  PageHeader,
  Pagination,
  SearchInput,
  Segmented,
  TBody,
  THead,
  Table,
  Td,
  Th,
  Tr,
  cx,
} from '../../components/ui'
import type { ActiveFilterChip } from '../../components/ui'
import { useReceipts, useTakingsLedger } from '../../lib/finance-api'
import type { ReceiptFilters, TakingsFilters, TakingsRow } from '../../lib/types/finance'
import { AmountRange, Figures, PeriodBar, count, isWeekend, shortDate, windowOf } from './filters'
import type { DateRange, PenceRange } from './filters'
import { fd, gbp, useFinancePeriod } from './shared'

type Tab = 'receipts' | 'takings'

const METHOD_NAME: Record<string, string> = {
  CARD: 'Card',
  CASH: 'Till cash',
  CASH_OFF_TILL: 'Own cash',
  VOUCHER: 'Voucher',
  ACCOUNT: 'Account',
  OTHER: 'Other',
}
const SOURCE_NAME: Record<string, string> = {
  POS_API: 'Lightspeed',
  CSV_UPLOAD: 'CSV export',
  MANUAL: 'Added here',
  LEGACY_WORKBOOK: 'Finance workbook',
}
const CHANNEL_NAME: Record<string, string> = { EPOS: 'Till', DELIVEROO: 'Deliveroo', JUST_EAT: 'Just Eat', OTHER: 'Other' }

const dash = <span className="text-ink-3">—</span>
const money = (p: number | null) => (p === null ? dash : gbp(p))

export function TransactionsScreen() {
  const { period, setPeriod, months } = useFinancePeriod()
  const [range, setRange] = useState<DateRange | null>(null)
  const [tab, setTab] = useState<Tab>('receipts')
  const win = windowOf(period, range)

  return (
    <>
      <PageHeader
        title="Transactions"
        subtitle="what the till and the bank recorded, as imported"
        actions={
          <Segmented<Tab>
            label="Ledger"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'receipts', label: 'Receipts' },
              { value: 'takings', label: 'Takings' },
            ]}
          />
        }
      />
      <PeriodBar period={period} months={months} onPeriod={setPeriod} range={range} onRange={setRange} />
      {period === null ? (
        <PageBody>
          <Loading what="Loading" />
        </PageBody>
      ) : tab === 'receipts' ? (
        <Receipts win={win} range={range} clearRange={() => setRange(null)} />
      ) : (
        <Takings win={win} range={range} clearRange={() => setRange(null)} />
      )}
    </>
  )
}

/** Reset to page 1 whenever anything but the page changes. */
function usePaging(deps: unknown[]): [number, (p: number) => void, number, (n: number) => void] {
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(50)
  const key = JSON.stringify(deps)
  useEffect(() => setPage(1), [key, size])
  return [page, setPage, size, setSize]
}

function rangeChip(range: DateRange | null, clear: () => void): ActiveFilterChip[] {
  return range ? [{ key: 'range', label: `${shortDate(range.from)} – ${shortDate(range.to)}`, onRemove: clear }] : []
}

function amountChip(a: PenceRange, clear: () => void): ActiveFilterChip[] {
  if (a.min === null && a.max === null) return []
  return [
    {
      key: 'amount',
      label: `${a.min !== null ? gbp(a.min) : '£0.00'} – ${a.max !== null ? gbp(a.max) : 'any'}`,
      onRemove: clear,
    },
  ]
}

function DayCell({ date, time }: { date: string; time?: string }) {
  const [wd, d, mon] = fd(date).split(' ')
  return (
    <span className="whitespace-nowrap">
      <span className={cx('inline-block w-9', isWeekend(date) ? 'font-bold text-ink' : 'text-ink-2')}>{wd}</span>
      <span className="fig inline-block w-5 text-right">{d}</span> <span className="text-ink-2">{mon}</span>
      {time && <span className="fig ml-2 text-ink-2">{time}</span>}
    </span>
  )
}

/* -------------------------------------------------------------- receipts --- */

function Receipts({ win, range, clearRange }: { win: DateRange | null; range: DateRange | null; clearRange: () => void }) {
  const [search, setSearch] = useState('')
  const [qText, setQText] = useState('')
  useEffect(() => {
    const t = window.setTimeout(() => setQText(search), 250)
    return () => window.clearTimeout(t)
  }, [search])
  const [channel, setChannel] = useState('all')
  const [amount, setAmount] = useState<PenceRange>({ min: null, max: null })
  const [hideVoided, setHideVoided] = useState(false)
  const [page, setPage, size, setSize] = usePaging([win, qText, channel, amount, hideVoided])
  const f: ReceiptFilters = {
    from: win?.from,
    to: win?.to,
    channel: channel === 'all' ? undefined : channel,
    q: qText || undefined,
    min_pence: amount.min ?? undefined,
    max_pence: amount.max ?? undefined,
    include_voided: !hideVoided,
    page,
    page_size: size,
  }
  const q = useReceipts(f)
  const d = q.data

  const chips = [
    ...rangeChip(range, clearRange),
    ...(qText ? [{ key: 'q', label: `“${qText}”`, onRemove: () => setSearch('') }] : []),
    ...(channel !== 'all' ? [{ key: 'ch', label: CHANNEL_NAME[channel] ?? channel, onRemove: () => setChannel('all') }] : []),
    ...amountChip(amount, () => setAmount({ min: null, max: null })),
    ...(hideVoided ? [{ key: 'v', label: 'Voided hidden', onRemove: () => setHideVoided(false) }] : []),
  ]
  const avg = d && d.total_rows - d.voided_count > 0 ? Math.floor((d.gross_pence * 2 + (d.total_rows - d.voided_count)) / ((d.total_rows - d.voided_count) * 2)) : null

  const body: ReactNode[] = []
  let prev = ''
  for (const r of d?.rows ?? []) {
    if (r.date !== prev) {
      prev = r.date
      body.push(
        <tr key={`d-${r.date}`} className="border-b border-line bg-canvas-2">
          <td colSpan={5} className="py-1.5 pl-2 text-sm font-bold text-ink-2">
            {fd(r.date)} {r.date.slice(0, 4)}
          </td>
        </tr>,
      )
    }
    body.push(
      <Tr key={r.receipt_id} className={cx(r.voided && 'text-ink-3 line-through decoration-ink-3/60')}>
        <Td className="fig whitespace-nowrap pl-2! text-ink-2">{r.time}</Td>
        <Td className="max-w-0">
          <div className="truncate" title={r.summary}>
            {r.summary}
          </div>
          <div className="truncate text-sm text-ink-3">
            {r.receipt_id}
            {r.voided ? ' · voided' : ''}
            {r.refund ? ' · refund' : ''}
          </div>
        </Td>
        <Td className="whitespace-nowrap text-sm text-ink-2">{CHANNEL_NAME[r.channel] ?? r.channel}</Td>
        <Td numeric className="text-ink-2">
          {r.items}
        </Td>
        <Td numeric strong>
          {gbp(r.gross_pence)}
        </Td>
      </Tr>,
    )
  }

  return (
    <>
      <div className="flex flex-none flex-col gap-2 border-b border-line px-4 py-2.5 sm:px-5">
        <FilterBar
          label="Filter receipts"
          search={
            <SearchInput
              label="Search item or receipt"
              placeholder="Search item or receipt"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-[34px]"
            />
          }
        >
          <FilterSelect
            label="Channel"
            value={channel}
            onChange={setChannel}
            options={[{ value: 'all', label: 'All channels' }, ...Object.entries(CHANNEL_NAME).map(([value, label]) => ({ value, label }))]}
          />
          <AmountRange label="Receipt total" value={amount} onChange={setAmount} />
          <FilterToggle active={hideVoided} onToggle={() => setHideVoided(!hideVoided)}>
            Hide voided
          </FilterToggle>
        </FilterBar>
        <ActiveFilters chips={chips} onClearAll={() => {
          clearRange()
          setSearch('')
          setChannel('all')
          setAmount({ min: null, max: null })
          setHideVoided(false)
        }} />
      </div>
      <PageBody flush>
        {q.isError ? (
          <ErrorBox error={q.error} what="receipts" />
        ) : !d ? (
          <Loading what="Loading receipts" />
        ) : (
          <div className={cx('px-4 pb-8 sm:px-5 compact:px-6', q.isFetching && 'opacity-70')}>
            <Figures
              items={[
                { label: 'Receipts', value: count(d.total_rows - d.voided_count), strong: true, sub: d.voided_count > 0 ? `+ ${count(d.voided_count)} voided` : undefined },
                { label: 'Takings', value: gbp(d.gross_pence), sub: 'as rung on the till' },
                { label: 'Avg receipt', value: avg === null ? '—' : gbp(avg) },
                {
                  label: 'Dates',
                  value: d.first_date && d.last_date ? `${shortDate(d.first_date)} – ${shortDate(d.last_date)}` : '—',
                },
              ]}
            />
            {d.caveats.length > 0 && <p className="pt-2 text-sm text-ink-2">{d.caveats.join(' ')}</p>}
            <div className="pt-3">
              <Table header="upper" minWidth={560} label="Lightspeed receipts">
                <THead>
                  <tr>
                    <Th width={64} className="pl-2!">
                      Time
                    </Th>
                    <Th>Items</Th>
                    <Th width={90}>Channel</Th>
                    <Th numeric width={56}>
                      Qty
                    </Th>
                    <Th numeric width={96}>
                      Total
                    </Th>
                  </tr>
                </THead>
                <TBody>
                  {body}
                  {d.rows.length === 0 && (
                    <tr>
                      <td colSpan={5} className="p-10 text-center text-md text-ink-2">
                        No receipts match these filters.
                      </td>
                    </tr>
                  )}
                </TBody>
              </Table>
            </div>
            <Pagination
              className="pt-3"
              page={d.page}
              pageSize={size}
              total={d.total_rows}
              onPage={setPage}
              onPageSize={setSize}
              noun="receipts"
            />
          </div>
        )}
      </PageBody>
    </>
  )
}

/* --------------------------------------------------------------- takings --- */

function Takings({ win, range, clearRange }: { win: DateRange | null; range: DateRange | null; clearRange: () => void }) {
  const [method, setMethod] = useState('all')
  const [source, setSource] = useState('all')
  const [usedOnly, setUsedOnly] = useState(false)
  const [amount, setAmount] = useState<PenceRange>({ min: null, max: null })
  const [page, setPage, size, setSize] = usePaging([win, method, source, usedOnly, amount])
  const f: TakingsFilters = {
    from: win?.from,
    to: win?.to,
    method: method === 'all' ? undefined : method,
    source: source === 'all' ? undefined : source,
    used_only: usedOnly || undefined,
    min_pence: amount.min ?? undefined,
    max_pence: amount.max ?? undefined,
    page,
    page_size: size,
  }
  const q = useTakingsLedger(f)
  const d = q.data

  const chips = [
    ...rangeChip(range, clearRange),
    ...(method !== 'all' ? [{ key: 'm', label: METHOD_NAME[method] ?? method, onRemove: () => setMethod('all') }] : []),
    ...(source !== 'all' ? [{ key: 's', label: SOURCE_NAME[source] ?? source, onRemove: () => setSource('all') }] : []),
    ...(usedOnly ? [{ key: 'u', label: 'Counted only', onRemove: () => setUsedOnly(false) }] : []),
    ...amountChip(amount, () => setAmount({ min: null, max: null })),
  ]

  const byMethod = useMemo(() => Object.entries(d?.by_method_pence ?? {}).sort((a, b) => b[1] - a[1]), [d])

  const body: ReactNode[] = []
  let prev = ''
  for (const r of d?.rows ?? []) {
    const first = r.date !== prev
    prev = r.date
    body.push(<TakingsLine key={r.id} r={r} first={first} />)
  }

  return (
    <>
      <div className="flex flex-none flex-col gap-2 border-b border-line px-4 py-2.5 sm:px-5">
        <FilterBar label="Filter takings">
          <FilterSelect
            label="Method"
            value={method}
            onChange={setMethod}
            options={[{ value: 'all', label: 'Card and cash' }, ...Object.entries(METHOD_NAME).map(([value, label]) => ({ value, label }))]}
          />
          <FilterSelect
            label="Source"
            value={source}
            onChange={setSource}
            options={[{ value: 'all', label: 'Every source' }, ...Object.entries(SOURCE_NAME).map(([value, label]) => ({ value, label }))]}
          />
          <AmountRange label="Amount" value={amount} onChange={setAmount} />
          <FilterToggle active={usedOnly} onToggle={() => setUsedOnly(!usedOnly)}>
            Counted only
          </FilterToggle>
        </FilterBar>
        <ActiveFilters chips={chips} onClearAll={() => {
          clearRange()
          setMethod('all')
          setSource('all')
          setUsedOnly(false)
          setAmount({ min: null, max: null })
        }} />
      </div>
      <PageBody flush>
        {q.isError ? (
          <ErrorBox error={q.error} what="takings" />
        ) : !d ? (
          <Loading what="Loading takings" />
        ) : (
          <div className={cx('px-4 pb-8 sm:px-5 compact:px-6', q.isFetching && 'opacity-70')}>
            <Figures
              items={[
                { label: 'Counted', value: gbp(d.used_gross_pence), strong: true, sub: `${count(d.total_rows)} rows` },
                ...byMethod.map(([m, p]) => ({ label: METHOD_NAME[m] ?? m, value: gbp(p) })),
              ]}
            />
            {d.caveats.length > 0 && <p className="pt-2 text-sm text-ink-2">{d.caveats.join(' ')}</p>}
            <div className="pt-3">
              <Table header="upper" minWidth={820} label="Takings by day, method and source">
                <THead>
                  <tr>
                    <Th width={112} className="pl-2!">
                      Day
                    </Th>
                    <Th width={96}>Method</Th>
                    <Th>Source</Th>
                    <Th numeric>Gross</Th>
                    <Th numeric>Refunds</Th>
                    <Th numeric>Fees</Th>
                    <Th numeric>Net</Th>
                    <Th numeric width={64}>
                      Txns
                    </Th>
                  </tr>
                </THead>
                <TBody>
                  {body}
                  {d.rows.length === 0 && (
                    <tr>
                      <td colSpan={8} className="p-10 text-center text-md text-ink-2">
                        No takings match these filters.
                      </td>
                    </tr>
                  )}
                </TBody>
              </Table>
            </div>
            <Pagination
              className="pt-3"
              page={d.page}
              pageSize={size}
              total={d.total_rows}
              onPage={setPage}
              onPageSize={setSize}
              noun="rows"
            />
          </div>
        )}
      </PageBody>
    </>
  )
}

function TakingsLine({ r, first }: { r: TakingsRow; first: boolean }) {
  return (
    <Tr className={cx(!r.used && 'text-ink-3', first && 'border-t border-t-line-strong')}>
      <Td className="pl-2!">{first ? <DayCell date={r.date} /> : null}</Td>
      <Td className="whitespace-nowrap">{METHOD_NAME[r.method] ?? r.method}</Td>
      <Td className="max-w-0">
        <div className="truncate text-sm" title={r.source_ref ?? undefined}>
          {SOURCE_NAME[r.source] ?? r.source}
          {r.basis === 'BANK_DEPOSIT' && <span className="text-ink-2"> · bank deposit</span>}
          {!r.used && <span> · not counted, another source wins</span>}
          {r.source_ref && <span className="text-ink-3"> · {r.source_ref}</span>}
        </div>
      </Td>
      <Td numeric strong={r.used}>
        {gbp(r.gross_pence)}
      </Td>
      <Td numeric>{money(r.refunds_pence)}</Td>
      <Td numeric>{money(r.fees_pence)}</Td>
      <Td numeric>{money(r.net_pence)}</Td>
      <Td numeric>{r.transactions === null ? dash : count(r.transactions)}</Td>
    </Tr>
  )
}
