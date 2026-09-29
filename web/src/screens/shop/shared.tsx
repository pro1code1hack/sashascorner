/**
 * Shared bits for the Online orders group (docs/shop/CONTRACT.md §7): the fixture
 * gate, the words and tones for an order's status, the next action on the board,
 * a photo slot that takes its upload function, and small formatters.
 */
import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Empty, Pill, cx } from '../../components/ui'
import type { StatusTone } from '../../components/ui'
import { LIVE, type WriteResult } from '../../lib/api'
import { useOperator } from '../../lib/operator'
import { useInvalidateShop } from '../../lib/shop-api'
import type { DiningOption, OrderAdmin, OrderStatus, PaymentMethod, PaymentStatus } from '../../lib/types/shop'
import { PhotoView, shrink } from '../menu/Photo'

/** Renders `children` only against the live API: the shop has no recorded fixtures. */
export function ShopGate({ children }: { children: ReactNode }) {
  if (!LIVE) {
    return (
      <Empty roomy>
        The online ordering screens need the live API, and this back office is showing recorded fixtures. Run it
        with VITE_LIVE=1 to see orders, the shop menu and its settings.
      </Empty>
    )
  }
  return <>{children}</>
}

export const orderPath = (id: number) => `/shop/orders/${id}`

/* -------------------------------------------------------------- status --- */

export function statusWord(s: OrderStatus): { label: string; tone: StatusTone } {
  switch (s) {
    case 'PENDING_PAYMENT':
      return { label: 'Awaiting payment', tone: 'ink-3' }
    case 'NEW':
      return { label: 'New', tone: 'alert' }
    case 'ACCEPTED':
      return { label: 'Accepted', tone: 'ink' }
    case 'PREPARING':
      return { label: 'Preparing', tone: 'ink' }
    case 'READY':
      return { label: 'Ready', tone: 'ink' }
    case 'COLLECTED':
      return { label: 'Collected', tone: 'ink-3' }
    case 'CANCELLED':
      return { label: 'Cancelled', tone: 'ink-3' }
    case 'REJECTED':
      return { label: 'Rejected', tone: 'ink-3' }
  }
}

/**
 * The one button on a board card: what moves the order along (contract §3.8).
 * The server lists what it would accept; the button shows only when it agrees.
 */
export function nextAction(s: OrderStatus, allowed?: readonly OrderStatus[]): { label: string; pending: string; to: OrderStatus } | null {
  const a = nextStep(s)
  if (a === null) return null
  return allowed === undefined || allowed.includes(a.to) ? a : null
}

function nextStep(s: OrderStatus): { label: string; pending: string; to: OrderStatus } | null {
  switch (s) {
    case 'NEW':
      return { label: 'Accept', pending: 'Accepting…', to: 'ACCEPTED' }
    case 'ACCEPTED':
      return { label: 'Start making', pending: 'Starting…', to: 'PREPARING' }
    case 'PREPARING':
      return { label: 'Ready to collect', pending: 'Marking…', to: 'READY' }
    case 'READY':
      return { label: 'Collected', pending: 'Recording…', to: 'COLLECTED' }
    default:
      return null
  }
}

export const LIVE_STATUSES: readonly OrderStatus[] = ['PENDING_PAYMENT', 'NEW', 'ACCEPTED', 'PREPARING', 'READY']

export function diningWord(d: DiningOption): string {
  return d === 'EAT_IN' ? 'Eat in' : 'Takeaway'
}

export function paymentWord(method: PaymentMethod, status: PaymentStatus): string {
  const how = method === 'ONLINE' ? 'online' : 'at the counter'
  switch (status) {
    case 'PAID':
      return `Paid ${how}`
    case 'REFUNDED':
      return 'Refunded'
    case 'FAILED':
      return 'Payment failed'
    default:
      return method === 'ONLINE' ? 'Not paid yet (online)' : 'Pays at the counter'
  }
}

