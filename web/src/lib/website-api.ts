/**
 * The public website's admin, inside the back office (owner, 2026-09-28).
 *
 * Every call goes to `/api/website/<path>`, which the back office forwards to the
 * site API's `/api/admin/<path>` after its own sign-in (cafeops/api/areas/website.py).
 * So there is one password, and the usual `request()` / `apiWrite()` apply: a 401
 * returns to Login, and a refusal is shown as the site wrote it.
 *
 * Paths below are the site's admin paths WITHOUT `/api/admin`: `sitePath('/bookings')`.
 *
 * Photos: the site returns `src` / `srcset` under its own media prefix
 * (`/api/media-files/…` or `/media/…`), which this origin does not serve. `mediaUrl`
 * and `mediaSrcset` rewrite them to `/api/website-media/…`, the back office's open
 * forward for them.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { API_BASE, ApiError, LIVE, apiWrite, authHeaders, request, type WriteResult } from './api'
import type { Media, SiteSettings, SiteSummary, WebsiteConnection } from './types/website'

export const WEBSITE_KEY = ['website'] as const

/** `/bookings` -> `/api/website/bookings`. */
export function sitePath(path: string): string {
  return `/api/website/${path.replace(/^\/+/, '')}`
}

/** A GET against the site admin. Throws ApiError like `request()`. */
export function siteGet<T>(path: string): Promise<T> {
  if (!LIVE) {
    const msg = 'The website screens read the live site. Run the back office with VITE_LIVE=1.'
    return Promise.reject(new ApiError(503, { detail: msg }, msg))
  }
  return request<T>(sitePath(path))
}

/** A write against the site admin; never throws (see `WriteResult`). */
export function siteWrite<T>(
  path: string,
  body: unknown,
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST',
): Promise<WriteResult<T>> {
  return apiWrite<T>(sitePath(path), body, method)
}

/**
 * A multipart upload (photos). `request()` sets a JSON content type, which would
 * lose the multipart boundary, so this one builds its own fetch with the same auth.
 * `onProgress` gets 0..1 when the browser reports it.
 */
export function siteUpload<T>(
  path: string,
  form: FormData,
  onProgress?: (fraction: number) => void,
): Promise<WriteResult<T>> {
  if (!LIVE) {
    return Promise.resolve({ kind: 'offline', message: 'Uploads need the live site (VITE_LIVE=1).' })
  }
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}${sitePath(path)}`)
    for (const [k, v] of Object.entries(authHeaders())) xhr.setRequestHeader(k, v)
    xhr.setRequestHeader('Accept', 'application/json')
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total)
      }
    }
    xhr.onload = () => {
      let payload: unknown = null
      try {
        payload = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        payload = xhr.responseText
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ kind: 'ok', data: payload as T })
        return
      }
      const detail = (payload as { detail?: unknown } | null)?.detail
      const message =
        typeof detail === 'string'
          ? detail
          : detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string'
            ? (detail as { message: string }).message
            : `The upload failed (${xhr.status}).`
      resolve(xhr.status === 413 || xhr.status === 422 || xhr.status === 409 ? { kind: 'refused', message, status: xhr.status } : { kind: 'failed', status: xhr.status, message })
    }
    xhr.onerror = () => resolve({ kind: 'failed', status: null, message: 'Could not reach the server.' })
    xhr.send(form)
  })
}

const MEDIA_PREFIX = /^(?:https?:\/\/[^/]+)?\/(?:api\/media-files|media)\//

/** A site photo URL, rewritten to this origin's open forward. */
export function mediaUrl(src: string): string {
  return src.replace(MEDIA_PREFIX, `${API_BASE}/api/website-media/`)
}

/** Every URL in a `srcset`, rewritten like `mediaUrl`. */
export function mediaSrcset(srcset: string): string {
  return srcset
    .split(',')
    .map((part) => {
      const [url = '', ...rest] = part.trim().split(/\s+/)
      return [mediaUrl(url), ...rest].join(' ')
    })
    .join(', ')
}

/** Is the site wired up (key set, API answering)? Cheap; answered by the back office. */
export function useWebsiteConnection() {
  return useQuery({
    queryKey: [...WEBSITE_KEY, 'connection'],
    queryFn: () => request<WebsiteConnection>('/api/website/connection'),
    enabled: LIVE,
    staleTime: 60 * 1000,
  })
}

/** A page on the public site: `livePageUrl(conn, '/menu')`. Null until the connection is known. */
export function livePageUrl(conn: WebsiteConnection | undefined, path: string): string | null {
  if (!conn) return null
  return `${conn.public_url}/${path.replace(/^\/+/, '')}`
}

/** Today, the week, and the counts the sidebar badges show. */
export function useWebsiteSummary() {
  return useQuery({
    queryKey: [...WEBSITE_KEY, 'summary'],
    queryFn: () => siteGet<SiteSummary>('/summary'),
    enabled: LIVE,
    staleTime: 60 * 1000,
    // The website may simply not be running; that is not worth a retry storm.
    retry: false,
  })
}

/** Café details, hours, booking rules and closures. One query, one key, one staleTime, for every screen that reads it. */
export const SETTINGS_KEY = [...WEBSITE_KEY, 'settings'] as const

export function useSiteSettings() {
  return useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: () => siteGet<SiteSettings>('/settings'),
    enabled: LIVE,
    staleTime: 5 * 60 * 1000,
  })
}

/* ------------------------------------------------------------- photos --- */

type Raw = Record<string, unknown>
const str = (v: unknown, d = '') => (typeof v === 'string' ? v : d)
const num = (v: unknown, d = 0) => (typeof v === 'number' && Number.isFinite(v) ? v : d)

/** `MediaOut.usage` arrives as `[{slot_key, position, label}]` (or bare keys); de-duplicated by slot. */
function normUsage(v: unknown): Media['usage'] {
  if (!Array.isArray(v)) return []
  const seen = new Map<string, { slot_key: string; label: string | null }>()
  for (const u of v) {
    const key = typeof u === 'string' ? u : str((u as Raw)?.slot_key ?? (u as Raw)?.key)
    if (!key || seen.has(key)) continue
    const label = typeof u === 'object' && u !== null ? (u as Raw).label : null
    seen.set(key, { slot_key: key, label: typeof label === 'string' ? label : null })
  }
  return [...seen.values()]
}

/** A library photo as the site sends it, with every field given a safe default. */
export function normMedia(v: unknown): Media {
  const r = (v ?? {}) as Raw
  return {
    id: num(r.id),
    src: str(r.src),
    srcset: str(r.srcset),
    width: num(r.width),
    height: num(r.height),
    alt: str(r.alt),
    original_name: str(r.original_name),
    bytes: num(r.bytes),
    blur: str(r.blur),
    usage: normUsage(r.usage),
  }
}

/** The photo library, under the one key Photos and Events share, so an upload reaches both. */
export const MEDIA_KEY = [...WEBSITE_KEY, 'photos', 'media'] as const

export function useMedia() {
  return useQuery({
    queryKey: MEDIA_KEY,
    queryFn: async () => {
      const v = await siteGet<unknown>('/media')
      const arr = Array.isArray(v) ? v : ((v as Raw | null)?.items ?? [])
      return Array.isArray(arr) ? arr.map(normMedia) : []
    },
    enabled: LIVE,
  })
}

/** Refetch everything under Website after a write (the badges included). */
export function useInvalidateWebsite(): () => Promise<void> {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: WEBSITE_KEY })
}
