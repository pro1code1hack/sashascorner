/**
 * Website › Bookings: tables booked online and by phone (moved from the site's
 * /admin/bookings, owner 2026-09-28).
 *
 * One day at a time (the list, plus seats taken at each time against capacity),
 * or a week. Typing in search looks a year either side. Every view setting is in
 * the hash (`#/website/bookings?date=2026-09-28&view=week&q=smith&status=arrived`),
 * and one booking opens as `#/website/bookings/<id>` over the same view; a phone
 * booking as `#/website/bookings/new`. The phone is the main place this is read.
 *
 * "Print day sheet" (from the old /admin/bookings) prints the day as a plain list
 * for the counter: time, name, party, phone, notes, with a box to tick on arrival.
 * The sheet is its own element under <body>, shown only by `@media print`, so the
 * app shell never reaches the paper.
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  Banner,
  Button,
  Empty,
  ErrorBox,
  FilterChip,
  FilterChipRow,
  Input,
  Loading,
  PageBody,
  PageHeader,
  SearchInput,
  SectionHead,
  Segmented,
  Toolbar,
} from '../../components/ui'
import { navigate, useLocation } from '../../lib/router'
import { useInvalidateWebsite, useSiteSettings } from '../../lib/website-api'
import type { Booking, BookingStatus } from '../../lib/types/website'
import { AddBookingDrawer, BookingDrawer, MissingBookingDrawer } from './bookings-drawers'
import {
  BookingRow,
  RowList,
  STATUS_LABEL,
  SlotTimeline,
  count,
  holds,
  hoursLine,
  patchBooking,
  people,
  useBookingDay,
  useBookingRange,
} from './bookings-parts'
import type { Notice } from './bookings-parts'
import { ISO_DATE, addDays, longDate, relDay, shortDate, todayISO, weekStart } from './dates'
import { WebsiteGate } from './shared'

const BASE = '/website/bookings'
type View = 'day' | 'week'
type Filter = 'all' | BookingStatus
const FILTERS: Filter[] = ['all', 'confirmed', 'arrived', 'no_show', 'cancelled']

interface ViewState {
  date: string
  view: View
  q: string
  status: Filter
}

function readState(q: URLSearchParams): ViewState {
  const d = q.get('date') ?? ''
  const s = q.get('status') ?? 'all'
  return {
    date: ISO_DATE.test(d) ? d : todayISO(),
    view: q.get('view') === 'week' ? 'week' : 'day',
    q: q.get('q') ?? '',
    status: (FILTERS as string[]).includes(s) ? (s as Filter) : 'all',
  }
}

function queryOf(s: ViewState): Record<string, string> {
  const out: Record<string, string> = {}
  if (s.date !== todayISO()) out.date = s.date
  if (s.view !== 'day') out.view = s.view
  if (s.q) out.q = s.q
  if (s.status !== 'all') out.status = s.status
  return out
}

export function BookingsScreen() {
  const loc = useLocation()
  const sub = loc.segments[2]
  return (
    <>
      <PageHeader
        title="Bookings"
        subtitle="Tables booked online and by phone"
        actions={
          // One primary per view: the drawer's own button takes over while it is open.
          sub === undefined ? (
            <Button
              variant="primary"
              className="min-h-11"
              onClick={() => navigate(`${BASE}/new`, { query: Object.fromEntries(loc.query) })}
            >
              Add booking
            </Button>
          ) : undefined
        }
      />
      <WebsiteGate>
        <BookingsBody />
      </WebsiteGate>
    </>
  )
}

function BookingsBody() {
  const loc = useLocation()
  const state = readState(loc.query)
  const sub = loc.segments[2]
  const today = todayISO()

  const set = (patch: Partial<ViewState>) => {
    navigate(loc.path, { query: queryOf({ ...state, ...patch }), replace: true })
  }

  // Links from elsewhere (Website › Today) say `?open=<id>` or `?add=1`, with or
  // without `date`. They become the sub-paths below, so closing the panel clears them.
  const openParam = loc.query.get('open')
  const addParam = loc.query.get('add')
  useEffect(() => {
    if (openParam !== null && /^\d+$/.test(openParam)) navigate(`${BASE}/${openParam}`, { query: queryOf(state), replace: true })
    else if (addParam === '1') navigate(`${BASE}/new`, { query: queryOf(state), replace: true })
    else if (openParam !== null || addParam !== null) navigate(loc.path, { query: queryOf(state), replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openParam, addParam])

  // Search is typed into, so it is local first and reaches the URL after a pause.
  const [q, setQ] = useState(state.q)
  const [seenQ, setSeenQ] = useState(state.q)
  if (seenQ !== state.q) {
    setSeenQ(state.q)
    setQ(state.q)
  }
  useEffect(() => {
    if (q === state.q) return
    const t = window.setTimeout(() => set({ q }), 250)
    return () => window.clearTimeout(t)
    // `set` reads the URL at the time it fires; re-arming on q alone is intended.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])

  const [notice, setNotice] = useState<Notice | null>(null)
  const [undoing, setUndoing] = useState(false)
  const invalidate = useInvalidateWebsite()
  const undo = async () => {
    const u = notice?.undo
    if (!u || undoing) return
    setUndoing(true)
    const r = await patchBooking(u.id, u.body)
    setUndoing(false)
    if (r.kind === 'ok') {
      setNotice({ tone: 'stale', text: `Undone. ${u.label}.` })
      void invalidate()
    } else setNotice({ tone: 'alert', text: r.message })
  }

  const searching = state.q.trim().length > 0
  const settings = useSiteSettings()
  const dayQ = useBookingDay(!searching && state.view === 'day' ? state.date : null)
  const from = weekStart(state.date)
  const weekQ = useBookingRange(from, addDays(from, 6), !searching && state.view === 'week')

  // The booking a sub-path names, from whatever is loaded; the row that opened it
  // covers a booking that has just left the loaded view. A link to a booking on
  // another date is looked up in the year either side, like search.
  const [opened, setOpened] = useState<Booking | null>(null)
  const openId = sub !== undefined && sub !== 'new' && /^\d+$/.test(sub) ? Number(sub) : null
  const inView = [...(dayQ.data?.bookings ?? []), ...(weekQ.data ?? [])].find((b) => b.id === openId)
  const viewSettled = !dayQ.isFetching && !weekQ.isFetching
  const poolWanted = searching || (openId !== null && inView === undefined && opened?.id !== openId && viewSettled)
  const poolQ = useBookingRange(addDays(today, -365), addDays(today, 365), poolWanted)
  const openBooking =
    openId === null
      ? null
      : (inView ?? poolQ.data?.find((b) => b.id === openId) ?? (opened?.id === openId ? opened : null))
  const stillLoading = dayQ.isFetching || weekQ.isFetching || poolQ.isFetching || (poolWanted && poolQ.isPending)

  const open = (b: Booking) => {
    setOpened(b)
    navigate(`${BASE}/${b.id}`, { query: queryOf(state) })
  }
  const close = () => navigate(BASE, { query: queryOf(state), replace: true })

  const step = (n: number) => set({ date: addDays(state.date, state.view === 'week' ? 7 * n : n), status: 'all' })
  const unit = state.view === 'week' ? 'week' : 'day'

  return (
    <>
      <Toolbar className="gap-x-3">
        <div role="group" aria-label={`Choose the ${unit}`} className="flex items-center gap-1.5">
          <Button className="min-h-11 min-w-11" aria-label={`Previous ${unit}`} onClick={() => step(-1)}>
            <span aria-hidden="true">‹</span>
          </Button>
          <Button className="min-h-11" onClick={() => set({ date: today, status: 'all' })}>
            {state.view === 'week' ? 'This week' : 'Today'}
          </Button>
          <Button className="min-h-11 min-w-11" aria-label={`Next ${unit}`} onClick={() => step(1)}>
            <span aria-hidden="true">›</span>
          </Button>
          <Input
            type="date"
            aria-label="Go to date"
            className="min-h-11 w-[9.5rem]"
            value={state.date}
            onChange={(e) => {
              if (ISO_DATE.test(e.target.value)) set({ date: e.target.value, status: 'all' })
            }}
          />
        </div>
        <Segmented<View>
          label="View"
          value={state.view}
          onChange={(v) => set({ view: v })}
          options={[
            { value: 'day', label: 'Day' },
            { value: 'week', label: 'Week' },
          ]}
        />
        <SearchInput
          label="Search bookings by name, reference, email or phone"
          placeholder="Search name, reference or phone"
          className="min-h-11 w-full sm:max-w-md"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setSeenQ(e.target.value)
          }}
        />
      </Toolbar>

      {notice && (
        <div className="flex-none pb-1">
          <Banner
            tone={notice.tone}
            onDismiss={() => setNotice(null)}
            action={
              notice.undo ? { label: 'Undo', onClick: () => void undo(), pending: undoing, pendingLabel: 'Undoing…' } : undefined
            }
          >
            {notice.text}
          </Banner>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <PageBody className="bg-canvas">
          <div className="mx-auto w-full max-w-[1100px]">
            {searching ? (
              <SearchView q={state.q.trim()} pool={poolQ} onOpen={open} notify={setNotice} />
            ) : state.view === 'day' ? (
              <DayView
                date={state.date}
                dayQ={dayQ}
                settings={settings.data}
                filter={state.status}
                setFilter={(f) => set({ status: f })}
                onOpen={open}
                notify={setNotice}
              />
            ) : (
              <WeekView
                from={from}
                weekQ={weekQ}
                settings={settings.data}
                goToDay={(d) => set({ date: d, view: 'day', status: 'all' })}
                onOpen={open}
                notify={setNotice}
              />
            )}
          </div>
        </PageBody>
        {sub === 'new' && (
          <AddBookingDrawer
            defaultDate={state.date < today ? today : state.date}
            onClose={close}
            onCreated={(b) => {
              setNotice({
                tone: 'stale',
                text: `Booked: ${b.name}, ${people(b.party)}, ${relDay(b.date)} at ${b.time}. Ref ${b.reference}.`,
              })
              navigate(BASE, {
                query: queryOf({ ...state, q: '', status: 'all', date: state.view === 'day' ? b.date : state.date }),
                replace: true,
              })
            }}
          />
        )}
        {openBooking && <BookingDrawer key={openBooking.id} booking={openBooking} onClose={close} notify={setNotice} />}
        {sub !== undefined && sub !== 'new' && !openBooking && !stillLoading && (
          <MissingBookingDrawer id={sub} onClose={close} />
        )}
      </div>
    </>
  )
}

type Settings = ReturnType<typeof useSiteSettings>['data']

/* -------------------------------------------------------------------- day --- */

