// In-page stand-in for the staff API (?mock=1), following CONTRACT §5. Development
// only: loaded by api.ts on demand, never part of the normal bundle path.
//
//   Pairing code: any 6 digits except 000000 (expired).
//   PINs: 1234 = Anna (staff), 2468 = Morag (manager). Anything else is wrong.
//   Cards (type the payload in "Enter code"):
//     SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c0001:a1b2c3d4   Olena, 5 stamps
//     SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c0002:a1b2c3d4   Jamie, free drink + birthday
//     SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c0003:a1b2c3d4   Priya, new card (convert)
//     SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c0004:a1b2c3d4   a deleted (void) card
//     SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c0001:ffffffff   bad signature
//     SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c9999:a1b2c3d4   unknown card
//     SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c0005:a1b2c3d4   Olena's Matcha club card (2/6)
//     SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c0006:a1b2c3d4   Jamie's Cake points card (95/100)
//   "Any drink" is capped at £6.00 here so over_price_cap can be seen.
//   Phase 3: Olena has two cards, Jamie a points card and a reward catalogue (any drink
//   or a slice of cake); Priya can add either club; till customers can be linked.
// State lives in sessionStorage so a reload keeps it.

import type { Drink, LookupMember, PosCustomer, Reward, RewardOption, ScanResult } from './types';

type Slug = 'stamp' | 'matcha-club' | 'cake-points';

interface MCard {
  id: string;
  member: string;
  program: Slug;
  first_name: string;
  contact: string;
  member_since: string;
  stamps: number;
  voided: boolean;
  migrated: boolean;
}
interface MReward extends Reward {
  card_id: string;
  redeemed_at: number | null;
  redeemed_item: number | null;
  voided: boolean;
  issued_by_event: number | null;
}
interface MEvent {
  id: number;
  card_id: string;
  delta: number;
  reason: 'PURCHASE' | 'PAPER_MIGRATION' | 'UNDO';
  at: number;
  staff: string;
  device: string;
  undone: boolean;
}
interface State {
  devices: Record<string, string>;
  sessions: Record<string, { user: number; device: string; expires: number }>;
  cards: MCard[];
  rewards: MReward[];
  events: MEvent[];
  linked: string[]; // members linked to a till customer
  nextId: number;
}

interface MProgram {
  name: string;
  kind: 'STAMPS' | 'POINTS';
  required: number;
  perPound: number | null;
  ready: string;
  options: (RewardOption & { match: RegExp; drinksOnly: boolean })[];
}

const PROGRAMS: Record<Slug, MProgram> = {
  stamp: {
    name: "Sasha's Corner Rewards",
    kind: 'STAMPS',
    required: 8,
    perPound: null,
    ready: 'Free drink ready',
    options: [
      { id: 71, name: 'Any drink', description: 'Hot or cold, any size', max_price_pence: 600, covers: 'any drink', match: /./, drinksOnly: true },
      { id: 72, name: 'Slice of cake', description: 'Instead of the drink', max_price_pence: 450, covers: "items with 'cake' or 'slice' in the name", match: /cake|slice|brownie/i, drinksOnly: false },
    ],
  },
  'matcha-club': {
    name: 'Matcha club',
    kind: 'STAMPS',
    required: 6,
    perPound: null,
    ready: 'Free matcha ready',
    options: [{ id: 73, name: 'Any matcha drink', description: null, max_price_pence: null, covers: "drinks with 'matcha' in the name", match: /matcha/i, drinksOnly: true }],
  },
  'cake-points': {
    name: 'Cake points',
    kind: 'POINTS',
    required: 100,
    perPound: 10,
    ready: 'Cake slice ready',
    options: [{ id: 74, name: 'Slice of cake', description: null, max_price_pence: 450, covers: "items with 'cake' or 'slice' in the name", match: /cake|slice|brownie/i, drinksOnly: false }],
  },
};
const SLUGS = Object.keys(PROGRAMS) as Slug[];
const CUSTOMERS: PosCustomer[] = [
  { customer_id: 'LSK-CUST-2041', label: 'Olena K.', last_receipt_id: 'LSK-R8812', last_at: new Date(Date.now() - 50 * 60_000).toISOString(), receipts: 3 },
  { customer_id: 'LSK-CUST-2057', label: 'Priya S.', last_receipt_id: 'LSK-R8809', last_at: new Date(Date.now() - 3 * 3600_000).toISOString(), receipts: 1 },
  { customer_id: 'LSK-CUST-1990', label: null, last_receipt_id: 'LSK-R8790', last_at: new Date(Date.now() - 26 * 3600_000).toISOString(), receipts: 2 },
];

