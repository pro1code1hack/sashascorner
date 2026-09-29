/**
 * One online order as a full page, `#/shop/orders/<id>`, laid out like Money ›
 * Overview and the supplier order page: the order as a statement (a row per line
 * with its options, subtotal, the reward discount, a total ruled in ink), then the
 * actions the status allows (contract §3.8), and a side column with the customer,
 * payment, the staff note and what happened when.
 *
 * Every write carries the operator's name as `by`. Collecting an order is what
 * records the sale (contract §3.1), so the button says so. A PAID online order
 * offers "Refund" (contract §10.F): the server asks the provider and refuses with
 * its sentence when it cannot. "Print ticket" prints the kitchen ticket alone
 * (code, due time, customer, table, lines, note) through the `@media print`
 * rules below; nothing else on the page reaches the paper.
 */
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, ConfirmTwiceButton, ErrorBox, Field, Input, Loading, StatusTag, Textarea, cx } from '../../components/ui'
import { gbp, stamp } from '../../lib/format'
import { useOperator } from '../../lib/operator'
import { href } from '../../lib/router'
import { SHOP_KEY, orderWrites, useShopOrder } from '../../lib/shop-api'
import type { OrderAdmin, OrderStatus } from '../../lib/types/shop'
import { OutcomeLine, useWrite } from '../stock/writes'
import { ShopGate, dayWord, diningWord, dueWord, nextAction, notifyChannels, optionsSummary, paymentWord, plural, statusWord, unitsCount } from './shared'

const ROW = 'grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 compact:grid-cols-[minmax(0,1fr)_60px_90px_100px]'
const AFTER = [SHOP_KEY]

/**
 * Print: only the ticket. Everything on the page is hidden by visibility (so the
 * layout is untouched) and the ticket is laid over the sheet. Colours are the
 * page's own tokens; nothing here is a screen colour.
 */
const PRINT_CSS = `
@media print {
  body * { visibility: hidden !important; }
  #shop-print-ticket, #shop-print-ticket * { visibility: visible !important; }
  #shop-print-ticket { display: block !important; position: fixed; inset: 0; width: auto; max-width: 92mm; padding: 8mm 6mm; margin: 0; background: var(--color-surface); color: var(--color-ink); font-size: 13pt; line-height: 1.3; }
  #shop-print-ticket .pt-code { font-size: 26pt; font-weight: 800; letter-spacing: -.01em; }
  #shop-print-ticket .pt-due { font-size: 20pt; font-weight: 800; }
  #shop-print-ticket .pt-meta { font-size: 14pt; font-weight: 700; margin-top: 2mm; }
  #shop-print-ticket .pt-lines { list-style: none; margin: 4mm 0 0; padding: 4mm 0 0; border-top: 1.5px dashed var(--color-ink); }
  #shop-print-ticket .pt-lines li { margin: 0 0 2.5mm; }
  #shop-print-ticket .pt-qty { font-weight: 800; }
  #shop-print-ticket .pt-opts { display: block; padding-left: 8mm; font-size: 12pt; }
  #shop-print-ticket .pt-note { margin-top: 4mm; padding: 2mm 3mm; border-left: 3px solid var(--color-ink); font-size: 12pt; }
  #shop-print-ticket .pt-foot { margin-top: 4mm; padding-top: 3mm; border-top: 1.5px dashed var(--color-ink); font-size: 11pt; }
}
`

