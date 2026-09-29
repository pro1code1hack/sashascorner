/**
 * Online orders › Live orders (`#/shop`): the board for the counter. Four columns,
 * New / Accepted / Preparing / Ready, one kitchen-style ticket per order (owner,
 * 2026-09-29: readable from a metre away — code and due time large, lines in
 * large type with options beneath, the note in a strip, one full-width next
 * action; the board is the one place cards are the point). Orders due more than
 * an hour out sit in "Later today" until their time comes. Polls every 15 s; a
 * chime rings when an order arrives (alert.ts). Nothing is optimistic.
 *
 * Every write carries the operator's name (`by`). Reject and cancel sit behind a
 * small "More" control on the ticket; the order's page has the whole story.
 *
 * Keyboard: a ticket is focusable; Enter on it fires its next action, Esc backs
 * out of a pending reject / cancel. The board carries one polite live region
 * that names a ticket when it arrives (not when the counter moves one along), so
 * a screen reader hears what the chime means; a status strip under the header
 * says how many new orders are waiting and since when, until they are accepted.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Button, ConfirmTwiceButton, Empty, ErrorBox, Field, InfoPanel, Input, LinkButton, Loading, PageBody, PageHeader, Pill, StatusTag, cx } from '../../components/ui'
import { LIVE } from '../../lib/api'
import { gbp } from '../../lib/format'
import { useOperator } from '../../lib/operator'
import { href } from '../../lib/router'
import { SHOP_KEY, orderWrites, useShopOrders, useShopSummary } from '../../lib/shop-api'
import type { OrderAdmin, OrderStatus } from '../../lib/types/shop'
import { OutcomeLine, useWrite } from '../stock/writes'
import { useNewOrderAlert } from './alert'
import { ShopGate, clockOf, dayWord, daysAhead, diningWord, linesSummary, nextAction, notifyChannels, orderPath, plural, statusWord, unitsCount } from './shared'
import { SoundBar, SoundToggle } from './SoundToggle'

/** Due further out than this waits in "Later today" rather than crowding the board. */
const LATER_AFTER_MINUTES = 60
/** A ticket keeps its strong left edge this long after it was placed. */
const FRESH_MS = 60_000
/** The shell's title for this route; the alert flashes it while orders wait. */
const PAGE_TITLE = "Live orders · Sasha's Corner"

const COLUMNS: ReadonlyArray<{ status: OrderStatus; head: string; empty: string }> = [
  { status: 'NEW', head: 'New', empty: 'No new orders. New ones appear here within 15 seconds.' },
  { status: 'ACCEPTED', head: 'Accepted', empty: 'Nothing accepted and waiting to be made.' },
  { status: 'PREPARING', head: 'Preparing', empty: 'Nothing being made right now.' },
  { status: 'READY', head: 'Ready', empty: 'Nothing waiting on the counter.' },
]

/**
 * What a screen reader hears when an order joins the board: "Order SC-… for Anna,
 * due 14:20". Only ids the board has not seen count, so the counter's own moves
 * between columns are silent.
 */
function useArrivals(rows: OrderAdmin[]): string {
  const seen = useRef<Set<number> | null>(null)
  const [text, setText] = useState('')
  useEffect(() => {
    const ids = new Set(rows.map((o) => o.id))
    if (seen.current === null) {
      seen.current = ids
      return
    }
    const fresh = rows.filter((o) => !seen.current?.has(o.id))
    seen.current = ids
    if (fresh.length === 0) return
    setText(fresh.map((o) => `Order ${o.code_display} for ${firstName(o.customer_name)}, ${o.asap ? 'as soon as possible' : `due ${dueClock(o)}`}`).join('. '))
  }, [rows])
  return text
}

/** "14:03" in London for an instant. */
function clockAt(ms: number): string {
  return new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' }).format(new Date(ms))
}

