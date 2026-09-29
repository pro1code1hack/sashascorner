// DEV ONLY. Canned answers for /api/shop/* so every screen can be looked at without
// the backend. Loaded only when `import.meta.env.DEV` and `?mock=` are both present
// (see api.ts); a production build never includes this file.
//
// `?mock=` takes comma-separated flags:
//   1          plain: shop open, both dining options, pay at counter only
//   closed     shop switched off (closed_message)
//   shut       outside ordering hours (open_now false, slots empty)
//   takeaway   eat in disabled
//   online     pay online available too
//   slow       every answer takes 1.6 s
//   down       the API is unreachable
//   signedin   X-Card-Token is treated as a valid member (Sasha, 5/8 stamps, a reward)
//   reorder    (with signedin) a past order whose lines include one gone and one sold out
//   empty      no banners, no upsells
//
// The catalogue is built from the site's real menu (src/data/menu.json), so category
// names, items and prices are the café's own. Photos, kcal, descriptions and option
// groups are invented for the mock only.
import menu from '../../data/menu.json';
import type {
  Catalogue,
  Category,
  Me,
  OptionGroup,
  OrderView,
  PlacedOrder,
  PlaceOrderBody,
  Product,
  Quote,
  QuoteBody,
  ShopConfig,
  SizeCode,
  SlotsResponse,
} from './types';

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

const DRINK_CATS = new Set([
  'autumn',
  'ube',
  'hot-drinks',
  'espresso',
  'seasonal-hot',
  'hot-matcha',
  'tea',
  'iced-coffee',
  'seasonal-iced',
  'iced-matcha',
  'bubble-tea',
  'milkshakes',
  'cold',
]);
const COFFEE_CATS = new Set(['espresso', 'iced-coffee', 'hot-drinks', 'autumn']);
const PHOTOS: Record<string, string> = {
  'hot-matcha': '/img/cafe/matcha-from-above.webp',
  'iced-matcha': '/img/cafe/blue-and-green-matcha.webp',
  cakes: '/img/cafe/muffin-and-cake.webp',
  'seasonal-hot': '/img/cafe/winter-lattes.webp',
  autumn: '/img/cafe/winter-lattes.webp',
};

