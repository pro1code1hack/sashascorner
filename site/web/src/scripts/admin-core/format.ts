// Dates, times and small words for the admin. Dates are YYYY-MM-DD strings in the
// café's own day (Europe/London); they are never turned into UTC instants.

const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const WD_LONG = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MON_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

export const WEEKDAYS_LONG = WD_LONG;

/** Today in Dundee, as YYYY-MM-DD. */
export function todayISO(): string {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')}`;
}

/** Minutes since midnight now, in Dundee. */
export function nowMinutes(): number {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date());
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return get('hour') * 60 + get('minute');
}

function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.split('-').map(Number);
  return [y, m, d];
}

/** Monday = 0, like the API. */
export function weekday(iso: string): number {
  const [y, m, d] = parts(iso);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}

export function addDays(iso: string, n: number): string {
  const [y, m, d] = parts(iso);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return t.toISOString().slice(0, 10);
}

export function daysBetween(a: string, b: string): number {
  const [y1, m1, d1] = parts(a);
  const [y2, m2, d2] = parts(b);
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86400000);
}

/** Monday of the week containing `iso`. */
export function weekStart(iso: string): string {
  return addDays(iso, -weekday(iso));
}

/** "Sat 26 Sep" */
export function shortDate(iso: string): string {
  const [, m, d] = parts(iso);
  return `${WD[weekday(iso)]} ${d} ${MON[m - 1]}`;
}

/** "Saturday 26 September" (+ year if not this year) */
export function longDate(iso: string): string {
  const [y, m, d] = parts(iso);
  const thisYear = Number(todayISO().slice(0, 4));
  return `${WD_LONG[weekday(iso)]} ${d} ${MON_LONG[m - 1]}${y !== thisYear ? ` ${y}` : ''}`;
}

/** "Today", "Tomorrow", "Yesterday", else shortDate. */
export function relDay(iso: string): string {
  const diff = daysBetween(todayISO(), iso);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  return shortDate(iso);
}

export const weekdayShort = (iso: string) => WD[weekday(iso)];

/** "18:30" -> "6.30pm" (the café's own style). */
export function clock(hhmm: string): string {
  if (!hhmm) return '';
  const [h, m] = hhmm.split(':').map(Number);
  const suffix = h >= 12 ? 'pm' : 'am';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m === 0 ? `${h12}${suffix}` : `${h12}.${String(m).padStart(2, '0')}${suffix}`;
}

export function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

export function fromMinutes(min: number): string {
  return `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
}

/** An ISO timestamp -> "2 hours ago" / "Yesterday 14:05" / "12 Sep". */
export function ago(ts: string): string {
  const t = new Date(ts).getTime();
  if (!Number.isFinite(t)) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) {
    const hrs = Math.floor(s / 3600);
    return `${hrs} hour${hrs === 1 ? '' : 's'} ago`;
  }
  const d = new Date(t);
  const local = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(d);
  return local;
}

/** Exact local timestamp for a title attribute. */
export function stamp(ts: string): string {
  const d = new Date(ts);
  if (!Number.isFinite(d.getTime())) return '';
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', dateStyle: 'full', timeStyle: 'short' }).format(d);
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
