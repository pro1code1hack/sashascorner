/**
 * One model for the Photos screen: the library, the slots, the drafts and the
 * save per slot. Every part of the screen reads it through `usePhotos()`.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useInvalidateWebsite } from '../../../lib/website-api'
import { dropDraft, setDraft, setStatus, useDraftState, type SlotStatus } from './drafts'
import { draftOf, putSlot, snap, usePutSlot, type DraftItem, type Media, type Slot } from './model'

export interface PhotosModel {
  slots: Slot[]
  slotMap: Map<string, Slot>
  media: Media[]
  mediaMap: Map<number, Media>
  draft: (key: string) => DraftItem[]
  isDirty: (key: string) => boolean
  dirtyKeys: string[]
  status: (key: string) => SlotStatus | undefined
  change: (key: string, items: DraftItem[]) => void
  revert: (key: string) => void
  save: (key: string) => Promise<boolean>
  /** Put photos into a slot (appending for a gallery, replacing for one photo). */
  place: (key: string, ids: number[]) => void
  /** Open the library picker for a slot. */
  pick: (key: string) => void
  pickerKey: string | null
  closePicker: () => void
  announce: (text: string) => void
  /** Which gallery item is open in the focal editor. */
  selected: (key: string) => number
  select: (key: string, i: number) => void
}

const Ctx = createContext<PhotosModel | null>(null)

export function usePhotos(): PhotosModel {
  const m = useContext(Ctx)
  if (!m) throw new Error('usePhotos outside PhotosProvider')
  return m
}

export function PhotosProvider({
  slots,
  media,
  children,
}: {
  slots: Slot[]
  media: Media[]
  children: ReactNode
}) {
  const ds = useDraftState()
  const putSlotCache = usePutSlot()
  const invalidate = useInvalidateWebsite()
  const [pickerKey, setPickerKey] = useState<string | null>(null)
  const [live, setLive] = useState('')
  const [sel, setSel] = useState<Record<string, number>>({})
  const liveTimer = useRef<number | null>(null)

  const slotMap = useMemo(() => new Map(slots.map((s) => [s.key, s])), [slots])
  const mediaMap = useMemo(() => new Map(media.map((m) => [m.id, m])), [media])

  const saved = useCallback(
    (key: string): DraftItem[] => {
      const s = slotMap.get(key)
      return s ? draftOf(s, mediaMap) : []
    },
    [slotMap, mediaMap],
  )
  const draft = useCallback((key: string) => ds.drafts.get(key) ?? saved(key), [ds.drafts, saved])
  const isDirty = useCallback(
    (key: string) => {
      const d = ds.drafts.get(key)
      return d !== undefined && slotMap.has(key) && snap(d) !== snap(saved(key))
    },
    [ds.drafts, saved, slotMap],
  )
  const dirtyKeys = useMemo(() => slots.map((s) => s.key).filter(isDirty), [slots, isDirty])

  const announce = useCallback((text: string) => {
    setLive('')
    if (liveTimer.current !== null) window.clearTimeout(liveTimer.current)
    // A fresh value after a tick makes repeated identical messages re-announce.
    liveTimer.current = window.setTimeout(() => setLive(text), 60)
  }, [])

  const save = useCallback(
    async (key: string): Promise<boolean> => {
      const s = slotMap.get(key)
      const items = ds.drafts.get(key)
      if (!s || !items || !isDirty(key)) return true
      setStatus(key, { text: 'Saving…', tone: 'busy' })
      const r = await putSlot(key, items)
      if (r.kind === 'ok') {
        putSlotCache({ ...r.data, key, label: s.label, page: s.page })
        dropDraft(key)
        const t = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' }).format(new Date())
        setStatus(key, { text: `Saved at ${t}. The site shows it within a minute.`, tone: 'ok' })
        announce(`${s.label}: saved.`)
        void invalidate()
        return true
      }
      setStatus(key, { text: `Not saved: ${r.message}`, tone: 'bad' })
      announce(`${s.label}: not saved. ${r.message}`)
      return false
    },
    [slotMap, ds.drafts, isDirty, putSlotCache, announce, invalidate],
  )

  const place = useCallback(
    (key: string, ids: number[]) => {
      const s = slotMap.get(key)
      if (!s || !ids.length) return
      const cur = draft(key)
      const fresh = ids.map((id) => ({ media_id: id, alt: '', focal: { x: 0.5, y: 0.5 } }))
      if (s.multiple) {
        const next = [...cur, ...fresh.filter((f) => !cur.some((c) => c.media_id === f.media_id))].slice(0, s.max)
        setSel((p) => ({ ...p, [key]: Math.max(0, next.length - 1) }))
        setDraft(key, next)
      } else {
        setDraft(key, fresh.slice(0, 1))
      }
      const n = ids.length
      announce(`${n === 1 ? 'Photo' : `${n} photos`} placed in ${s.label}. Set the focal point, then save.`)
    },
    [slotMap, draft, announce],
  )

  const model: PhotosModel = {
    slots,
    slotMap,
    media,
    mediaMap,
    draft,
    isDirty,
    dirtyKeys,
    status: (key) => ds.status.get(key),
    change: setDraft,
    revert: (key) => {
      dropDraft(key)
      setStatus(key, null)
      announce('Changes undone.')
    },
    save,
    place,
    pick: setPickerKey,
    pickerKey,
    closePicker: () => setPickerKey(null),
    announce,
    selected: (key) => sel[key] ?? 0,
    select: (key, i) => setSel((p) => ({ ...p, [key]: i })),
  }

  return (
    <Ctx.Provider value={model}>
      {children}
      <div className="sr-only" role="status" aria-live="polite">
        {live}
      </div>
    </Ctx.Provider>
  )
}