function DayView({
  date,
  dayQ,
  settings,
  filter,
  setFilter,
  onOpen,
  notify,
}: {
  date: string
  dayQ: ReturnType<typeof useBookingDay>
  settings: Settings
  filter: Filter
  setFilter: (f: Filter) => void
  onOpen: (b: Booking) => void
  notify: (n: Notice) => void
}) {
  const day = dayQ.data
  const title = `${date === todayISO() ? 'Today · ' : ''}${longDate(date)}`
  let sub = ''
  if (day) {
    const live = day.bookings.filter(holds)
    const guests = live.reduce((a, b) => a + b.party, 0)
    const hl = hoursLine(settings, date)
    sub = day.closed
      ? hl.startsWith('Closed')
        ? hl
        : `Closed${day.reason ? `: ${day.reason}` : ''}`
      : `${hl ? `${hl} · ` : ''}${count(live.length, 'booking')}, ${count(guests, 'guest')}`
  }

  const counts: Record<Filter, number> = { all: 0, confirmed: 0, arrived: 0, no_show: 0, cancelled: 0 }
  for (const b of day?.bookings ?? []) {
    counts.all++
    counts[b.status]++
  }
  const shown = (day?.bookings ?? []).filter((b) => filter === 'all' || b.status === filter)
  const fullTimes = day ? day.slots.filter((s) => s.covers_booked >= s.capacity).length : 0

  return (
    <section aria-labelledby="bk-day-h" aria-busy={dayQ.isFetching}>
      <div className="mb-3 flex flex-col gap-0.5">
        <SectionHead
          size="panel"
          right={
            day && !day.closed ? (
              <Button className="min-h-11" onClick={() => window.print()}>
                Print day sheet
              </Button>
            ) : undefined
          }
        >
          <span id="bk-day-h">{title}</span>
        </SectionHead>
        {sub && <p className="text-base text-ink-2">{sub}</p>}
      </div>
      {day && <DaySheet day={day} title={title} sub={sub} />}
      {dayQ.isPending && <Loading what="Reading the day's bookings" />}
      {dayQ.isError && <ErrorBox error={dayQ.error} what="the day's bookings" />}
      {day && (
        <div className="grid gap-5 compact:grid-cols-[minmax(0,1fr)_minmax(260px,320px)]">
          <div className="min-w-0">
            {counts.all > 0 && (
              <FilterChipRow label="Show" scroll className="mb-2.5">
                {FILTERS.filter((f) => f === 'all' || counts[f] > 0 || filter === f).map((f) => (
                  <FilterChip key={f} active={filter === f} onClick={() => setFilter(f)} count={counts[f]}>
                    {f === 'all' ? 'All' : STATUS_LABEL[f]}
                  </FilterChip>
                ))}
              </FilterChipRow>
            )}
            {shown.length ? (
              <RowList label="Bookings for this day">
                {shown.map((b) => (
                  <BookingRow key={b.id} b={b} onOpen={onOpen} notify={notify} />
                ))}
              </RowList>
            ) : (
              <div className="rounded-card-lg bg-surface shadow-raised">
                <Empty
                  action={
                    filter !== 'all' ? (
                      <Button className="min-h-11" onClick={() => setFilter('all')}>
                        Show all bookings
                      </Button>
                    ) : !day.closed && date >= todayISO() ? (
                      <Button className="min-h-11" onClick={() => navigate(`${BASE}/new`, { query: { date } })}>
                        Add a phone booking
                      </Button>
                    ) : undefined
                  }
                >
                  {day.closed ? 'Closed. No bookings.' : filter === 'all' ? 'No bookings for this day yet.' : 'None with this status.'}
                </Empty>
              </div>
            )}
          </div>
          <section aria-labelledby="bk-tl-h" className="min-w-0 self-start rounded-card-lg bg-surface px-4 py-3.5 shadow-raised">
            <h3 id="bk-tl-h" className="text-label font-bold uppercase tracking-[.06em] text-ink-2">
              Seats taken by time
            </h3>
            {!day.closed && (
              <p className="mb-2 mt-1 text-sm text-ink-2">
                Out of {day.capacity} seats. A table is held for {settings ? settings.booking.duration_minutes : 90} minutes.
                {fullTimes > 0 && <b className="text-bad-ink"> {count(fullTimes, 'time')} full.</b>}
              </p>
            )}
            <SlotTimeline day={day} />
          </section>
        </div>
      )}
    </section>
  )
}

