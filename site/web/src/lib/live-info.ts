// Runtime café info. The pages bake info.json at build time; hours and closures
// are edited in the back office, so the browser asks /api/info once per page view
// and the "open now" line and hours tables follow the live settings without a
// rebuild. One request however many components ask; null when the API is down,
// in which case the baked values stand.
import type { Info } from './types';

let pending: Promise<Info | null> | null = null;

export function liveInfo(): Promise<Info | null> {
  pending ??= fetch('/api/info', { cache: 'no-store' })
    .then((r) => (r.ok ? (r.json() as Promise<Info>) : null))
    .catch(() => null);
  return pending;
}

/** Today's date in the café's own timezone, as YYYY-MM-DD. */
export function londonToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London' }).format(now);
}

type AnyHours = { weekday: number; open?: string; close?: string; closed?: boolean };

/** Runs of consecutive days with the same times, the way the hours tables fold them. */
export function foldHours(
  hours: AnyHours[],
  text: (h: AnyHours) => string,
): Array<{ days: number[]; text: string }> {
  const rows: Array<{ days: number[]; text: string }> = [];
  for (const h of [...hours].sort((a, b) => a.weekday - b.weekday)) {
    const t = text(h);
    const last = rows.at(-1);
    if (last && last.text === t && last.days.at(-1) === h.weekday - 1) last.days.push(h.weekday);
    else rows.push({ days: [h.weekday], text: t });
  }
  return rows;
}