export function OrderPage({ orderId }: { orderId: number }) {
  const q = useShopOrder(orderId, true)
  const o = q.data
  const st = o ? statusWord(o.status) : null
  return (
    <>
      <header className="flex flex-none flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-4 py-3 sm:px-5">
        <a href={href('/shop/orders')} className="text-base font-bold text-brand-ink no-underline hover:underline">
          ‹ All orders
        </a>
        <span aria-hidden="true" className="text-ink-3">
          /
        </span>
        <h1 className="fig min-w-0 flex-1 truncate text-2xl font-extrabold tracking-[-.01em]">
          {o ? o.code_display : 'Order'} {o && <span className="font-normal text-ink-2">· {o.customer_name}</span>}
        </h1>
        {st && <StatusTag tone={st.tone}>{st.label}</StatusTag>}
        {o && (
          <span className="flex w-full items-center gap-2 sm:w-auto">
            <CopyCode code={o.code_display} />
            <Button size="sm" onClick={() => window.print()}>
              Print ticket
            </Button>
          </span>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-surface">
        <div className="px-4 pb-10 pt-5 sm:px-6 2xl:px-8">
          <ShopGate>
            {q.isPending && <Loading what="Reading the order" />}
            {q.isError && <ErrorBox error={q.error} what={`order #${orderId}`} />}
            {o && (
              <div className="grid gap-8 compact:grid-cols-[minmax(0,1fr)_minmax(260px,340px)]">
                <Statement key={`${o.id}-${o.status}-${o.payment_status}`} o={o} />
                <aside className="flex min-w-0 flex-col gap-6">
                  <Customer o={o} />
                  <Payment o={o} />
                  <StaffNote key={`${o.id}-${o.staff_note ?? ''}`} o={o} />
                  <Timeline o={o} />
                </aside>
              </div>
            )}
          </ShopGate>
        </div>
      </div>
      {o && <PrintTicket o={o} />}
    </>
  )
}

/** Copies "SC-XXXXXX" for a message to the customer; says so for a moment. */
function CopyCode({ code }: { code: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  useEffect(() => {
    if (state === 'idle') return
    const t = window.setTimeout(() => setState('idle'), 2000)
    return () => window.clearTimeout(t)
  }, [state])
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setState('copied')
    } catch {
      setState('failed')
    }
  }
  return (
    <Button size="sm" onClick={() => void copy()} aria-live="polite">
      {state === 'copied' ? 'Copied' : state === 'failed' ? 'Could not copy' : 'Copy code'}
    </Button>
  )
}

/* ------------------------------------------------------------ statement --- */

type Ending = 'cancel' | 'reject' | 'refund'

function Statement({ o }: { o: OrderAdmin }) {
  const [operator] = useOperator()
  const w = useWrite()
  const [ending, setEnding] = useState<Ending | null>(null)
  const [reason, setReason] = useState('')
  const due = dueWord(o.minutes_until_due)
  const day = dayWord(o)
  const allowed = o.allowed_transitions
  const act = nextAction(o.status, allowed)
  const live = ['NEW', 'ACCEPTED', 'PREPARING', 'READY', 'PENDING_PAYMENT'].includes(o.status)
  const canMarkPaid = o.payment_method === 'COUNTER' && o.payment_status !== 'PAID' && live && o.status !== 'PENDING_PAYMENT'
  const may = (s: OrderStatus) => allowed.includes(s)
  const done = () => {
    setEnding(null)
    setReason('')
  }

  const move = (to: OrderStatus, why?: string) =>
    void w.run(() => orderWrites.status(o.id, to, operator, why), {
      invalidate: AFTER,
      ok: () => {
        done()
        return to === 'COLLECTED' ? 'Collected: the sale is recorded and any stamps are on the card.' : `${statusWord(to).label}.`
      },
    })
  const refund = (why?: string) =>
    void w.run(() => orderWrites.refund(o.id, operator, why), {
      invalidate: AFTER,
      ok: () => {
        done()
        return `Refunded ${gbp(o.total_pence)} through the payment provider.`
      },
    })

  const endingWords: Record<Ending, { label: string; hint: string; placeholder: string; armed: string; confirm: string }> = {
    reject: { label: 'Why it is rejected', hint: 'The customer sees this on their order page.', placeholder: 'Sold out of the pumpkin sauce', armed: 'Tap again to reject', confirm: 'Reject this order' },
    cancel: { label: 'Why it is cancelled', hint: 'The customer sees this on their order page.', placeholder: 'Customer asked to cancel', armed: 'Tap again to cancel', confirm: 'Cancel this order' },
    refund: { label: 'Why it is refunded', hint: 'Kept with the order; the provider gets it as the refund reason.', placeholder: 'Order could not be made', armed: `Tap again to refund ${gbp(o.total_pence)}`, confirm: `Refund ${gbp(o.total_pence)}` },
  }

  return (
    <section aria-labelledby="order-title" className="min-w-0">
      <h2 id="order-title" className="text-2xl font-extrabold tracking-[-.01em]">
        What was ordered
      </h2>
      <p className="mb-4 mt-1 text-base text-ink-2">
        Placed {o.placed_local} · {o.asap ? 'ASAP, ' : ''}for {day ? `${day} ${o.requested_local}` : o.requested_local}
        {live && (
          <>
            {' '}
            (<span className={cx(due.late && 'font-bold text-bad-ink')}>{due.text}</span>)
          </>
        )}{' '}
        · {diningWord(o.dining)}
        {o.table ? <span className="font-bold text-ink"> · Table {o.table}</span> : ''} · {plural(unitsCount(o), 'item')}
      </p>

      <div role="table" aria-label="Order lines">
        <div role="row" className={cx(ROW, 'hidden border-b-2 border-ink pb-1.5 text-label font-bold uppercase tracking-[.06em] text-ink-3 compact:grid')}>
          <span role="columnheader">Item</span>
          <span role="columnheader" className="text-right">
            Qty
          </span>
          <span role="columnheader" className="text-right">
            Each
          </span>
          <span role="columnheader" className="text-right">
            Total
          </span>
        </div>
        {o.lines.map((l) => (
          <div role="row" key={l.id} className={cx(ROW, 'items-center border-b-[1.5px] border-dashed border-line py-2.5 text-lg')}>
            <span role="rowheader" className="min-w-0">
              <span className="text-ink">
                {l.name}
                {l.size_label && <span className="text-ink-2"> ({l.size_label})</span>}
              </span>
              {l.options.length > 0 && <span className="block text-sm text-ink-2">{optionsSummary(l.options)}</span>}
              <span className="block text-sm text-ink-2 compact:hidden">
                {l.qty} × {gbp(l.unit_price_pence)} = <b className="text-ink">{gbp(l.line_total_pence)}</b>
              </span>
            </span>
            <span role="cell" className="fig text-right">
              {l.qty}
            </span>
            <span role="cell" className="fig hidden text-right text-ink-2 compact:block">
              {gbp(l.unit_price_pence)}
            </span>
            <span role="cell" className="fig hidden text-right font-bold compact:block">
              {gbp(l.line_total_pence)}
            </span>
          </div>
        ))}
        <SumRow label="Subtotal" value={gbp(o.subtotal_pence)} />
        {o.discount_pence > 0 && <SumRow label="Free drink (Rewards)" value={`−${gbp(o.discount_pence)}`} />}
        <SumRow label="Total" value={gbp(o.total_pence)} big />
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        {act && (
          <Button variant="primary" pending={w.pending} pendingLabel={act.pending} onClick={() => move(act.to)}>
            {act.label}
            {act.to === 'COLLECTED' && o.payment_status !== 'PAID' && o.payment_method === 'COUNTER' ? ' · paid' : ''}
          </Button>
        )}
        {o.status === 'ACCEPTED' && may('READY') && (
          <Button disabled={w.pending} onClick={() => move('READY')}>
            Skip to ready
          </Button>
        )}
        {canMarkPaid && (
          <Button disabled={w.pending} onClick={() => void w.run(() => orderWrites.paid(o.id, operator), { invalidate: AFTER, ok: () => 'Marked paid.' })}>
            Mark paid
          </Button>
        )}
        {may('REJECTED') && ending === null && (
          <Button variant="danger" disabled={w.pending} onClick={() => setEnding('reject')}>
            Reject
          </Button>
        )}
        {may('CANCELLED') && ending === null && (
          <Button variant="danger" disabled={w.pending} onClick={() => setEnding('cancel')}>
            Cancel order
          </Button>
        )}
        {o.refundable && ending === null && (
          <Button variant="danger-soft" disabled={w.pending} onClick={() => setEnding('refund')}>
            Refund
          </Button>
        )}
      </div>
      <OutcomeLine outcome={w.outcome} className="mt-2" />

      {ending !== null && (
        <div className="mt-4 rounded-card border border-line bg-canvas-2 px-3.5 py-3">
          <Field label={endingWords[ending].label} hint={endingWords[ending].hint}>
            <Input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} placeholder={endingWords[ending].placeholder} />
          </Field>
          {ending !== 'refund' && o.payment_status === 'PAID' && (
            <p className="mt-2 text-sm text-ink-2">
              This order is paid: {ending === 'cancel' ? 'cancelling' : 'rejecting'} records that a refund is needed
              {o.refundable ? '; use Refund afterwards to send the money back through the provider' : ', and you refund it yourself'}.
            </p>
          )}
          {ending === 'refund' && (
            <p className="mt-2 text-sm text-ink-2">
              The whole {gbp(o.total_pence)} goes back to the card it was paid with; the provider decides how long that takes. The order itself is not cancelled by this.
            </p>
          )}
          <div className="mt-2.5 flex flex-wrap gap-2">
            <ConfirmTwiceButton
              armedLabel={endingWords[ending].armed}
              pending={w.pending}
              pendingLabel={ending === 'refund' ? 'Refunding…' : 'Recording…'}
              onConfirm={() => (ending === 'refund' ? refund(reason.trim() || undefined) : move(ending === 'reject' ? 'REJECTED' : 'CANCELLED', reason.trim() || undefined))}
            >
              {endingWords[ending].confirm}
            </ConfirmTwiceButton>
            <Button variant="ghost" size="sm" onClick={done}>
              Keep it
            </Button>
          </div>
        </div>
      )}

      {o.status === 'READY' && (
        <p className="mt-2 text-sm text-ink-2">Collected records the sale (one till line per item, so stock depletes tonight) and stamps the Rewards card if the customer has one.</p>
      )}
      {o.cancel_reason && (
        <p className="mt-2 text-sm text-ink-2">
          {statusWord(o.status).label} by {o.cancelled_by ?? 'unknown'}: {o.cancel_reason}
        </p>
      )}
    </section>
  )
}

