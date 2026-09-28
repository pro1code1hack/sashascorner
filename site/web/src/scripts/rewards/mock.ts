// DEV ONLY. Canned answers for the loyalty API so every page state can be looked at
// without the backend. Loaded only when `import.meta.env.DEV` and `?mock=` are both
// present (see api.ts); a production build never includes this file.
//
// `?mock=` takes comma-separated flags:
//   /rewards      1 (plain) · exists (join -> 409) · email|sms|ask_staff (recover)
//                 nowallet (wallets not configured) · slow · down (API unreachable)
//                 verify with code 123456; anything else is bad_code
//   /c/<id>       s0..s7 (stamps) · ready · birthday · live (+1 stamp per poll)
//                 404 · 401 · down
//                 phase 3: clubs (a second card + one to join) · matcha (this IS the
//                 matcha club card) · points (a points card, 64/100)
//   /rewards      phase 3: clubs (the join form offers the other programmes)
import type { CardState, JoinResult, Program, PublicProgram } from './card';

const PROGRAMS: PublicProgram[] = [
  { slug: 'stamp', name: "Sasha's Corner Rewards", kind: 'STAMPS', description: null, stamps_required: 8, points_per_pound: null, reward_text: 'Any drink, on us', reward_ready_label: 'Free drink ready', is_default: true },
  { slug: 'matcha-club', name: 'Matcha club', kind: 'STAMPS', description: 'Every 6th matcha is free. Counts alongside your main card.', stamps_required: 6, points_per_pound: null, reward_text: 'A matcha of your choice, on us', reward_ready_label: 'Free matcha ready', is_default: false },
  { slug: 'cake-points', name: 'Cake points', kind: 'POINTS', description: '10 points for every pound. 100 points is a slice of cake.', stamps_required: 100, points_per_pound: 10, reward_text: 'A slice of cake, on us', reward_ready_label: 'Cake slice ready', is_default: false },
];

