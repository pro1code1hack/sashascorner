/**
 * Dates and times for the Website group, decided once.
 *
 * The site sends the café's own day (YYYY-MM-DD in Europe/London) and wall-clock
 * times (HH:MM). Nothing here turns either into a UTC instant: calendar
 * arithmetic runs on Date.UTC so no zone can shift the day, and "today" is read
 * from the London clock. Weekdays are Monday-first (0 = Monday), like the site API.
 */

export const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
export const WD_LONG = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const MON_LONG = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

export const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

const LONDON = 'Europe/London'

/** Today in Dundee, as YYYY-MM-DD. */
export function todayISO(): string {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: LONDON,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}`
}

/** Minutes since midnight now, in Dundee. */
export function nowMinutes(): number {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: LONDON,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date())
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0)
  return get('hour') * 60 + get('minute')
}

/** The time now in Dundee, "18:30". */
export function nowHHMM(): string {
  const m = nowMinutes()
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

export function ymd(iso: string): [number, number, number] {
  const [y = 1970, m = 1, d = 1] = iso.split('-').map(Number)
  return [y, m, d]
}

/** Monday = 0, like the site API. */
export function weekday(iso: string): number {
  const [y, m, d] = ymd(iso)
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7
}

export function addDays(iso: string, n: number): string {
  const [y, m, d] = ymd(iso)
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10)
}

export function daysBetween(a: string, b: string): number {
  const [y1, m1, d1] = ymd(a)
  const [y2, m2, d2] = ymd(b)
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86400000)
}

/** The Monday of the week `iso` is in. */
export const weekStart = (iso: string) => addDays(iso, -weekday(iso))

/** "Sat 26 Sep" */
export function shortDate(iso: string): string {
  const [, m, d] = ymd(iso)
  return `${WD[weekday(iso)]} ${d} ${MON[m - 1]}`
}

/** "Saturday 26 September" (+ the year when it isn't this one). */
export function longDate(iso: string): string {
  if (!ISO_DATE.test(iso)) return iso
  const [y, m, d] = ymd(iso)
  const thisYear = Number(todayISO().slice(0, 4))
  return `${WD_LONG[weekday(iso)]} ${d} ${MON_LONG[m - 1]}${y !== thisYear ? ` ${y}` : ''}`
}

/** "Today", "Tomorrow", "Yesterday", else "Sat 26 Sep". */
export function relDay(iso: string): string {
  const diff = daysBetween(todayISO(), iso)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Tomorrow'
  if (diff === -1) return 'Yesterday'
  return shortDate(iso)
}

/** "18:30" -> "6.30pm", "19:00" -> "7pm": the café's own style. */
export function clock(hhmm: string | null | undefined): string {
  if (!hhmm) return ''
  const [h = 0, m = 0] = hhmm.split(':').map(Number)
  const suffix = h >= 12 ? 'pm' : 'am'
  const h12 = h % 12 === 0 ? 12 : h % 12
  return m === 0 ? `${h12}${suffix}` : `${h12}.${String(m).padStart(2, '0')}${suffix}`
}

/** "18:30" -> 1110. */
export function toMinutes(hhmm: string): number {
  const [h = 0, m = 0] = hhmm.split(':').map(Number)
  return h * 60 + m
}
