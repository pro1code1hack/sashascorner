/**
 * Website › Photos, moved in from the site's /admin/photos (owner, 2026-09-28).
 *
 *   #/website/photos              Places on the site: what is empty, then every
 *                                 slot by page with its focal point and Save
 *   #/website/photos?tab=library  Photo library: upload, describe, delete
 *
 * The site owns the data (site/BRIEF.md "Media & image slots"); unsaved slot
 * edits live in photos/drafts.ts so a refetch never loses them.
 */
import { useEffect } from 'react'
import { Button, ErrorBox, Loading, PageBody, PageHeader, cx } from '../../components/ui'
import { href, navigate, useLocation } from '../../lib/router'
import { PhotosProvider, usePhotos } from './photos/context'
import { LibraryTab } from './photos/LibraryTab'
import { goToSlot, useMedia, useSlots } from './photos/model'
import { Picker } from './photos/Picker'
import { PlacesTab } from './photos/PlacesTab'
import { WebsiteGate } from './shared'

type Tab = 'places' | 'library'

const PATH = '/website/photos'

export function PhotosScreen() {
  const tab: Tab = useLocation().query.get('tab') === 'library' ? 'library' : 'places'
  return (
    <>
      <PageHeader title="Photos" subtitle={<PhotosTabs current={tab} />} />
      <WebsiteGate>
        <Loaded tab={tab} />
      </WebsiteGate>
    </>
  )
}

function PhotosTabs({ current }: { current: Tab }) {
  const tabs = [
    { id: 'places' as const, label: 'Places on the site', to: href(PATH) },
    { id: 'library' as const, label: 'Photo library', to: href(PATH, { tab: 'library' }) },
  ]
  return (
    <span className="inline-flex flex-wrap items-center gap-1" role="navigation" aria-label="Photos sections">
      {tabs.map((t) => (
        <a
          key={t.id}
          href={t.to}
          aria-current={t.id === current ? 'page' : undefined}
          className={cx(
            'inline-flex h-8 items-center whitespace-nowrap rounded-full px-3.5 text-base font-bold no-underline transition-colors max-compact:h-11',
            t.id === current ? 'bg-brand-wash text-brand-ink' : 'text-ink-2 hover:bg-canvas hover:text-ink',
          )}
        >
          {t.label}
        </a>
      ))}
    </span>
  )
}

function Loaded({ tab }: { tab: Tab }) {
  const media = useMedia()
  const slots = useSlots()
  if (media.isError || slots.isError) {
    return (
      <PageBody>
        <ErrorBox error={media.error ?? slots.error} what="the photos" />
      </PageBody>
    )
  }
  if (!media.data || !slots.data) {
    return (
      <PageBody>
        <Loading what="Loading the photos" />
      </PageBody>
    )
  }
  return (
    <PhotosProvider slots={slots.data} media={media.data}>
      <Body tab={tab} />
    </PhotosProvider>
  )
}

function Body({ tab }: { tab: Tab }) {
  const p = usePhotos()
  const dirty = p.dirtyKeys

  // Placed-but-unsaved photos live only in this browser tab: ask before leaving it.
  useEffect(() => {
    if (!dirty.length) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty.length])

  const toPlace = (key: string) => {
    navigate(PATH)
    // After the places tab has rendered.
    window.setTimeout(() => goToSlot(key), 50)
  }

  const saveAll = async () => {
    const keys = [...dirty]
    const results: boolean[] = []
    for (const k of keys) results.push(await p.save(k))
    const failed = keys.filter((_, i) => !results[i])
    p.announce(failed.length ? `${failed.length} could not be saved. See the message beside each one.` : 'All changes saved.')
    if (failed[0]) toPlace(failed[0])
  }

  const busy = dirty.some((k) => p.status(k)?.tone === 'busy')
  const first = dirty[0]

  return (
    <>
      <div className="flex min-h-0 flex-1">
        <PageBody className="bg-canvas">
          <div className="mx-auto flex w-full max-w-[1100px] flex-col">
            {tab === 'library' ? (
              <LibraryTab onPlace={toPlace} />
            ) : (
              <PlacesTab onLibrary={() => navigate(PATH, { query: { tab: 'library' } })} />
            )}
          </div>
        </PageBody>
        <Picker />
      </div>
      {dirty.length > 0 && (
        <div className="flex flex-none flex-wrap items-center gap-x-4 gap-y-2 border-t border-alert bg-alert-wash px-4 py-2.5 sm:px-5">
          <p className="min-w-0 flex-1 text-base font-semibold">
            {dirty.length === 1 && first
              ? `Unsaved changes in ${p.slotMap.get(first)?.label ?? 'one place'}`
              : `Unsaved changes in ${dirty.length} places`}
          </p>
          {tab === 'library' && first && (
            <Button variant="ghost" className="max-compact:h-11" onClick={() => toPlace(first)}>
              Show them
            </Button>
          )}
          <Button
            variant={tab === 'places' ? 'primary' : 'secondary'}
            onClick={() => void saveAll()}
            pending={busy}
            pendingLabel="Saving…"
            className="max-compact:h-11"
          >
            Save all
          </Button>
        </div>
      )}
    </>
  )
}