/* -------------------------------------------------------------- day sheet --- */

const PRINT_CSS = `
.bk-sheet { display: none; }
@media print {
  @page { margin: 12mm; }
  body > *:not(.bk-sheet) { display: none !important; }
  html, body { background: #fff !important; height: auto !important; overflow: visible !important; }
  .bk-sheet { display: block; color: #000; font: 11pt/1.35 system-ui, sans-serif; }
  .bk-sheet h1 { font-size: 16pt; margin: 0 0 2pt; }
  .bk-sheet p { margin: 0 0 10pt; }
  .bk-sheet table { width: 100%; border-collapse: collapse; }
  .bk-sheet th { text-align: left; font-size: 9pt; text-transform: uppercase; letter-spacing: .05em; border-bottom: 1.5pt solid #000; padding: 3pt 6pt 3pt 0; }
  .bk-sheet td { vertical-align: top; border-bottom: .5pt solid #999; padding: 5pt 6pt 5pt 0; }
  .bk-sheet tr { break-inside: avoid; }
  .bk-sheet .n { text-align: right; font-variant-numeric: tabular-nums; }
  .bk-sheet .t { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 700; }
  .bk-sheet .ph { font-variant-numeric: tabular-nums; white-space: nowrap; }
  .bk-sheet .box { width: 12pt; height: 12pt; border: 1.2pt solid #000; }
  .bk-sheet .notes { white-space: pre-wrap; }
}`