const GROUPS: OptionGroup[] = [
  {
    id: 1,
    name: 'Milk',
    prompt: 'Choose your milk',
    kind: 'single',
    layout: 'tiles',
    required: true,
    min_select: 1,
    max_select: 1,
    collapsed: false,
    options: [
      { id: 11, name: 'Whole milk', description: null, price_delta_pence: 0, kcal: 182, is_default: true, available: true, photo_url: null },
      { id: 12, name: 'Skimmed milk', description: null, price_delta_pence: 0, kcal: 98, is_default: false, available: true, photo_url: null },
      { id: 13, name: 'Oat milk', description: null, price_delta_pence: 50, kcal: 130, is_default: false, available: true, photo_url: null },
      { id: 14, name: 'Almond milk', description: null, price_delta_pence: 50, kcal: 60, is_default: false, available: true, photo_url: null },
      { id: 15, name: 'Coconut milk', description: null, price_delta_pence: 50, kcal: 75, is_default: false, available: false, photo_url: null },
      { id: 16, name: 'Soya milk', description: null, price_delta_pence: 0, kcal: 110, is_default: false, available: true, photo_url: null },
    ],
  },
  {
    id: 2,
    name: 'Cup',
    prompt: null,
    kind: 'single',
    layout: 'tiles',
    required: true,
    min_select: 1,
    max_select: 1,
    collapsed: false,
    options: [
      { id: 21, name: 'Takeaway cup', description: null, price_delta_pence: 0, kcal: null, is_default: true, available: true, photo_url: null },
      { id: 22, name: 'Own cup', description: 'Bring it to the counter', price_delta_pence: -20, kcal: null, is_default: false, available: true, photo_url: null },
    ],
  },
  {
    id: 3,
    name: 'Coffee bean',
    prompt: null,
    kind: 'single',
    layout: 'photo_tiles',
    required: true,
    min_select: 1,
    max_select: 1,
    collapsed: false,
    options: [
      { id: 31, name: 'House blend', description: 'Chocolate and hazelnut, medium roast', price_delta_pence: 0, kcal: null, is_default: true, available: true, photo_url: '/img/cafe/window.webp' },
      { id: 32, name: 'Single origin', description: 'Bright and fruity. Changes monthly', price_delta_pence: 30, kcal: null, is_default: false, available: true, photo_url: '/img/cafe/interior.webp' },
      { id: 33, name: 'Decaf', description: 'Swiss water process', price_delta_pence: 0, kcal: null, is_default: false, available: true, photo_url: null },
    ],
  },
  {
    id: 4,
    name: 'Extras and syrups',
    prompt: 'Add extras and syrups',
    kind: 'multi',
    layout: 'tiles',
    required: false,
    min_select: 0,
    max_select: 3,
    collapsed: true,
    options: [
      { id: 41, name: 'Extra shot', description: null, price_delta_pence: 60, kcal: 2, is_default: false, available: true, photo_url: null },
      { id: 42, name: 'Caramel syrup', description: null, price_delta_pence: 50, kcal: 40, is_default: false, available: true, photo_url: null },
      { id: 43, name: 'Vanilla syrup', description: null, price_delta_pence: 50, kcal: 40, is_default: false, available: true, photo_url: null },
      { id: 44, name: 'Hazelnut syrup', description: null, price_delta_pence: 50, kcal: 40, is_default: false, available: true, photo_url: null },
      { id: 45, name: 'Whipped cream', description: null, price_delta_pence: 50, kcal: 90, is_default: false, available: true, photo_url: null },
    ],
  },
  {
    id: 5,
    name: 'Customise',
    prompt: null,
    kind: 'multi',
    layout: 'checklist',
    required: false,
    min_select: 0,
    max_select: null,
    collapsed: false,
    options: [
      { id: 51, name: 'Extra hot', description: null, price_delta_pence: 0, kcal: null, is_default: false, available: true, photo_url: null },
      { id: 52, name: 'No lid', description: null, price_delta_pence: 0, kcal: null, is_default: false, available: true, photo_url: null },
      { id: 53, name: 'Cinnamon dusting', description: null, price_delta_pence: 0, kcal: null, is_default: false, available: true, photo_url: null },
      { id: 54, name: 'Chocolate dusting', description: null, price_delta_pence: 0, kcal: null, is_default: false, available: true, photo_url: null },
    ],
  },
];

const KCAL: Record<SizeCode, number> = { S: 244, M: 339, XL: 420, ONE: 310 };

