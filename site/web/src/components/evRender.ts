// One renderer for /events, used twice: at build time (from src/data/events.json,
// so crawlers see the events) and in the browser (from /api/events, so a new event
// shows without a rebuild). It returns HTML strings; every value is escaped.
// Styles live in events.astro under `.evp` (global, because this markup is created
// outside Astro's scoping).
import { clock, gbp } from '../lib/format';

export interface EventImage {
  media_id: number;
  src: string;
  srcset: string;
  width: number;
  height: number;
  alt: string;
  blur: string;
}
export interface CafeEvent {
  slug: string;
  title: string;
  starts_at: string;
  ends_at: string | null;
  date: string;
  start: string;
  end: string | null;
  description: string;
  price_pence: number | null;
  capacity: number | null;
  places_left: number | null;
  full: boolean;
  image: EventImage | null;
}
export interface EventsData {
  generated_at: string;
  max_party: number;
  events: CafeEvent[];
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

export const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

/** "2026-10-10" -> parts, read as a calendar date (no time zone involved). */
export function dateParts(iso: string) {
  const [y, m, d] = iso.split('-').map(Number);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return { day: d, month: MONTHS[m - 1], mon: MONTHS[m - 1].slice(0, 3), weekday: DAYS[wd], year: y };
}
/** "Saturday 10 October" */
export const longDate = (iso: string) => {
  const p = dateParts(iso);
  return `${p.weekday} ${p.day} ${p.month}`;
};
export const timeRange = (e: Pick<CafeEvent, 'start' | 'end'>) => (e.end ? `${clock(e.start)} – ${clock(e.end)}` : `from ${clock(e.start)}`);

/** Sets the RSVP form's party options. */
export const partyMax = (e: CafeEvent, max: number) => Math.max(1, Math.min(max, e.places_left ?? max));

function paragraphs(text: string): string {
  return text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => `<p>${esc(p).replace(/\n/g, '<br />')}</p>`)
    .join('');
}

function form(e: CafeEvent, max: number): string {
  const id = `ev-${e.slug}`;
  const options = Array.from({ length: partyMax(e, max) }, (_, n) => `<option value="${n + 1}">${n + 1}</option>`).join('');
  return `
<details class="ev__rsvp">
  <summary class="btn btn--caramel">Reply to come <span class="arrow" aria-hidden="true">→</span></summary>
  <form class="ev__form" data-rsvp="${esc(e.slug)}" novalidate>
    <label class="field">
      <span>Your name</span>
      <input class="input" name="name" autocomplete="name" required maxlength="80" aria-describedby="${id}-err-name" />
      <small class="ev__ferr" id="${id}-err-name" data-err="name"></small>
    </label>
    <label class="field">
      <span>Email or phone</span>
      <input class="input" name="contact" autocomplete="email" required maxlength="254" aria-describedby="${id}-hint ${id}-err-contact" />
      <small class="ev__hint" id="${id}-hint">So we can reach you if anything changes.</small>
      <small class="ev__ferr" id="${id}-err-contact" data-err="contact"></small>
    </label>
    <label class="field ev__party">
      <span>How many of you?</span>
      <select class="input" name="party" aria-describedby="${id}-err-party">${options}</select>
      <small class="ev__ferr" id="${id}-err-party" data-err="party"></small>
    </label>
    <label class="field ev__wide">
      <span>Anything we should know? <span class="ev__opt">(optional)</span></span>
      <textarea class="input" name="note" rows="3" maxlength="500"></textarea>
    </label>
    <label class="hp" aria-hidden="true">Website <input name="website" tabindex="-1" autocomplete="off" /></label>
    <p class="ev__err ev__wide" data-error role="alert" hidden></p>
    <div class="ev__wide"><button class="btn btn--caramel" type="submit" data-submit>Send reply <span class="arrow" aria-hidden="true">→</span></button></div>
  </form>
  <div class="ev__done" data-done hidden tabindex="-1" role="status"></div>
</details>`;
}

export function renderEvent(e: CafeEvent, max: number): string {
  const p = dateParts(e.date);
  const meta = [
    `<span>${esc(timeRange(e))}</span>`,
    e.price_pence !== null ? `<span class="num">${esc(gbp(e.price_pence))}</span>` : '',
    e.full
      ? '<span class="ev__full">Full</span>'
      : e.places_left !== null
        ? `<span><span class="num" data-left>${e.places_left}</span> ${e.places_left === 1 ? 'place' : 'places'} left</span>`
        : '',
  ]
    .filter(Boolean)
    .join('<span aria-hidden="true" class="ev__dot">·</span>');
  const img = e.image
    ? `<img class="ev__img" src="${esc(e.image.src)}" srcset="${esc(e.image.srcset)}" sizes="(max-width: 760px) 100vw, 420px" width="${e.image.width}" height="${e.image.height}" alt="${esc(e.image.alt || e.title)}" loading="lazy" decoding="async" style="background-image:url('${esc(e.image.blur)}')" />`
    : '';
  const reply = e.full
    ? `<p class="ev__fullnote">This one is full. <a href="/visit?topic=events#contact">Message us</a> and we'll tell you if a place comes free.</p>`
    : form(e, max);
  return `
<article class="ev" id="${esc(e.slug)}" data-event="${esc(e.slug)}" aria-labelledby="${esc(e.slug)}-title">
  <p class="ev__when"><time datetime="${esc(e.starts_at)}"><span class="ev__day num">${p.day}</span><span class="ev__mon">${p.mon}</span><span class="ev__wd">${p.weekday}</span></time></p>
  <div class="ev__body">
    <h2 class="ev__title" id="${esc(e.slug)}-title">${esc(e.title)}</h2>
    <p class="ev__meta"><span class="sr-only">${esc(longDate(e.date))}, </span>${meta}</p>
    ${img}
    <div class="ev__desc">${paragraphs(e.description)}</div>
    ${reply}
  </div>
</article>`;
}

export function renderEvents(data: EventsData, opts: { instagram: string }): string {
  if (!data.events.length) {
    const ig = opts.instagram
      ? ` We announce them on <a href="${esc(opts.instagram)}" target="_blank" rel="noopener">Instagram<span class="sr-only"> (opens in a new tab)</span></a> first.`
      : '';
    return `
<div class="ev-empty">
  <h2>Nothing on the calendar just now.</h2>
  <p>When we plan an evening at the café, it goes up here with a way to reply.${ig}</p>
  <p>Planning something of your own, for a group or a birthday? <a href="/visit?topic=events#contact">Send us a message</a>.</p>
</div>`;
  }
  return data.events.map((e) => renderEvent(e, data.max_party)).join('');
}

/** What changes the page; generated_at does not. */
export const fingerprint = (data: EventsData) => JSON.stringify([data.max_party, data.events]);
