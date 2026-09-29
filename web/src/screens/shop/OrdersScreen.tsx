/**
 * Online orders › All orders (`#/shop/orders`): every online order, newest first,
 * one row each, with the filters on top (status scope, dates, search) and pages
 * from the server. A row opens the order's own page (`#/shop/orders/<id>`).
 * "Download CSV" pulls every page of the current filter and builds the file in
 * the browser (csv.ts): UTF-8 with a BOM, money as "£x.xx".
 */
import { useEffect, useState } from 'react'
import {
  ActiveFilters,
  Button,
  Empty,
  ErrorBox,
  FilterBar,
  FilterSelect,
  Loading,
  PageBody,
  PageHeader,
  Pagination,
  SearchInput,
  StatusLine,
  StatusTag,
  cx,
} from '../../components/ui'
import type { ActiveFilterChip, FilterOption, Outcome } from '../../components/ui'
import { gbp } from '../../lib/format'
import { href, useLocation } from '../../lib/router'
import { fetchAllOrders, useShopOrders } from '../../lib/shop-api'
import type { OrderAdmin, OrdersScope } from '../../lib/types/shop'
import { dayMonth, todayIso } from '../stock/fmt'
import { buildCsv, csvMoney, downloadCsv } from './csv'
import { OrderPage } from './OrderPage'
import { LIST_HEAD, ShopGate, diningWord, linesSummary, orderPath, paymentWord, plural, statusWord, unitsCount } from './shared'

/** `#/shop/orders` is the list; `#/shop/orders/<id>` is one order's page. */
export function ShopOrdersArea() {
  const seg = useLocation().segments
  const id = seg[2]
  if (id !== undefined && /^\d+$/.test(id)) return <OrderPage orderId={Number(id)} />
  return <OrdersListScreen />
}

const SCOPES: FilterOption[] = [
  { value: 'all', label: 'All orders' },
  { value: 'live', label: 'In progress' },
  { value: 'today', label: 'Today' },
]

/** Docked (≥ 900): code · order · placed · due · status · total; the payment column joins at ≥ 1280. */
const COLS = 'compact:grid-cols-[96px_minmax(0,1fr)_100px_120px_136px_84px] wide:grid-cols-[96px_minmax(0,1fr)_116px_136px_150px_110px_90px]'

const CSV_HEADER = ['Code', 'Placed', 'Requested', 'Status', 'Customer', 'Dining', 'Table', 'Lines', 'Subtotal', 'Discount', 'Total', 'Payment method', 'Payment status', 'Member'] as const

function csvRow(o: OrderAdmin): string[] {
  return [
    o.code_display,
    o.placed_local,
    `${o.asap ? 'ASAP ' : ''}${o.requested_local}`,
    statusWord(o.status).label,
    o.customer_name,
    diningWord(o.dining),
    o.table ?? '',
    linesSummary(o),
    csvMoney(o.subtotal_pence),
    csvMoney(o.discount_pence),
    csvMoney(o.total_pence),
    o.payment_method === 'ONLINE' ? 'Online' : 'Counter',
    o.payment_status === 'PAID' ? 'Paid' : o.payment_status === 'REFUNDED' ? 'Refunded' : o.payment_status === 'FAILED' ? 'Failed' : 'Unpaid',
    o.member ? `${o.member.first_name} (#${o.member.id})` : '',
  ]
}

/** The typed text, settled: the server is asked once typing pauses. */
function useDebounced(value: string, ms: number): string {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return v
}

