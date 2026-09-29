/**
 * Photos: data, shapes and small helpers (ported from the site's
 * scripts/admin/{api,types,state,dom}.ts).
 *
 * Wire shapes differ from lib/types/website.ts in two places, so they are
 * normalised: `GET /api/slots` is a MAP `{slots: {<key>: {...}}}`, not an array
 * (here); `MediaOut.usage` is `[{slot_key, position, label}]`, not `string[]`
 * (lib/website-api.ts `normMedia`, under the one media key Events reads too).
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { request, type WriteResult } from '../../../lib/api'
import type { Media as LibMedia } from '../../../lib/types/website'
import { MEDIA_KEY, WEBSITE_KEY, normMedia, sitePath, siteWrite, useMedia } from '../../../lib/website-api'

export { MEDIA_KEY, normMedia, useMedia }

/* ------------------------------------------------------------ shapes --- */

export interface Focal {
  x: number
  y: number
}

export interface Usage {
  slot_key: string
  label: string | null
}

/** The library photo, as lib/types/website.ts declares it (`usage` de-duplicated by slot key). */
export type Media = LibMedia

export interface SlotItem {
  media_id: number
  src: string
  srcset: string
  width: number
  height: number
  alt: string
  focal: Focal
  blur: string
}

export interface Slot {
  key: string
  label: string
  page: string
  aspect: string
  multiple: boolean
  max: number
  hint: string
  items: SlotItem[]
}

/** What the owner is editing for one slot, before it is saved. */
export interface DraftItem {
  media_id: number
  /** Per-slot description. Empty means "use the photo's own description". */
  alt: string
  focal: Focal
}

/** Something with an image to show: a library photo or a slot item. */
export interface Pic {
  src: string
  srcset: string
  width: number
  height: number
  alt: string
  blur: string
  original_name?: string
}

/* ------------------------------------------------------- normalisers --- */

type Raw = Record<string, unknown>
const str = (v: unknown, d = '') => (typeof v === 'string' ? v : d)
const num = (v: unknown, d = 0) => (typeof v === 'number' && Number.isFinite(v) ? v : d)
export const clamp01 = (v: number) => Math.min(1, Math.max(0, v))
export const round3 = (v: number) => Math.round(v * 1000) / 1000

function normFocal(v: unknown): Focal {
  const f = (v ?? {}) as Raw
  return { x: clamp01(num(f.x, 0.5)), y: clamp01(num(f.y, 0.5)) }
}

function normItem(v: unknown): SlotItem {
  const r = (v ?? {}) as Raw
  return {
    media_id: num(r.media_id),
    src: str(r.src),
    srcset: str(r.srcset),
    width: num(r.width),
    height: num(r.height),
    alt: str(r.alt),
    focal: normFocal(r.focal),
    blur: str(r.blur),
  }
}

export function normSlot(key: string, v: unknown): Slot {
  const r = (v ?? {}) as Raw
  return {
    key,
    label: str(r.label, key),
    page: str(r.page, '/'),
    aspect: str(r.aspect, '4/3'),
    multiple: r.multiple === true,
    max: Math.max(1, num(r.max, 1)),
    hint: str(r.hint),
    items: Array.isArray(r.items) ? r.items.map(normItem) : [],
  }
}

/* ------------------------------------------------------------- reads --- */

export const SLOTS_KEY = [...WEBSITE_KEY, 'photos', 'slots'] as const

export function useSlots() {
  return useQuery({
    queryKey: SLOTS_KEY,
    // The site's public /api/slots answers `Cache-Control: public, max-age=30`,
    // which the forward passes back. A refetch right after a save must not be
    // served that stale copy, so this read skips the browser cache.
    queryFn: async () => {
      const v = await request<unknown>(sitePath('/slots'), { cache: 'no-store' })
      const map = ((v as Raw | null)?.slots ?? {}) as Raw
      return Object.entries(map).map(([k, s]) => normSlot(k, s))
    },
  })
}

/** Put a freshly uploaded or edited photo into the cached library at once. */
export function usePutMedia() {
  const qc = useQueryClient()
  return (m: Media) =>
    qc.setQueryData<Media[]>(MEDIA_KEY, (old) => {
      if (!old) return [m]
      return old.some((x) => x.id === m.id) ? old.map((x) => (x.id === m.id ? m : x)) : [m, ...old]
    })
}

