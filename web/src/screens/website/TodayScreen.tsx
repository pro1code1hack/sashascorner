/**
 * Website › Today: the old /admin dashboard (site/web/src/pages/admin/index.astro),
 * moved into the back office (owner, 2026-09-28).
 *
 * No KPI tiles: today's bookings as a list with Arrived / No-show on each row, the
 * seats taken per slot as quiet bars, the next seven days as one row list, and
 * three plain notes (messages, photos, menu). Dates and times from the site are the
 * café's own (Europe/London) date and HH:MM; they are never shifted (./dates.ts).
 */
import { useEffect, useState } from 'react'
import { Button, Empty, ErrorBox, LinkButton, Loading, PageBody, PageHeader, Pill, SectionHead, StatusLine, cx } from '../../components/ui'
import type { Outcome, PillTone } from '../../components/ui'
import { useIsDocked } from '../../lib/media'
import { href } from '../../lib/router'
import type { Booking, BookingDay, BookingStatus, SiteSettings, SiteSummary } from '../../lib/types/website'
import { siteWrite, useInvalidateWebsite, useSiteSettings, useWebsiteSummary } from '../../lib/website-api'
import { STATUS_LABEL, count, holds, useBookingDay } from './bookings-parts'
import { clock, longDate, nowMinutes, shortDate, toMinutes, todayISO, weekday } from './dates'
import { WebsiteGate } from './shared'

/* ------------------------------------------------------------- words --- */

const STATUS_TONE: Record<BookingStatus, PillTone> = {
  confirmed: 'brand',
  arrived: 'ok',
  no_show: 'warn',
  cancelled: 'muted',
}

/* ------------------------------------------------------------ screen --- */