function OrdersListScreen() {
  const [scope, setScope] = useState<OrdersScope>('all')
  const [q, setQ] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const needle = useDebounced(q.trim(), 300)
  const orders = useShopOrders({ status: scope, q: needle || undefined, from: from || undefined, to: to || undefined, page, page_size: pageSize })
  const [exporting, setExporting] = useState(false)
  const [exportNote, setExportNote] = useState<Outcome | null>(null)
  const exportCsv = async () => {
    setExporting(true)
    setExportNote(null)
    try {
      const all = await fetchAllOrders({ status: scope, q: needle || undefined, from: from || undefined, to: to || undefined })
      const name = `online-orders_${scope}${from ? `_from-${from}` : ''}${to ? `_to-${to}` : ''}_${todayIso()}.csv`
      downloadCsv(name, buildCsv(CSV_HEADER, all.map(csvRow)))
      setExportNote({ kind: 'ok', text: `${plural(all.length, 'order')} in the file.` })
    } catch (e) {
      setExportNote({ kind: 'error', text: e instanceof Error ? `Could not build the file: ${e.message}` : 'Could not build the file.' })
    } finally {
      setExporting(false)
    }
  }

  const reset = () => setPage(1)
  const chips: ActiveFilterChip[] = []
  if (scope !== 'all')
    chips.push({
      key: 'scope',
      label: SCOPES.find((s) => s.value === scope)?.label ?? scope,
      onRemove: () => {
        setScope('all')
        reset()
      },
    })
  if (from)
    chips.push({
      key: 'from',
      label: `From ${dayMonth(from)}`,
      onRemove: () => {
        setFrom('')
        reset()
      },
    })
  if (to)
    chips.push({
      key: 'to',
      label: `To ${dayMonth(to)}`,
      onRemove: () => {
        setTo('')
        reset()
      },
    })

  const data = orders.data
  const dateBox = 'h-[34px] rounded-full border border-line-control bg-surface px-3 text-base text-ink'

  return (
    <>
      <PageHeader title="All orders" subtitle="Every order placed online, newest first. Open one to move it along, note it or cancel it." />
      <PageBody className="compact:px-5">
        <ShopGate>
          <div className="flex flex-col gap-3">
            <FilterBar
              activeCount={chips.length}
              label="Filter online orders"
              search={
                <SearchInput
                  label="Search orders"
                  placeholder="Code, name, phone or item"
                  value={q}
                  onChange={(e) => {
                    setQ(e.target.value)
                    reset()
                  }}
                />
              }
            >
              <FilterSelect
                label="Status"
                value={scope}
                onChange={(v) => {
                  setScope(v as OrdersScope)
                  reset()
                }}
                options={SCOPES}
              />
              <label className="flex items-center gap-1.5 text-sm text-ink-2">
                From
                <input
                  type="date"
                  value={from}
                  onChange={(e) => {
                    setFrom(e.target.value)
                    reset()
                  }}
                  className={dateBox}
                />
              </label>
              <label className="flex items-center gap-1.5 text-sm text-ink-2">
                to
                <input
                  type="date"
                  value={to}
                  onChange={(e) => {
                    setTo(e.target.value)
                    reset()
                  }}
                  className={dateBox}
                />
              </label>
              <span className="ml-auto flex flex-wrap items-center gap-2">
                <StatusLine outcome={exportNote} className="min-h-0" />
                <Button variant="outline" size="sm" pending={exporting} pendingLabel="Building…" disabled={!data || data.total === 0} onClick={() => void exportCsv()}>
                  Download CSV
                </Button>
              </span>
            </FilterBar>
            <ActiveFilters
              chips={chips}
              onClearAll={() => {
                setScope('all')
                setFrom('')
                setTo('')
                setQ('')
                reset()
              }}
              summary={data ? `${plural(data.total, 'order')}` : undefined}
            />

            {orders.isPending && <Loading what="Reading orders" />}
            {orders.isError && <ErrorBox error={orders.error} what="online orders" />}
            {data && data.items.length === 0 && (
              <Empty>{chips.length || needle ? 'No orders match these filters.' : 'No online orders yet. They appear here as customers place them.'}</Empty>
            )}
            {data && data.items.length > 0 && (
              <div className="overflow-hidden rounded-card-lg bg-surface shadow-raised" aria-busy={orders.isFetching || undefined}>
                {/* Visual column heads only; each row's cells carry their own hidden labels. */}
                <div className={cx(LIST_HEAD, COLS)} aria-hidden="true">
                  <span>Code</span>
                  <span>Order</span>
                  <span>Placed</span>
                  <span>Due</span>
                  <span className="hidden wide:block">Payment</span>
                  <span>Status</span>
                  <span className="text-right">Total</span>
                </div>
                <ul>
                  {data.items.map((o) => (
                    <OrderRow key={o.id} o={o} />
                  ))}
                </ul>
              </div>
            )}
            {data && data.total > 0 && (
              <Pagination
                page={data.page}
                pageSize={data.page_size}
                total={data.total}
                onPage={setPage}
                onPageSize={(n) => {
                  setPageSize(n)
                  reset()
                }}
                noun="orders"
              />
            )}
          </div>
        </ShopGate>
      </PageBody>
    </>
  )
}

function OrderRow({ o }: { o: OrderAdmin }) {
  const st = statusWord(o.status)
  // A cancelled or rejected row keeps full contrast: the status tag says what happened.
  return (
    <li className="border-b border-line-row last:border-b-0">
      <a
        href={href(orderPath(o.id))}
        className={cx('grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 px-3.5 py-2.5 text-ink no-underline hover:bg-canvas-2', COLS)}
      >
        <span className="fig hidden font-bold compact:block">{o.code_display}</span>
        <span className="min-w-0">
          <span className="block truncate text-md font-bold">
            <span className="fig compact:hidden">{o.code_display} · </span>
            {o.customer_name}{' '}
            <span className="font-normal text-ink-2">
              · {diningWord(o.dining)}
              {o.table ? ` · Table ${o.table}` : ''}
            </span>
          </span>
          <span className="block truncate text-sm text-ink-2" title={linesSummary(o)}>
            {plural(unitsCount(o), 'item')}: {linesSummary(o)}
          </span>
          <span className="block truncate text-sm text-ink-2 compact:hidden">
            {o.placed_local} · due {o.requested_local} · {st.label} · {paymentWord(o.payment_method, o.payment_status)}
          </span>
        </span>
        <span className="fig hidden text-base compact:block">
          <span className="sr-only">Placed </span>
          {o.placed_local}
        </span>
        <span className="fig hidden text-base compact:block">
          <span className="sr-only">Due </span>
          {o.asap ? `ASAP · ${o.requested_local}` : o.requested_local}
          {o.table ? <span className="block text-sm text-ink-2">Table {o.table}</span> : null}
        </span>
        <span className="hidden text-sm text-ink-2 wide:block">
          <span className="sr-only">Payment </span>
          {paymentWord(o.payment_method, o.payment_status)}
        </span>
        <span className="hidden compact:block">
          <span className="sr-only">Status </span>
          <StatusTag tone={st.tone}>{st.label}</StatusTag>
          <span className="mt-0.5 block truncate text-sm text-ink-2 wide:hidden">{paymentWord(o.payment_method, o.payment_status)}</span>
        </span>
        <span className="fig text-right text-base font-bold">
          <span className="sr-only">Total </span>
          {gbp(o.total_pence)}
        </span>
      </a>
    </li>
  )
}
