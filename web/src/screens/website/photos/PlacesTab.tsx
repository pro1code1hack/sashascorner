/**
 * "Places on the site": what is empty, then every slot grouped by page.
 */
import { useState } from 'react'
import { Button, SectionHead } from '../../../components/ui'
import { livePageUrl, useWebsiteConnection } from '../../../lib/website-api'
import { usePhotos } from './context'
import { PAGE_ORDER, goToSlot, pageName, type Slot } from './model'
import { SlotCard } from './SlotCard'

const SHOW = 8

export function PlacesTab({ onLibrary }: { onLibrary: () => void }) {
  const p = usePhotos()
  const byPage = new Map<string, Slot[]>()
  for (const s of p.slots) {
    const list = byPage.get(s.page) ?? []
    list.push(s)
    byPage.set(s.page, list)
  }
  const pages = [...byPage.keys()].sort((a, b) => {
    const ia = PAGE_ORDER.indexOf(a)
    const ib = PAGE_ORDER.indexOf(b)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b)
  })

  return (
    <div className="flex flex-col gap-6">
      <Missing onLibrary={onLibrary} />
      <p className="max-w-[70ch] text-base text-ink-2">
        Each frame is drawn in the shape the website shows it. Choose a photo, tap its most important part, then press
        Save.
      </p>
      {pages.length === 0 && <p className="text-base text-ink-2">No photo places are set up on the site yet.</p>}
      {pages.map((page) => (
        <PageGroup key={page} page={page} slots={byPage.get(page) ?? []} />
      ))}
    </div>
  )
}

function PageGroup({ page, slots }: { page: string; slots: Slot[] }) {
  const conn = useWebsiteConnection()
  const url = livePageUrl(conn.data, page)
  const id = `photo-page-${page.replace(/[^a-z0-9]+/gi, '-') || 'home'}`
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line pb-2">
        <h2 id={id} className="text-label font-bold uppercase tracking-[.05em] text-ink-2">
          {pageName(page)} page
        </h2>
        {url && (
          <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center text-base font-semibold text-brand-ink compact:min-h-0">
            Open the live page<span className="sr-only"> (opens in a new tab)</span>
            <span aria-hidden="true">&nbsp;↗</span>
          </a>
        )}
      </div>
      {slots.map((s) => (
        <SlotCard key={s.key} slot={s} />
      ))}
    </section>
  )
}

/** The empty places, the galleries that could take more, and photos with no description. */
function Missing({ onLibrary }: { onLibrary: () => void }) {
  const p = usePhotos()
  const [showAll, setShowAll] = useState(false)
  const empty = p.slots.filter((s) => s.items.length === 0)
  const partial = p.slots.filter((s) => s.multiple && s.items.length > 0 && s.items.length < s.max)
  const noAlt = p.media.filter((m) => !m.alt).length
  const done = p.slots.length - empty.length
  const shown = showAll ? empty : empty.slice(0, SHOW)

  const nothing = !empty.length && !partial.length && !noAlt

  return (
    <section aria-labelledby="photo-missing-t" className="flex flex-col gap-3 rounded-card border border-line bg-surface px-4 py-4">
      <SectionHead
        as="h2"
        right={
          p.slots.length ? (
            <span className="fig text-base text-ink-2">
              {done} of {p.slots.length} places have photos
            </span>
          ) : undefined
        }
      >
        <span id="photo-missing-t">{nothing ? 'Nothing missing' : 'Empty places'}</span>
      </SectionHead>
      {nothing && <p className="text-base text-ink-2">Every place on the site has a photo, and every photo has a description.</p>}
      {empty.length > 0 && (
        <ol id="photo-missing-list" className="grid gap-x-6 gap-y-1.5 compact:grid-cols-2">
          {shown.map((s) => (
            <MissingLink key={s.key} s={s} extra={s.multiple ? `, up to ${s.max}` : ''} />
          ))}
        </ol>
      )}
      {empty.length > SHOW && !showAll && (
        <div>
          <Button variant="link" aria-controls="photo-missing-list" onClick={() => setShowAll(true)} className="min-h-11 compact:min-h-0">
            Show all {empty.length} empty places
          </Button>
        </div>
      )}
      {partial.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h3 className="text-label font-bold uppercase tracking-[.05em] text-ink-2">Could take more</h3>
          <ul className="grid gap-x-6 gap-y-1.5 compact:grid-cols-2">
            {partial.map((s) => (
              <MissingLink key={s.key} s={s} extra={`, ${s.items.length} of ${s.max}`} />
            ))}
          </ul>
        </div>
      )}
      {noAlt > 0 && (
        <p className="text-base text-ink-2">
          <button
            type="button"
            onClick={onLibrary}
            className="min-h-11 font-semibold text-brand-ink underline-offset-2 hover:underline compact:min-h-0"
          >
            {noAlt} {noAlt === 1 ? 'photo has' : 'photos have'} no description
          </button>{' '}
          — descriptions are read aloud to people who can’t see the photos.
        </p>
      )}
    </section>
  )
}

function MissingLink({ s, extra }: { s: Slot; extra: string }) {
  return (
    <li className="flex flex-col">
      <span>
        <button
          type="button"
          onClick={() => goToSlot(s.key)}
          className="min-h-11 text-left text-base font-semibold text-brand-ink underline-offset-2 hover:underline compact:min-h-0"
        >
          {s.label}
        </button>
        <span className="text-base text-ink-2">
          {' '}
          · {pageName(s.page)} page{extra}
        </span>
      </span>
      {s.hint && <span className="text-sm text-ink-2">{s.hint}</span>}
    </li>
  )
}
