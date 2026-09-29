/**
 * Website › Events: evenings at the café, shown on the public site's Events page.
 * Moved from the site's /admin/events (owner, 2026-09-28).
 *
 *   #/website/events               Coming up | Past (?tab=past)
 *   #/website/events/new           add an event
 *   #/website/events/<id>          edit, publish, delete; its replies
 *
 * The public /events page shows published events that have not ended.
 */
import { ErrorBox, LinkButton, Loading, PageBody, PageHeader, Segmented } from '../../components/ui'
import { href, navigate, useLocation } from '../../lib/router'
import { WebsiteGate } from './shared'
import { EventPage, NewEventPage } from './events-page'
import { EventStatus, priceText, seatsText, shortDate, timeRange, useEvents } from './events-shared'
import type { AdminEvent } from './events-shared'

type Tab = 'upcoming' | 'past'

export function EventsScreen() {
  const sub = useLocation().segments[2]
  if (sub === 'new') return <NewEventPage />
  if (sub !== undefined && /^\d+$/.test(sub)) return <EventPage eventId={Number(sub)} />
  return <EventsList />
}

function EventsList() {
  const loc = useLocation()
  const tab: Tab = loc.query.get('tab') === 'past' ? 'past' : 'upcoming'
  return (
    <>
      <PageHeader
        title="Events"
        subtitle="Evenings at the café, on the website’s Events page."
        actions={
          <LinkButton variant="primary" href={href('/website/events/new')} className="max-sm:h-11">
            New event
          </LinkButton>
        }
      />
      <PageBody className="bg-canvas">
        <WebsiteGate>
          <EventsBody tab={tab} />
        </WebsiteGate>
      </PageBody>
    </>
  )
}

function EventsBody({ tab }: { tab: Tab }) {
  const q = useEvents()
  if (q.isError) return <ErrorBox error={q.error} what="the events" />
  if (q.isPending) return <Loading what="Reading the events" />
  const all = q.data
  const upcoming = all.filter((e) => !e.past).sort((a, b) => a.starts_at.localeCompare(b.starts_at))
  const past = all.filter((e) => e.past).sort((a, b) => b.starts_at.localeCompare(a.starts_at))
  const list = tab === 'upcoming' ? upcoming : past
  const setTab = (t: Tab) =>
    navigate('/website/events', {
      replace: true,
      query: { tab: t === 'past' ? 'past' : undefined },
    })
  return (
    <div className="mx-auto flex max-w-[1100px] flex-col gap-4">
      <Segmented
        label="Which events"
        value={tab}
        onChange={setTab}
        className="self-start"
        options={[
          { value: 'upcoming', label: `Coming up · ${upcoming.length}` },
          { value: 'past', label: `Past · ${past.length}` },
        ]}
      />
      {list.length === 0 ? (
        <p className="rounded-card border border-dashed border-line-strong px-4 py-6 text-center text-base text-ink-2">
          {tab === 'upcoming'
            ? 'Nothing coming up. Add an event and publish it to show it on the website.'
            : 'No past events yet.'}
        </p>
      ) : (
        <ul className="overflow-hidden rounded-card-lg border border-line bg-surface">
          {list.map((e) => (
            <EventRow key={e.id} e={e} />
          ))}
        </ul>
      )}
    </div>
  )
}

function EventRow({ e }: { e: AdminEvent }) {
  const meta = [seatsText(e), `${e.rsvps} ${e.rsvps === 1 ? 'reply' : 'replies'}`, priceText(e.price_pence)]
    .filter(Boolean)
    .join(' · ')
  return (
    <li className="border-b border-line last:border-b-0">
      <a
        href={href(`/website/events/${e.id}`)}
        className="grid min-h-14 grid-cols-[4.75rem_minmax(0,1fr)] gap-x-4 gap-y-1 px-4 py-3 text-ink no-underline hover:bg-canvas sm:grid-cols-[6rem_minmax(0,1fr)_auto] sm:items-center"
      >
        <span className="flex flex-col leading-tight">
          <span className="fig text-md font-bold">{shortDate(e.date)}</span>
          <span className="fig text-sm text-ink-2">{timeRange(e)}</span>
        </span>
        <span className="flex min-w-0 flex-col gap-1">
          <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <span className="text-md font-bold [overflow-wrap:anywhere]">{e.title}</span>
            <EventStatus e={e} />
          </span>
          <span className="fig text-sm text-ink-2">{meta}</span>
        </span>
        <span className="col-start-2 text-sm font-semibold text-brand-ink sm:col-start-auto">Edit · replies</span>
      </a>
    </li>
  )
}
