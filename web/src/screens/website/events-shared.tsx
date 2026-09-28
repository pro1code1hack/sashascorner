/**
 * Website › Events: the shapes, reads and words the list and the event page share.
 *
 * Types mirror site/backend/sashasite/events_api.py (AdminEventOut, AdminRsvpOut,
 * AdminEventIn / AdminEventPatchIn, RsvpPatchIn, DeletedOut). Dates and times
 * arrive already in café-local form (Europe/London): `date` "2026-10-03",
 * `start` / `end` "18:30". They are shown as given, never re-zoned.
 */
import { useQuery } from "@tanstack/react-query";
import { Pill } from "../../components/ui";
import { gbp, plural } from "../../lib/format";
import { WEBSITE_KEY, siteGet } from "../../lib/website-api";

/* ---------------------------------------------------------------- types --- */

export interface EventImage {
  media_id: number;
  src: string;
  srcset: string;
  width: number;
  height: number;
  alt: string;
  blur: string;
}

export interface AdminEvent {
  id: number;
  slug: string;
  title: string;
  /** Café-local date, YYYY-MM-DD. */
  date: string;
  /** Café-local wall clock, HH:MM. */
  start: string;
  end: string | null;
  starts_at: string;
  ends_at: string | null;
  description: string;
  price_pence: number | null;
  capacity: number | null;
  image_media_id: number | null;
  image: EventImage | null;
  published: boolean;
  /** Started (a started event takes no more replies). */
  past: boolean;
  /** People in live (not cancelled) replies. */
  seats_taken: number;
  /** Live replies. */
  rsvps: number;
  created_at: string;
  updated_at: string;
}

export interface AdminRsvp {
  id: number;
  name: string;
  email: string | null;
  phone: string | null;
  party: number;
  note: string | null;
  created_at: string;
  cancelled_at: string | null;
}

/** POST /events body. PATCH takes any subset (AdminEventPatchIn). */
export interface AdminEventIn {
  slug?: string | null;
  title: string;
  date: string;
  start: string;
  end: string | null;
  description: string;
  price_pence: number | null;
  capacity: number | null;
  image_media_id: number | null;
  published: boolean;
}

export interface DeletedOut {
  id: number;
  rsvps_deleted: number;
}

/* ---------------------------------------------------------------- reads --- */

export const EVENTS_KEY = [...WEBSITE_KEY, "events"] as const;

export function useEvents() {
  return useQuery({
    queryKey: EVENTS_KEY,
    queryFn: () => siteGet<AdminEvent[]>("/events"),
  });
}

export function useRsvps(eventId: number) {
  return useQuery({
    queryKey: [...EVENTS_KEY, eventId, "rsvps"],
    queryFn: () => siteGet<AdminRsvp[]>(`/events/${eventId}/rsvps`),
  });
}

/* ---------------------------------------------------------------- words --- */

const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WD_LONG = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];
const MON = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];
const MON_LONG = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

function parts(iso: string): [number, number, number] {
  const [y = 0, m = 1, d = 1] = iso.split("-").map(Number);
  return [y, m, d];
}

/** Today's date in Dundee, YYYY-MM-DD. */
export function todayLondon(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/London",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

/** "2026-10-03" -> "Sat 3 Oct". Calendar arithmetic in UTC, so no zone can shift the day. */
export function shortDate(iso: string): string {
  const [y, m, d] = parts(iso);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return `${WD[wd]} ${d} ${MON[m - 1]}`;
}

/** "Saturday 3 October" (+ the year when it is not this year). */
export function longDate(iso: string): string {
  const [y, m, d] = parts(iso);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  const thisYear = Number(todayLondon().slice(0, 4));
  return `${WD_LONG[wd]} ${d} ${MON_LONG[m - 1]}${y !== thisYear ? ` ${y}` : ""}`;
}

/** "18:30" -> "6.30pm", "19:00" -> "7pm". */
export function clock(hhmm: string): string {
  const [h = 0, m = 0] = hhmm.split(":").map(Number);
  const suffix = h >= 12 ? "pm" : "am";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m === 0
    ? `${h12}${suffix}`
    : `${h12}.${String(m).padStart(2, "0")}${suffix}`;
}

export function timeRange(e: Pick<AdminEvent, "start" | "end">): string {
  return e.end ? `${clock(e.start)}–${clock(e.end)}` : clock(e.start);
}

export function isFull(e: AdminEvent): boolean {
  return !e.past && e.capacity !== null && e.seats_taken >= e.capacity;
}

export function seatsText(e: AdminEvent): string {
  if (e.capacity !== null)
    return `${e.seats_taken} of ${e.capacity} places taken`;
  return e.seats_taken
    ? `${e.seats_taken} ${plural(e.seats_taken, "person", "people")} coming`
    : "No replies yet";
}

export function priceText(p: number | null): string | null {
  return p === null ? null : gbp(p);
}

/** Pence as the price box shows it: 1250 -> "12.50". */
export function penceToInput(p: number | null): string {
  if (p === null) return "";
  return `${Math.floor(p / 100)}.${String(p % 100).padStart(2, "0")}`;
}

/** The price box back to pence, without floats. Empty is "no price"; `bad` is not a price. */
export function parsePence(s: string): number | null | "bad" {
  const t = s.trim().replace(/^£/, "");
  if (!t) return null;
  const m = /^(\d{1,4})(?:\.(\d{1,2}))?$/.exec(t);
  if (!m) return "bad";
  return Number(m[1]) * 100 + Number((m[2] ?? "0").padEnd(2, "0"));
}

/** Status in words: every state has one, not only a colour. Full is the one crossed threshold. */
export function EventStatus({ e }: { e: AdminEvent }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {e.past ? (
        <Pill tone="muted">Past</Pill>
      ) : e.published ? (
        <Pill tone="neutral">On the website</Pill>
      ) : (
        <Pill tone="brand">Draft, not on the website</Pill>
      )}
      {isFull(e) && <Pill tone="warn">Full</Pill>}
    </span>
  );
}
