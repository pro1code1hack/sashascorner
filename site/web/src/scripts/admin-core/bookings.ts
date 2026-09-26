// Booking pieces shared by the dashboard and the bookings page: the row with its
// inline Arrived / No-show buttons, the slot-load timeline, the edit panel and the
// "Add booking" panel for phone-ins.

import { api, ApiError, errorText } from './api';
import { addDays, clock, longDate, plural, relDay, todayISO, toMinutes } from './format';
import { announce, busy, clearFieldErrors, confirmTwice, h, openDrawer, showFieldErrors, toast } from './ui';
import type { Booking, BookingDay, BookingPatch, BookingRules, BookingStatus, NewBooking, Settings } from './types';

export const STATUS_LABEL: Record<BookingStatus, string> = {
  confirmed: 'Booked',
  arrived: 'Arrived',
  no_show: 'No-show',
  cancelled: 'Cancelled',
};
const STATUS_TONE: Record<BookingStatus, string> = {
  confirmed: 'adm-pill--brand',
  arrived: 'adm-pill--ok',
  no_show: 'adm-pill--warn',
  cancelled: 'adm-pill--muted',
};

export const holds = (b: Booking) => b.status === 'confirmed' || b.status === 'arrived';

export function statusPill(s: BookingStatus): HTMLElement {
  return h('span', { class: `adm-pill ${STATUS_TONE[s]}` }, STATUS_LABEL[s]);
}

// ---- rules (cached settings) ------------------------------------------------------

let rulesP: Promise<Settings> | null = null;
export function settings(refresh = false): Promise<Settings> {
  if (!rulesP || refresh) rulesP = api.get<Settings>('/api/admin/settings');
  rulesP.catch(() => (rulesP = null));
  return rulesP;
}

export function getDay(date: string): Promise<BookingDay> {
  return api.get<BookingDay>('/api/admin/bookings/day', { date });
}

export function patchBooking(id: number, body: BookingPatch): Promise<Booking> {
  return api.patch<Booking>(`/api/admin/bookings/${id}`, body);
}

/**
 * Room at `time` for `party`, from the day's slot loads. A table is held for
 * `duration_minutes`, so every slot in [time, time + duration) must fit.
 * `except` is a booking being edited: its own covers don't count against it.
 */
export function roomAt(day: BookingDay, rules: BookingRules, time: string, party: number, except?: Booking) {
  const start = toMinutes(time);
  const end = start + rules.duration_minutes;
  let peak = 0;
  for (const s of day.slots) {
    const t = toMinutes(s.time);
    if (t < start || t >= end) continue;
    let c = s.covers_booked;
    if (except && except.date === day.date && holds(except)) {
      const es = toMinutes(except.time);
      if (t >= es && t < es + rules.duration_minutes) c -= except.party;
    }
    peak = Math.max(peak, c);
  }
  const cap = day.capacity;
  return { peak, free: Math.max(0, cap - peak), fits: peak + party <= cap, capacity: cap };
}

// ---- timeline -------------------------------------------------------------------

/** One quiet bar per slot: covers booked against capacity. */
export function timeline(day: BookingDay, opts: { compact?: boolean } = {}): HTMLElement {
  if (day.closed || !day.slots.length) {
    return h('p', { class: 'adm-empty' }, 'Closed. No tables to book.');
  }
  const rows = day.slots.map((s) => {
    const pct = s.capacity > 0 ? Math.min(100, (s.covers_booked / s.capacity) * 100) : 0;
    const full = s.covers_booked >= s.capacity;
    const bar = h('span', { class: `adm-meter${full ? ' adm-meter--full' : ''}`, 'aria-hidden': 'true' }, h('span', { style: `width:${pct}%` }));
    return h(
      'li',
      { class: `adm-tl-row${s.covers_booked === 0 ? ' adm-tl-row--empty' : ''}` },
      h('span', { class: 'adm-tl-time' }, clock(s.time)),
      bar,
      h(
        'span',
        { class: `adm-tl-fig${full ? ' adm-tl-fig--full' : ''}` },
        h('span', { class: 'adm-sr' }, `${clock(s.time)}: `),
        `${s.covers_booked}`,
        h('span', { class: 'adm-muted' }, ` / ${s.capacity}`),
        h('span', { class: 'adm-sr' }, ' seats taken'),
      ),
    );
  });
  return h('ul', { class: `adm-tl${opts.compact ? ' adm-tl--compact' : ''}`, 'aria-label': 'Seats taken at each time, out of capacity' }, rows);
}

