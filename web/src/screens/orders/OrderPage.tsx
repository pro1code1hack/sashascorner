/**
 * One supplier order as a full page, `#/orders/<po id>` (owner, 2026-09-26: "one
 * single dedicated page for order id", "more readable … the table format for what
 * has been ordered", "I like the format how it looks in money").
 *
 * Laid out like Money › Overview: flat on the page, a big title, the order as a
 * statement (a row per line with dashed rules, subtotal, delivery, a total ruled in
 * ink), and a side column with the receipt photo and what happened when.
 *
 * The whole life of an order happens here now that the Telegram bot is not in use
 * (DECISIONS 18): a draft's packs are set and it is confirmed by a named person
 * (invariant 1), then marked sent, received into stock (a batch and a DELIVERY
 * movement per line) or cancelled. Only the actions the server lists are shown.
 */
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, ErrorBox, Loading, StatusTag, Stepper, cx } from '../../components/ui'
import { gbp, stamp } from '../../lib/format'
import { useOperator } from '../../lib/operator'
import { href } from '../../lib/router'
import { KEYS, orderWrites, stockApi } from '../../lib/stock-api'
import type { PurchaseOrder } from '../../lib/types/stock'
import { dayMonth, fmtD, fmtQ } from '../stock/fmt'
import { OutcomeLine, useWrite } from '../stock/writes'
import { AFTER_ORDER_WRITE, ArmChip, ReceiptSlot, ReceiveSheet } from './OrderBits'
import { statusWord } from './status'

const ROW = 'grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 compact:grid-cols-[minmax(0,1fr)_130px_90px_100px_150px]'