/** "2 new orders waiting since 14:03", or '' when nothing is NEW: the chime's visual twin. */
function waitingWords(items: OrderAdmin[] | undefined): string {
  const fresh = (items ?? []).filter((o) => o.status === 'NEW')
  if (fresh.length === 0) return ''
  const since = Math.min(...fresh.map((o) => Date.parse(o.placed_at)).filter((t) => !Number.isNaN(t)))
  return `${plural(fresh.length, 'new order')} waiting${Number.isFinite(since) ? ` since ${clockAt(since)}` : ''}`
}

/** The clock, ticking every `ms`, for countdowns and the fresh edge. */
function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(t)
  }, [ms])
  return now
}

export function LiveOrdersScreen() {
  const summary = useShopSummary(true)
  const q = useShopOrders({ status: 'live', page_size: 200 }, true)
  const alert = useNewOrderAlert(q.data?.items, PAGE_TITLE)
  const s = summary.data
  const counts = s?.counts
  const live = counts ? counts.new + counts.accepted + counts.preparing + counts.ready : null
  const waiting = waitingWords(q.data?.items)
  return (
    <>
      <PageHeader
        title="Live orders"
        subtitle="Orders placed online for collection, as they come in. Takeaway unless it says eat in."
        actions={LIVE ? <SoundToggle alert={alert} /> : undefined}
        saved={
          s ? (
            <span aria-live="polite">
              {live === null ? '' : `${plural(live, 'order')} in progress`}
              {counts && counts.today_collected > 0 ? ` · ${counts.today_collected} collected today` : ''}
              {s.today_revenue_pence > 0 ? ` · ${gbp(s.today_revenue_pence)} today` : ''}
            </span>
          ) : undefined
        }
      />
      {LIVE && <SoundBar alert={alert} />}
      {/* Always mounted, so the live region exists before its text arrives. Stays until every NEW order is accepted. */}
      <p
        role="status"
        aria-live="polite"
        className={cx('flex-none text-base font-bold text-ink', waiting !== '' && 'border-b border-line-soft bg-brand-wash px-5 py-2 text-brand-ink')}
      >
        {waiting}
      </p>
      <PageBody className="compact:px-5">
        <ShopGate>
          <p className="sr-only">With a ticket focused, Enter moves it along and Escape backs out of a reject or cancel.</p>
          {summary.isError && <ErrorBox error={summary.error} what="the shop" />}
          {s && !s.enabled && (
            <div className="mb-4">
              <InfoPanel
                action={
                  <a href={href('/shop/settings')} className="font-bold underline underline-offset-2">
                    Open settings
                  </a>
                }
              >
                <strong>Ordering is off.</strong> The shop shows customers the closed message and takes no orders.
                Switch it on in settings when the counter is ready.
              </InfoPanel>
            </div>
          )}
          {s && s.enabled && !s.open_now && (
            <p className="mb-4 text-sm text-ink-2">Outside ordering hours right now: customers see the next opening time, and orders already placed still show here.</p>
          )}
          <Board q={q} />
        </ShopGate>
      </PageBody>
    </>
  )
}

/* --------------------------------------------------------------- board --- */