const KEY = 'sc-staff-mock-state';
const UNDO_MS = 2 * 60_000;
const SIG = 'a1b2c3d4';
const USERS = [
  { id: 1, name: 'Anna', role: 'staff' as const, pin: '1234' },
  { id: 2, name: 'Morag', role: 'manager' as const, pin: '2468' },
];
const id = (n: number) => `0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c000${n}`;

const DRINKS: Drink[] = [
  ['Latte', 'Hot drinks', 340],
  ['Cappuccino', 'Hot drinks', 340],
  ['Flat white', 'Hot drinks', 330],
  ['White americano', 'Hot drinks', 300],
  ['Black americano', 'Hot drinks', 280],
  ['Raff coffee', 'Signature hot drinks', 450],
  ['Rose latte', 'Signature hot drinks', 420],
  ['Lavender latte', 'Signature hot drinks', 420],
  ['Pistachio latte', 'Signature hot drinks', 450],
  ['Honey vanilla latte', 'Signature hot drinks', 420],
  ['Matcha latte', 'Hot matcha', 420],
  ['Strawberry matcha', 'Hot matcha', 450],
  ['Pure ube latte', 'Ube', 480],
  ['Iced latte', 'Iced coffee', 380],
  ['Iced white americano', 'Iced coffee', 340],
  ['Iced honey blue matcha', 'Signature iced drinks', 520],
  ['Iced lavender blue matcha', 'Signature iced drinks', 520],
  ['Iced mango matcha', 'Iced green matcha', 490],
  ['Iced strawberry matcha', 'Iced green matcha', 490],
  ['Passion fruit bubble tea', 'Creamy bubble tea', 550],
  ['Green apple bubble tea', 'Creamy bubble tea', 550],
  ['Strawberry milkshake', 'Milkshakes', 650],
  ['Peach iced tea', 'Iced teas, refreshers & smoothies', 390],
  ['English breakfast', 'Tea', 250],
  ['Iced ube coconut latte', 'Ube', null],
  ['Kyiv cake (slice)', 'Cakes', 420],
  ['Brownie bar', 'Cakes', 380],
  ['Raspberry and matcha cheesecake', 'Cakes', 520],
].map(([name, category, price], i) => ({ menu_item_id: 101 + i, name: name as string, category: category as string, price_pence: price as number | null }));