function SumRow({ label, value, big }: { label: string; value: ReactNode; big?: boolean }) {
  return (
    <div
      role="row"
      className={cx(
        'grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 py-1.5',
        big ? 'border-b-2 border-ink text-xl font-bold' : 'border-b-[1.5px] border-dashed border-line text-lg',
      )}
    >
      <span role="rowheader">{label}</span>
      <span role="cell" className="fig text-right">
        {value}
      </span>
    </div>
  )
}

/* --------------------------------------------------------------- ticket --- */

/** The kitchen ticket, on paper only: `hidden` on screen, shown by PRINT_CSS. */
function PrintTicket({ o }: { o: OrderAdmin }) {
  const day = dayWord(o)
  return (
    <>
      <style>{PRINT_CSS}</style>
      <div id="shop-print-ticket" className="hidden" aria-hidden="true">
        <div className="pt-code">{o.code_display}</div>
        <div className="pt-due">
          {o.asap ? 'ASAP · ' : ''}
          {day ? `${day} ` : ''}
          {o.requested_local}
        </div>
        <div className="pt-meta">
          {o.customer_name} · {diningWord(o.dining)}
          {o.table ? ` · Table ${o.table}` : ''}
        </div>
        <ul className="pt-lines">
          {o.lines.map((l) => (
            <li key={l.id}>
              <span className="pt-qty">{l.qty} ×</span> {l.name}
              {l.size_label ? ` (${l.size_label})` : ''}
              {l.options.length > 0 && <span className="pt-opts">{l.options.map((op) => op.name).join(' · ')}</span>}
            </li>
          ))}
        </ul>
        {o.note && <p className="pt-note">Note: {o.note}</p>}
        <p className="pt-foot">
          {plural(unitsCount(o), 'item')} · {paymentWord(o.payment_method, o.payment_status)} · {gbp(o.total_pence)}
        </p>
      </div>
    </>
  )
}