function Board({ q }: { q: ReturnType<typeof useShopOrders> }) {
  const now = useNow(10_000)
  const items = q.data?.items
  const { board, later, pending } = useMemo(() => {
    const all = items ?? []
    const onBoard = (o: OrderAdmin) => o.status !== 'PENDING_PAYMENT' && dueIn(o, now) <= LATER_AFTER_MINUTES
    const board = all.filter(onBoard)
    const later = all.filter((o) => o.status !== 'PENDING_PAYMENT' && !onBoard(o)).sort((a, b) => dueIn(a, now) - dueIn(b, now))
    const pending = all.filter((o) => o.status === 'PENDING_PAYMENT')
    return { board, later, pending }
  }, [items, now])
  const arrivals = useArrivals(board)

  if (q.isPending) return <Loading what="Reading the board" />
  if (q.isError) return <ErrorBox error={q.error} what="live orders" />

  const total = board.length + later.length
  const otherDays = later.some((o) => daysAhead(o, now) > 0)
  return (
    <div className="flex flex-col gap-5">
      <p role="status" aria-live="polite" className="sr-only">
        {arrivals}
      </p>
      {total === 0 && pending.length === 0 ? (
        <Empty roomy>Nothing in progress. Orders placed online appear here the moment they are placed; the board checks every 15 seconds.</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 wide:grid-cols-4">
          {COLUMNS.map((c) => (
            <Column key={c.status} c={c} rows={board.filter((o) => o.status === c.status).sort((a, b) => dueIn(a, now) - dueIn(b, now))} now={now} />
          ))}
        </div>
      )}

      {later.length > 0 && (
        <section aria-labelledby="later-head" className="flex flex-col gap-2">
          <h2 id="later-head" className="flex items-baseline gap-2 border-b border-line pb-1.5 text-label font-bold uppercase tracking-[.06em] text-ink-3">
            {otherDays ? 'Later' : 'Later today'} <span className="fig text-base text-ink">{later.length}</span>
          </h2>
          <p className="text-sm text-ink-2">
            {otherDays ? 'Scheduled for more than an hour from now, or for another day.' : 'Scheduled for more than an hour from now.'} They move onto the board when they are due within the hour.
          </p>
          <ul className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
            {later.map((o) => (
              <LaterRow key={o.id} o={o} now={now} />
            ))}
          </ul>
        </section>
      )}

      {pending.length > 0 && (
        <p className="text-sm text-ink-2">
          {plural(pending.length, 'order')} awaiting online payment; they join the board once payment confirms, and expire after 30 minutes if not.
        </p>
      )}
    </div>
  )
}

function Column({ c, rows, now }: { c: (typeof COLUMNS)[number]; rows: OrderAdmin[]; now: number }) {
  return (
    <section aria-labelledby={`col-${c.status}`} className="flex min-w-0 flex-col gap-2.5 wide:max-h-[calc(100vh-220px)] wide:overflow-y-auto wide:pr-1">
      <h2 id={`col-${c.status}`} className="sticky top-0 z-[1] flex items-baseline gap-2 border-b border-line bg-canvas pb-1.5 text-label font-bold uppercase tracking-[.06em] text-ink-3">
        {c.head}
        <span className="fig text-base text-ink">{rows.length}</span>
      </h2>
      {rows.length === 0 ? <p className="py-3 text-sm text-ink-2">{c.empty}</p> : rows.map((o) => <Ticket key={o.id} o={o} now={now} />)}
    </section>
  )
}

/* -------------------------------------------------------------- ticket --- */

/** Minutes until the slot, from the clock rather than the poll, so it counts down. */
function dueIn(o: OrderAdmin, now: number): number {
  const t = Date.parse(o.requested_at)
  if (Number.isNaN(t)) return o.minutes_until_due
  return Math.round((t - now) / 60_000)
}

/** "14:20" from the server's "Today 14:20"; "Tomorrow 09:00" keeps its day. */
function dueClock(o: OrderAdmin): string {
  const day = dayWord(o)
  return day ? `${day} ${clockOf(o.requested_local)}` : clockOf(o.requested_local)
}

function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] ?? name
}