function seed(): State {
  const day = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString().slice(0, 10);
  const now = Date.now();
  return {
    devices: {},
    sessions: {},
    cards: [
      { id: id(1), member: 'olena', program: 'stamp', first_name: 'Olena', contact: '07••• •••312', member_since: day(40), stamps: 5, voided: false, migrated: false },
      { id: id(2), member: 'jamie', program: 'stamp', first_name: 'Jamie', contact: 'j•••@example.com', member_since: day(120), stamps: 1, voided: false, migrated: true },
      { id: id(3), member: 'priya', program: 'stamp', first_name: 'Priya', contact: '07••• •••845', member_since: day(0), stamps: 0, voided: false, migrated: false },
      { id: id(4), member: 'gone', program: 'stamp', first_name: 'Deleted member', contact: '', member_since: day(200), stamps: 0, voided: true, migrated: false },
      { id: id(5), member: 'olena', program: 'matcha-club', first_name: 'Olena', contact: '07••• •••312', member_since: day(12), stamps: 2, voided: false, migrated: false },
      { id: id(6), member: 'jamie', program: 'cake-points', first_name: 'Jamie', contact: 'j•••@example.com', member_since: day(30), stamps: 95, voided: false, migrated: false },
    ],
    linked: ['jamie'],
    rewards: [
      { id: 501, card_id: id(2), kind: 'STAMP_CARD', label: 'Free drink ready', expires_at: null, redeemed_at: null, redeemed_item: null, voided: false, issued_by_event: null },
      {
        id: 502,
        card_id: id(2),
        kind: 'BIRTHDAY',
        label: 'Birthday drink',
        expires_at: new Date(now + 5 * 86_400_000).toISOString(),
        redeemed_at: null,
        redeemed_item: null,
        voided: false,
        issued_by_event: null,
      },
    ],
    events: [{ id: 900, card_id: id(1), delta: 1, reason: 'PURCHASE', at: now - 26 * 3600_000, staff: 'Morag', device: 'Till tablet', undone: false }],
    nextId: 1000,
  };
}

function load(): State {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as State;
  } catch {
    /* fresh */
  }
  return seed();
}
function save(s: State): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* ignore */
  }
}

const err = (status: number, error: string, detail: string) => ({ status, body: { error, detail } });
const ok = (body: unknown, status = 200) => ({ status, body });
const rand = (n: number) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

function view(s: State, c: MCard, now: number, siblings = true): ScanResult {
  const p = PROGRAMS[c.program];
  const rewards = s.rewards
    .filter((r) => r.card_id === c.id && !r.redeemed_at && !r.voided && (!r.expires_at || Date.parse(r.expires_at) > now))
    .map(({ id, kind, label, expires_at }) => ({ id, kind, label, expires_at }));
  const mine = s.cards.filter((x) => x.member === c.member && !x.voided);
  const recent = s.events.filter((e) => e.card_id === c.id && e.reason === 'PURCHASE' && !e.undone && now - e.at < 10 * 60_000);
  const last = [...s.events].reverse().find((e) => e.card_id === c.id);
  return {
    card_id: c.id,
    first_name: c.first_name,
    member_since: c.member_since,
    stamps_current: c.stamps,
    stamps_required: p.required,
    rewards,
    stamps_last_10_min: recent.reduce((a, e) => a + e.delta, 0),
    last_event: last ? { id: last.id, delta: last.delta, reason: last.reason, at: new Date(last.at).toISOString(), staff_name: last.staff } : null,
    voided: c.voided,
    program_slug: c.program,
    program_name: p.name,
    program_kind: p.kind,
    points_per_pound: p.perPound,
    reward_ready_label: p.ready,
    max_stamps_per_scan: 3,
    reward_options: p.options.map(({ id, name, description, max_price_pence, covers }) => ({ id, name, description, max_price_pence, covers })),
    other_cards: siblings && !c.voided ? mine.filter((x) => x.id !== c.id).map((x) => view(s, x, now, false)) : [],
    joinable: siblings && !c.voided ? SLUGS.filter((slug) => !mine.some((x) => x.program === slug)).map((slug) => ({ slug, name: PROGRAMS[slug].name, kind: PROGRAMS[slug].kind })) : [],
    lightspeed_linked: s.linked.includes(c.member),
  };
}

function addStamps(s: State, c: MCard, delta: number, reason: MEvent['reason'], staff: string, dev: string, now: number) {
  const ev: MEvent = { id: s.nextId++, card_id: c.id, delta, reason, at: now, staff, device: dev, undone: false };
  s.events.push(ev);
  c.stamps += delta;
  let issued = false;
  const p = PROGRAMS[c.program];
  while (c.stamps >= p.required) {
    c.stamps -= p.required;
    s.rewards.push({ id: s.nextId++, card_id: c.id, kind: 'STAMP_CARD', label: p.ready, expires_at: null, redeemed_at: null, redeemed_item: null, voided: false, issued_by_event: ev.id });
    issued = true;
  }
  return { ev, issued };
}