/** "3 min late" / "due in 12 min" / "due now", from the server's minutes. */
export function dueWord(minutes: number): { text: string; late: boolean } {
  if (minutes < 0) return { text: `${-minutes} min late`, late: true }
  if (minutes === 0) return { text: 'due now', late: false }
  if (minutes >= 120) return { text: `due in ${Math.round(minutes / 60)} h`, late: false }
  return { text: `due in ${minutes} min`, late: false }
}

/** "2× Iced latte (Medium), 1× Croissant". */
export function linesSummary(o: Pick<OrderAdmin, 'lines'>): string {
  return o.lines.map((l) => `${l.qty}× ${l.name}${l.size_label ? ` (${l.size_label})` : ''}`).join(', ')
}

export function optionsSummary(opts: OrderAdmin['lines'][number]['options']): string {
  return opts.map((op) => op.name).join(', ')
}

/**
 * How the customer gets updates about this order: "text" when they opted in to
 * SMS, "push" when a push subscription exists, "email" when an address was
 * given. Field names follow Agent A's shipment; anything absent is simply not shown.
 */
export function notifyChannels(o: OrderAdmin): string[] {
  const out: string[] = []
  if (o.sms_opt_in) out.push('text')
  if (o.push_subscribed || o.push_subscription_id) out.push('push')
  if (o.customer_email) out.push('email')
  return out
}

const LONDON = 'Europe/London'

/** YYYY-MM-DD in London for an instant. */
function londonDay(ms: number): string {
  return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: LONDON }).format(new Date(ms))
}

/**
 * 0 when the order is for today (London), 1 for tomorrow, and so on. The
 * server's `days_ahead` when it sends one; otherwise worked out from the slot.
 */
export function daysAhead(o: Pick<OrderAdmin, 'requested_at' | 'days_ahead'>, now: number = Date.now()): number {
  if (typeof o.days_ahead === 'number') return o.days_ahead
  const t = Date.parse(o.requested_at)
  if (Number.isNaN(t)) return 0
  const a = londonDay(now)
  const b = londonDay(t)
  if (a === b) return 0
  // Calendar days apart, from the two date strings (never the clock: BST changes would shift it).
  const [ay, am, ad] = a.split('-').map(Number) as [number, number, number]
  const [by, bm, bd] = b.split('-').map(Number) as [number, number, number]
  return Math.round((Date.UTC(by, bm - 1, bd) - Date.UTC(ay, am - 1, ad)) / 86_400_000)
}

/** "Tomorrow", "Thursday" (within the week) or "Thu 8 Oct" for an order on another day; null for today. */
export function dayWord(o: Pick<OrderAdmin, 'requested_at' | 'days_ahead'>, now: number = Date.now()): string | null {
  const n = daysAhead(o, now)
  if (n <= 0) return null
  if (n === 1) return 'Tomorrow'
  const t = Date.parse(o.requested_at)
  if (Number.isNaN(t)) return `In ${n} days`
  const opts: Intl.DateTimeFormatOptions = n < 7 ? { weekday: 'long', timeZone: LONDON } : { weekday: 'short', day: 'numeric', month: 'short', timeZone: LONDON }
  return new Intl.DateTimeFormat('en-GB', opts).format(new Date(t))
}

/** "14:20" from the server's "Today 14:20" / "Tomorrow 14:20" / "Mon 14:20". */
export function clockOf(requestedLocal: string): string {
  const m = /(\d{1,2}:\d{2})\s*$/.exec(requestedLocal)
  return m?.[1] ?? requestedLocal
}

