/**
 * Supplier orders, as expenses (owner, 2026-09-26): every stored purchase order,
 * newest first, in the Menu list's row layout. Filter by text, supplier, status,
 * date and receipt; a row opens the order's own page, where it is received into
 * stock, marked sent or cancelled (the actions the SERVER allows, `actions[]`).
 * Confirming happens on the order page, signed by a person (invariant 1).
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ActiveFilters, Empty, ErrorBox, FilterBar, FilterSelect, FilterToggle, Loading, SearchInput, StatusTag, cx } from '../../components/ui'
import type { ActiveFilterChip, FilterOption } from '../../components/ui'
import { gbp } from '../../lib/format'
import { href } from '../../lib/router'
import { KEYS, stockApi } from '../../lib/stock-api'
import type { POStatus, PurchaseOrder } from '../../lib/types/stock'
import { dayMonth } from '../stock/fmt'
import { ReceiptThumb, orderPath, orderWhat } from './OrderBits'
import { statusWord } from './status'

const COLS = 'compact:grid-cols-[44px_minmax(0,1fr)_96px_96px_150px_100px]'

type StatusFilter = 'all' | 'open' | POStatus
const STATUS_OPTIONS: FilterOption[] = [
  { value: 'all', label: 'Any status' },
  { value: 'open', label: 'Open (not received)' },
  { value: 'DRAFT', label: 'Draft (to confirm)' },
  { value: 'PENDING_CONFIRM', label: 'Waiting to confirm' },
  { value: 'CONFIRMED', label: 'Confirmed' },
  { value: 'SENT', label: 'Sent' },
  { value: 'RECEIVED', label: 'Received' },
  { value: 'CANCELLED', label: 'Cancelled' },
]
const OPEN: readonly string[] = ['DRAFT', 'PENDING_CONFIRM', 'CONFIRMED', 'SENT']
type Receipt = 'all' | 'with' | 'without'
type Sort = 'new' | 'old' | 'total' | 'arrives'
const SORTS: FilterOption[] = [
  { value: 'new', label: 'Sort: newest' },
  { value: 'old', label: 'Sort: oldest' },
  { value: 'total', label: 'Sort: biggest total' },
  { value: 'arrives', label: 'Sort: arriving soonest' },
]

const total = (o: PurchaseOrder) => o.total_pence + o.delivery_fee_pence

export function History() {
  const [q, setQ] = useState('')
  const [supplier, setSupplier] = useState('all')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [receipt, setReceipt] = useState<Receipt>('all')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [sort, setSort] = useState<Sort>('new')
  const orders = useQuery({ queryKey: KEYS.orders, queryFn: () => stockApi.orders(), staleTime: 30_000 })

  const all = useMemo(() => orders.data?.orders ?? [], [orders.data])
  const suppliers = useMemo(() => {
    const m = new Map<number, string>()
    for (const o of all) m.set(o.supplier_id, o.supplier_name)
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [all])

  const rows = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    const out = all.filter((o) => {
      if (supplier !== 'all' && String(o.supplier_id) !== supplier) return false
      if (status === 'open' ? !OPEN.includes(o.status) : status !== 'all' && o.status !== status) return false
      if (receipt === 'with' && !o.receipt_url) return false
      if (receipt === 'without' && o.receipt_url) return false
      const day = o.created_at.slice(0, 10)
      if (from && day < from) return false
      if (to && day > to) return false
      const hay = `${o.supplier_name} ${orderWhat(o)} #${o.po_id}`.toLowerCase()
      return words.every((w) => hay.includes(w))
    })
    return out.sort((a, b) =>
      sort === 'old'
        ? a.created_at.localeCompare(b.created_at)
        : sort === 'total'
          ? total(b) - total(a)
          : sort === 'arrives'
            ? a.target_delivery_date.localeCompare(b.target_delivery_date)
            : b.created_at.localeCompare(a.created_at),
    )
  }, [all, q, supplier, status, receipt, from, to, sort])

  if (orders.isPending) return <Loading what="Reading orders" />
  if (orders.isError) return <ErrorBox error={orders.error} what="orders" />

  const chips: ActiveFilterChip[] = []
  if (supplier !== 'all')
    chips.push({ key: 's', label: suppliers.find(([id]) => String(id) === supplier)?.[1] ?? 'Supplier', onRemove: () => setSupplier('all') })
  if (status !== 'all') chips.push({ key: 'st', label: STATUS_OPTIONS.find((o) => o.value === status)?.label ?? status, onRemove: () => setStatus('all') })
  if (receipt !== 'all') chips.push({ key: 'r', label: receipt === 'with' ? 'Has a receipt' : 'No receipt yet', onRemove: () => setReceipt('all') })
  if (from) chips.push({ key: 'f', label: `From ${dayMonth(from)}`, onRemove: () => setFrom('') })
  if (to) chips.push({ key: 't', label: `To ${dayMonth(to)}`, onRemove: () => setTo('') })
  const shownTotal = rows.filter((o) => o.status !== 'CANCELLED').reduce((n, o) => n + total(o), 0)

  return (
    <div className="flex flex-col gap-3">
      <FilterBar
        activeCount={chips.length}
        label="Filter orders"
        search={<SearchInput label="Search orders" placeholder="Supplier, ingredient or #id" value={q} onChange={(e) => setQ(e.target.value)} />}
        trailing={<FilterSelect label="Sort" value={sort} allValue={sort} onChange={(v) => setSort(v as Sort)} options={SORTS} />}
      >
        <FilterSelect
          label="Supplier"
          value={supplier}
          onChange={setSupplier}
          options={[{ value: 'all', label: 'All suppliers' }, ...suppliers.map(([id, name]) => ({ value: String(id), label: name }))]}
        />
        <FilterSelect label="Status" value={status} onChange={(v) => setStatus(v as StatusFilter)} options={STATUS_OPTIONS} />
        <FilterToggle active={receipt === 'without'} onToggle={() => setReceipt(receipt === 'without' ? 'all' : 'without')}>
          No receipt yet
        </FilterToggle>
        <label className="flex items-center gap-1.5 text-sm text-ink-2">
          From
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="h-[34px] rounded-full border border-line-control bg-surface px-3 text-base text-ink" />
        </label>
        <label className="flex items-center gap-1.5 text-sm text-ink-2">
          to
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="h-[34px] rounded-full border border-line-control bg-surface px-3 text-base text-ink" />
        </label>
      </FilterBar>
      <ActiveFilters
        chips={chips}
        onClearAll={() => {
          setSupplier('all')
          setStatus('all')
          setReceipt('all')
          setFrom('')
          setTo('')
          setQ('')
        }}
        summary={
          <>
            {rows.length} of {all.length} orders · <span className="fig">{gbp(shownTotal)}</span> excluding cancelled
          </>
        }
      />
      {rows.length === 0 ? (
        <Empty>{all.length === 0 ? 'No orders yet.' : 'No orders match these filters.'}</Empty>
      ) : (
        <div className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
          <div className={cx('hidden gap-3 border-b border-line px-3.5 py-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 compact:grid', COLS)}>
            <span />
            <span>Order</span>
            <span>Created</span>
            <span>Arrives</span>
            <span>Status</span>
            <span className="text-right">Total</span>
          </div>
          <ul>
            {rows.map((o) => (
              <OrderRow key={o.po_id} o={o} />
            ))}
          </ul>
          <p className="border-t border-line px-3.5 py-2 text-xs text-ink-2">
            Open an order to confirm it, receive it into stock, add its receipt, mark it sent or cancel it. Totals include delivery fees.
          </p>
        </div>
      )}
    </div>
  )
}

function OrderRow({ o }: { o: PurchaseOrder }) {
  const st = statusWord(o.status)
  const label = st.label
  const canReceive = o.actions.includes('receive')
  return (
    <li className="border-b border-line-row last:border-b-0">
      <a
        href={href(orderPath(o.po_id))}
        className={cx(
          'grid grid-cols-[44px_minmax(0,1fr)_auto] items-center gap-x-3 px-3.5 py-2.5 text-ink no-underline hover:bg-canvas-2',
          COLS,
          o.status === 'CANCELLED' && 'opacity-60',
        )}
      >
        <ReceiptThumb url={o.receipt_url} />
        <span className="min-w-0">
          <span className="block truncate text-md font-bold">
            {o.supplier_name} <span className="font-normal text-ink-3">#{o.po_id}</span>
          </span>
          <span className="block truncate text-sm text-ink-2" title={orderWhat(o)}>
            {orderWhat(o)}
          </span>
          <span className="block truncate text-sm text-ink-2 compact:hidden">
            {dayMonth(o.created_at)} · arrives {dayMonth(o.target_delivery_date)} · {label}
            {canReceive ? ' · ready to receive' : ''}
          </span>
        </span>
        <span className="hidden text-base compact:block">{dayMonth(o.created_at)}</span>
        <span className="hidden text-base compact:block">{dayMonth(o.target_delivery_date)}</span>
        <span className="hidden flex-col items-start gap-0.5 compact:flex">
          <StatusTag tone={st.tone}>{label}</StatusTag>
          {canReceive && <span className="text-xs text-brand-ink">ready to receive</span>}
        </span>
        <span className="fig text-right text-base font-bold">
          {gbp(total(o))}
          {o.delivery_fee_pence > 0 && <span className="block text-xs font-normal text-ink-2">incl. {gbp(o.delivery_fee_pence)} fee</span>}
        </span>
      </a>
    </li>
  )
}
