/**
 * Bookings, the pieces: dates in the café's own day, the reads, the row with its
 * Arrived / No-show buttons, the seats-by-time timeline and the time picker that
 * shows room at each slot. Moved from the site admin (admin-core/bookings.ts).
 *
 * Dates are YYYY-MM-DD strings in Europe/London and times are HH:MM local; none
 * of them is ever turned into a UTC instant (see ./dates.ts).
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Field, Select, StatusTag, cx } from '../../components/ui'
import type { WriteResult } from '../../lib/api'
import { WEBSITE_KEY, siteGet, siteWrite, useInvalidateWebsite, useSiteSettings } from '../../lib/website-api'
import type { Booking, BookingDay, BookingPatch, BookingRules, BookingStatus, SiteSettings } from '../../lib/types/website'
import { ISO_DATE, relDay, toMinutes, weekday } from './dates'

/* ------------------------------------------------------------------ words --- */

/** "1 person", "3 people", "2 bookings". */
export const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
export const people = (n: number) => count(n, 'person', 'people')

/* ------------------------------------------------------------------ reads --- */

export const bookingsKey = (...rest: unknown[]) => [...WEBSITE_KEY, 'bookings', ...rest]

export function useBookingDay(date: string | null) {
  return useQuery({
    queryKey: bookingsKey('day', date),
    queryFn: () => siteGet<BookingDay>(`/bookings/day?date=${date ?? ''}`),
    enabled: date !== null && ISO_DATE.test(date),
  })
}

export function useBookingRange(from: string, to: string, enabled = true) {
  return useQuery({
    queryKey: bookingsKey('range', from, to),
    queryFn: () => siteGet<Booking[]>(`/bookings?from=${from}&to=${to}`),
    enabled,
  })
}

export function patchBooking(id: number, body: BookingPatch): Promise<WriteResult<Booking>> {
  return siteWrite<Booking>(`/bookings/${id}`, body, 'PATCH')
}

/**
 * The site refuses a full slot with 409 "Over capacity: …", and a closed day or
 * out-of-hours time with 422. Only the first can be overridden. The status tells
 * them apart; the wording is the fallback when a refusal carries none.
 */
export const isCapacityRefusal = (r: { message: string; status?: number }) =>
  r.status !== undefined ? r.status === 409 : /over capacity|filled up|full/i.test(r.message)

/* ----------------------------------------------------------------- status --- */

export const STATUS_LABEL: Record<BookingStatus, string> = {
  confirmed: 'Booked',
  arrived: 'Arrived',
  no_show: 'No-show',
  cancelled: 'Cancelled',
}

export const holds = (b: Booking) => b.status === 'confirmed' || b.status === 'arrived'

/** A word, never a fade: cancelled and no-show read as such. */
export function StatusWord({ status }: { status: BookingStatus }) {
  if (status === 'confirmed') return null
  return <StatusTag tone={status === 'arrived' ? 'ink' : 'ink-3'}>{STATUS_LABEL[status]}</StatusTag>
}

/** "Open 09:00–19:00", "Closed: staff training", from the site settings. */
export function hoursLine(s: SiteSettings | undefined, date: string): string {
  if (!s) return ''
  const closure = s.closures.find((c) => c.date === date)
  if (closure) return `Closed: ${closure.note || 'special closure'}`
  const h = s.cafe.hours.find((x) => x.weekday === weekday(date))
  if (!h || h.closed || !h.open || !h.close) return 'Closed that day of the week'
  return `Open ${h.open}–${h.close}`
}

/* ------------------------------------------------------------------- room --- */

export interface Room {
  peak: number
  free: number
  fits: boolean
  capacity: number
}

/**
 * Room at `time` for `party`, from the day's slot loads. A table is held for
 * `duration_minutes`, so every slot in [time, time + duration) must fit.
 * `except` is a booking being edited: its own covers don't count against it.
 */
export function roomAt(day: BookingDay, rules: BookingRules, time: string, party: number, except?: Booking): Room {
  const start = toMinutes(time)
  const end = start + rules.duration_minutes
  let peak = 0
  for (const s of day.slots) {
    const t = toMinutes(s.time)
    if (t < start || t >= end) continue
    let c = s.covers_booked
    if (except && except.date === day.date && holds(except)) {
      const es = toMinutes(except.time)
      if (t >= es && t < es + rules.duration_minutes) c -= except.party
    }
    peak = Math.max(peak, c)
  }
  const cap = day.capacity
  return { peak, free: Math.max(0, cap - peak), fits: peak + party <= cap, capacity: cap }
}