function build(flags: Set<string>): Catalogue {
  const categories: Category[] = [];
  const products: Product[] = [];
  let pid = 100;
  let mid = 1000;
  const cakeIds: number[] = [];
  for (const [ci, c] of menu.categories.entries()) {
    categories.push({
      id: ci + 1,
      slug: c.slug,
      name: c.name,
      blurb: c.blurb ?? null,
      photo_url: PHOTOS[c.slug] ?? null,
      // Every second photo is "borrowed" from the category's first product (Agent F's flag).
      photo_is_fallback: !!PHOTOS[c.slug] && ci % 2 === 1,
      product_count: c.items.length,
    });
    const drink = DRINK_CATS.has(c.slug);
    for (const [ii, it] of c.items.entries()) {
      const id = ++pid;
      const sizes = it.sizes.map((s) => {
        const code = (s.code === 'One' ? 'ONE' : s.code) as SizeCode;
        return { menu_item_id: ++mid, code, label: s.code === 'One' ? '' : s.label, price_pence: s.price_pence, kcal: drink ? KCAL[code] : null };
      });
      const groups = drink
        ? [GROUPS[0], GROUPS[1], ...(COFFEE_CATS.has(c.slug) ? [GROUPS[2]] : []), GROUPS[3], GROUPS[4]]
        : [];
      const kcalBySize = drink && sizes.length > 1 ? Object.fromEntries(sizes.map((s) => [s.code, s.kcal])) : null;
      const first = ii === 0;
      const p: Product = {
        id,
        slug: it.id,
        name: it.name,
        category_slug: c.slug,
        description: it.description ?? (first ? 'Made to order at the counter. Ask us for it hot or iced.' : null),
        note: it.note ?? (c.slug === 'autumn' && first ? 'Dairy-free option not available: the pumpkin sauce contains dairy.' : null),
        kcal: drink ? KCAL[sizes[0]?.code ?? 'ONE'] : c.slug === 'cakes' ? 420 : null,
        kcal_by_size: kcalBySize as Product['kcal_by_size'],
        nutrition: first ? { energy_kj: '1418', fat_g: '12', saturates_g: '7', carbs_g: '40', sugars_g: '36', protein_g: '9', salt_g: null } : null,
        allergens: drink ? ['milk'] : c.slug === 'cakes' ? ['milk', 'gluten', 'egg'] : [],
        // Agent F's honesty flag: drinks and cakes are filled in, the third item of every
        // other category is checked and has none, the rest are not listed yet.
        allergens_state: drink || c.slug === 'cakes' ? 'listed' : ii === 2 ? 'none' : 'unknown',
        dietary: drink ? ['vegetarian', 'contains-caffeine', 'decaf-available'] : c.slug === 'cakes' ? ['vegetarian'] : [],
        ingredients_text: first && drink ? 'Espresso, milk, syrup. Whipped cream on request.' : null,
        photo_url: first ? (PHOTOS[c.slug] ?? null) : null,
        badge: c.slug === 'autumn' ? 'Back for autumn' : c.slug === 'ube' && first ? 'New' : null,
        available: !(c.slug === 'cakes' && ii === 2),
        featured: first,
        from_price_pence: Math.min(...sizes.map((s) => s.price_pence)),
        default_size: sizes.some((s) => s.code === 'M') ? 'M' : (sizes[0]?.code ?? 'ONE'),
        sizes,
        option_groups: groups,
        upsells: [],
      };
      products.push(p);
      if (c.slug === 'cakes') cakeIds.push(id);
    }
  }
  if (!flags.has('empty')) {
    for (const p of products) if (DRINK_CATS.has(p.category_slug ?? '')) p.upsells = [{ heading: 'Fancy a cake?', product_ids: cakeIds.slice(0, 4) }];
  }
  return {
    generated_at: new Date().toISOString(),
    version: 'mock-1',
    banners: flags.has('empty')
      ? []
      : [
          { id: 1, title: 'Autumn is here', subtitle: 'Pumpkin spice, banana bread hojicha and brown sugar bubble tea.', photo_url: '/img/cafe/winter-lattes.webp', link_href: '/order/c/autumn' },
          { id: 2, title: 'Stamps count online too', subtitle: 'Sign in with your Rewards card and every drink you collect is stamped.', photo_url: '/img/cafe/interior.webp', link_href: '/order/account' },
          { id: 3, title: 'Matcha whisked to order', subtitle: null, photo_url: '/img/cafe/matcha-from-above.webp', link_href: '/order/c/hot-matcha' },
        ],
    categories,
    products,
    basket_upsells: flags.has('empty') ? [] : [{ heading: 'Fancy a cake?', product_ids: cakeIds.slice(0, 4) }],
  };
}

