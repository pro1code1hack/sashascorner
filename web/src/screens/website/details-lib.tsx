/**
 * Helpers for Website › Café details: the settings query, the PUT with per-field
 * 422 errors, and a per-section draft that a background refetch cannot wipe.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, LIVE, request } from '../../lib/api'
import { WEBSITE_KEY, siteGet, sitePath } from '../../lib/website-api'
import type { BookingRules, CafeSettings, Closure, SiteSettings } from '../../lib/types/website'

export const SETTINGS_KEY = [...WEBSITE_KEY, 'settings'] as const

export function useSiteSettings() {
  return useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: () => siteGet<SiteSettings>('/settings'),
    enabled: LIVE,
  })
}

/** `PUT /api/admin/settings` takes any subset; `cafe` and `booking` merge field by field. */
export interface SettingsPut {
  cafe?: Partial<Omit<CafeSettings, 'email'>> & { email?: string }
  booking?: Partial<BookingRules>
  closures?: Closure[]
}

export type PutResult =
  | { kind: 'ok'; data: SiteSettings }
  /** A 422: messages keyed by dotted path without `body` (`cafe.socials.instagram`). */
  | { kind: 'invalid'; fields: Record<string, string> }
  | { kind: 'failed'; message: string }

/**
 * FastAPI 422 detail -> { "cafe.name": "String should have at least 1 character" }.
 * Same rules as the old site admin (site/web/src/scripts/admin-core/api.ts `fieldErrors`).
 */
export function fieldErrors(detail: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  if (!Array.isArray(detail)) return out
  for (const d of detail as { loc?: unknown[]; msg?: unknown }[]) {
    const loc = (d.loc ?? []).filter((p) => p !== 'body' && p !== 'query').map(String)
    const key = loc.join('.') || '_'
    const msg = String(d.msg ?? 'Not valid').replace(/^Value error, /, '')
    if (!out[key]) out[key] = msg.charAt(0).toUpperCase() + msg.slice(1)
  }
  return out
}

export async function putSettings(body: SettingsPut): Promise<PutResult> {
  if (!LIVE) return { kind: 'failed', message: 'The website screens need the live back office (VITE_LIVE=1).' }
  try {
    const data = await request<SiteSettings>(sitePath('/settings'), { method: 'PUT', body: JSON.stringify(body) })
    return { kind: 'ok', data }
  } catch (e) {
    if (e instanceof ApiError) {
      const detail = (e.payload as { detail?: unknown } | null)?.detail
      if (e.status === 422 && Array.isArray(detail)) return { kind: 'invalid', fields: fieldErrors(detail) }
      if (typeof detail === 'string') return { kind: 'failed', message: detail }
      if (e.status === 0 || e.status >= 500) return { kind: 'failed', message: 'Something went wrong on the website’s server. Try again in a moment.' }
      return { kind: 'failed', message: `That didn’t save (error ${e.status}).` }
    }
    return { kind: 'failed', message: 'Couldn’t reach the server. Check the connection and try again.' }
  }
}

/**
 * The form's copy of one section. It follows the saved value until the owner
 * types; after that a refetch (another section saving, window focus) leaves it
 * alone. `accept` takes a newly saved value as the new starting point.
 */
export function useDraft<T>(source: T) {
  const key = JSON.stringify(source)
  const [base, setBase] = useState(key)
  const [draft, setDraft] = useState<T>(source)
  const dirty = JSON.stringify(draft) !== base
  if (key !== base && !dirty) {
    setBase(key)
    setDraft(source)
  }
  const accept = (next: T) => {
    setBase(JSON.stringify(next))
    setDraft(next)
  }
  return { draft, setDraft, dirty, accept }
}

/** Today in the café's time zone, YYYY-MM-DD. */
export function todayISO(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London' }).format(new Date())
}

/** "Friday 25 December", with the year when it isn't this year. */
export function longDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`)
  if (Number.isNaN(d.getTime())) return iso
  const sameYear = iso.slice(0, 4) === todayISO().slice(0, 4)
  return new Intl.DateTimeFormat('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    ...(sameYear ? {} : { year: 'numeric' }),
    timeZone: 'UTC',
  }).format(d)
}