/** Party as typed: a whole number of at least 1, else null. */
export function partyOf(text: string): number | null {
  if (!/^\s*\d+\s*$/.test(text)) return null
  const n = Number(text)
  return n >= 1 ? n : null
}

/**
 * The room a booking would find: the day's loads plus the rules. `room` is null
 * until both have loaded, or when nothing is picked.
 */
export function useRoom(date: string, time: string, party: number | null, except?: Booking) {
  const day = useBookingDay(ISO_DATE.test(date) ? date : null)
  const settings = useSiteSettings()
  const d = day.data
  const rules = settings.data?.booking
  const room = d && rules && time && !d.closed ? roomAt(d, rules, time, party ?? 1, except) : null
  return { day, settings, room }
}

/* --------------------------------------------------------------- timeline --- */

/** One quiet bar per slot: covers seated against capacity. Red only when full. */
export function SlotTimeline({ day }: { day: BookingDay }) {
  if (day.closed || day.slots.length === 0) {
    return <p className="text-base text-ink-2">Closed. No tables to book.</p>
  }
  return (
    <ul aria-label="Seats taken at each time, out of capacity" className="flex flex-col">
      {day.slots.map((s) => {
        const full = s.covers_booked >= s.capacity
        const pct = s.capacity > 0 ? Math.min(100, (s.covers_booked / s.capacity) * 100) : 0
        return (
          <li key={s.time} className="grid grid-cols-[3rem_minmax(0,1fr)_5.5rem] items-center gap-2.5 py-1">
            <span className="fig text-sm text-ink-2" aria-hidden="true">
              {s.time}
            </span>
            <span aria-hidden="true" className="h-2 overflow-hidden rounded-full bg-wash">
              <span className={cx('block h-full', full ? 'bg-alert' : 'bg-brand')} style={{ width: `${pct}%` }} />
            </span>
            <span className={cx('fig text-right text-sm', full && 'font-bold text-bad-ink')}>
              <span className="sr-only">{s.time}: </span>
              {s.covers_booked}
              <span className="font-normal text-ink-2"> / {s.capacity}</span>
              {full && ' full'}
              <span className="sr-only"> seats taken</span>
            </span>
          </li>
        )
      })}
    </ul>
  )
}

/* ------------------------------------------------------------------- rows --- */

/** A page-level note after a change, with an undo when one makes sense. */
export interface Notice {
  tone: 'stale' | 'alert'
  text: string
  undo?: { id: number; body: BookingPatch; label: string }
}

export function BookingRow({
  b,
  showDate = false,
  onOpen,
  notify,
}: {
  b: Booking
  showDate?: boolean
  onOpen: (b: Booking) => void
  notify: (n: Notice) => void
}) {
  const invalidate = useInvalidateWebsite()
  const [pending, setPending] = useState<BookingStatus | null>(null)
  const busy = useRef(false)

  const setStatus = async (status: BookingStatus) => {
    if (busy.current) return
    busy.current = true
    setPending(status)
    const r = await patchBooking(b.id, { status })
    busy.current = false
    setPending(null)
    if (r.kind === 'ok') {
      notify({
        tone: 'stale',
        text:
          status === 'confirmed'
            ? `${b.name} is back to booked.`
            : `${b.name}: ${STATUS_LABEL[status].toLowerCase()}.`,
        undo: { id: b.id, body: { status: b.status }, label: `${b.name}: back to ${STATUS_LABEL[b.status].toLowerCase()}` },
      })
      void invalidate()
    } else {
      notify({ tone: 'alert', text: r.message })
    }
  }

  const actions =
    b.status === 'confirmed' ? (
      <>
        <Button
          className="min-h-11"
          pending={pending === 'arrived'}
          pendingLabel="Saving…"
          disabled={pending !== null}
          aria-label={`Mark ${b.name} arrived`}
          onClick={() => void setStatus('arrived')}
        >
          Arrived
        </Button>
        <Button
          variant="ghost"
          className="min-h-11"
          pending={pending === 'no_show'}
          pendingLabel="Saving…"
          disabled={pending !== null}
          aria-label={`Mark ${b.name} as a no-show`}
          onClick={() => void setStatus('no_show')}
        >
          No-show
        </Button>
      </>
    ) : b.status === 'arrived' || b.status === 'no_show' ? (
      <Button
        variant="ghost"
        className="min-h-11"
        pending={pending === 'confirmed'}
        pendingLabel="Saving…"
        aria-label={`Undo: set ${b.name} back to booked`}
        onClick={() => void setStatus('confirmed')}
      >
        Undo
      </Button>
    ) : null

  return (
    <li className="border-b border-line-row last:border-b-0">
      <div className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 px-3.5 py-2.5 sm:grid-cols-[3.5rem_minmax(0,1fr)_auto] sm:items-center">
        <div className="fig pt-1.5 sm:pt-0">
          {showDate && <span className="block text-xs font-bold text-ink-2">{relDay(b.date)}</span>}
          <span className="text-md font-bold">{b.time}</span>
        </div>
        <button
          type="button"
          onClick={() => onOpen(b)}
          className="-mx-1.5 min-h-11 min-w-0 rounded-control px-1.5 py-1 text-left hover:bg-canvas-2"
        >
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="min-w-0 truncate text-md font-bold text-ink">{b.name}</span>
            <StatusWord status={b.status} />
          </span>
          <span className="block text-sm text-ink-2">
            {people(b.party)} · {b.source === 'admin' ? 'phone' : 'online'} · <span className="fig">{b.reference}</span>
          </span>
          {b.notes && <span className="line-clamp-2 block text-sm text-ink">Note: {b.notes}</span>}
        </button>
        {actions && <div className="col-start-2 flex flex-wrap gap-2 sm:col-start-auto sm:justify-end">{actions}</div>}
      </div>
    </li>
  )
}