function configFor(flags: Set<string>): ShopConfig {
  return {
    enabled: !flags.has('closed'),
    closed_message: 'Online ordering is taking a short break. Order at the counter.',
    hero_title: 'Order ahead. Collect from Commercial Street.',
    hero_subtitle: "Takeaway made to order. Skip the queue: pay here or at the counter, we'll have it ready.",
    dining: { takeaway: true, eat_in: !flags.has('takeaway'), default: 'takeaway' },
    pay: { counter: true, online: flags.has('online') },
    kcal_notice: 'Adults need around 2000 kcal a day.',
    allergen_notice:
      'Our drinks and food are prepared in a small kitchen where milk, gluten, nuts, soya, egg, sesame and sulphites are all handled, so we cannot guarantee any item is free from them. If you have an allergy or intolerance, please tell us at the counter before you collect.',
    collection_note: 'Collect from the counter at 23 Commercial Street. Say your name or show the order code.',
    terms_url: '/privacy',
    open_now: !flags.has('shut'),
    next_open_local: flags.has('shut') ? 'Tomorrow 09:00' : null,
    cafe: { name: "Sasha's Corner", address_line: '23 Commercial Street', postcode: 'DD1 3DD', phone: '07398 433317' },
    loyalty: { program_name: "Sasha's Corner Rewards", stamps_required: 8, reward_text: 'Any drink, on us' },
  };
}

let cat: Catalogue | null = null;
const orders = new Map<string, { view: OrderView; token: string; placedAt: number; lines: QuoteBody['lines'] }>();

function price(cat: Catalogue, body: QuoteBody, withReward: boolean): Quote {
  const lines = body.lines.map((l) => {
    const p = cat.products.find((x) => x.id === l.product_id);
    const size = p?.sizes.find((s) => s.menu_item_id === l.menu_item_id);
    const opts = p ? p.option_groups.flatMap((g) => g.options.filter((o) => l.option_ids.includes(o.id)).map((o) => ({ group: g.name, name: o.name, price_delta_pence: o.price_delta_pence }))) : [];
    const problems: string[] = [];
    if (!p || !size) problems.push('This item is no longer on the menu.');
    else if (!p.available) problems.push('Sold out today.');
    const unit = (size?.price_pence ?? 0) + opts.reduce((s, o) => s + o.price_delta_pence, 0);
    return { ...l, name: p?.name ?? 'Item', size_label: size?.label ?? '', unit_price_pence: unit, line_total_pence: unit * l.qty, options: opts, problems };
  });
  const subtotal = lines.reduce((s, l) => s + l.line_total_pence, 0);
  let discount = 0;
  let idx: number | null = null;
  if (withReward) {
    lines.forEach((l, i) => {
      const p = cat.products.find((x) => x.id === l.product_id);
      if (p && DRINK_CATS.has(p.category_slug ?? '') && l.unit_price_pence > discount) {
        discount = l.unit_price_pence;
        idx = i;
      }
    });
  }
  return {
    lines,
    subtotal_pence: subtotal,
    discount_pence: discount,
    total_pence: subtotal - discount,
    reward: { applied: idx !== null, line_index: idx, text: idx !== null ? 'Free drink: any drink, on us' : null },
    problems: lines.flatMap((l) => l.problems),
  };
}

const CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
const code = () => 'SC-' + Array.from({ length: 6 }, () => CODE_CHARS[Math.floor(Math.random() * CODE_CHARS.length)]).join('');

const STATUS_LABEL: Record<string, string> = {
  PENDING_PAYMENT: 'Waiting for payment',
  NEW: 'Received',
  ACCEPTED: 'Accepted',
  PREPARING: 'Being made',
  READY: 'Ready to collect',
  COLLECTED: 'Collected',
  CANCELLED: 'Cancelled',
  REJECTED: 'Not accepted',
};
const STEP: Record<string, number> = { PENDING_PAYMENT: 0, NEW: 0, ACCEPTED: 1, PREPARING: 2, READY: 3, COLLECTED: 4, CANCELLED: 0, REJECTED: 0 };