export function unitsCount(o: Pick<OrderAdmin, 'lines'>): number {
  return o.lines.reduce((n, l) => n + l.qty, 0)
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

/** A yes/no pill without colour: colour is for crossed thresholds only. */
export function OnOff({ on, yes, no }: { on: boolean; yes: string; no: string }) {
  return <Pill tone={on ? 'brand' : 'muted'}>{on ? yes : no}</Pill>
}

/* --------------------------------------------------------------- photo --- */

/**
 * A photo slot for a category, product, option or banner: click or drop an image;
 * it is shrunk in the browser and sent as the raw body. `upload` and `clear` are
 * the entity's own calls; the slot refetches the catalogue when they land.
 */
export function ShopPhotoSlot({
  url,
  name,
  upload,
  clear,
  aspect = 'wide',
  disabledReason,
}: {
  url: string | null
  name: string
  upload: (blob: Blob, by: string | null) => Promise<WriteResult<unknown>>
  clear: () => Promise<WriteResult<unknown>>
  aspect?: 'wide' | 'square' | 'tall'
  /** Why a photo cannot be added yet ("Save the group first"). */
  disabledReason?: string
}) {
  const input = useRef<HTMLInputElement>(null)
  const [operator] = useOperator()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const invalidate = useInvalidateShop()

  const send = async (file: File | undefined) => {
    if (!file || disabledReason) return
    setMessage(null)
    setBusy(true)
    try {
      const blob = await shrink(file).catch(() => file)
      const r = await upload(blob, operator)
      if (r.kind === 'ok') await invalidate()
      else setMessage(r.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        disabled={Boolean(disabledReason)}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          void send(e.dataTransfer.files[0])
        }}
        aria-label={url ? `Replace the photo of ${name}` : `Add a photo of ${name}`}
        className={cx(
          'relative w-full flex-none overflow-hidden rounded-card disabled:opacity-60',
          aspect === 'wide' ? 'aspect-[16/9]' : aspect === 'square' ? 'aspect-square' : 'aspect-[4/3]',
          over && 'ring-2 ring-brand',
        )}
      >
        <PhotoView
          url={url}
          placeholder={busy ? 'Uploading…' : url ? '' : (disabledReason ?? 'Drop a photo here, or click to choose one')}
          className="absolute inset-0"
        />
      </button>
      <input
        ref={input}
        type="file"
        accept="image/webp,image/jpeg,image/png"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          void send(e.target.files?.[0])
          e.target.value = ''
        }}
      />
      {(url || message) && (
        <div className="flex items-center gap-2">
          {message && (
            <span role="alert" className="flex-1 text-sm text-bad-ink">
              {message}
            </span>
          )}
          {url && (
            <Button
              variant="link"
              className="ml-auto"
              onClick={async () => {
                const r = await clear()
                if (r.kind === 'ok') await invalidate()
                else setMessage(r.message)
              }}
            >
              Remove photo
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

/** Small square thumbnail for list rows; the dashed slot when none. */
export function Thumb({ url, className }: { url: string | null; className?: string }) {
  return (
    <span className={cx('relative size-11 flex-none overflow-hidden rounded-control', className)}>
      <PhotoView url={url} placeholder="" className="absolute inset-0" />
    </span>
  )
}

/** A white panel with an h2, the menu item page's section style. */
export function Panel({
  title,
  right,
  children,
  className,
  id,
}: {
  title?: ReactNode
  right?: ReactNode
  children: ReactNode
  className?: string
  id?: string
}) {
  return (
    <section id={id} className={cx('min-w-0 rounded-card-lg border border-line bg-surface px-4 py-4 sm:px-5', className)}>
      {(title || right) && (
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {title && <h2 className="text-lg font-extrabold tracking-[-.01em]">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  )
}

/** The uppercase column label row for a list laid out as a grid (Orders' row style). */
export const LIST_HEAD = 'hidden gap-3 border-b border-line px-3.5 py-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 compact:grid'

/** Move an array element; used by every reorderable list here. */
export function moved<T>(arr: readonly T[], from: number, to: number): T[] {
  const out = [...arr]
  const [x] = out.splice(from, 1)
  if (x !== undefined) out.splice(to, 0, x)
  return out
}

/** A whole number typed into a field, or null when blank. Never parseInt. */
export function intOrNull(text: string): number | null | undefined {
  const t = text.trim()
  if (t === '') return null
  if (!/^\d+$/.test(t)) return undefined
  return Number(t)
}
