/**
 * Helpers for Website › Café details: the PUT with per-field 422 errors, and a
 * per-section draft that a background refetch cannot wipe. The settings query
 * itself is `useSiteSettings` in lib/website-api.ts, shared with Bookings and Today.
 */
import { useState } from 'react'
import { ApiError, LIVE, request } from '../../lib/api'
import { sitePath } from '../../lib/website-api'
import type { BookingRules, CafeSettings, Closure, SiteSettings } from '../../lib/types/website'

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
 * What a Pydantic validation `type` means, in the owner's words. `ctx` carries the
 * bound for the limit types. A `value_error` is a sentence the site wrote itself
 * (its hours and rules checks), so that one is shown as written; anything not
 * listed falls back to "This isn't valid."
 */
function plainError(type: string, msg: string, ctx: Record<string, unknown>): string {
  const n = (k: string) => (typeof ctx[k] === 'number' ? String(ctx[k]) : null)
  switch (type) {
    case 'missing':
      return 'This is needed.'
    case 'string_too_short':
    case 'too_short': {
      const min = n('min_length')
      return min === '1' ? 'Can’t be empty.' : min ? `At least ${min} characters.` : 'Too short.'
    }
    case 'string_too_long':
    case 'too_long': {
      const max = n('max_length')
      return max ? `At most ${max} characters.` : 'Too long.'
    }
    case 'string_type':
      return 'Must be text.'
    case 'int_parsing':
    case 'int_type':
    case 'int_from_float':
      return 'Must be a whole number.'
    case 'float_parsing':
    case 'float_type':
    case 'decimal_parsing':
      return 'Must be a number.'
    case 'greater_than_equal':
      return n('ge') ? `Must be ${n('ge')} or more.` : 'Too small.'
    case 'greater_than':
      return n('gt') ? `Must be more than ${n('gt')}.` : 'Too small.'
    case 'less_than_equal':
      return n('le') ? `Must be ${n('le')} or less.` : 'Too big.'
    case 'less_than':
      return n('lt') ? `Must be less than ${n('lt')}.` : 'Too big.'
    case 'string_pattern_mismatch':
      return 'Not in the right form.'
    case 'time_parsing':
    case 'time_type':
      return 'Not a time. Use HH:MM.'
    case 'date_parsing':
    case 'date_type':
    case 'date_from_datetime_parsing':
      return 'Not a date.'
    case 'url_parsing':
    case 'url_type':
    case 'url_scheme':
      return 'Not a web address.'
    case 'bool_parsing':
    case 'bool_type':
      return 'Must be yes or no.'
    case 'value_error': {
      const m = msg.replace(/^Value error, /, '')
      if (/valid email address/i.test(m)) return 'Not an email address.'
      return m ? m.charAt(0).toUpperCase() + m.slice(1) : 'This isn’t valid.'
    }
    default:
      return 'This isn’t valid.'
  }
}

/**
 * FastAPI 422 detail -> { "cafe.name": "Can’t be empty." }. Keys are the dotted
 * path without `body`; the first error per field wins.
 */
export function fieldErrors(detail: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  if (!Array.isArray(detail)) return out
  for (const d of detail as { loc?: unknown[]; msg?: unknown; type?: unknown; ctx?: unknown }[]) {
    const loc = (d.loc ?? []).filter((p) => p !== 'body' && p !== 'query').map(String)
    const key = loc.join('.') || '_'
    const ctx = d.ctx && typeof d.ctx === 'object' ? (d.ctx as Record<string, unknown>) : {}
    if (!out[key]) out[key] = plainError(String(d.type ?? ''), String(d.msg ?? ''), ctx)
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