export async function mockFetch(method: string, path: string, body: unknown, headers: Record<string, string>): Promise<{ status: number; body: unknown }> {
  await wait(180);
  const s = load();
  const now = Date.now();
  const b = (body ?? {}) as Record<string, unknown>;
  const url = new URL(path, location.origin);
  const route = `${method} ${url.pathname}`;
  const devToken = headers['X-Staff-Device'] ?? '';
  const devName = s.devices[devToken];

  try {
    if (route === 'POST /api/staff/device/pair') {
      const code = String(b.pairing_code ?? '');
      if (!/^\d{6}$/.test(code)) return err(422, 'invalid', 'The pairing code is 6 digits.');
      if (code === '000000') return err(400, 'bad_pairing_code', 'That pairing code is wrong or has expired. Ask a manager for a new one.');
      const token = rand(24);
      s.devices[token] = String(b.device_name || 'Scanner');
      return ok({ device_token: token });
    }
    if (!devName) return err(401, 'unknown_device', 'This device is not paired, or it was removed. Pair it again.');

    if (route === 'POST /api/staff/login') {
      const u = USERS.find((x) => x.pin === String(b.pin ?? ''));
      if (!u) return err(401, 'bad_pin', "That PIN didn't match anyone. Try again.");
      const token = rand(24);
      const expires = now + 12 * 3600_000;
      s.sessions[token] = { user: u.id, device: devName, expires };
      return ok({ session_token: token, expires_at: new Date(expires).toISOString(), user: { id: u.id, name: u.name, role: u.role } });
    }

    const bearer = (headers.Authorization ?? '').replace(/^Bearer /, '');
    const sess = s.sessions[bearer];
    if (!sess || sess.expires < now) return err(401, 'session_expired', 'Your session has ended. Enter your PIN again.');
    const user = USERS.find((u) => u.id === sess.user)!;
    const cardById = (cid: unknown) => s.cards.find((c) => c.id === cid);

    switch (route) {
      case 'POST /api/staff/logout':
        delete s.sessions[bearer];
        return ok(null, 204);
      case 'GET /api/staff/me':
        return ok({ user: { id: user.id, name: user.name, role: user.role }, device: { id: 1, name: devName }, expires_at: new Date(sess.expires).toISOString() });
      case 'POST /api/staff/scan': {
        const m = /^SC1:([0-9a-f-]{36}):([0-9a-f]{8})$/i.exec(String(b.payload ?? '').trim());
        if (!m) return err(400, 'bad_signature', "This QR code isn't a valid Sasha's Corner card.");
        const c = cardById(m[1]);
        if (!c) return err(404, 'unknown_card', "We don't have this card. Ask the customer to open their card again, or join at /rewards.");
        if (m[2] !== SIG) return err(400, 'bad_signature', "This card's code doesn't check out. It may be a screenshot of an old or edited pass.");
        return ok(view(s, c, now));
      }
      case 'POST /api/staff/stamp': {
        const c = cardById(b.card_id);
        if (!c) return err(404, 'unknown_card', 'Card not found.');
        if (c.voided) return err(409, 'card_voided', 'This card has been deleted. It cannot take stamps.');
        const delta = Number(b.delta);
        if (![1, 2, 3].includes(delta)) return err(422, 'invalid', 'Stamps per scan are 1 to 3.');
        if (PROGRAMS[c.program].kind === 'POINTS') return err(409, 'points_card', `${PROGRAMS[c.program].name} is a points card: enter what they spent.`);
        const recent = view(s, c, now).stamps_last_10_min;
        if (recent + delta > 3) {
          if (!b.manager_pin) return err(409, 'manager_pin_required', `This card already had ${recent} stamp${recent === 1 ? '' : 's'} in the last 10 minutes. A manager's PIN is needed for more.`);
          const mgr = USERS.find((u) => u.pin === b.manager_pin && u.role !== 'staff');
          if (!mgr) return err(403, 'bad_manager_pin', "That isn't a manager's PIN.");
        }
        const { ev, issued } = addStamps(s, c, delta, 'PURCHASE', user.name, devName, now);
        return ok({ card: view(s, c, now), event_id: ev.id, undo_until: new Date(now + UNDO_MS).toISOString(), reward_issued: issued });
      }
      case 'POST /api/staff/migrate': {
        const c = cardById(b.card_id);
        if (!c) return err(404, 'unknown_card', 'Card not found.');
        if (c.migrated) return err(409, 'already_migrated', `A paper card was already added to ${c.first_name}'s card. Each card can take one paper card.`);
        const n = Number(b.paper_stamps);
        if (!(n >= 1 && n <= 7)) return err(422, 'invalid', 'A paper card has 1 to 7 stickers.');
        c.migrated = true;
        const { ev, issued } = addStamps(s, c, n, 'PAPER_MIGRATION', user.name, devName, now);
        return ok({ card: view(s, c, now), event_id: ev.id, undo_until: new Date(now + UNDO_MS).toISOString(), reward_issued: issued });
      }
      case 'POST /api/staff/redeem': {
        const r = s.rewards.find((x) => x.id === b.reward_id && !x.redeemed_at && !x.voided);
        if (!r) return err(404, 'unknown_reward', 'That reward has already been used.');
        const drink = DRINKS.find((d) => d.menu_item_id === b.menu_item_id);
        const opts = PROGRAMS[cardById(r.card_id)!.program].options;
        let opt = opts.find((o) => o.id === b.option_id) ?? (opts.length === 1 ? opts[0] : undefined);
        if (!opt && drink) opt = opts.find((o) => o.match.test(drink.name) && (!o.drinksOnly || drink.category !== 'Cakes'));
        if (!opt) return err(422, 'option_required', 'This card has several rewards to choose from: pick which one they are having.');
        if (drink && (!opt.match.test(drink.name) || (opt.drinksOnly && drink.category === 'Cakes')))
          return err(409, 'not_covered', `${opt.name} doesn't cover ${drink.name}: it covers ${opt.covers}.`);
        const cap = opt.max_price_pence;
        if (drink && cap !== null && drink.price_pence !== null && drink.price_pence > cap)
          return err(409, 'over_price_cap', `The reward covers up to £${(cap / 100).toFixed(2)}; ${drink.name} is £${(drink.price_pence / 100).toFixed(2)}.`);
        r.redeemed_at = now;
        r.redeemed_item = drink?.menu_item_id ?? null;
        return ok({ card: view(s, cardById(r.card_id)!, now), reward_id: r.id, undo_until: new Date(now + UNDO_MS).toISOString() });
      }
      case 'POST /api/staff/undo': {
        if (b.event_id) {
          const ev = s.events.find((e) => e.id === b.event_id);
          if (!ev || ev.undone) return err(409, 'undo_expired', 'That has already been undone.');
          if (now - ev.at > UNDO_MS) return err(409, 'undo_expired', 'Undo is only possible for 2 minutes. A manager can correct it from the Members page.');
          const c = cardById(ev.card_id)!;
          ev.undone = true;
          const issuedReward = s.rewards.find((r) => r.issued_by_event === ev.id && !r.redeemed_at && !r.voided);
          if (issuedReward) {
            issuedReward.voided = true;
            c.stamps += PROGRAMS[c.program].required;
          }
          c.stamps -= ev.delta;
          if (ev.reason === 'PAPER_MIGRATION') c.migrated = false;
          s.events.push({ id: s.nextId++, card_id: c.id, delta: -ev.delta, reason: 'UNDO', at: now, staff: user.name, device: devName, undone: false });
          return ok({ card: view(s, c, now) });
        }
        const r = s.rewards.find((x) => x.id === b.reward_id);
        if (!r || !r.redeemed_at) return err(409, 'undo_expired', 'That redemption has already been undone.');
        if (now - r.redeemed_at > UNDO_MS) return err(409, 'undo_expired', 'Undo is only possible for 2 minutes.');
        r.redeemed_at = null;
        r.redeemed_item = null;
        return ok({ card: view(s, cardById(r.card_id)!, now) });
      }
      case 'GET /api/staff/drinks': {
        const oid = Number(url.searchParams.get('option_id') ?? 0);
        const opt = SLUGS.flatMap((slug) => PROGRAMS[slug].options).find((o) => o.id === oid);
        if (!opt) return ok({ items: DRINKS.filter((d) => d.category !== 'Cakes') });
        const items = DRINKS.filter((d) => opt.match.test(d.name) && (opt.drinksOnly ? d.category !== 'Cakes' : true));
        return ok({ items, max_price_pence: opt.max_price_pence, covers: opt.covers });
      }
      case 'POST /api/staff/spend': {
        const c = cardById(b.card_id);
        if (!c) return err(404, 'unknown_card', 'Card not found.');
        const p = PROGRAMS[c.program];
        if (p.kind !== 'POINTS') return err(409, 'stamp_card', `${p.name} takes stamps, not a spend.`);
        const pence = Number(b.spend_pence);
        if (pence > 5000 && !b.manager_pin) return err(409, 'manager_pin_required', `£${(pence / 100).toFixed(2)} is more than £50.00; a manager's PIN confirms it is not a typo.`);
        if (b.manager_pin && !USERS.find((u) => u.pin === b.manager_pin && u.role !== 'staff')) return err(403, 'bad_manager_pin', "That isn't a manager's PIN.");
        const pts = Math.floor((pence * (p.perPound ?? 0)) / 100);
        const { ev, issued } = addStamps(s, c, pts, 'PURCHASE', user.name, devName, now);
        return ok({ card: view(s, c, now), event_id: ev.id, undo_until: new Date(now + UNDO_MS).toISOString(), reward_issued: issued });
      }
      case 'POST /api/staff/add-card': {
        const c = cardById(b.card_id);
        const slug = String(b.program) as Slug;
        if (!c || !PROGRAMS[slug]) return err(404, 'unknown_program', 'There is no rewards programme with that name.');
        if (s.cards.some((x) => x.member === c.member && x.program === slug)) return err(409, 'already_in_program', `There is already a ${PROGRAMS[slug].name} card for this member.`);
        const n = s.cards.length + 1;
        const card: MCard = { ...c, id: id(n), program: slug, stamps: 0, migrated: false, member_since: new Date(now).toISOString().slice(0, 10) };
        s.cards.push(card);
        return ok({ card: view(s, card, now) });
      }
      case 'GET /api/staff/pos-customers':
        return ok({ customers: CUSTOMERS });
      case 'POST /api/staff/link-pos': {
        const c = cardById(b.card_id);
        if (!c) return err(404, 'unknown_card', 'Card not found.');
        if (!b.customer_id && !b.receipt_id) return err(422, 'customer_required', 'Give a Lightspeed customer or receipt number.');
        if (!s.linked.includes(c.member)) s.linked.push(c.member);
        return ok({ card: view(s, c, now) });
      }
      case 'GET /api/staff/lookup': {
        const q = (url.searchParams.get('q') ?? '').trim().toLowerCase();
        const members: LookupMember[] = s.cards
          .filter((c) => !c.voided && c.program === 'stamp' && q.length >= 2 && (c.first_name.toLowerCase().includes(q) || c.contact.toLowerCase().includes(q) || q.replace(/\D/g, '').length >= 3))
          .map((c) => ({ card_id: c.id, first_name: c.first_name, contact_masked: c.contact }));
        return ok({ members });
      }
      case 'POST /api/staff/recovery-link': {
        const c = cardById(b.card_id);
        if (!c) return err(404, 'unknown_card', 'Card not found.');
        return ok({ url: `https://sashascorner.co.uk/c/${c.id}#t=${rand(16)}` });
      }
    }
    return err(404, 'not_found', `The mock has no ${route}.`);
  } finally {
    save(s);
  }
}
