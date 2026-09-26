/**
 * Order history (§2.3): every stored purchase order, newest first, with the
 * actions the SERVER says are allowed for its status (`actions[]`). Never a
 * confirm: that is Telegram's (invariant 1).
 *
 * "Received → stock" opens an inline receive sheet (C16): packs per line
 * (default: what was ordered and not yet received) and a use-by date that
 * defaults to today + shelf life, labelled "assumed" until it is edited. An
 * untouched date is not sent, so the server stamps the batch as assumed.
 */
import { Fragment, useEffect, useMemo, useState } from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { OperatorNeeded } from '../../components/shell/Operator'
import { Button, Empty, ErrorBox, Input, Loading, Select, StatusTag, cx } from '../../components/ui'
import { mustDec } from '../../lib/dec'
import { gbp } from '../../lib/format'
import { useOperator } from '../../lib/operator'
import { KEYS, orderWrites, stockApi } from '../../lib/stock-api'
import type { PurchaseOrder } from '../../lib/types/stock'
import { addDays, dayMonth, fmtQ, todayIso } from '../stock/fmt'
import { OutcomeLine, useWrite } from '../stock/writes'
import { statusWord } from './status'

const GRID = 'grid grid-cols-[90px_minmax(0,1fr)_minmax(0,2fr)_90px_150px_80px_270px] gap-2.5'
const AFTER = [KEYS.orders, KEYS.draft, KEYS.stock]