function Ticket({ o, now }: { o: OrderAdmin; now: number }) {
  const [operator] = useOperator()
  const w = useWrite()
  const [more, setMore] = useState(false)
  const [reason, setReason] = useState('')
  // Bumped on Esc: re-keys the confirm buttons so an armed one disarms.
  const [armKey, setArmKey] = useState(0)
  const mins = dueIn(o, now)
  const late = mins < 0
  const fresh = now - Date.parse(o.placed_at) < FRESH_MS
  const act = nextAction(o.status, o.allowed_transitions)
  const may = (s: OrderStatus) => o.allowed_transitions.includes(s)
  const paid = o.payment_status === 'PAID'
  const channels = notifyChannels(o)
  const move = (to: OrderStatus, why?: string) =>
    void w.run(() => orderWrites.status(o.id, to, operator, why), {
      invalidate: [SHOP_KEY],
      after: () => {
        setMore(false)
        setReason('')
      },
    })
  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === 'Escape') {
      if (more) {
        e.preventDefault()
        setMore(false)
        setReason('')
        setArmKey((k) => k + 1)
        e.currentTarget.focus()
      }
      return
    }
    // Enter on the ticket itself (not on a button or field inside it) is its next action.
    if (e.key === 'Enter' && e.target === e.currentTarget && act && !w.pending) {
      e.preventDefault()
      move(act.to)
    }
  }

  return (
    <article
      tabIndex={0}
      onKeyDown={onKey}
      aria-label={`Order ${o.code_display} for ${o.customer_name}, ${o.asap ? 'as soon as possible' : `due ${dueClock(o)}`}${o.table ? `, table ${o.table}` : ''}${act ? `. Enter: ${act.label}` : ''}`}
      className={cx(
        'flex flex-col gap-2.5 rounded-card-lg border bg-surface px-3.5 py-3 shadow-raised focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand',
        late ? 'border-alert' : 'border-line',
        fresh && 'border-l-4 border-l-brand',
      )}
    >
      <header className="flex items-start justify-between gap-3">
        <a href={href(orderPath(o.id))} className="fig text-2xl font-extrabold tracking-[-.01em] text-ink no-underline hover:underline">
          {o.code_display}
        </a>
        <span className="text-right">
          <span className="fig block text-2xl font-extrabold tracking-[-.01em]">{dueClock(o)}</span>
          <span className={cx('fig block text-sm', late ? 'font-bold text-bad-ink' : 'text-ink-2')}>
            {late ? `late by ${-mins} min` : mins === 0 ? 'due now' : `in ${mins} min`}
            {o.asap ? ' · ASAP' : ''}
          </span>
        </span>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-lg font-bold">{firstName(o.customer_name)}</span>
        {fresh && <Pill tone="brand">New</Pill>}
        <Pill tone={o.dining === 'EAT_IN' ? 'brand' : 'neutral'}>{diningWord(o.dining)}</Pill>
        {o.dining === 'EAT_IN' && o.table && <span className="fig text-lg font-extrabold">Table {o.table}</span>}
      </div>

      <ul className="flex flex-col gap-1.5 border-t border-dashed border-line pt-2">
        {o.lines.map((l) => (
          <li key={l.id}>
            <span className="text-lg leading-snug">
              <span className="fig font-extrabold">{l.qty} ×</span> {l.name}
              {l.size_label && <span className="text-ink-2"> ({l.size_label})</span>}
            </span>
            {l.options.length > 0 && <span className="block pl-6 text-base text-ink-2">{l.options.map((op) => op.name).join(' · ')}</span>}
          </li>
        ))}
      </ul>

      {o.note && (
        <p className="border-l-4 border-ink bg-wash px-3 py-2 text-base">
          <span className="font-bold">Note: </span>
          {o.note}
        </p>
      )}
      {o.staff_note && <p className="text-sm text-ink-2">Staff: {o.staff_note}</p>}

      <footer className="flex flex-col gap-2 border-t border-dashed border-line pt-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-base">
          <span className={cx(paid ? 'text-ink-2' : 'font-bold')}>
            {paid ? (o.payment_method === 'ONLINE' ? 'Paid online' : 'Paid at counter') : `Pay at counter · ${gbp(o.total_pence)}`}
            {paid && ` · ${gbp(o.total_pence)}`}
          </span>
          {o.member && (
            <Pill tone="brand">
              Rewards member · {plural(o.member.stamps_current, 'stamp')}
              {o.reward_id !== null ? ' · free drink' : ''}
            </Pill>
          )}
          <span className="ml-auto text-right text-sm text-ink-2">
            {plural(unitsCount(o), 'item')}
            {channels.length > 0 && <span className="block">Updates by {channels.join(', ')}</span>}
          </span>
        </div>
        {act && (
          <Button variant="primary" size="lg" block pending={w.pending} pendingLabel={act.pending} onClick={() => move(act.to)}>
            {act.label}
          </Button>
        )}
        <div className="flex items-center justify-between gap-2">
          <LinkButton variant="link" href={href(orderPath(o.id))} className="min-h-11 px-1">
            Open
          </LinkButton>
          {(may('CANCELLED') || may('REJECTED')) && (
            <Button variant="ghost" size="md" aria-expanded={more} onClick={() => setMore((m) => !m)}>
              {more ? 'Less' : 'More…'}
            </Button>
          )}
        </div>
        {more && (
          <div className="flex flex-col gap-2 rounded-card bg-canvas-2 px-3 py-2.5">
            <Field label="Why" hint="The customer sees this on their order page.">
              <Input size="sm" value={reason} maxLength={300} placeholder="Sold out of the pumpkin sauce" onChange={(e) => setReason(e.target.value)} />
            </Field>
            <div className="flex flex-wrap gap-2">
              {may('REJECTED') && (
                <ConfirmTwiceButton key={`r${armKey}`} size="md" armedLabel="Tap again to reject" pending={w.pending} onConfirm={() => move('REJECTED', reason.trim() || undefined)}>
                  Reject
                </ConfirmTwiceButton>
              )}
              {may('CANCELLED') && (
                <ConfirmTwiceButton key={`c${armKey}`} size="md" armedLabel="Tap again to cancel" pending={w.pending} onConfirm={() => move('CANCELLED', reason.trim() || undefined)}>
                  Cancel order
                </ConfirmTwiceButton>
              )}
              <Button variant="ghost" size="md" onClick={() => { setMore(false); setReason('') }}>
                Keep it
              </Button>
            </div>
          </div>
        )}
        <OutcomeLine outcome={w.outcome} />
      </footer>
    </article>
  )
}