export async function mockApi(
  method: string,
  path: string,
  body: unknown,
  mock: string,
  token: string | null,
): Promise<{ status: number; body: unknown }> {
  const f = new Set(mock.split(','));
  await wait(f.has('slow') ? 1600 : 250);
  if (f.has('down')) throw new TypeError('mock: network down');
  cat ??= build(f);
  const cfg = configFor(f);
  const member = f.has('signedin') && !!token;
  const url = new URL(path, location.origin);
  const p = url.pathname;

  if (p === '/api/shop/config') return { status: 200, body: cfg };
  if (p === '/api/shop/catalogue') return { status: 200, body: cat };
  if (p === '/api/shop/slots') {
    const now = new Date();
    const date = url.searchParams.get('date') ?? now.toISOString().slice(0, 10);
    if (f.has('shut')) return { status: 200, body: { date, open: false, reason: 'Online orders are closed for today. Back tomorrow at 09:00.', asap: { available: false, at: '', local: '' }, slots: [], days: [date] } satisfies SlotsResponse };
    const slots: SlotsResponse['slots'] = [];
    const start = new Date(now.getTime() + 10 * 60_000);
    start.setMinutes(Math.ceil(start.getMinutes() / 10) * 10, 0, 0);
    for (let i = 0; i < 18; i++) {
      const at = new Date(start.getTime() + i * 10 * 60_000);
      slots.push({ at: at.toISOString(), local: at.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }), available: i !== 3 });
    }
    return { status: 200, body: { date, open: true, reason: null, asap: { available: true, at: slots[0].at, local: slots[0].local }, slots, days: [date] } satisfies SlotsResponse };
  }
  if (p === '/api/shop/quote' && method === 'POST') {
    const b = body as QuoteBody;
    return { status: 200, body: price(cat, b, member && b.reward) };
  }
  if (p === '/api/shop/orders' && method === 'POST') {
    if (!cfg.enabled) return { status: 403, body: { error: 'shop_closed', detail: cfg.closed_message } };
    const b = body as PlaceOrderBody;
    const q = price(cat, b, member && b.reward);
    if (q.problems.length) return { status: 422, body: { error: 'invalid_request', detail: q.problems[0] } };
    if (q.total_pence !== b.expected_total_pence) return { status: 409, body: { error: 'price_changed', detail: 'Prices changed.', quote: q } };
    const c = code();
    const t = 'mock-order-token-' + c;
    const at = b.asap ? new Date(Date.now() + 15 * 60_000) : new Date(b.requested_at ?? Date.now());
    const online = b.payment === 'online';
    const view: OrderView = {
      code: c,
      status: online ? 'PENDING_PAYMENT' : 'NEW',
      status_label: STATUS_LABEL[online ? 'PENDING_PAYMENT' : 'NEW'],
      status_step: 0,
      dining: b.dining,
      table: b.dining === 'eat_in' ? (b.table ?? null) : null,
      asap: b.asap,
      requested_at: at.toISOString(),
      requested_local: at.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
      placed_at: new Date().toISOString(),
      ready_at: null,
      collected_at: null,
      customer: { name: b.customer.name },
      lines: q.lines.map((l) => ({ name: l.name, size_label: l.size_label, qty: l.qty, unit_price_pence: l.unit_price_pence, line_total_pence: l.line_total_pence, options: l.options })),
      subtotal_pence: q.subtotal_pence,
      discount_pence: q.discount_pence,
      total_pence: q.total_pence,
      payment: { method: b.payment, status: 'UNPAID' },
      collection_note: cfg.collection_note,
      cancel_allowed: true,
      events: [{ at: new Date().toISOString(), kind: 'placed', detail: null }],
    };
    orders.set(c, { view, token: t, placedAt: Date.now(), lines: b.lines });
    const placed: PlacedOrder = {
      code: c,
      status: view.status,
      access_token: t,
      total_pence: q.total_pence,
      requested_at: view.requested_at,
      requested_local: view.requested_local,
      payment: { method: b.payment, status: 'UNPAID', checkout_url: online ? `/order/status/${c}?t=${t}&paid=1` : null },
    };
    return { status: 201, body: placed };
  }
  const om = p.match(/^\/api\/shop\/orders\/([^/]+)(\/cancel)?$/);
  if (om) {
    const o = orders.get(decodeURIComponent(om[1]));
    if (!o) return { status: 404, body: { error: 'not_found', detail: "We can't find that order." } };
    if (om[2] && method === 'POST') {
      if (o.view.status !== 'NEW' && o.view.status !== 'PENDING_PAYMENT') return { status: 409, body: { error: 'bad_transition', detail: 'This order is already being made and can no longer be cancelled.' } };
      o.view = { ...o.view, status: 'CANCELLED', status_label: STATUS_LABEL.CANCELLED, cancel_allowed: false, events: [...o.view.events, { at: new Date().toISOString(), kind: 'cancelled', detail: 'By you' }] };
      return { status: 200, body: o.view };
    }
    // The order advances one step every 40 s so the status page has something to show.
    const steps = ['NEW', 'ACCEPTED', 'PREPARING', 'READY', 'COLLECTED'] as const;
    if (o.view.status !== 'CANCELLED' && o.view.status !== 'PENDING_PAYMENT') {
      const i = Math.min(4, Math.floor((Date.now() - o.placedAt) / 40_000));
      const s = steps[i];
      if (s !== o.view.status) {
        o.view = { ...o.view, status: s, status_label: STATUS_LABEL[s], status_step: STEP[s], cancel_allowed: s === 'NEW', ready_at: i >= 3 ? new Date().toISOString() : o.view.ready_at, events: [...o.view.events, { at: new Date().toISOString(), kind: s.toLowerCase(), detail: null }] };
      }
    }
    if (o.view.status === 'PENDING_PAYMENT' && url.searchParams.get('paid') === '1') {
      o.view = { ...o.view, status: 'NEW', status_label: STATUS_LABEL.NEW, payment: { method: 'online', status: 'PAID' }, events: [...o.view.events, { at: new Date().toISOString(), kind: 'paid', detail: null }] };
    }
    return { status: 200, body: o.view };
  }
  if (p === '/api/shop/me') {
    if (!member) return { status: 401, body: { error: 'bad_token', detail: 'Sign in again.' } };
    const me: Me = {
      first_name: 'Sasha',
      email: 'sasha@example.com',
      phone: '+447700900123',
      card_id: '3f2b8c1e-9a4d-4e7b-8c21-5d6f7a8b9c0d',
      stamps_current: 5,
      stamps_required: 8,
      reward: f.has('reward') ? { id: 1, text: 'Any drink, on us' } : null,
      recent_orders: [
        ...[...orders.values()].map((o) => ({ code: o.view.code, status: o.view.status, placed_at: o.view.placed_at, total_pence: o.view.total_pence, lines: o.lines, reorder_complete: true })),
        // A past order with a line that is gone (`reorder_complete: false`) and one sold out.
        ...(f.has('reorder')
          ? [
              {
                code: 'PASTONE',
                status: 'COLLECTED' as const,
                placed_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
                total_pence: 1210,
                lines: [
                  { product_id: cat.products[0].id, menu_item_id: cat.products[0].sizes[0].menu_item_id, qty: 2, option_ids: [13] },
                  { product_id: 999_999, menu_item_id: 999_999, qty: 1, option_ids: [] },
                  ...(cat.products.find((x) => !x.available) ? [{ product_id: cat.products.find((x) => !x.available)!.id, menu_item_id: cat.products.find((x) => !x.available)!.sizes[0].menu_item_id, qty: 1, option_ids: [] }] : []),
                ],
                reorder_complete: false,
              },
            ]
          : []),
      ],
    };
    return { status: 200, body: me };
  }
  return { status: 404, body: { error: 'not_found', detail: `mock: no route for ${method} ${p}` } };
}
