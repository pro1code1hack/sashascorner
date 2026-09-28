/**
 * Website › Today: the old /admin dashboard (site/web/src/pages/admin/index.astro),
 * moved into the back office (owner, 2026-09-28).
 *
 * No KPI tiles: today's bookings as a list with Arrived / No-show on each row, the
 * seats taken per slot as quiet bars, the next seven days as one compact row, and
 * three plain notes (messages, photos, menu). Dates and times from the site are the
 * café's own (Europe/London) date and HH:MM; they are never shifted.
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, ErrorBox, Loading, PageBody, PageHeader, Pill, cx } from '../../components/ui'
import type { PillTone } from '../../components/ui'
import { href } from '../../lib/router'
import type { Booking, BookingDay, BookingStatus, SiteSettings, SiteSummary } from '../../lib/types/website'
import { WEBSITE_KEY, siteGet, siteWrite, useInvalidateWebsite, useWebsiteSummary } from '../../lib/website-api'
import { WebsiteGate } from './shared'

/* ------------------------------------------------------------- words --- */

const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const WD_LONG = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const MON_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

const STATUS_LABEL: Record<BookingStatus, string> = {
  confirmed: 'Booked',
  arrived: 'Arrived',
  no_show: 'No-show',
  cancelled: 'Cancelled',
}
const STATUS_TONE: Record<BookingStatus, PillTone> = {
  confirmed: 'brand',
  arrived: 'ok',
  no_show: 'warn',
  cancelled: 'muted',
}