/** Replace one slot in the cache with what the server saved. */
export function usePutSlot() {
  const qc = useQueryClient()
  return (s: Slot) => qc.setQueryData<Slot[]>(SLOTS_KEY, (old) => (old ?? []).map((x) => (x.key === s.key ? s : x)))
}

/* ------------------------------------------------------------ writes --- */

export async function putSlot(key: string, items: DraftItem[]): Promise<WriteResult<Slot>> {
  const r = await siteWrite<unknown>(
    `/slots/${encodeURIComponent(key)}`,
    {
      items: items.map((i) => ({
        media_id: i.media_id,
        ...(i.alt.trim() ? { alt: i.alt.trim() } : {}),
        focal: { x: round3(i.focal.x), y: round3(i.focal.y) },
      })),
    },
    'PUT',
  )
  return r.kind === 'ok' ? { kind: 'ok', data: normSlot(key, r.data) } : r
}

export async function patchAlt(id: number, alt: string): Promise<WriteResult<Media>> {
  const r = await siteWrite<unknown>(`/media/${id}`, { alt }, 'PATCH')
  return r.kind === 'ok' ? { kind: 'ok', data: normMedia(r.data) } : r
}

export function deleteMedia(id: number, force: boolean): Promise<WriteResult<unknown>> {
  return siteWrite<unknown>(`/media/${id}${force ? '?force=1' : ''}`, undefined, 'DELETE')
}

/* ------------------------------------------------------------ drafts --- */

/** A slot as served, turned into an editable draft. The API gives `alt` as
 *  `alt_override ?? media.alt`; an override is whatever differs from the photo. */
export function draftOf(slot: Slot, media: Map<number, Media>): DraftItem[] {
  return slot.items.map((i) => {
    const m = media.get(i.media_id)
    return { media_id: i.media_id, alt: m && i.alt !== m.alt ? i.alt : '', focal: { ...i.focal } }
  })
}

export const snap = (items: DraftItem[]) =>
  JSON.stringify(items.map((i) => [i.media_id, i.alt.trim(), round3(i.focal.x), round3(i.focal.y)]))

/** Image data for a draft item: from the library, else from the slot as served. */
export function picFor(slot: Slot, media: Map<number, Media>, id: number): Pic | null {
  const m = media.get(id)
  if (m) return m
  return slot.items.find((i) => i.media_id === id) ?? null
}

/* ----------------------------------------------------------- helpers --- */

export const pct = (v: number) => `${Math.round(v * 1000) / 10}%`

export function kb(bytes: number): string {
  if (!bytes) return ''
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`
}

export function slotAnchor(key: string) {
  return `photo-slot-${key.replace(/[^a-z0-9]+/gi, '-')}`
}

const PAGE_NAMES: Record<string, string> = {
  '/': 'Home',
  '/about': 'About',
  '/menu': 'Menu',
  '/order': 'Order',
  '/visit': 'Visit',
  '/book': 'Book a table',
}
export const PAGE_ORDER = ['/', '/about', '/menu', '/order', '/visit', '/book']

export function pageName(path: string) {
  return PAGE_NAMES[path] ?? path.replace(/^\//, '').replace(/[-/]/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

/** CSS aspect string ("4/5", "16 / 9", "1.5") as width / height. */
export function ratio(aspect: string): number {
  const [w, h] = aspect.split('/').map((s) => parseFloat(s))
  if (!w) return 4 / 3
  return h ? w / h : w
}

/** "4/3" -> "4:3". */
export const aspectLabel = (a: string) => a.replace(/\s*\/\s*/, ':')

/** The narrower frame a phone is likely to show for the same slot. */
export function phoneAspect(aspect: string): string {
  const r = ratio(aspect)
  if (r > 1.05) return '4/5'
  if (r > 0.7) return '9/16'
  return '9/19'
}

export function focalWords(f: Focal) {
  const across = f.x < 0.34 ? 'left' : f.x > 0.66 ? 'right' : 'centre'
  const down = f.y < 0.34 ? 'top' : f.y > 0.66 ? 'bottom' : 'middle'
  return `${Math.round(f.x * 100)}% across, ${Math.round(f.y * 100)}% down (${down} ${across})`
}

export function nameOf(m: Pic | null | undefined, fallback: string) {
  return m?.alt || m?.original_name || fallback
}

/** A slot's element, for "go to this place" links. */
export function goToSlot(key: string) {
  const el = document.getElementById(slotAnchor(key))
  if (!el) return
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  el.focus({ preventScroll: true })
}