/* ------------------------------------------------------------ side column --- */

function Side({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-lg font-bold">{title}</h3>
      {children}
    </section>
  )
}

function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl>
      {rows.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[90px_minmax(0,1fr)] gap-2 border-b-[1.5px] border-dashed border-line py-1.5 text-base">
          <dt className="text-ink-2">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function Customer({ o }: { o: OrderAdmin }) {
  const rows: [string, ReactNode][] = [['Name', o.customer_name]]
  if (o.customer_phone) rows.push(['Phone', <a href={`tel:${o.customer_phone}`}>{o.customer_phone}</a>])
  if (o.customer_email) rows.push(['Email', <a href={`mailto:${o.customer_email}`}>{o.customer_email}</a>])
  rows.push([
    'Rewards',
    o.member ? (
      <>
        <a href={href(`/loyalty/members/${o.member.id}`)}>{o.member.first_name}'s card</a>
        <span className="text-ink-2"> · {plural(o.member.stamps_current, 'stamp')}</span>
        {o.reward_id !== null && <span className="text-ink-2"> · free drink used here</span>}
      </>
    ) : (
      <span className="text-ink-2">not a member</span>
    ),
  ])
  const channels = notifyChannels(o)
  rows.push([
    'Updates',
    channels.length ? `by ${channels.join(', ')}` : <span className="text-ink-2">{o.customer_phone ? 'phone given, no texts opted in' : 'no way to reach them'}</span>,
  ])
  rows.push(['Dining', o.table ? `${diningWord(o.dining)} · Table ${o.table}` : diningWord(o.dining)])
  rows.push(['Allergies', o.allergy_ack ? 'Notice acknowledged at checkout' : <span className="font-bold text-bad-ink">Notice not acknowledged</span>])
  if (o.note) rows.push(['Their note', <span className="italic">“{o.note}”</span>])
  return (
    <Side title="Customer">
      <KV rows={rows} />
    </Side>
  )
}

function Payment({ o }: { o: OrderAdmin }) {
  const rows: [string, ReactNode][] = [['Status', paymentWord(o.payment_method, o.payment_status)]]
  if (o.paid_at) rows.push(['Paid at', stamp(o.paid_at)])
  if (o.payment_ref) rows.push(['Provider', <span className="fig break-all text-sm">{o.payment_ref}</span>])
  if (o.payment_intent) rows.push(['Payment id', <span className="fig break-all text-sm">{o.payment_intent}</span>])
  if (o.pos_ref) rows.push(['Till ref', <span className="fig break-all text-sm">{o.pos_ref}</span>])
  if (o.sale_receipt_id) rows.push(['Sale', <span className="fig text-sm">{o.sale_receipt_id}</span>])
  if (o.refundable) rows.push(['Refund', <span className="text-ink-2">possible through the provider (button under the order)</span>])
  return (
    <Side title="Payment">
      <KV rows={rows} />
    </Side>
  )
}

function StaffNote({ o }: { o: OrderAdmin }) {
  const [operator] = useOperator()
  const w = useWrite()
  const [text, setText] = useState(o.staff_note ?? '')
  useEffect(() => setText(o.staff_note ?? ''), [o.staff_note])
  const dirty = text.trim() !== (o.staff_note ?? '').trim()
  return (
    <Side title="Staff note">
      <Textarea rows={2} maxLength={300} className="min-h-0" placeholder="Only the counter sees this" value={text} onChange={(e) => setText(e.target.value)} />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={!dirty}
          pending={w.pending}
          pendingLabel="Saving…"
          onClick={() => void w.run(() => orderWrites.note(o.id, text.trim(), operator), { invalidate: AFTER, ok: () => 'Note saved.' })}
        >
          Save note
        </Button>
        <OutcomeLine outcome={w.outcome} />
      </div>
    </Side>
  )
}

const BAD_EVENTS = new Set(['pos_failed', 'notify_failed', 'refund_failed', 'refund_needed', 'reward_unavailable'])

function Timeline({ o }: { o: OrderAdmin }) {
  const events = [...o.events].sort((a, b) => a.at.localeCompare(b.at))
  return (
    <Side title="What happened">
      {events.length === 0 ? (
        <p className="text-base text-ink-2">No events recorded.</p>
      ) : (
        <ol>
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`} className="grid grid-cols-[minmax(0,1fr)] gap-0.5 border-b-[1.5px] border-dashed border-line py-1.5 text-base">
              <span>
                <span className={cx('font-semibold', BAD_EVENTS.has(e.kind) && 'text-bad-ink')}>{eventWord(e.kind)}</span>
                <span className="text-ink-2"> · {e.actor}</span>
              </span>
              <span className="text-sm text-ink-2">
                {stamp(e.at)}
                {e.detail ? ` · ${e.detail}` : ''}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Side>
  )
}

function eventWord(kind: string): string {
  const words: Record<string, string> = {
    placed: 'Placed',
    paid: 'Paid',
    accepted: 'Accepted',
    preparing: 'Started making',
    ready: 'Ready to collect',
    collected: 'Collected',
    cancelled: 'Cancelled',
    rejected: 'Rejected',
    expired: 'Expired unpaid',
    note: 'Note',
    notified: 'Customer told',
    notify_failed: 'Could not tell the customer',
    stamped: 'Card stamped',
    sale_recorded: 'Sale recorded',
    pos_pushed: 'Sent to the till',
    pos_failed: 'Till did not take it',
    refund_needed: 'Refund needed',
    refunded: 'Refunded',
    refund_failed: 'Refund did not go through',
    reward_released: 'Free drink released',
    reward_unavailable: 'Free drink no longer available',
  }
  return words[kind] ?? kind.replace(/_/g, ' ')
}