// ---- rows -------------------------------------------------------------------------

export interface RowOptions {
  onChange: (b: Booking) => void;
  showDate?: boolean;
  /** Only the name button opens the panel; the row is still clickable with a mouse. */
  onOpen: (b: Booking, opener: HTMLElement) => void;
}

export function bookingRow(b: Booking, o: RowOptions): HTMLElement {
  const openBtn = h(
    'button',
    { type: 'button', class: 'adm-bk-open', 'aria-label': `${b.name}, ${plural(b.party, 'person', 'people')} at ${clock(b.time)}${o.showDate ? `, ${relDay(b.date)}` : ''}. Open booking` },
    h('span', { class: 'adm-bk-name' }, b.name),
    h(
      'span',
      { class: 'adm-bk-meta' },
      `${plural(b.party, 'person', 'people')}`,
      b.source === 'admin' ? ' · phone' : ' · online',
      ` · ${b.reference}`,
    ),
    b.notes ? h('span', { class: 'adm-bk-notes' }, b.notes) : null,
  );
  const actions = h('div', { class: 'adm-bk-actions' });
  const setStatus = async (btn: HTMLButtonElement, status: BookingStatus, doing: string) => {
    try {
      const nb = await busy(btn, doing, () => patchBooking(b.id, { status }));
      toast(`${b.name}: ${STATUS_LABEL[status].toLowerCase()}.`, {
        action: { label: 'Undo', run: () => void patchBooking(b.id, { status: b.status }).then(o.onChange, (e) => toast(errorText(e), { tone: 'bad' })) },
      });
      o.onChange(nb);
    } catch (e) {
      toast(errorText(e), { tone: 'bad' });
    }
  };
  if (b.status === 'confirmed') {
    const arrived = h('button', { type: 'button', class: 'adm-btn adm-btn--secondary adm-bk-act' }, 'Arrived');
    const noshow = h('button', { type: 'button', class: 'adm-btn adm-btn--ghost adm-bk-act' }, 'No-show');
    arrived.setAttribute('aria-label', `Mark ${b.name} arrived`);
    noshow.setAttribute('aria-label', `Mark ${b.name} as a no-show`);
    arrived.addEventListener('click', () => setStatus(arrived, 'arrived', 'Saving…'));
    noshow.addEventListener('click', () => setStatus(noshow, 'no_show', 'Saving…'));
    actions.append(arrived, noshow);
  } else {
    actions.append(statusPill(b.status));
    if (b.status === 'arrived' || b.status === 'no_show') {
      const undo = h('button', { type: 'button', class: 'adm-btn adm-btn--ghost adm-bk-act', 'aria-label': `Undo: set ${b.name} back to booked` }, 'Undo');
      undo.addEventListener('click', () => setStatus(undo, 'confirmed', 'Saving…'));
      actions.append(undo);
    }
  }
  const row = h(
    'li',
    { class: `adm-bk adm-bk--${b.status}`, 'data-id': b.id },
    h(
      'div',
      { class: 'adm-bk-when' },
      o.showDate ? h('span', { class: 'adm-bk-date' }, relDay(b.date)) : null,
      h('span', { class: 'adm-bk-time' }, clock(b.time)),
    ),
    openBtn,
    actions,
  );
  openBtn.addEventListener('click', () => o.onOpen(b, openBtn));
  row.addEventListener('click', (e) => {
    const t = e.target as HTMLElement;
    if (t.closest('button, a, input')) return;
    o.onOpen(b, openBtn);
  });
  return row;
}