export function OrderPage({ poId }: { poId: number }) {
  const q = useQuery({ queryKey: KEYS.order(poId), queryFn: () => stockApi.order(poId) })
  const o = q.data
  const st = o ? statusWord(o.status) : null
  return (
    <>
      <header className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-3 sm:px-5">
        <a href={href('/orders')} className="text-base font-bold text-brand-ink no-underline hover:underline">
          ‹ Orders
        </a>
        <span aria-hidden="true" className="text-ink-3">
          /
        </span>
        <h1 className="min-w-0 flex-1 truncate text-2xl font-extrabold tracking-[-.01em]">
          {o ? o.supplier_name : 'Order'} <span className="font-normal text-ink-3">#{poId}</span>
        </h1>
        {st && <StatusTag tone={st.tone}>{st.label}</StatusTag>}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-surface">
        <div className="px-4 pb-10 pt-5 sm:px-6 2xl:px-8">
          {q.isPending && <Loading what="Reading the order" />}
          {q.isError && <ErrorBox error={q.error} what={`order #${poId}`} />}
          {o && (
            <div className="grid gap-8 compact:grid-cols-[minmax(0,1fr)_minmax(260px,340px)]">
              <Statement key={`${o.po_id}-${o.status}`} o={o} />
              <aside className="flex min-w-0 flex-col gap-6">
                <section>
                  <h3 className="mb-2 text-lg font-bold">Receipt</h3>
                  <ReceiptSlot o={o} />
                </section>
                <Timeline o={o} />
              </aside>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

/* ------------------------------------------------------------ statement --- */

function Statement({ o }: { o: PurchaseOrder }) {
  const [operator] = useOperator()
  const w = useWrite()
  const [receiving, setReceiving] = useState(false)
  const draft = o.actions.includes('confirm')
  const [packs, setPacks] = useState<Record<number, number>>(() => Object.fromEntries(o.lines.map((l) => [l.po_line_id, l.final_packs])))
  useEffect(() => setPacks(Object.fromEntries(o.lines.map((l) => [l.po_line_id, l.final_packs]))), [o.lines])

  const packsOf = (id: number, fallback: number) => (draft ? (packs[id] ?? fallback) : fallback)
  const subtotal = o.lines.reduce((n, l) => n + packsOf(l.po_line_id, l.final_packs) * l.unit_price_pence, 0)
  const total = subtotal + o.delivery_fee_pence
  const changed = draft && o.lines.some((l) => (packs[l.po_line_id] ?? l.final_packs) !== l.final_packs)
  const anyReceived = o.lines.some((l) => l.received_qty !== null)

  return (
    <section aria-labelledby="order-title" className="min-w-0">
      <h2 id="order-title" className="text-2xl font-extrabold tracking-[-.01em]">
        What was ordered
      </h2>
      <p className="mb-4 mt-1 text-base text-ink-2">
        Created {dayMonth(o.created_at)} · arrives {fmtD(o.target_delivery_date)} · {o.lines.length}{' '}
        {o.lines.length === 1 ? 'line' : 'lines'}
        {o.terms_are_placeholders && (
          <>
            {' · '}
            <a href={href('/suppliers')}>{o.supplier_name}’s terms are still a guess</a>
          </>
        )}
      </p>

      <div role="table" aria-label="Order lines">
        <div role="row" className={cx(ROW, 'hidden border-b-2 border-ink pb-1.5 text-label font-bold uppercase tracking-[.06em] text-ink-3 compact:grid')}>
          <span role="columnheader">Item</span>
          <span role="columnheader" className="text-right">
            Packs
          </span>
          <span role="columnheader" className="text-right">
            Each
          </span>
          <span role="columnheader" className="text-right">
            Total
          </span>
          <span role="columnheader" className="text-right">
            Came in
          </span>
        </div>
        {o.lines.map((l) => {
          const n = packsOf(l.po_line_id, l.final_packs)
          const notes = [
            n !== l.suggested_packs ? `suggested ${l.suggested_packs}` : null,
            l.is_top_up ? 'added to reach the minimum' : null,
            l.checklist_requested_by ? `asked for from the checklist by ${l.checklist_requested_by}` : null,
          ].filter(Boolean)
          return (
            <div role="row" key={l.po_line_id} className={cx(ROW, 'items-center border-b-[1.5px] border-dashed border-line py-2.5 text-lg')}>
              <span role="rowheader" className="min-w-0">
                <a href={href(`/stock/${l.ingredient_id}`)} className="text-ink no-underline hover:underline">
                  {l.ingredient_name}
                </a>
                <span className="block text-sm text-ink-2">
                  {fmtQ(l.pack_size, l.pack_unit)} a pack{notes.length ? ` · ${notes.join(' · ')}` : ''}
                </span>
                {l.cap_reason && <span className="block text-sm text-bad-ink">{l.cap_reason}</span>}
                <span className="block text-sm text-ink-2 compact:hidden">
                  {n} × {gbp(l.unit_price_pence)} = <b className="text-ink">{gbp(n * l.unit_price_pence)}</b>
                  {l.received_qty !== null ? ` · ${fmtQ(l.received_qty, l.unit)} in` : ''}
                </span>
              </span>
              <span role="cell" className="flex justify-end">
                {draft ? (
                  <Stepper
                    label={`packs of ${l.ingredient_name}`}
                    value={<span className="fig inline-block min-w-6 text-center text-lg">{n}</span>}
                    canDecrement={n > 0}
                    onDecrement={() => setPacks((p) => ({ ...p, [l.po_line_id]: Math.max(0, n - 1) }))}
                    onIncrement={() => setPacks((p) => ({ ...p, [l.po_line_id]: n + 1 }))}
                  />
                ) : (
                  <span className="fig">{n}</span>
                )}
              </span>
              <span role="cell" className="fig hidden text-right text-ink-2 compact:block">
                {gbp(l.unit_price_pence)}
              </span>
              <span role="cell" className="fig hidden text-right font-bold compact:block">
                {gbp(n * l.unit_price_pence)}
              </span>
              <span role="cell" className="hidden text-right text-base compact:block">
                {l.received_qty === null ? (
                  <span className="text-ink-3">not yet</span>
                ) : (
                  <>
                    <span className="fig">{fmtQ(l.received_qty, l.unit)}</span>
                    {l.received_expires_at && <span className="block text-sm text-ink-2">use by {dayMonth(l.received_expires_at)}</span>}
                  </>
                )}
              </span>
            </div>
          )
        })}
        <SumRow label="Subtotal" value={gbp(subtotal)} />
        <SumRow label="Delivery" value={o.delivery_fee_pence > 0 ? gbp(o.delivery_fee_pence) : 'free'} quiet={o.delivery_fee_pence === 0} />
        <SumRow label="Total" value={gbp(total)} big />
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        {draft && (
          <Button
            variant="primary"
            disabled={w.pending || total === o.delivery_fee_pence}
            pending={w.pending}
            pendingLabel="Confirming…"
            onClick={() =>
              void w.run(() => orderWrites.confirm(o.po_id, operator, packs), {
                invalidate: AFTER_ORDER_WRITE,
                ok: () => `Confirmed by ${operator}. Mark it sent once it has gone to ${o.supplier_name}.`,
              })
            }
          >
            Confirm order · {gbp(total)}
          </Button>
        )}
        {changed && (
          <Button variant="ghost" onClick={() => setPacks(Object.fromEntries(o.lines.map((l) => [l.po_line_id, l.final_packs])))}>
            Undo changes
          </Button>
        )}
        {o.actions.includes('receive') && !receiving && (
          <Button variant="primary" onClick={() => setReceiving(true)}>
            {anyReceived ? 'Receive the rest' : 'Receive into stock'}
          </Button>
        )}
        {o.actions.includes('mark_sent') && (
          <Button
            disabled={w.pending}
            onClick={() => void w.run(() => orderWrites.markSent(o.po_id, operator), { invalidate: AFTER_ORDER_WRITE, ok: () => 'Marked sent.' })}
          >
            Mark sent to {o.supplier_name}
          </Button>
        )}
        {o.actions.includes('cancel') && (
          <ArmChip
            disabled={w.pending}
            onConfirm={() => void w.run(() => orderWrites.cancel(o.po_id, operator), { invalidate: AFTER_ORDER_WRITE, ok: () => 'Cancelled.' })}
          >
            Cancel order
          </ArmChip>
        )}
      </div>
      <OutcomeLine outcome={w.outcome} className="mt-2" />
      {draft && (
        <p className="mt-2 text-sm text-ink-2">
          Nothing is ordered until you confirm. Set the packs first; a line at 0 stays on the order as declined.
        </p>
      )}
      {receiving && (
        <div className="mt-4">
          <ReceiveSheet o={o} onDone={() => setReceiving(false)} />
        </div>
      )}
      {o.actions.includes('receive') && (
        <p className="mt-2 text-sm text-ink-2">Receiving puts it on the shelf: each line becomes a batch with its use-by date, and Stock updates straight away.</p>
      )}
    </section>
  )
}

function SumRow({ label, value, big, quiet }: { label: string; value: ReactNode; big?: boolean; quiet?: boolean }) {
  return (
    <div
      role="row"
      className={cx(
        'grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 py-1.5',
        big ? 'border-b-2 border-ink text-xl font-bold' : 'border-b-[1.5px] border-dashed border-line text-lg',
      )}
    >
      <span role="rowheader">{label}</span>
      <span role="cell" className={cx('fig text-right compact:mr-[166px]', quiet && 'text-ink-2')}>
        {value}
      </span>
    </div>
  )
}

/* ------------------------------------------------------------- timeline --- */

function Timeline({ o }: { o: PurchaseOrder }) {
  const steps: [string, string | null][] = [
    ['Created', stamp(o.created_at)],
    ['Confirmed', o.confirmed_at ? `${stamp(o.confirmed_at)}${o.confirmed_by ? ` · ${o.confirmed_by}` : ''}` : null],
    ['Sent', o.sent_at ? `${stamp(o.sent_at)}${o.sent_by ? ` · ${o.sent_by}` : ''}` : null],
    ['Received', o.status === 'RECEIVED' ? 'all lines in' : o.lines.some((l) => l.received_qty !== null) ? 'partly' : null],
  ]
  if (o.cancelled_at) steps.push(['Cancelled', `${stamp(o.cancelled_at)}${o.cancelled_by ? ` · ${o.cancelled_by}` : ''}${o.cancel_reason ? ` · ${o.cancel_reason}` : ''}`])
  return (
    <section>
      <h3 className="mb-2 text-lg font-bold">What happened</h3>
      <ol>
        {steps.map(([k, v]) => (
          <li key={k} className="grid grid-cols-[90px_minmax(0,1fr)] gap-2 border-b-[1.5px] border-dashed border-line py-1.5 text-base">
            <span className={v ? 'text-ink' : 'text-ink-3'}>{k}</span>
            <span className={v ? 'text-ink-2' : 'text-ink-3'}>{v ?? '—'}</span>
          </li>
        ))}
      </ol>
      {o.routing_reason && <p className="mt-2 text-sm text-ink-2">{o.routing_reason}</p>}
    </section>
  )
}