export function History() {
  const [supplier, setSupplier] = useState<number | 'all'>('all')
  const [receiving, setReceiving] = useState<number | null>(null)
  const orders = useQuery({ queryKey: KEYS.orders, queryFn: () => stockApi.orders(), staleTime: 30_000 })
  const suppliers = useQuery({ queryKey: KEYS.suppliers, queryFn: stockApi.suppliers, staleTime: 60_000 })

  const names = useMemo(() => {
    const m = new Map<number, string>()
    for (const s of suppliers.data ?? []) m.set(s.supplier_id, s.name)
    for (const o of orders.data?.orders ?? []) m.set(o.supplier_id, o.supplier_name)
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [suppliers.data, orders.data])

  if (orders.isPending) return <Loading what="Reading orders" />
  if (orders.isError) return <ErrorBox error={orders.error} what="order history" />
  const rows = orders.data.orders.filter((o) => supplier === 'all' || o.supplier_id === supplier)

  return (
    <div className="flex flex-col gap-3">
      <label className="flex items-center gap-2 text-base text-ink-2">
        Supplier
        <Select
          size="xs"
          className="w-auto! min-w-40"
          value={supplier === 'all' ? 'all' : String(supplier)}
          onChange={(e) => setSupplier(e.target.value === 'all' ? 'all' : Number(e.target.value))}
        >
          <option value="all">All suppliers</option>
          {names.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </Select>
      </label>
      {rows.length === 0 ? (
        <Empty>No orders yet.</Empty>
      ) : (
        <div className="scroll-x relative">
          <div className="min-w-[980px]">
            <div className={cx(GRID, 'border-b border-line py-1.5 text-sm text-ink-2')} role="row">
              <span>Created</span>
              <span>Supplier</span>
              <span>What</span>
              <span className="text-right">Total</span>
              <span>Status</span>
              <span>Arrives</span>
              <span><span className="sr-only">Actions</span></span>
            </div>
            {rows.map((o) => (
              <Fragment key={o.po_id}>
                <OrderRow o={o} receiving={receiving === o.po_id} onReceive={() => setReceiving(receiving === o.po_id ? null : o.po_id)} />
                {receiving === o.po_id && <ReceiveSheet o={o} onDone={() => setReceiving(null)} />}
              </Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function what(o: PurchaseOrder): string {
  return o.lines.map((l) => `${l.final_packs}× ${l.ingredient_name}`).join(', ')
}

function OrderRow({ o, receiving, onReceive }: { o: PurchaseOrder; receiving: boolean; onReceive: () => void }) {
  const [operator] = useOperator()
  const w = useWrite()
  const st = statusWord(o.status)
  const total = o.total_pence + o.delivery_fee_pence
  return (
    <div className="border-b border-line py-[7px] text-base">
      <div className={cx(GRID, 'items-center')}>
        <span>{dayMonth(o.created_at)}</span>
        <span className="truncate">{o.supplier_name}</span>
        <span className="truncate text-ink-2" title={what(o)}>
          {what(o)}
        </span>
        <span className="fig text-right">
          {gbp(total)}
          {o.delivery_fee_pence > 0 && <span className="block text-xs text-ink-2">incl. {gbp(o.delivery_fee_pence)} fee</span>}
        </span>
        <span>
          <StatusTag tone={st.tone}>{st.label.replace(' (in Telegram /orders)', '')}</StatusTag>
        </span>
        <span>{dayMonth(o.target_delivery_date)}</span>
        <span className="flex flex-wrap justify-end gap-1.5">
          {o.actions.includes('mark_sent') && (
            <ActionChip
              disabled={operator === null || w.pending}
              onClick={() =>
                operator &&
                w.run(() => orderWrites.markSent(o.po_id, operator), { invalidate: AFTER, ok: () => 'Marked sent.' })
              }
            >
              Mark sent
            </ActionChip>
          )}
          {o.actions.includes('receive') && (
            <ActionChip aria-expanded={receiving} onClick={onReceive}>
              Received → stock
            </ActionChip>
          )}
          {o.actions.includes('cancel') && (
            <ArmChip
              disabled={operator === null || w.pending}
              onConfirm={() =>
                operator &&
                void w.run(() => orderWrites.cancel(o.po_id, operator), { invalidate: AFTER, ok: () => 'Cancelled.' })
              }
            >
              Cancel
            </ArmChip>
          )}
        </span>
      </div>
      {o.status === 'PENDING_CONFIRM' && (
        <p className="mt-1 text-sm text-ink-2">Waiting in Telegram: nothing is ordered until someone taps Confirm there.</p>
      )}
      {o.status === 'CANCELLED' && o.cancelled_by && (
        <p className="mt-1 text-sm text-ink-2">
          Cancelled by {o.cancelled_by}
          {o.cancel_reason ? `: ${o.cancel_reason}` : ''}.
        </p>
      )}
      {o.actions.length > 0 && operator === null && (
        <div className="mt-1.5 max-w-md">
          <OperatorNeeded what="act on an order" />
        </div>
      )}
      <OutcomeLine outcome={w.outcome} className="mt-1" />
    </div>
  )
}

function ReceiveSheet({ o, onDone }: { o: PurchaseOrder; onDone: () => void }) {
  const [operator] = useOperator()
  const stock = useQuery({ queryKey: KEYS.stock, queryFn: stockApi.stock, staleTime: 60_000 })
  const shelf = useMemo(() => {
    const m = new Map<number, number | null>()
    for (const r of stock.data?.rows ?? []) m.set(r.ingredient_id, r.shelf_life.shelf_life_days)
    return m
  }, [stock.data])
  const remaining = (l: PurchaseOrder['lines'][number]): string => {
    if (l.received_qty === null) return String(l.final_packs)
    if (l.unit !== l.pack_unit) return ''
    const got = mustDec(l.received_qty)
    const size = mustDec(l.pack_size)
    // Whole packs still to come, when the units agree; otherwise ask.
    const left = l.final_packs - Number((got.u * 10n ** BigInt(size.s)) / (size.u * 10n ** BigInt(got.s)))
    return left > 0 ? String(left) : ''
  }
  const [packs, setPacks] = useState<Record<number, string>>(() =>
    Object.fromEntries(o.lines.map((l) => [l.po_line_id, remaining(l)])),
  )
  const [dates, setDates] = useState<Record<number, string>>({})
  const w = useWrite()

  const lines = o.lines
    .filter((l) => /^\d+$/.test((packs[l.po_line_id] ?? '').trim()) && Number(packs[l.po_line_id]) > 0)
    .map((l) => ({
      po_line_id: l.po_line_id,
      received_packs: Number(packs[l.po_line_id]),
      ...(dates[l.po_line_id] ? { expires_on: dates[l.po_line_id] } : {}),
    }))

  const submit = async () => {
    if (operator === null || lines.length === 0) return
    await w.run(() => orderWrites.receive(o.po_id, { received_by: operator, lines }), {
      invalidate: AFTER,
      ok: (r) =>
        `Received ${r.receipts.length} line(s) into stock. ${r.order.status === 'RECEIVED' ? 'The order is complete.' : 'Some lines are still to come.'}`,
      after: (r) => {
        if (r.order.status === 'RECEIVED') onDone()
      },
    })
  }

  return (
    <div className="mb-2 rounded-card border border-line bg-canvas-2 px-3.5 py-3">
      <p className="mb-2 text-sm text-ink-2">
        Count what came in and read the use-by date off the carton. A date left as it is stays marked as assumed.
      </p>
      {o.lines.map((l) => {
        const days = shelf.get(l.ingredient_id) ?? null
        const assumed = days === null ? '' : addDays(todayIso(), days)
        const edited = dates[l.po_line_id] !== undefined
        return (
          <div key={l.po_line_id} className="grid grid-cols-[minmax(0,1fr)_80px_150px] items-end gap-2 border-b border-line py-1.5 text-base">
            <span className="truncate pb-1.5">
              {l.ingredient_name}{' '}
              <span className="text-sm text-ink-2">
                · ordered {l.final_packs} × {fmtQ(l.pack_size, l.pack_unit)}
                {l.received_qty !== null ? `, ${fmtQ(l.received_qty, l.unit)} in already` : ''}
              </span>
            </span>
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Packs
              <Input
                size="xs"
                numeric
                value={packs[l.po_line_id] ?? ''}
                onChange={(e) => setPacks({ ...packs, [l.po_line_id]: e.target.value })}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Use by{!edited && assumed ? ' (assumed)' : ''}
              <Input
                size="xs"
                type="date"
                value={dates[l.po_line_id] ?? assumed}
                className={cx(!edited && 'italic')}
                onChange={(e) => setDates({ ...dates, [l.po_line_id]: e.target.value })}
              />
            </label>
          </div>
        )
      })}
      {operator === null && (
        <div className="mt-2">
          <OperatorNeeded what="receive a delivery" />
        </div>
      )}
      <div className="mt-2.5 flex gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={operator === null || lines.length === 0}
          pending={w.pending}
          pendingLabel="Receiving…"
          onClick={submit}
        >
          Receive
        </Button>
        <Button variant="ghost" size="sm" onClick={onDone}>
          Close
        </Button>
      </div>
      <OutcomeLine outcome={w.outcome} className="mt-2" />
    </div>
  )
}

/** The design's small outline action chip (§2.3): 12px, radius 12. */
function ActionChip({
  danger = false,
  armed = false,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { danger?: boolean; armed?: boolean }) {
  return (
    <button
      type="button"
      className={cx(
        'h-7 whitespace-nowrap rounded-button border px-3 text-xs disabled:opacity-50',
        danger
          ? cx('border-alert font-bold text-bad-ink hover:bg-alert-wash', armed ? 'bg-alert-wash' : 'bg-surface')
          : 'border-line-strong bg-surface hover:bg-canvas',
      )}
      {...rest}
    />
  )
}

/** Cancel with no preview: the first tap arms it for 4 seconds, the second fires. */
function ArmChip({ children, onConfirm, disabled }: { children: ReactNode; onConfirm: () => void; disabled?: boolean }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 4000)
    return () => clearTimeout(t)
  }, [armed])
  return (
    <ActionChip
      disabled={disabled}
      danger
      armed={armed}
      onClick={() => {
        if (armed) {
          setArmed(false)
          onConfirm()
        } else setArmed(true)
      }}
    >
      {armed ? 'Tap again to cancel' : children}
    </ActionChip>
  )
}
