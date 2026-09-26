// In-page stand-in for the admin API (?mock=1), following site/ADMIN.md exactly.
// State lives in sessionStorage so it survives moving between admin pages in one tab.
//
// Sign-in: any password works except "wrong" (401), "slow" (429) and "unset" (503).
// The mock password for the change-password form is "dev-admin".

import type { Booking, BookingRules, Closure, Message, Settings } from './types';

interface State {
  authed: boolean;
  password: string;
  bookings: Booking[];
  messages: Message[];
  settings: Settings;
  nextId: number;
}

const KEY = 'sc-adm-mock-state';

function todayLocal(): string {
  const p = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const g = (t: string) => p.find((x) => x.type === t)?.value ?? '';
  return `${g('year')}-${g('month')}-${g('day')}`;
}
function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}
const weekday = (iso: string) => {
  const [y, m, d] = iso.split('-').map(Number);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
};
const mins = (t: string) => {
  const [h, m] = t.split(':').map(Number);
  return h * 60 + m;
};
const hhmm = (n: number) => `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
const REF = 'ACDEFGHJKMNPQRTUVWXY34679';
const ref = () => Array.from({ length: 6 }, () => REF[Math.floor(Math.random() * REF.length)]).join('');

function seed(): State {
  const t = todayLocal();
  const now = new Date().toISOString();
  let id = 1;
  const b = (day: number, time: string, name: string, party: number, extra: Partial<Booking> = {}): Booking => ({
    id: id++,
    reference: ref(),
    name,
    email: extra.source === 'admin' ? null : `${name.split(' ')[0].toLowerCase()}@example.com`,
    phone: extra.source === 'admin' ? '07700 900' + String(100 + id) : null,
    party,
    date: addDays(t, day),
    time,
    status: 'confirmed',
    notes: null,
    source: 'web',
    created_at: now,
    cancelled_at: null,
    ...extra,
  });
  const bookings: Booking[] = [
    b(0, '10:00', 'Aileen Brodie', 2, { status: 'arrived' }),
    b(0, '11:30', 'Tom Kerr', 4, { notes: 'Pushchair, near the window if possible' }),
    b(0, '12:30', 'Priya Nair', 6, { source: 'admin' }),
    b(0, '12:30', 'Jamie Fraser', 8, { notes: 'Birthday: they are bringing a cake' }),
    b(0, '13:00', 'Morag Ross', 5),
    b(0, '15:00', 'Olena Kovalenko', 3, { source: 'admin' }),
    b(0, '09:30', 'Ben Walker', 2, { status: 'no_show' }),
    b(0, '16:30', 'Chris Dunn', 2, { status: 'cancelled', cancelled_at: now }),
    b(1, '10:30', 'Sophie Hamilton', 2),
    b(1, '12:00', 'Leah Grant', 4),
    b(2, '11:00', 'Mark Paterson', 6),
    b(3, '14:00', 'Hannah Scott', 2),
    b(3, '14:30', 'Euan Reid', 3),
    b(5, '12:00', 'Katie Murray', 8, { notes: 'Baby shower' }),
    b(-1, '12:00', 'Iain Stewart', 4, { status: 'arrived' }),
  ];
  const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();
  const messages: Message[] = [
    { id: 1, name: 'Rachel Anderson', email: 'rachel@example.com', topic: 'order', message: 'Hi! Could I order a whole Kyiv cake for Saturday the 4th? We would collect around 2pm. My number is 07700 900123 if easier to call.', created_at: hoursAgo(2), status: 'new', handled_at: null },
    { id: 2, name: 'Dundee Uni Ukrainian Society', email: 'uksoc@example.ac.uk', topic: 'events', message: 'We would love to hold a small social at the café one evening in October, about 15 people. Is that something you do? What would you need from us?', created_at: hoursAgo(20), status: 'new', handled_at: null },
    { id: 3, name: 'Callum', email: 'callum@example.com', topic: 'feedback', message: 'The blue matcha was amazing, and the staff were so kind to my gran. Thank you.', created_at: hoursAgo(50), status: 'handled', handled_at: hoursAgo(30) },
    { id: 4, name: 'Local Eats Magazine', email: 'editor@example.com', topic: 'press', message: 'We are writing a round-up of new cafés in Dundee city centre and would like to include you. Could we send a photographer?', created_at: hoursAgo(120), status: 'archived', handled_at: hoursAgo(100) },
    { id: 5, name: 'Nadia', email: 'nadia@example.com', topic: 'jobs', message: 'Hello, are you hiring baristas for weekends? I have two years of experience. +44 7700 900456', created_at: hoursAgo(5), status: 'new', handled_at: null },
  ];
  const hours = [0, 1, 2, 3, 4, 5, 6].map((w) => ({ weekday: w, open: '09:00', close: w === 6 ? '17:00' : '19:00', closed: false }));
  const settings: Settings = {
    cafe: {
      name: "Sasha's Corner",
      phone: '07398 433317',
      email: '',
      address: { line1: '23 Commercial Street', city: 'Dundee', postcode: 'DD1 3DD', country: 'GB' },
      geo: { lat: 56.4612158, lng: -2.9668779 },
      hours,
      socials: { instagram: 'https://www.instagram.com/sashascorner_uk', facebook: 'https://www.facebook.com/p/Sashas-Corner-61582666970753', tiktok: '' },
    },
    booking: { covers_per_slot: 24, max_party: 8, slot_minutes: 30, duration_minutes: 90, last_seating_before_close_minutes: 60, min_lead_minutes: 60, horizon_days: 30 },
    closures: [{ date: addDays(t, 12), note: 'Private event' }],
    telegram_configured: true,
  };
  return { authed: false, password: 'dev-admin', bookings, messages, settings, nextId: id };
}

function load(): State {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as State;
  } catch {
    /* fall through */
  }
  const s = seed();
  save(s);
  return s;
}
function save(s: State): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* ignore */
  }
}

type R = { status: number; body: unknown };
const ok = (body: unknown, status = 200): R => ({ status, body });
const err = (status: number, detail: unknown): R => ({ status, body: { detail } });
const v422 = (loc: (string | number)[], msg: string): R => err(422, [{ loc: ['body', ...loc], msg, type: 'value_error' }]);

// ---- capacity (same model as sashasite/booking.py) ----------------------------

const holds = (b: Booking) => b.status === 'confirmed' || b.status === 'arrived';

function dayHours(s: State, date: string) {
  const h = s.settings.cafe.hours.find((x) => x.weekday === weekday(date));
  const closure = s.settings.closures.find((c) => c.date === date);
  if (!h || h.closed || !h.open || !h.close || closure) return null;
  return { open: mins(h.open), close: mins(h.close) };
}

function slotTimes(s: State, date: string): string[] {
  const h = dayHours(s, date);
  if (!h) return [];
  const r = s.settings.booking;
  const out: string[] = [];
  for (let t = h.open; t <= h.close - r.last_seating_before_close_minutes; t += r.slot_minutes) out.push(hhmm(t));
  return out;
}

function coversAt(s: State, date: string, t: number, exceptId?: number): number {
  const dur = s.settings.booking.duration_minutes;
  return s.bookings
    .filter((b) => b.date === date && holds(b) && b.id !== exceptId && mins(b.time) <= t && t < mins(b.time) + dur)
    .reduce((a, b) => a + b.party, 0);
}

function fits(s: State, date: string, time: string, party: number, exceptId?: number): boolean {
  const r = s.settings.booking;
  const start = mins(time);
  for (let t = start; t < start + r.duration_minutes; t += r.slot_minutes) {
    if (coversAt(s, date, t, exceptId) + party > r.covers_per_slot) return false;
  }
  return true;
}

function dayView(s: State, date: string) {
  const cap = s.settings.booking.covers_per_slot;
  const times = slotTimes(s, date);
  return {
    date,
    closed: times.length === 0,
    reason: times.length === 0 ? s.settings.closures.find((c) => c.date === date)?.note || "We're closed on this day of the week." : null,
    capacity: cap,
    slots: times.map((t) => ({ time: t, covers_booked: coversAt(s, date, mins(t)), capacity: cap })),
    bookings: s.bookings.filter((b) => b.date === date).sort((a, b) => a.time.localeCompare(b.time) || a.id - b.id),
  };
}

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const HM = /^([01]\d|2[0-3]):[0-5]\d$/;

// ---- router -------------------------------------------------------------------

export async function mockFetch(method: string, path: string, body: unknown): Promise<R> {
  await new Promise((r) => setTimeout(r, 120 + Math.random() * 180));
  const s = load();
  const url = new URL(path, location.origin);
  const p = url.pathname;
  const qp = url.searchParams;
  const data = (body ?? {}) as Record<string, unknown>;
  const res = route(s, method, p, qp, data);
  save(s);
  return res;
}

type Extra = (method: string, path: string, query: URLSearchParams, body: Record<string, unknown>) => R | null;
const extras: Extra[] = [];
/** Let another admin page add its own mock routes (return null to pass). */
export function extendMock(fn: Extra): void {
  extras.push(fn);
}

function route(s: State, method: string, p: string, qp: URLSearchParams, data: Record<string, unknown>): R {
  if (method === 'POST' && p === '/api/admin/login') {
    const pw = String(data.password ?? '');
    if (pw === 'wrong') return err(401, 'Wrong password');
    if (pw === 'slow') return err(429, 'Too many attempts');
    if (pw === 'unset') return err(503, 'Admin password is not configured');
    s.authed = true;
    return ok({ ok: true });
  }
  if (method === 'POST' && p === '/api/admin/logout') {
    s.authed = false;
    return ok(null, 204);
  }
  if (!s.authed) return err(401, 'Not signed in');

  if (method === 'GET' && p === '/api/admin/me') return ok({ authenticated: true, password_source: 'env' });

  if (method === 'POST' && p === '/api/admin/password') {
    if (String(data.current ?? '') !== s.password) return err(400, 'The current password is wrong.');
    const n = String(data.new ?? '');
    if (n.length < 10) return v422(['new'], 'String should have at least 10 characters');
    s.password = n;
    return ok({ ok: true });
  }

  // bookings
  if (method === 'GET' && p === '/api/admin/bookings/day') {
    const date = qp.get('date') ?? '';
    if (!ISO.test(date)) return err(422, [{ loc: ['query', 'date'], msg: 'Invalid date' }]);
    return ok(dayView(s, date));
  }
  if (method === 'GET' && p === '/api/admin/bookings') {
    const from = qp.get('from') ?? '0000-00-00';
    const to = qp.get('to') ?? '9999-99-99';
    const st = qp.get('status');
    return ok(
      s.bookings
        .filter((b) => b.date >= from && b.date <= to && (!st || b.status === st))
        .sort((a, b) => a.date.localeCompare(b.date) || a.time.localeCompare(b.time)),
    );
  }
  if (method === 'POST' && p === '/api/admin/bookings') {
    const name = String(data.name ?? '').trim();
    const party = Number(data.party);
    const date = String(data.date ?? '');
    const time = String(data.time ?? '');
    if (!name) return v422(['name'], 'Enter a name');
    if (!Number.isInteger(party) || party < 1) return v422(['party'], 'Input should be greater than or equal to 1');
    if (party > 40) return v422(['party'], 'Input should be less than or equal to 40');
    if (!ISO.test(date)) return v422(['date'], 'Invalid date');
    if (!HM.test(time)) return v422(['time'], 'Invalid time');
    if (!slotTimes(s, date).includes(time)) return err(422, 'That is not a bookable time on that day (closed, or outside the slots).');
    if (!data.override_capacity && !fits(s, date, time, party)) return err(409, 'That time is full.');
    const bk: Booking = {
      id: s.nextId++,
      reference: ref(),
      name,
      email: (data.email as string) || null,
      phone: (data.phone as string) || null,
      party,
      date,
      time,
      status: 'confirmed',
      notes: (data.notes as string) || null,
      source: 'admin',
      created_at: new Date().toISOString(),
      cancelled_at: null,
    };
    s.bookings.push(bk);
    return ok(bk, 201);
  }
  const bm = p.match(/^\/api\/admin\/bookings\/(\d+)$/);
  if (bm && method === 'PATCH') {
    const bk = s.bookings.find((b) => b.id === Number(bm[1]));
    if (!bk) return err(404, 'No such booking');
    const next = { ...bk };
    if (data.status !== undefined) {
      if (!['confirmed', 'cancelled', 'arrived', 'no_show'].includes(String(data.status))) return v422(['status'], 'Invalid status');
      next.status = data.status as Booking['status'];
      next.cancelled_at = next.status === 'cancelled' ? new Date().toISOString() : null;
    }
    if (data.notes !== undefined) next.notes = (data.notes as string) || null;
    if (data.party !== undefined) {
      const n = Number(data.party);
      if (!Number.isInteger(n) || n < 1) return v422(['party'], 'Input should be greater than or equal to 1');
      next.party = n;
    }
    if (data.date !== undefined) {
      if (!ISO.test(String(data.date))) return v422(['date'], 'Invalid date');
      next.date = String(data.date);
    }
    if (data.time !== undefined) {
      if (!HM.test(String(data.time))) return v422(['time'], 'Invalid time');
      next.time = String(data.time);
    }
    const moved = next.date !== bk.date || next.time !== bk.time || next.party > bk.party || (holds(next) && !holds(bk));
    if (moved && holds(next)) {
      if (!slotTimes(s, next.date).includes(next.time)) return err(422, 'That is not a bookable time on that day.');
      if (!data.override_capacity && !fits(s, next.date, next.time, next.party, bk.id)) return err(409, 'That time is full.');
    }
    Object.assign(bk, next);
    return ok(bk);
  }

  // messages
  if (method === 'GET' && p === '/api/admin/messages') {
    const st = qp.get('status') ?? 'all';
    return ok(s.messages.filter((m) => st === 'all' || m.status === st).sort((a, b) => b.created_at.localeCompare(a.created_at)));
  }
  const mm = p.match(/^\/api\/admin\/messages\/(\d+)$/);
  if (mm && method === 'PATCH') {
    const m = s.messages.find((x) => x.id === Number(mm[1]));
    if (!m) return err(404, 'No such message');
    const st = String(data.status ?? '');
    if (!['new', 'handled', 'archived'].includes(st)) return v422(['status'], 'Invalid status');
    m.status = st as Message['status'];
    m.handled_at = st === 'new' ? null : m.handled_at ?? new Date().toISOString();
    return ok(m);
  }

  // settings
  if (method === 'GET' && p === '/api/admin/settings') return ok(s.settings);
  if (method === 'PUT' && p === '/api/admin/settings') {
    if (data.booking) {
      const b = data.booking as Record<string, unknown>;
      const keys: (keyof BookingRules)[] = ['covers_per_slot', 'max_party', 'slot_minutes', 'duration_minutes', 'last_seating_before_close_minutes', 'min_lead_minutes', 'horizon_days'];
      for (const k of keys) {
        if (b[k] === undefined) continue;
        const n = Number(b[k]);
        const min = k === 'min_lead_minutes' || k === 'last_seating_before_close_minutes' ? 0 : 1;
        if (!Number.isInteger(n) || n < min) return v422(['booking', k], `Input should be greater than or equal to ${min}`);
      }
      if (Number(b.max_party ?? s.settings.booking.max_party) > Number(b.covers_per_slot ?? s.settings.booking.covers_per_slot))
        return v422(['booking', 'max_party'], 'Max party cannot be more than capacity');
    }
    if (data.cafe) {
      const c = data.cafe as Settings['cafe'];
      if (c.name !== undefined && !String(c.name).trim()) return v422(['cafe', 'name'], 'Enter a name');
      if (c.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(c.email)) return v422(['cafe', 'email'], 'value is not a valid email address');
      for (const [i, h] of (c.hours ?? []).entries()) {
        if (h.closed) continue;
        if (!HM.test(String(h.open)) || !HM.test(String(h.close))) return v422(['cafe', 'hours', i, 'open'], 'Use a time like 09:00');
        if (mins(String(h.close)) <= mins(String(h.open))) return v422(['cafe', 'hours', i, 'close'], 'Closing time must be after opening');
      }
    }
    if (data.closures) {
      for (const [i, c] of (data.closures as Closure[]).entries()) {
        if (!ISO.test(c.date)) return v422(['closures', i, 'date'], 'Invalid date');
      }
    }
    if (data.cafe) s.settings.cafe = { ...s.settings.cafe, ...(data.cafe as Settings['cafe']) };
    if (data.booking) s.settings.booking = { ...s.settings.booking, ...(data.booking as BookingRules) };
    if (data.closures) s.settings.closures = [...(data.closures as Closure[])].sort((a, b) => a.date.localeCompare(b.date));
    return ok(s.settings);
  }

  // dashboard
  if (method === 'GET' && p === '/api/admin/summary') {
    const t = todayLocal();
    const d = dayView(s, t);
    const live = d.bookings.filter(holds);
    return ok({
      today: {
        date: t,
        closed: d.closed,
        bookings: live.length,
        covers: live.reduce((a, b) => a + b.party, 0),
        capacity: d.capacity,
        next: d.bookings.filter((b) => b.status === 'confirmed'),
      },
      week: Array.from({ length: 7 }, (_, i) => {
        const date = addDays(t, i);
        const bs = s.bookings.filter((b) => b.date === date && holds(b));
        return { date, bookings: bs.length, covers: bs.reduce((a, b) => a + b.party, 0) };
      }),
      messages_new: s.messages.filter((m) => m.status === 'new').length,
      photos_missing: 7,
      menu: { source: 'board', warnings: 2 },
    });
  }

  for (const fn of extras) {
    const r = fn(method, p, qp, data);
    if (r) return r;
  }
  return err(404, 'Not Found');
}
