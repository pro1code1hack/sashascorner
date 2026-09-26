// Mirrors the site API contract (site/backend). Money is integer pence.
export interface Size {
  code: 'S' | 'M' | 'XL' | 'One' | string;
  label: string;
  price_pence: number;
}
export interface MenuItem {
  id: string;
  name: string;
  description: string | null;
  note: string | null;
  signature: boolean;
  seasonal: { name: string } | null;
  sizes: Size[];
}
export interface MenuCategory {
  slug: string;
  name: string;
  blurb: string;
  items: MenuItem[];
}
export interface Menu {
  generated_at: string;
  categories: MenuCategory[];
  extras?: { title: string; items: Array<{ name: string; price_text: string }> };
}
export type Hours =
  | { weekday: number; open: string; close: string; closed?: false }
  | { weekday: number; closed: true };
export interface Info {
  name: string;
  address: { line1: string; city: string; postcode: string; country: string };
  geo: { lat: number; lng: number };
  phone: string;
  email: string;
  hours: Hours[];
  socials: { instagram: string; facebook: string; tiktok: string };
  booking: { max_party: number; slot_minutes: number; horizon_days: number; min_lead_minutes: number };
  confirmed: boolean;
}
export interface Slot {
  time: string;
  available: boolean;
  remaining_covers: number;
}
export interface Availability {
  date: string;
  party: number;
  closed: boolean;
  reason: string | null;
  slots: Slot[];
}
export interface Booking {
  reference: string;
  manage_token: string;
  status: 'confirmed' | 'cancelled';
  date: string;
  time: string;
  party: number;
  name: string;
  created_at?: string;
}