// ---- time picker with live capacity ------------------------------------------------

interface Picker {
  el: HTMLElement;
  select: HTMLSelectElement;
  note: HTMLElement;
  load: (date: string, keep?: string) => Promise<void>;
  refresh: () => void;
  predictedFull: () => boolean;
}

function timePicker(getParty: () => number, except?: Booking): Picker {
  const select = h('select', { class: 'adm-input', name: 'time', id: `tp-${Math.random().toString(36).slice(2, 7)}`, 'data-field': 'time' });
  const note = h('p', { class: 'adm-hint', 'aria-live': 'polite' });
  let day: BookingDay | null = null;
  let rules: BookingRules | null = null;
  let seq = 0;

  const refresh = () => {
    if (!day || !rules) return;
    const party = getParty() || 1;
    const keep = select.value;
    select.replaceChildren();
    if (day.closed || !day.slots.length) {
      select.append(h('option', { value: '' }, 'Closed that day'));
      select.disabled = true;
      note.textContent = 'The café is closed that day, so there are no times to book.';
      return;
    }
    select.disabled = false;
    for (const s of day.slots) {
      const r = roomAt(day, rules, s.time, party, except);
      const label = r.fits ? `${clock(s.time)} · room for ${r.free}` : `${clock(s.time)} · full for ${party}`;
      select.append(h('option', { value: s.time }, label));
    }
    // An existing booking's time may no longer be a slot (hours changed): keep it.
    if (keep && !day.slots.some((s) => s.time === keep)) select.append(h('option', { value: keep }, `${clock(keep)} · not a slot any more`));
    if (keep) select.value = keep;
    else {
      const firstFree = day.slots.find((s) => roomAt(day!, rules!, s.time, party, except).fits);
      if (firstFree) select.value = firstFree.time;
    }
    describe();
  };
  const describe = () => {
    if (!day || !rules || !select.value) return;
    const party = getParty() || 1;
    const r = roomAt(day, rules, select.value, party, except);
    note.textContent = r.fits
      ? `${r.peak} of ${r.capacity} seats taken around ${clock(select.value)}. Room for ${r.free} more.`
      : party > r.capacity
        ? `${party} is more than all ${r.capacity} seats in the café.`
        : `Full: ${r.peak} of ${r.capacity} seats are taken around ${clock(select.value)}, so ${party} won't fit.`;
    note.classList.toggle('adm-err', !r.fits);
  };
  select.addEventListener('change', describe);

  const load = async (date: string, keep?: string) => {
    const mine = ++seq;
    note.textContent = 'Checking tables…';
    note.classList.remove('adm-err');
    try {
      const [d, s] = await Promise.all([getDay(date), settings()]);
      if (mine !== seq) return;
      day = d;
      rules = s.booking;
      if (keep) {
        select.replaceChildren(h('option', { value: keep }, clock(keep)));
        select.value = keep;
      }
      refresh();
    } catch (e) {
      if (mine !== seq) return;
      note.textContent = errorText(e);
    }
  };
  return {
    el: select,
    select,
    note,
    load,
    refresh,
    predictedFull: () => (day && rules && select.value ? !roomAt(day, rules, select.value, getParty() || 1, except).fits : false),
  };
}

function field(labelText: string, control: HTMLElement, hint?: HTMLElement | string | null): HTMLElement {
  if (!control.id) control.id = `f-${Math.random().toString(36).slice(2, 8)}`;
  return h(
    'div',
    { class: 'adm-field' },
    h('label', { class: 'adm-label', for: control.id }, labelText),
    control,
    typeof hint === 'string' ? h('p', { class: 'adm-hint' }, hint) : hint ?? null,
  );
}

// ---- edit panel -------------------------------------------------------------------