/** A white list of rows, as on Members. */
export function RowList({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <ul aria-label={label} className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
      {children}
    </ul>
  )
}

/* ------------------------------------------------------------ time picker --- */

/**
 * The time select, each slot labelled with its room for this party. An existing
 * booking's time that is no longer a slot (hours changed) is kept and said so.
 * With no value yet, the first slot with room is picked.
 */
export function TimePicker({
  date,
  party,
  value,
  onChange,
  except,
  error,
}: {
  date: string
  party: number | null
  value: string
  onChange: (t: string) => void
  except?: Booking
  error?: string
}) {
  const { day, settings, room } = useRoom(date, value, party, except)
  const d = day.data
  const rules = settings.data?.booking
  const p = party ?? 1

  useEffect(() => {
    if (value || !d || !rules || d.closed) return
    const first = d.slots.find((s) => roomAt(d, rules, s.time, p, except).fits) ?? d.slots[0]
    if (first) onChange(first.time)
  }, [value, d, rules, p, except, onChange])

  let hint: React.ReactNode
  if (!ISO_DATE.test(date)) hint = 'Pick a date first.'
  else if (day.isPending || settings.isPending) hint = 'Checking tables…'
  else if (day.isError) hint = <span className="text-bad-ink">Couldn&rsquo;t read that day&rsquo;s tables. {String(day.error instanceof Error ? day.error.message : '')}</span>
  else if (d?.closed) hint = `The café is closed that day${d.reason ? ` (${d.reason.replace(/\.$/, '')})` : ''}, so there are no times to book.`
  else if (room) {
    hint = room.fits ? (
      `${room.peak} of ${room.capacity} seats taken around ${value}. Room for ${room.free} more.`
    ) : (
      <span className="font-bold text-bad-ink">
        {p > room.capacity
          ? `Full: ${p} is more than all ${room.capacity} seats in the café.`
          : `Full: ${room.peak} of ${room.capacity} seats are taken around ${value}, so ${p} won’t fit.`}
      </span>
    )
  }

  const closed = !d || d.closed || d.slots.length === 0
  const isSlot = d?.slots.some((s) => s.time === value) ?? false
  return (
    <Field label="Time" hint={<span aria-live="polite">{hint}</span>} error={error}>
      <Select value={value} disabled={closed && !value} onChange={(e) => onChange(e.target.value)}>
        {closed && !value && <option value="">{d?.closed ? 'Closed that day' : 'Checking…'}</option>}
        {d && rules && !d.closed &&
          d.slots.map((s) => {
            const r = roomAt(d, rules, s.time, p, except)
            return (
              <option key={s.time} value={s.time}>
                {r.fits ? `${s.time} · room for ${r.free}` : `${s.time} · full for ${p}`}
              </option>
            )
          })}
        {value && !isSlot && <option value={value}>{value} · not a slot{d?.closed ? '' : ' any more'}</option>}
      </Select>
    </Field>
  )
}
