/**
 * Wire shapes for the public website's admin (site/ADMIN.md), reached through the
 * back office's `/api/website/*` forward (cafeops/api/areas/website.py).
 *
 * Kept literal: the site API owns these and the screens read the fields directly.
 * Screen-specific shapes (events, the website menu) live next to their screens.
 */

export type BookingStatus = 'confirmed' | 'cancelled' | 'arrived' | 'no_show'
export type MessageStatus = 'new' | 'handled' | 'archived'

export interface Booking {
  id: number
  reference: string
  name: string
  email: string | null
  phone: string | null
  party: number
  /** YYYY-MM-DD, the café's local date. */
  date: string
  /** HH:MM, local. */
  time: string
  status: BookingStatus
  notes: string | null
  source: 'web' | 'admin'
  created_at: string
  cancelled_at: string | null
}

export interface SlotLoad {
  time: string
  covers_booked: number
  capacity: number
}

export interface BookingDay {
  date: string
  closed: boolean
  /** The closure note, or why the day is closed; null when open. */
  reason?: string | null
  capacity: number
  slots: SlotLoad[]
  bookings: Booking[]
}

export interface NewBooking {
  name: string
  phone?: string
  email?: string
  party: number
  date: string
  time: string
  notes?: string
  override_capacity?: boolean
}

export interface BookingPatch {
  status?: BookingStatus
  notes?: string
  party?: number
  date?: string
  time?: string
  override_capacity?: boolean
}

export interface Message {
  id: number
  name: string
  email: string
  topic: string
  message: string
  created_at: string
  status: MessageStatus
  handled_at: string | null
}

export interface Hours {
  /** 0 = Monday. */
  weekday: number
  open?: string | null
  close?: string | null
  closed?: boolean
}

export interface CafeSettings {
  name: string
  phone: string
  email: string
  address: { line1: string; city: string; postcode: string; country: string; [k: string]: string }
  geo: { lat: number; lng: number }
  hours: Hours[]
  socials: Record<string, string>
}

export interface BookingRules {
  covers_per_slot: number
  max_party: number
  slot_minutes: number
  duration_minutes: number
  last_seating_before_close_minutes: number
  min_lead_minutes: number
  horizon_days: number
}

export interface Closure {
  date: string
  note: string
}

export interface SiteSettings {
  cafe: CafeSettings
  booking: BookingRules
  closures: Closure[]
  telegram_configured: boolean
}

export interface SiteSummary {
  today: { date: string; closed: boolean; bookings: number; covers: number; capacity: number; next: Booking[] }
  week: { date: string; bookings: number; covers: number }[]
  messages_new: number
  photos_missing: number
  /** ADMIN.md says `warnings`; the backend sends a count. Both are handled. */
  menu: { source: 'ops' | 'board'; warnings: number | string[] }
}

/** `GET /api/website/connection`, answered by the back office itself. */
export interface WebsiteConnection {
  /** SITE_SERVICE_KEY is set. */
  configured: boolean
  /** The site API answered its health check. */
  reachable: boolean
  /** The public site's origin, no trailing slash. */
  public_url: string
}

/* ------------------------------------------------------------ photos --- */
/* The Photos screen owns the slot shapes (screens/website/photos/model.ts); the
 * library's photo is here because Events picks from it too. */

/** A photo in the library (`GET /api/website/media`). */
export interface Media {
  id: number
  src: string
  srcset: string
  width: number
  height: number
  alt: string
  original_name: string
  bytes: number
  blur: string
  /** The places this photo is in on the site. */
  usage: { slot_key: string; label: string | null }[]
}
