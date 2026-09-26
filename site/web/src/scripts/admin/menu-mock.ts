// In-memory stand-in for the website menu admin endpoints (`?mock=1`), built from
// the static menu.json so it looks like the real menu. Follows site/ADMIN.md.
//   ?mock=1&board=1   the "board" source (read-only screen)
//   ?mock=1&fail=1    every write fails, to see the failed-save states
import { menu } from '../../lib/data';
import type { MenuApi, OrderBody } from './menu-api';
import { normMenu } from './menu-api';

const q = new URLSearchParams(location.search);
const BOARD = q.has('board');
const FAIL = q.has('fail');
const FOOD = new Set(['breakfast', 'lunch', 'waffles', 'cakes']);

const wait = (ms = 250 + Math.random() * 350) => new Promise((r) => setTimeout(r, ms));

const state = {
  source: BOARD ? 'board' : 'ops',
  warnings: [] as string[],
  categories: menu.categories.map((c, ci) => ({
    name: c.name,
    slug: c.slug,
    kind: FOOD.has(c.slug) ? 'FOOD' : 'DRINKS',
    blurb: c.blurb ?? '',
    hidden: false,
    position: ci,
    items: c.items.map((i, ii) => ({
      key: i.name,
      name: i.name,
      sizes: i.sizes,
      seasonal: i.seasonal ?? null,
      ops_note: i.note ?? (ii === 1 ? 'Made with our house syrup.' : null),
      web: { description: i.description ?? null, signature: !!i.signature, hidden: false, position: ii, use_ops_note: !i.description && !!(i.note ?? ii === 1) },
    })),
  })),
  unassigned: BOARD
    ? []
    : [
        { key: 'Pistachio latte', name: 'Pistachio latte', sizes: [{ code: 'M', label: 'Medium', price_pence: 420 }], seasonal: null, web: { description: null, signature: false, hidden: false, position: null } },
        { key: 'Cheese toastie', name: 'Cheese toastie', sizes: [{ code: 'One', label: 'One size', price_pence: 550 }], seasonal: null, web: { description: null, signature: false, hidden: false, position: null } },
      ],
  drift: {
    price_mismatches: [
      { board_name: 'Latte', ops_name: 'Latte', size: 'XL', board_pence: 380, ops_pence: 400 },
      { board_name: 'Kyiv cake', ops_name: 'Kyiv Cake', size: 'One', board_pence: 400, ops_pence: null },
    ],
    board_only: ['Iced banana bread hojicha  [autumn]', 'Brunch waffle'],
    ops_only: ['Pistachio latte'],
  },
};

type Cat = (typeof state.categories)[number];
type It = Cat['items'][number];

function fail() {
  if (FAIL) throw Object.assign(new Error('Practice mode: this save was made to fail (?fail=1).'), { status: 500 });
  if (BOARD) throw Object.assign(new Error('The menu comes from the boards file, so it cannot be edited here yet.'), { status: 409 });
}

function findItem(key: string): It | undefined {
  for (const c of state.categories) for (const i of c.items) if (i.key === key) return i;
  return undefined;
}

export const mockMenuApi: MenuApi = {
  async load() {
    await wait();
    return normMenu(JSON.parse(JSON.stringify(state)));
  },
  async putCategory(slug, body) {
    await wait();
    fail();
    const c = state.categories.find((x) => x.slug === slug);
    if (!c) throw Object.assign(new Error('That category is not there any more.'), { status: 404 });
    if (body.blurb !== undefined) c.blurb = body.blurb ?? '';
    if (body.hidden !== undefined) c.hidden = body.hidden;
  },
  async putItem(key, body) {
    await wait();
    fail();
    const i = findItem(key);
    if (!i) throw Object.assign(new Error('That item is not there any more.'), { status: 404 });
    if (body.description !== undefined) i.web.description = body.description;
    if (body.signature !== undefined) i.web.signature = body.signature;
    if (body.hidden !== undefined) i.web.hidden = body.hidden;
    if (body.use_ops_note !== undefined) i.web.use_ops_note = body.use_ops_note;
  },
  async order(body: OrderBody) {
    await wait();
    fail();
    const bySlug = new Map(state.categories.map((c) => [c.slug, c]));
    state.categories = body.categories.map((s) => bySlug.get(s)).filter((c): c is Cat => !!c);
    state.categories.forEach((c, ci) => {
      c.position = ci;
      const want = body.items[c.slug];
      if (!want) return;
      const byKey = new Map(c.items.map((i) => [i.key, i]));
      c.items = want.map((k) => byKey.get(k)).filter((i): i is It => !!i);
      c.items.forEach((i, ii) => (i.web.position = ii));
    });
  },
};