const ID = '3f2b8c1e-9a4d-4e7b-8c21-5d6f7a8b9c0d';
const TOKEN = 'mock-token-Zq3v8Xk2mPq9rT4sW1yB6nC0dE5fG7hJ8kL2mN4pQ';
let stamps: number | null = null;
let optIn = true;

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function mockApi(
  method: string,
  path: string,
  body: unknown,
  mock: string,
): Promise<{ status: number; body: unknown }> {
  const f = new Set(mock.split(','));
  await wait(f.has('slow') ? 1600 : 350);
  if (f.has('down')) throw new TypeError('mock: network down');
  const wallets = !f.has('nowallet');
  const join = (id = ID): JoinResult => ({
    card_id: id,
    token: TOKEN,
    web_card_url: `/c/${id}#t=${TOKEN}`,
    apple_pass_url: wallets ? `/api/loyalty/card/${id}/apple.pkpass?t=${TOKEN}` : null,
    google_save_url: wallets ? `/api/loyalty/card/${id}/google?t=${TOKEN}` : null,
  });

  if (path === '/api/loyalty/program') {
    const p: Program = {
      name: "Sasha's Corner Rewards",
      stamps_required: 8,
      reward_text: 'Any drink, on us',
      birthday_reward: true,
      referral_stamps: 1,
      max_stamps_per_scan: 3,
      wallets: { apple: wallets, google: wallets },
    };
    return { status: 200, body: p };
  }
  if (path === '/api/loyalty/programs') {
    return { status: 200, body: { programs: f.has('clubs') ? PROGRAMS : PROGRAMS.slice(0, 1) } };
  }
  if (path === '/api/loyalty/join') {
    if (f.has('exists')) return { status: 409, body: { error: 'already_member', detail: 'That contact already has a card.' } };
    const also = ((body as { also_join?: string[] }).also_join ?? []).map((slug, i) => {
      const cid = ID.replace(/0d$/, `1${i}`);
      return { program_slug: slug, card_id: cid, token: TOKEN, web_card_url: `/c/${cid}#t=${TOKEN}` };
    });
    return { status: 201, body: { ...join(), extra_cards: also } };
  }
  const jp = path.match(/^\/api\/loyalty\/card\/([^/?]+)\/programs$/);
  if (jp) {
    const cid = ID.replace(/0d$/, '2a');
    return { status: 201, body: join(cid) };
  }
  if (path === '/api/loyalty/recover') {
    const delivery = f.has('ask_staff') ? 'ask_staff' : f.has('sms') ? 'sms' : 'email';
    return { status: 202, body: { delivery } };
  }
  if (path === '/api/loyalty/recover/verify') {
    const code = (body as { code?: string }).code;
    return code === '123456'
      ? { status: 200, body: join() }
      : { status: 400, body: { error: 'bad_code', detail: 'That code is wrong or has expired.' } };
  }

  const m = path.match(/^\/api\/loyalty\/card\/([^/?]+)(\/preferences)?$/);
  if (m) {
    if (f.has('404')) return { status: 404, body: { error: 'not_found', detail: 'No such card.' } };
    if (f.has('401')) return { status: 401, body: { error: 'bad_token', detail: 'Wrong card token.' } };
    if (method === 'DELETE') return { status: 204, body: null };
    if (m[2] && method === 'PATCH') optIn = Boolean((body as { marketing_opt_in?: boolean }).marketing_opt_in);
    const s = [...f].find((x) => /^s\d$/.test(x));
    if (stamps === null) stamps = s ? Number(s.slice(1)) : 5;
    else if (f.has('live') && method === 'GET') stamps = (stamps + 1) % 8;
    const now = Date.now();
    const kind = f.has('points') ? PROGRAMS[2] : f.has('matcha') ? PROGRAMS[1] : PROGRAMS[0];
    const clubs = f.has('clubs') || f.has('matcha') || f.has('points');
    const state: CardState = {
      card_id: m[1],
      first_name: 'Olena',
      member_since: '2026-09-28',
      program_name: kind.name,
      stamps_required: kind.stamps_required,
      stamps_current: f.has('points') ? 64 : f.has('ready') ? 1 : stamps,
      reward_text: kind.reward_text,
      program_slug: kind.slug,
      program_kind: kind.kind,
      points_per_pound: kind.points_per_pound,
      reward_ready_label: kind.reward_ready_label,
      program_description: kind.description,
      other_cards: clubs
        ? PROGRAMS.filter((p) => p.slug !== kind.slug && p.slug !== 'cake-points').map((p, i) => ({
            card_id: ID.replace(/0d$/, `3${i}`),
            token: TOKEN,
            program_slug: p.slug,
            program_name: p.name,
            program_kind: p.kind,
            stamps_current: p.slug === 'stamp' ? 5 : 2,
            stamps_required: p.stamps_required,
            reward_ready: false,
            web_card_url: `/c/${ID.replace(/0d$/, `3${i}`)}?mock=${mock}#t=${TOKEN}`,
          }))
        : [],
      joinable: clubs && kind.slug !== 'cake-points' ? [PROGRAMS[2]] : [],
      rewards: [
        ...(f.has('ready') ? [{ id: 11, kind: 'STAMP_CARD', label: kind.reward_ready_label, expires_at: null }] : []),
        ...(f.has('birthday')
          ? [{ id: 12, kind: 'BIRTHDAY', label: 'Birthday drink', expires_at: new Date(now + 6 * 864e5).toISOString() }]
          : []),
      ],
      qr_payload: `SC1:${m[1]}:1a2b3c4d`,
      marketing_opt_in: optIn,
      updated_at: new Date(now).toISOString(),
      wallets: {
        apple_pass_url: wallets ? `/api/loyalty/card/${m[1]}/apple.pkpass?t=${TOKEN}` : null,
        google_save_url: wallets ? `/api/loyalty/card/${m[1]}/google?t=${TOKEN}` : null,
      },
    };
    return { status: 200, body: state };
  }
  return { status: 404, body: { error: 'not_found' } };
}