/** The printed day: bookings that hold a table (confirmed, arrived), by time. */
function DaySheet({ day, title, sub }: { day: NonNullable<ReturnType<typeof useBookingDay>['data']>; title: string; sub: string }) {
  const rows = day.bookings.filter(holds).sort((a, b) => a.time.localeCompare(b.time) || a.id - b.id)
  return createPortal(
    <div className="bk-sheet" aria-hidden="true">
      <style>{PRINT_CSS}</style>
      <h1>Bookings · {title.replace(/^Today · /, '')}</h1>
      <p>{sub}</p>
      {rows.length ? (
        <table>
          <thead>
            <tr>
              <th aria-label="Arrived" />
              <th>Time</th>
              <th>Name</th>
              <th className="n">Party</th>
              <th>Phone</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.id}>
                <td>
                  <div className="box" />
                </td>
                <td className="t">{b.time}</td>
                <td>{b.name}</td>
                <td className="n">{b.party}</td>
                <td className="ph">{b.phone ?? ''}</td>
                <td className="notes">{b.notes ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>No bookings.</p>
      )}
    </div>,
    document.body,
  )
}

/* ------------------------------------------------------------------- week --- */

function WeekView({
  from,
  weekQ,
  settings,
  goToDay,
  onOpen,
  notify,
}: {
  from: string
  weekQ: ReturnType<typeof useBookingRange>
  settings: Settings
  goToDay: (d: string) => void
  onOpen: (b: Booking) => void
  notify: (n: Notice) => void
}) {
  const today = todayISO()
  const list = weekQ.data
  return (
    <section aria-labelledby="bk-week-h" aria-busy={weekQ.isFetching}>
      <SectionHead size="panel" className="mb-3">
        <span id="bk-week-h">Week of {longDate(from)}</span>
      </SectionHead>
      {weekQ.isPending && <Loading what="Reading the week's bookings" />}
      {weekQ.isError && <ErrorBox error={weekQ.error} what="the week's bookings" />}
      {list && (
        <div className="flex flex-col gap-4">
          {Array.from({ length: 7 }, (_, i) => {
            const d = addDays(from, i)
            const bs = list.filter((b) => b.date === d).sort((a, b) => a.time.localeCompare(b.time))
            const live = bs.filter(holds)
            const guests = live.reduce((a, b) => a + b.party, 0)
            const hl = hoursLine(settings, d)
            const closed = hl.startsWith('Closed')
            return (
              <section key={d} aria-labelledby={`bk-w-${d}`}>
                <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-3">
                  <h3 id={`bk-w-${d}`} className="text-md font-extrabold">
                    <button
                      type="button"
                      onClick={() => goToDay(d)}
                      className="min-h-11 text-brand-ink underline-offset-2 hover:underline"
                    >
                      {d === today ? 'Today, ' : ''}
                      {shortDate(d)}
                    </button>
                  </h3>
                  <span className="text-sm text-ink-2">
                    {closed && bs.length === 0 ? hl : `${count(live.length, 'booking')}, ${count(guests, 'guest')}`}
                  </span>
                </div>
                {bs.length > 0 && (
                  <RowList label={`Bookings for ${shortDate(d)}`}>
                    {bs.map((b) => (
                      <BookingRow key={b.id} b={b} onOpen={onOpen} notify={notify} />
                    ))}
                  </RowList>
                )}
              </section>
            )
          })}
        </div>
      )}
    </section>
  )
}