export function TodayScreen() {
  return (
    <>
      <PageHeader
        title="Today"
        subtitle="who is coming, and what needs you"
        actions={<LinkButton className="min-h-11" href={href('/website/bookings', { add: 1 })}>Add booking</LinkButton>}
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
  // The same query (key and all) that Bookings reads, so a status change on either reaches both.
  const day = useBookingDay(date)
  const settings = useSiteSettings()

  const failed = summary.error ?? day.error ?? settings.error
  if (failed) {
    return (
      <div className="flex flex-col items-start gap-3">
        <ErrorBox error={failed} what="today’s bookings" />
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
    <div className="mx-auto grid w-full max-w-[1100px] items-start gap-7 compact:grid-cols-[minmax(0,1.7fr)_minmax(260px,1fr)] compact:gap-8">
      <TodaySection summary={summary.data} day={day.data} settings={settings.data} />
      <aside className="flex min-w-0 flex-col gap-6.5">
        <WeekRow summary={summary.data} settings={settings.data} />
        <Notes summary={summary.data} />
      </aside>
    </div>
  )
}

/* ------------------------------------------------------------- today --- */

function TodaySection({ summary, day, settings }: { summary: SiteSummary; day: BookingDay; settings: SiteSettings }) {
  const today = summary.today.date || day.date
  const now = useMinuteTick()
  const invalidate = useInvalidateWebsite()
  const [pending, setPending] = useState<number | null>(null)
  const [outcome, setOutcome] = useState<Outcome | null>(null)

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
    setOutcome(null)
    const r = await siteWrite<Booking>(`/bookings/${b.id}`, { status }, 'PATCH')
    setPending(null)
    if (r.kind !== 'ok') {
      setOutcome({ kind: 'error', text: r.message })
      return
    }
    await invalidate()
    const from = b.status
    setOutcome({
      kind: 'ok',
      text: `${b.name}: ${STATUS_LABEL[status].toLowerCase()}.`,
      action: {
        label: 'Undo',
        onClick: () => {
          setOutcome(null)
          void siteWrite<Booking>(`/bookings/${b.id}`, { status: from }, 'PATCH').then(async (u) => {
            if (u.kind === 'ok') {
              await invalidate()
              setOutcome({ kind: 'ok', text: `${b.name}: back to ${STATUS_LABEL[from].toLowerCase()}.` })
            } else setOutcome({ kind: 'error', text: u.message })
          })
        },
      },
    })
  }

  return (
    <section aria-labelledby="today-h" className="min-w-0">
      <SectionHead size="panel" right={openLine && <Pill tone={openLine.tone}>{openLine.text}</Pill>}>
        <span id="today-h">{longDate(today)}</span>
      </SectionHead>

      <p className="mt-3 mb-4 text-xl text-ink">
        {live.length ? (
          <>
            <span className="fig font-bold">{count(covers, 'guest')}</span> in{' '}
            <span className="fig font-bold">{count(live.length, 'booking')}</span>.{' '}
            <span className="whitespace-nowrap text-ink-2">{waiting ? `${waiting} still to arrive.` : 'Everyone booked has arrived.'}</span>
          </>
        ) : day.closed ? (
          'No bookings.'
        ) : (
          'No bookings yet today.'
        )}
      </p>

      <StatusLine outcome={outcome} className="mb-3" />

      {shown.length ? (
        <ul aria-label="Today’s bookings" className="border-t border-line">
          {shown.map((b) => (
            <BookingRow key={b.id} b={b} busy={pending === b.id} onStatus={(s) => void setStatus(b, s)} />
          ))}
        </ul>
      ) : (
        <div className="border-y border-line">
          <Empty
            action={
              !day.closed && (
                <LinkButton className="min-h-11" href={href('/website/bookings', { add: 1 })}>
                  Add a phone booking
                </LinkButton>
              )
            }
          >
            {day.closed ? 'Closed today.' : 'Nobody has booked for today. Walk-ins only.'}
          </Empty>
        </div>
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
            <Button className="min-h-11" pending={busy} pendingLabel="Saving…" onClick={() => onStatus('arrived')} aria-label={`Mark ${b.name} arrived`}>
              Arrived
            </Button>
            <Button variant="ghost" className="min-h-11" disabled={busy} onClick={() => onStatus('no_show')} aria-label={`Mark ${b.name} as a no-show`}>
              No-show
            </Button>
          </>
        ) : (
          <>
            <Pill tone={STATUS_TONE[b.status]}>{STATUS_LABEL[b.status]}</Pill>
            {(b.status === 'arrived' || b.status === 'no_show') && (
              <Button
                variant="ghost"
                className="min-h-11"
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

/** One quiet bar per slot: seats taken against capacity. Folded by default below the docked width. */
function SlotBars({ day }: { day: BookingDay }) {
  const docked = useIsDocked()
  const [open, setOpen] = useState(docked)
  return (
    <details open={open} className="mt-5" onToggle={(e) => setOpen(e.currentTarget.open)}>
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

/** The next seven days as one list: day · bookings · guests. Each row opens that day on Bookings. */
function WeekRow({ summary, settings }: { summary: SiteSummary; settings: SiteSettings }) {
  const closures = new Set(settings.closures.map((c) => c.date))
  return (
    <section aria-labelledby="week-h">
      <SectionHead>
        <span id="week-h">Next 7 days</span>
      </SectionHead>
      <ol className="mt-2 border-t border-line">
        {summary.week.map((d, i) => {
          const hw = settings.cafe.hours.find((x) => x.weekday === weekday(d.date))
          const closed = closures.has(d.date) || !hw || hw.closed === true
          return (
            <li key={d.date}>
              <a
                href={href('/website/bookings', { date: d.date })}
                aria-current={i === 0 ? 'date' : undefined}
                className={cx(
                  'flex min-h-11 items-center gap-3 border-b border-line px-1 py-2 text-base text-ink no-underline hover:bg-canvas-2',
                  i === 0 && 'font-bold',
                )}
              >
                <span className="w-24 flex-none">{i === 0 ? 'Today' : shortDate(d.date)}</span>
                {closed ? (
                  <span className="text-ink-2">Closed</span>
                ) : (
                  <span className="fig min-w-0 flex-1">
                    {count(d.bookings, 'booking')}
                    <span className="text-ink-2"> · {count(d.covers, 'guest')}</span>
                  </span>
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
  const num = (x: number) => <span className="fig font-bold">{x}</span>
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
      <a className={row} href={href('/menu')}>
        <span className="min-w-0 flex-1">
          {m.source === 'ops'
            ? 'The website menu is Menu items: descriptions, signature marks and what is shown come from Online ordering.'
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
