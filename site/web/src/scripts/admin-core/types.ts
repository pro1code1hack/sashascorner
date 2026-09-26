// Wire shapes from site/ADMIN.md. Kept literal: the pages read these fields directly.

export type BookingStatus = 'confirmed' | 'cancelled' | 'arrived' | 'no_show';
export type MessageStatus = 'new' | 'handled' | 'archived';

export interface Booking {
  id: number;
  reference: string;
  name: string;
  email: string | null;
  phone: string | null;
  party: number;
  date: string; // YYYY-MM-DD
  time: string; // HH:MM
  status: BookingStatus;
  notes: string | null;
  source: 'web' | 'admin';
  created_at: string;
  cancelled_at: string | null;
}

export interface SlotLoad {
  time: string;
  covers_booked: number;
  capacity: number;
}

export interface BookingDay {
  date: string;
  closed: boolean;
  /** The closure note, or why the day is closed; null when open. */
  reason?: string | null;
  capacity: number;
  slots: SlotLoad[];
  bookings: Booking[];
}

export interface NewBooking {
  name: string;
  phone?: string;
  email?: string;
  party: number;
  date: string;
  time: string;
  notes?: string;
  override_capacity?: boolean;
}

export interface BookingPatch {
  status?: BookingStatus;
  notes?: string;
  party?: number;
  date?: string;
  time?: string;
  override_capacity?: boolean;
}

export interface Message {
  id: number;
  name: string;
  email: string;
  topic: string;
  message: string;
  created_at: string;
  status: MessageStatus;
  handled_at: string | null;
}

export interface Hours {
  weekday: number; // 0 = Monday
  open?: string | null;
  close?: string | null;
  closed?: boolean;
}

export interface CafeSettings {
  name: string;
  phone: string;
  email: string;
  address: { line1: string; city: string; postcode: string; country: string; [k: string]: string };
  geo: { lat: number; lng: number };
  hours: Hours[];
  socials: Record<string, string>;
}

export interface BookingRules {
  covers_per_slot: number;
  max_party: number;
  slot_minutes: number;
  duration_minutes: number;
  last_seating_before_close_minutes: number;
  min_lead_minutes: number;
  horizon_days: number;
}

export interface Closure {
  date: string;
  note: string;
}

export interface Settings {
  cafe: CafeSettings;
  booking: BookingRules;
  closures: Closure[];
  telegram_configured: boolean;
}

export interface Summary {
  today: { date: string; closed: boolean; bookings: number; covers: number; capacity: number; next: Booking[] };
  week: { date: string; bookings: number; covers: number }[];
  messages_new: number;
  photos_missing: number;
  /** The contract says `warnings`; the backend sends a count. Both are handled. */
  menu: { source: 'ops' | 'board'; warnings: number | string[] };
}

export interface Me {
  authenticated: boolean;
  password_source: 'db' | 'env';
}