export function openBookingPanel(b: Booking, onChange: (b: Booking) => void): void {
  let current = b;
  const contact = h(
    'div',
    { class: 'adm-bk-contact' },
    b.phone ? h('a', { class: 'adm-btn adm-btn--secondary', href: `tel:${b.phone.replace(/[^\d+]/g, '')}` }, `Call ${b.phone}`) : null,
    b.email ? h('a', { class: 'adm-btn adm-btn--secondary', href: `mailto:${b.email}?subject=${encodeURIComponent(`Your booking at Sasha's Corner (${b.reference})`)}` }, 'Email') : null,
  );
  const facts = h(
    'dl',
    { class: 'adm-kv' },
    h('dt', {}, 'Reference'),
    h('dd', {}, b.reference),
    h('dt', {}, 'When'),
    h('dd', {}, `${longDate(b.date)}, ${clock(b.time)}`),
    h('dt', {}, 'Booked'),
    h('dd', {}, b.source === 'admin' ? 'By phone (added here)' : 'Online'),
    b.email ? h('dt', {}, 'Email') : null,
    b.email ? h('dd', {}, b.email) : null,
    b.phone ? h('dt', {}, 'Phone') : null,
    b.phone ? h('dd', {}, b.phone) : null,
  );

  // Status
  const statusSeg = h('div', { class: 'adm-seg', role: 'radiogroup', 'aria-label': 'Status' });
  const statuses: BookingStatus[] = ['confirmed', 'arrived', 'no_show'];
  const paintSeg = () => {
    statusSeg.querySelectorAll('button').forEach((btn) => btn.setAttribute('aria-checked', String(btn.dataset.s === current.status)));
  };
  for (const s of statuses) {
    const btn = h('button', { type: 'button', role: 'radio', 'data-s': s }, STATUS_LABEL[s]);
    btn.addEventListener('click', async () => {
      if (current.status === s) return;
      try {
        current = await busy(btn, '…', () => patchBooking(b.id, { status: s }));
        paintSeg();
        onChange(current);
        toast(`Marked ${STATUS_LABEL[s].toLowerCase()}.`);
      } catch (e) {
        toast(errorText(e), { tone: 'bad' });
      }
    });
    statusSeg.append(btn);
  }
  paintSeg();

  // Edit form
  const party = h('input', { class: 'adm-input', type: 'number', name: 'party', min: 1, max: 40, inputmode: 'numeric', value: b.party, 'data-field': 'party' });
  const date = h('input', { class: 'adm-input', type: 'date', name: 'date', value: b.date, 'data-field': 'date' });
  const notes = h('textarea', { class: 'adm-input', name: 'notes', rows: 3, 'data-field': 'notes' });
  notes.value = b.notes ?? '';
  const picker = timePicker(() => Number(party.value), b);
  const overrideWrap = h('label', { class: 'adm-check', hidden: true }, h('input', { type: 'checkbox', name: 'override' }), 'Book it anyway, over capacity');
  const override = overrideWrap.querySelector('input')!;
  const formErr = h('p', { class: 'adm-err', role: 'alert', hidden: true });
  const save = h('button', { type: 'submit', class: 'adm-btn' }, 'Save changes');
  const form = h(
    'form',
    { class: 'adm-form', novalidate: true },
    h('h3', { class: 'adm-section-head' }, 'Change the booking'),
    h('div', { class: 'adm-grid2' }, field('Party', party), field('Date', date)),
    field('Time', picker.el, picker.note),
    field('Notes', notes, 'Only you see these.'),
    overrideWrap,
    formErr,
    save,
  );
  party.addEventListener('input', () => picker.refresh());
  date.addEventListener('change', () => date.value && picker.load(date.value));
  void picker.load(b.date, b.time);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearFieldErrors(form);
    formErr.hidden = true;
    const body: BookingPatch = {};
    const p = Number(party.value);
    if (p !== current.party) body.party = p;
    if (date.value !== current.date) body.date = date.value;
    if (picker.select.value && picker.select.value !== current.time) body.time = picker.select.value;
    if ((notes.value.trim() || null) !== (current.notes || null)) body.notes = notes.value.trim();
    if (!Object.keys(body).length) {
      toast('Nothing changed.');
      return;
    }
    if (override.checked) body.override_capacity = true;
    try {
      current = await busy(save, 'Saving…', () => patchBooking(b.id, body));
      override.checked = false;
      overrideWrap.hidden = true;
      onChange(current);
      toast(body.date || body.time ? `Moved to ${relDay(current.date)}, ${clock(current.time)}.` : 'Booking saved.');
      drawer.close();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        formErr.textContent = 'That time is full. Pick another time, or tick “Book it anyway” if you can make room.';
        formErr.hidden = false;
        overrideWrap.hidden = false;
        announce(formErr.textContent, true);
      } else if (err instanceof ApiError && err.status === 422 && showFieldErrors(form, err.fields)) {
        /* shown by the fields */
      } else {
        formErr.textContent = errorText(err);
        formErr.hidden = false;
      }
    }
  });

  // Cancel / reinstate
  const danger = h('div', { class: 'adm-bk-danger' });
  const renderDanger = () => {
    danger.replaceChildren();
    if (current.status === 'cancelled') {
      const re = h('button', { type: 'button', class: 'adm-btn adm-btn--secondary' }, 'Reinstate booking');
      re.addEventListener('click', async () => {
        try {
          current = await busy(re, 'Reinstating…', () => patchBooking(b.id, { status: 'confirmed' }));
          onChange(current);
          paintSeg();
          renderDanger();
          toast('Booking reinstated.');
        } catch (e) {
          toast(e instanceof ApiError && e.status === 409 ? 'That time is full now, so it can’t be reinstated as it was. Move it first.' : errorText(e), { tone: 'bad' });
        }
      });
      danger.append(h('p', { class: 'adm-hint' }, 'This booking is cancelled.'), re);
    } else {
      const cancel = h('button', { type: 'button', class: 'adm-btn adm-btn--danger' }, 'Cancel booking');
      confirmTwice(cancel, 'Tap again to cancel it', async () => {
        try {
          current = await busy(cancel, 'Cancelling…', () => patchBooking(b.id, { status: 'cancelled' }));
          onChange(current);
          toast(`${b.name}'s booking is cancelled. They are not notified automatically.`, {
            timeout: 7000,
            action: { label: 'Undo', run: () => void patchBooking(b.id, { status: 'confirmed' }).then(onChange, (x) => toast(errorText(x), { tone: 'bad' })) },
          });
          drawer.close();
        } catch (e) {
          toast(errorText(e), { tone: 'bad' });
        }
      });
      danger.append(cancel, h('p', { class: 'adm-hint' }, 'The guest is not told automatically. Call or email them.'));
    }
  };
  renderDanger();

  const body = h(
    'div',
    { class: 'adm-stack' },
    h('div', { class: 'adm-bk-head' }, h('p', { class: 'adm-bk-party' }, `${plural(b.party, 'person', 'people')} · ${relDay(b.date)}, ${clock(b.time)}`), contact),
    facts,
    h('div', { class: 'adm-field' }, h('span', { class: 'adm-label' }, 'Status'), statusSeg),
    form,
    danger,
  );
  const drawer = openDrawer({ title: b.name, body });
}