function LaterRow({ o, now }: { o: OrderAdmin; now: number }) {
  const st = statusWord(o.status)
  const day = dayWord(o, now)
  return (
    <li className="border-b border-line-row last:border-b-0">
      <a
        href={href(orderPath(o.id))}
        className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 px-3.5 py-2.5 text-ink no-underline hover:bg-canvas-2 compact:grid-cols-[110px_150px_minmax(0,1fr)_170px_100px_90px]"
      >
        {/* The list has no header row: each docked cell says what it is for a screen reader. */}
        <span className="fig hidden font-bold compact:block">{o.code_display}</span>
        <span className="fig hidden compact:block">
          <span className="sr-only">Due </span>
          {day ? <span className="font-bold">{day} </span> : ''}
          {clockOf(o.requested_local)}
          {o.asap ? <span className="text-sm text-ink-2"> · ASAP</span> : ''}
        </span>
        <span className="min-w-0">
          <span className="block truncate font-semibold">
            <span className="fig compact:hidden">
              {o.code_display} · {day ? `${day} ` : ''}
              {clockOf(o.requested_local)} ·{' '}
            </span>
            {o.customer_name}
            {o.table ? <span className="font-normal text-ink-2"> · Table {o.table}</span> : ''}
          </span>
          <span className="block truncate text-sm text-ink-2">{linesSummary(o)}</span>
        </span>
        <span className="hidden text-sm text-ink-2 compact:block">
          {diningWord(o.dining)}
          {o.table ? ` · Table ${o.table}` : ''} · {plural(unitsCount(o), 'item')}
        </span>
        <span className="hidden compact:block">
          <span className="sr-only">Status </span>
          <StatusTag tone={st.tone}>{st.label}</StatusTag>
        </span>
        <span className="fig text-right font-bold">
          <span className="sr-only">Total </span>
          {gbp(o.total_pence)}
        </span>
      </a>
    </li>
  )
}