/** Today in Dundee, as YYYY-MM-DD. */
function todayISO(): string {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}`
}

/** Minutes since midnight now, in Dundee. */
function nowMinutes(): number {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date())
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0)
  return get('hour') * 60 + get('minute')
}

function ymd(iso: string): [number, number, number] {
  const [y = 1970, m = 1, d = 1] = iso.split('-').map(Number)
  return [y, m, d]
}

/** Monday = 0, like the site API. */
function weekday(iso: string): number {
  const [y, m, d] = ymd(iso)
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7
}

/** "Monday 28 September" (+ the year when it isn't this year). */
function longDate(iso: string): string {
  const [y, m, d] = ymd(iso)
  const thisYear = Number(todayISO().slice(0, 4))
  return `${WD_LONG[weekday(iso)]} ${d} ${MON_LONG[m - 1]}${y !== thisYear ? ` ${y}` : ''}`
}

/** "18:30" -> "6.30pm", the café's own style. */
function clock(hhmm: string | null | undefined): string {
  if (!hhmm) return ''
  const [h = 0, m = 0] = hhmm.split(':').map(Number)
  const suffix = h >= 12 ? 'pm' : 'am'
  const h12 = h % 12 === 0 ? 12 : h % 12
  return m === 0 ? `${h12}${suffix}` : `${h12}.${String(m).padStart(2, '0')}${suffix}`
}

function toMinutes(hhmm: string): number {
  const [h = 0, m = 0] = hhmm.split(':').map(Number)
  return h * 60 + m
}

const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
const holds = (b: Booking) => b.status === 'confirmed' || b.status === 'arrived'

/** A link that looks like the kit's secondary button. */
const LINK_BUTTON =
  'inline-flex h-10 items-center justify-center whitespace-nowrap rounded-control border border-line-control bg-surface px-3.5 text-base font-semibold text-ink no-underline hover:bg-canvas'

/* ------------------------------------------------------------ screen --- */

export function TodayScreen() {
  return (
    <>
      <PageHeader
        title="Today"
        subtitle="who is coming, and what needs you"
        actions={
          <a className={LINK_BUTTON} href={href('/website/bookings', { add: 1 })}>
            Add booking
          </a>
        }
      />
      <PageBody>
        <WebsiteGate>
          <TodayBody />
        </WebsiteGate>
      </PageBody>
    </>
  )
}

/** The minute, re-read every 60s so "Open now" turns into "Closed now" on its own. */
function useMinuteTick(): number {
  const [now, setNow] = useState(nowMinutes)
  useEffect(() => {
    const t = window.setInterval(() => setNow(nowMinutes()), 60_000)
    return () => window.clearInterval(t)
  }, [])
  return now
}

function TodayBody() {
  const summary = useWebsiteSummary()
  const date = summary.data?.today.date ?? todayISO()
  const day = useQuery({
    queryKey: [...WEBSITE_KEY, 'bookings', 'day', date],
    queryFn: () => siteGet<BookingDay>(`/bookings/day?date=${date}`),
  })
  const settings = useQuery({
    queryKey: [...WEBSITE_KEY, 'settings'],
    queryFn: () => siteGet<SiteSettings>('/settings'),
    staleTime: 5 * 60 * 1000,
  })

  const failed = summary.error ?? day.error ?? settings.error
  if (failed) {
    return (
      <div className="flex flex-col items-start gap-3">
        <ErrorBox error={failed} what="Today’s bookings didn’t load" />
        <Button
          size="md"
          pending={summary.isFetching || day.isFetching || settings.isFetching}
          pendingLabel="Loading…"
          onClick={() => {
            void summary.refetch()
            void day.refetch()
            void settings.refetch()
          }}
        >
          Try again
        </Button>
      </div>
    )
  }
  if (!summary.data || !day.data || !settings.data) return <Loading what="Loading today" />

  return (
    <div className="grid items-start gap-7 lg:grid-cols-[minmax(0,1.7fr)_minmax(260px,1fr)] lg:gap-8">
      <TodaySection summary={summary.data} day={day.data} settings={settings.data} />
      <aside className="flex min-w-0 flex-col gap-6.5">
        <WeekRow summary={summary.data} settings={settings.data} />
        <Notes summary={summary.data} />
      </aside>
    </div>
  )
}

/* ------------------------------------------------------------- today --- */

type Notice = { tone: 'ok' | 'bad'; text: string; undo?: () => void } | null

function TodaySection({ summary, day, settings }: { summary: SiteSummary; day: BookingDay; settings: SiteSettings }) {
  const today = summary.today.date || day.date
  const now = useMinuteTick()
  const invalidate = useInvalidateWebsite()
  const [pending, setPending] = useState<number | null>(null)
  const [notice, setNotice] = useState<Notice>(null)

  // Open / closed, always in words.
  const hours = settings.cafe.hours.find((x) => x.weekday === weekday(today))
  const closure = settings.closures.find((c) => c.date === today)
  const closedToday = summary.today.closed || day.closed
  let openLine: { tone: PillTone; text: string } | null = null
  if (closedToday) {
    openLine = {
      tone: 'neutral',
      text: closure ? `Closed: ${closure.note || 'special closure'}` : day.reason ? `Closed today. ${day.reason}` : 'Closed today',
    }
  } else if (hours && !hours.closed && hours.open && hours.close) {
    const isOpen = now >= toMinutes(hours.open) && now < toMinutes(hours.close)
    openLine = { tone: isOpen ? 'ok' : 'neutral', text: `${isOpen ? 'Open now' : 'Closed now'} · ${clock(hours.open)} to ${clock(hours.close)}` }
  }

  // Everything on today's sheet; cancelled ones are counted, not listed.
  const live = day.bookings.filter(holds)
  const covers = live.reduce((a, b) => a + b.party, 0)
  const waiting = day.bookings.filter((b) => b.status === 'confirmed').length
  const shown = day.bookings.filter((b) => b.status !== 'cancelled').sort((a, b) => a.time.localeCompare(b.time))
  const cancelled = day.bookings.length - shown.length

  async function setStatus(b: Booking, status: BookingStatus) {
    setPending(b.id)
    setNotice(null)
    const r = await siteWrite<Booking>(`/bookings/${b.id}`, { status }, 'PATCH')
    setPending(null)
    if (r.kind !== 'ok') {
      setNotice({ tone: 'bad', text: r.message })
      return
    }
    await invalidate()
    const from = b.status
    setNotice({
      tone: 'ok',
      text: `${b.name}: ${STATUS_LABEL[status].toLowerCase()}.`,
      undo: () => {
        setNotice(null)
        void siteWrite<Booking>(`/bookings/${b.id}`, { status: from }, 'PATCH').then(async (u) => {
          if (u.kind === 'ok') {
            await invalidate()
            setNotice({ tone: 'ok', text: `${b.name}: back to ${STATUS_LABEL[from].toLowerCase()}.` })
          } else setNotice({ tone: 'bad', text: u.message })
        })
      },
    })
  }

  const big = (n: number) => <span className="fig text-4xl font-extrabold tracking-[-.02em] text-ink">{n}</span>

  return (
    <section aria-labelledby="today-h" className="min-w-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
        <h2 id="today-h" className="text-2xl font-extrabold tracking-[-.01em]">
          {longDate(today)}
        </h2>
        {openLine && <Pill tone={openLine.tone}>{openLine.text}</Pill>}
      </div>

      <p className="mt-3 mb-4 text-xl text-ink">
        {live.length ? (
          <>
            {big(covers)} {covers === 1 ? 'guest' : 'guests'} in {big(live.length)} {live.length === 1 ? 'booking' : 'bookings'}.{' '}
            <span className="whitespace-nowrap text-ink-2">{waiting ? `${waiting} still to arrive.` : 'Everyone booked has arrived.'}</span>
          </>
        ) : day.closed ? (
          'No bookings.'
        ) : (
          'No bookings yet today.'
        )}
      </p>

      <NoticeLine notice={notice} />

      {shown.length ? (
        <ul aria-label="Today’s bookings" className="border-t border-line">
          {shown.map((b) => (
            <BookingRow key={b.id} b={b} busy={pending === b.id} onStatus={(s) => void setStatus(b, s)} />
          ))}
        </ul>
      ) : (
        <p className="border-y border-line py-6 text-center text-md text-ink-2">
          {day.closed ? 'Closed today.' : 'Nobody has booked for today. Walk-ins only.'}
        </p>
      )}

      {cancelled > 0 && (
        <p className="mt-2.5 text-base text-ink-2">
          {count(cancelled, 'cancelled booking')} not shown.{' '}
          <a href={href('/website/bookings', { date: today })} className="text-brand-ink underline underline-offset-2">
            See the whole day
          </a>
        </p>
      )}

      {!day.closed && <SlotBars day={day} />}
    </section>
  )
}

/** The last write's outcome, with Undo after a status change. */
function NoticeLine({ notice }: { notice: Notice }) {
  return (
    <div aria-live="polite">
      {notice && (
        <p
          role={notice.tone === 'bad' ? 'alert' : 'status'}
          className={cx(
            'mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-control px-3 py-2 text-base',
            notice.tone === 'bad' ? 'bg-bad-wash text-bad-ink' : 'bg-canvas text-ink',
          )}
        >
          <span>{notice.text}</span>
          {notice.undo && (
            <Button variant="link" onClick={notice.undo} className="min-h-6">
              Undo
            </Button>
          )}
        </p>
      )}
    </div>
  )
}

function BookingRow({ b, busy, onStatus }: { b: Booking; busy: boolean; onStatus: (s: BookingStatus) => void }) {
  return (
    <li className="grid grid-cols-[3.75rem_minmax(0,1fr)] items-start gap-x-3 gap-y-2 border-b border-line py-3 sm:grid-cols-[4.5rem_minmax(0,1fr)_auto] sm:items-center">
      <span className="fig pt-0.5 text-lg font-extrabold">{clock(b.time)}</span>
      <div className="min-w-0">
        {/* The whole booking (move, change, cancel) is edited on Bookings. */}
        <a
          href={href('/website/bookings', { date: b.date, open: b.id })}
          className="text-lg font-bold break-words text-ink underline-offset-2 hover:underline"
        >
          {b.name}
        </a>
        <div className="text-base text-ink-2">
          {count(b.party, 'person', 'people')} · {b.source === 'admin' ? 'phone' : 'online'} · <span className="fig">{b.reference}</span>
        </div>
        {b.notes && <div className="mt-0.5 text-base break-words text-ink">{b.notes}</div>}
      </div>
      <div className="col-start-2 flex flex-wrap items-center gap-2 sm:col-start-3 sm:justify-end">
        {b.status === 'confirmed' ? (
          <>
            <Button pending={busy} pendingLabel="Saving…" onClick={() => onStatus('arrived')} aria-label={`Mark ${b.name} arrived`}>
              Arrived
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => onStatus('no_show')} aria-label={`Mark ${b.name} as a no-show`}>
              No-show
            </Button>
          </>
        ) : (
          <>
            <Pill tone={STATUS_TONE[b.status]}>{STATUS_LABEL[b.status]}</Pill>
            {(b.status === 'arrived' || b.status === 'no_show') && (
              <Button
                variant="ghost"
                pending={busy}
                pendingLabel="Saving…"
                onClick={() => onStatus('confirmed')}
                aria-label={`Undo: set ${b.name} back to booked`}
              >
                Undo
              </Button>
            )}
          </>
        )}
      </div>
    </li>
  )
}

/** One quiet bar per slot: seats taken against capacity. Folded by default on a phone. */
function SlotBars({ day }: { day: BookingDay }) {
  const ref = useRef<HTMLDetailsElement>(null)
  const [open, setOpen] = useState(true)
  useEffect(() => {
    if (ref.current && window.matchMedia('(max-width: 759.98px)').matches) ref.current.open = false
  }, [])
  return (
    <details ref={ref} open className="mt-5" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 [&::-webkit-details-marker]:hidden">
        <span className="text-label font-bold tracking-[.06em] text-ink-2 uppercase">Seats taken by time</span>
        <span className="text-base text-ink-2">{open ? 'Hide ▴' : 'Show ▾'}</span>
      </summary>
      {day.slots.length === 0 ? (
        <p className="py-3 text-base text-ink-2">Closed. No tables to book.</p>
      ) : (
        <ul aria-label="Seats taken at each time, out of capacity" className="flex flex-col gap-1">
          {day.slots.map((s) => {
            const pct = s.capacity > 0 ? Math.min(100, (s.covers_booked / s.capacity) * 100) : 0
            const full = s.covers_booked >= s.capacity
            return (
              <li key={s.time} className="grid grid-cols-[4rem_minmax(0,1fr)_6rem] items-center gap-3 text-base">
                <span className="fig text-ink">{clock(s.time)}</span>
                <span className="h-2 overflow-hidden rounded-full bg-wash" aria-hidden="true">
                  <span className={cx('block h-full rounded-full', full ? 'bg-alert' : 'bg-brand')} style={{ width: `${pct}%` }} />
                </span>
                <span className={cx('fig text-right', full ? 'font-bold text-bad-ink' : 'text-ink')}>
                  <span className="sr-only">{clock(s.time)}: </span>
                  {s.covers_booked}
                  <span className={full ? undefined : 'text-ink-2'}> / {s.capacity}</span>
                  {full ? ' full' : <span className="sr-only"> seats taken</span>}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </details>
  )
}

/* -------------------------------------------------------------- side --- */

function WeekRow({ summary, settings }: { summary: SiteSummary; settings: SiteSettings }) {
  const closures = new Set(settings.closures.map((c) => c.date))
  return (
    <section aria-labelledby="week-h">
      <h2 id="week-h" className="text-lg font-extrabold tracking-[-.01em]">
        Next 7 days
      </h2>
      <ol className="mt-2.5 grid grid-cols-7 gap-1">
        {summary.week.map((d, i) => {
          const hw = settings.cafe.hours.find((x) => x.weekday === weekday(d.date))
          const closed = closures.has(d.date) || !hw || hw.closed === true
          const spoken = `${i === 0 ? 'Today' : longDate(d.date)}: ${closed ? 'closed' : `${count(d.bookings, 'booking')}, ${count(d.covers, 'guest')}`}`
          return (
            <li key={d.date} className="min-w-0">
              <a
                href={href('/website/bookings', { date: d.date })}
                aria-current={i === 0 ? 'date' : undefined}
                className={cx(
                  'flex min-h-[76px] flex-col items-center gap-0.5 rounded-button border px-0.5 pt-2.5 pb-2 text-ink no-underline transition-colors hover:border-brand',
                  i === 0 ? 'border-brand-line bg-brand-wash' : 'border-line bg-surface',
                )}
              >
                <span className="sr-only">{spoken}</span>
                <span aria-hidden="true" className={cx('text-label font-bold tracking-[.06em] uppercase', i === 0 ? 'text-brand-ink' : 'text-ink-2')}>
                  {i === 0 ? 'Today' : WD[weekday(d.date)]}
                </span>
                {closed ? (
                  <span aria-hidden="true" className="pt-2 text-xs text-ink-2">
                    Closed
                  </span>
                ) : (
                  <>
                    <span aria-hidden="true" className="fig text-2xl leading-7 font-extrabold">
                      {d.bookings}
                    </span>
                    <span aria-hidden="true" className="fig text-xs text-ink-2">
                      {d.covers} ppl
                    </span>
                  </>
                )}
              </a>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function Notes({ summary }: { summary: SiteSummary }) {
  const n = summary.messages_new
  const pm = summary.photos_missing
  const m = summary.menu
  const warnings: string[] = Array.isArray(m.warnings)
    ? m.warnings
    : m.warnings
      ? [`${count(m.warnings, 'thing')} to check on the menu page`]
      : []
  const row = 'flex min-h-14 items-center gap-2.5 border-b border-line px-1 py-2.5 text-md text-ink no-underline hover:bg-canvas-2'
  const num = (x: number) => <span className="fig text-xl font-extrabold">{x}</span>
  const go = (
    <span aria-hidden="true" className="text-2xl text-ink-3">
      ›
    </span>
  )
  return (
    <section aria-label="Things to look at" className="flex flex-col border-t border-line">
      <a className={row} href={href('/website/messages')}>
        <span className="min-w-0 flex-1">
          {n ? (
            <>
              {num(n)} new {n === 1 ? 'message' : 'messages'} from the contact form
            </>
          ) : (
            'No new messages'
          )}
        </span>
        {go}
      </a>
      <a className={row} href={href('/website/photos')}>
        <span className="min-w-0 flex-1">
          {pm ? <>Photos still missing: {num(pm)}</> : 'Every photo spot on the website has a photo'}
        </span>
        {go}
      </a>
      <a className={row} href={href('/website/menu')}>
        <span className="min-w-0 flex-1">
          {m.source === 'ops'
            ? 'The website menu comes from the till (back office).'
            : 'The website menu comes from the menu board file, not the till yet.'}
          {warnings.length > 0 && (
            <ul className="mt-1 flex flex-col gap-0.5 text-sm text-bad-ink">
              {warnings.slice(0, 3).map((w) => (
                <li key={w}>{w}</li>
              ))}
              {warnings.length > 3 && <li>and {warnings.length - 3} more</li>}
            </ul>
          )}
        </span>
        {go}
      </a>
    </section>
  )
}