// ---- add booking (phone-ins) ------------------------------------------------------

export function openAddBooking(defaultDate: string, onCreated: (b: Booking) => void): void {
  const name = h('input', { class: 'adm-input', name: 'name', autocomplete: 'off', required: true, 'data-field': 'name' });
  const phone = h('input', { class: 'adm-input', name: 'phone', type: 'tel', autocomplete: 'off', inputmode: 'tel', 'data-field': 'phone' });
  const email = h('input', { class: 'adm-input', name: 'email', type: 'email', autocomplete: 'off', 'data-field': 'email' });
  const party = h('input', { class: 'adm-input', name: 'party', type: 'number', min: 1, max: 40, value: 2, inputmode: 'numeric', 'data-field': 'party' });
  const date = h('input', { class: 'adm-input', name: 'date', type: 'date', value: defaultDate || todayISO(), 'data-field': 'date' });
  const notes = h('textarea', { class: 'adm-input', name: 'notes', rows: 2, 'data-field': 'notes' });
  const picker = timePicker(() => Number(party.value));
  const overrideWrap = h('label', { class: 'adm-check', hidden: true }, h('input', { type: 'checkbox', name: 'override' }), 'Book it anyway, over capacity');
  const override = overrideWrap.querySelector('input')!;
  const formErr = h('p', { class: 'adm-err', role: 'alert', hidden: true });
  const submit = h('button', { type: 'submit', class: 'adm-btn' }, 'Add booking');
  const quick = h(
    'div',
    { class: 'adm-chips', role: 'group', 'aria-label': 'Quick date' },
    [0, 1, 2].map((n) => {
      const d = addDays(todayISO(), n);
      const c = h('button', { type: 'button', class: 'adm-chip' }, relDay(d));
      c.addEventListener('click', () => {
        date.value = d;
        void picker.load(d);
      });
      return c;
    }),
  );
  const form = h(
    'form',
    { class: 'adm-form', novalidate: true },
    field('Name', name),
    h('div', { class: 'adm-grid2' }, field('Phone', phone, 'Optional'), field('Party', party)),
    field('Email', email, 'Optional. They get no email from this; it is just for your records.'),
    field('Date', date),
    quick,
    field('Time', picker.el, picker.note),
    field('Notes', notes, 'Optional: high chair, birthday, dog…'),
    overrideWrap,
    formErr,
  );
  party.addEventListener('input', () => {
    picker.refresh();
    overrideWrap.hidden = !picker.predictedFull();
  });
  picker.select.addEventListener('change', () => (overrideWrap.hidden = !picker.predictedFull()));
  date.addEventListener('change', () => date.value && picker.load(date.value).then(() => (overrideWrap.hidden = !picker.predictedFull())));
  void picker.load(date.value).then(() => (overrideWrap.hidden = !picker.predictedFull()));

  const doSubmit = async () => {
    clearFieldErrors(form);
    formErr.hidden = true;
    const missing: Record<string, string> = {};
    if (!name.value.trim()) missing.name = 'Enter a name so you know who is coming.';
    const p = Number(party.value);
    if (!Number.isInteger(p) || p < 1) missing.party = 'Enter how many people (1 or more).';
    if (!date.value) missing.date = 'Pick a date.';
    if (!picker.select.value) missing.time = 'Pick a time.';
    if (Object.keys(missing).length) {
      showFieldErrors(form, missing);
      return;
    }
    const body: NewBooking = { name: name.value.trim(), party: p, date: date.value, time: picker.select.value };
    if (phone.value.trim()) body.phone = phone.value.trim();
    if (email.value.trim()) body.email = email.value.trim();
    if (notes.value.trim()) body.notes = notes.value.trim();
    if (override.checked) body.override_capacity = true;
    try {
      const created = await busy(submit, 'Adding…', () => api.post<Booking>('/api/admin/bookings', body));
      onCreated(created);
      toast(`Booked: ${created.name}, ${plural(created.party, 'person', 'people')}, ${relDay(created.date)} at ${clock(created.time)}. Ref ${created.reference}.`, { timeout: 7000 });
      drawer.close();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        formErr.textContent = 'That time is full. Pick another time, or tick “Book it anyway” if you can make room.';
        formErr.hidden = false;
        overrideWrap.hidden = false;
        announce(formErr.textContent, true);
      } else if (err instanceof ApiError && err.status === 422 && showFieldErrors(form, err.fields)) {
        /* shown */
      } else {
        formErr.textContent = errorText(err);
        formErr.hidden = false;
      }
    }
  };
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    void doSubmit();
  });
  submit.addEventListener('click', (e) => {
    e.preventDefault();
    void doSubmit();
  });
  const drawer = openDrawer({ title: 'Add a phone booking', body: form, foot: submit });
}