/* ----------------------------------------------------------------- search --- */

function SearchView({
  q,
  pool,
  onOpen,
  notify,
}: {
  q: string
  pool: ReturnType<typeof useBookingRange>
  onOpen: (b: Booking) => void
  notify: (n: Notice) => void
}) {
  const needle = q.toLowerCase()
  const digits = needle.replace(/\D/g, '')
  const hits = (pool.data ?? [])
    .filter(
      (b) =>
        b.name.toLowerCase().includes(needle) ||
        b.reference.toLowerCase().includes(needle) ||
        (b.email ?? '').toLowerCase().includes(needle) ||
        (digits.length >= 4 && (b.phone ?? '').replace(/\D/g, '').includes(digits)),
    )
    .sort((a, b) => b.date.localeCompare(a.date) || a.time.localeCompare(b.time))
  const title = pool.data ? (hits.length ? `${count(hits.length, 'booking')} matching “${q}”` : `Nothing matches “${q}”`) : 'Searching…'
  return (
    <section aria-labelledby="bk-search-h">
      <SectionHead size="panel" className="mb-1">
        <span id="bk-search-h" aria-live="polite">
          {title}
        </span>
      </SectionHead>
      <p className="mb-3 text-sm text-ink-2">
        A year either side of today, latest date first{hits.length > 100 ? '; the first 100 are shown' : ''}.
      </p>
      {pool.isError && <ErrorBox error={pool.error} what="bookings to search" />}
      {hits.length > 0 && (
        <RowList label="Search results">
          {hits.slice(0, 100).map((b) => (
            <BookingRow key={b.id} b={b} showDate onOpen={onOpen} notify={notify} />
          ))}
        </RowList>
      )}
    </section>
  )
}
