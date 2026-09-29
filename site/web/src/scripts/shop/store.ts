// App state: the catalogue and config (signals), and the basket, persisted on this
// device under 'sc.shop.basket.v1'. When the catalogue's `version` changes the basket
// is re-priced implicitly (prices are always read from the current catalogue) and any
// line whose product, size or option has gone is dropped, with a notice for the customer.
import { signal, type Signal } from '@preact/signals';
import { shopApi, shopError } from './api';
import type { Catalogue, Dining, Option, OptionGroup, Product, QuoteLineIn, ShopConfig, Size } from './types';

export interface BasketLine {
  product_id: number;
  menu_item_id: number;
  qty: number;
  option_ids: number[];
}

const BASKET_KEY = 'sc.shop.basket.v1';
const CATALOGUE_KEY = 'sc.shop.catalogue.v1';

export const catalogue: Signal<Catalogue | null> = signal<Catalogue | null>(null);
export const config: Signal<ShopConfig | null> = signal<ShopConfig | null>(null);
/** Why the catalogue or config could not be loaded, in words; null while fine. */
export const loadError: Signal<string | null> = signal<string | null>(null);
/** True while loadCatalogue() is in flight (skeletons read this, not `catalogue === null`). */
export const loading: Signal<boolean> = signal<boolean>(false);
/** `navigator.onLine`, kept current. Offline, the basket stays editable; nothing is fetched. */
export const online: Signal<boolean> = signal<boolean>(typeof navigator === 'undefined' ? true : navigator.onLine !== false);
if (typeof window !== 'undefined') {
  window.addEventListener('online', () => {
    online.value = true;
    // Back on the network: whatever failed while offline is tried again.
    if (loadError.value || !config.value || !catalogue.value) void loadCatalogue();
  });
  window.addEventListener('offline', () => (online.value = false));
}

// ---- persistence -------------------------------------------------------------------
interface Persisted {
  dining: Dining;
  lines: BasketLine[];
  version: string | null;
}
function readBasket(): Persisted {
  try {
    const raw = localStorage.getItem(BASKET_KEY);
    if (!raw) return { dining: 'takeaway', lines: [], version: null };
    const p = JSON.parse(raw) as Partial<Persisted>;
    const lines = Array.isArray(p.lines) ? p.lines.filter(isLine) : [];
    return { dining: p.dining === 'eat_in' ? 'eat_in' : 'takeaway', lines, version: typeof p.version === 'string' ? p.version : null };
  } catch {
    return { dining: 'takeaway', lines: [], version: null };
  }
}
function isLine(x: unknown): x is BasketLine {
  const l = x as BasketLine;
  return (
    !!l &&
    Number.isInteger(l.product_id) &&
    Number.isInteger(l.menu_item_id) &&
    Number.isInteger(l.qty) &&
    l.qty > 0 &&
    Array.isArray(l.option_ids) &&
    l.option_ids.every((n) => Number.isInteger(n))
  );
}
let knownVersion: string | null = null;
function writeBasket() {
  try {
    const p: Persisted = { dining: basket.dining.value, lines: basket.lines.value, version: knownVersion };
    localStorage.setItem(BASKET_KEY, JSON.stringify(p));
  } catch {
    /* storage off: the basket lives for this visit only */
  }
}

// A sentence for the next screen when it is a full page load away (the /account
// island's "Order again" lands on /order/basket): kept for one visit in sessionStorage.
const NOTICE_KEY = 'sc.shop.notice.v1';
function takeStashed(key: string): string | null {
  try {
    const v = sessionStorage.getItem(key);
    if (v) sessionStorage.removeItem(key);
    return v;
  } catch {
    return null;
  }
}
/** Shows `text` as the basket notice on the next load of the shop (see `basket.notice`). */
export function stashNotice(text: string): void {
  try {
    sessionStorage.setItem(NOTICE_KEY, text);
  } catch {
    /* storage off: the notice is lost, the basket itself is right */
  }
}

const initial = readBasket();
knownVersion = initial.version;
const MAX_QTY = 20;
const clampQty = (n: number) => Math.min(MAX_QTY, Math.max(1, Math.round(n)));
const cleanLine = (l: BasketLine): BasketLine => ({
  product_id: l.product_id,
  menu_item_id: l.menu_item_id,
  qty: clampQty(l.qty),
  option_ids: [...new Set(l.option_ids)],
});

export const basket = {
  lines: signal<BasketLine[]>(initial.lines),
  dining: signal<Dining>(initial.dining),
  /** A sentence for the customer after the catalogue changed under their basket. */
  notice: signal<string | null>(takeStashed(NOTICE_KEY)),
  MAX_QTY,
  add(line: BasketLine): void {
    const l = cleanLine(line);
    // The same product, size and options again: one line, more of it.
    const i = basket.lines.value.findIndex((x) => sameLine(x, l));
    if (i >= 0) basket.update(i, { ...l, qty: basket.lines.value[i].qty + l.qty });
    else basket.lines.value = [...basket.lines.value, l];
    writeBasket();
  },
  update(index: number, line: BasketLine): void {
    basket.lines.value = basket.lines.value.map((x, i) => (i === index ? cleanLine(line) : x));
    writeBasket();
  },
  remove(index: number): void {
    basket.lines.value = basket.lines.value.filter((_, i) => i !== index);
    writeBasket();
  },
  duplicate(index: number): void {
    const l = basket.lines.value[index];
    if (!l) return;
    const next = [...basket.lines.value];
    next.splice(index + 1, 0, { ...l, option_ids: [...l.option_ids] });
    basket.lines.value = next;
    writeBasket();
  },
  clear(): void {
    basket.lines.value = [];
    basket.notice.value = null;
    writeBasket();
  },
  setDining(d: Dining): void {
    basket.dining.value = d;
    writeBasket();
  },
  count(): number {
    return basket.lines.value.reduce((n, l) => n + l.qty, 0);
  },
  subtotal(cat: Catalogue | null): number {
    if (!cat) return 0;
    return basket.lines.value.reduce((sum, l) => sum + lineTotal(l, cat), 0);
  },
};

const sameLine = (a: BasketLine, b: BasketLine) =>
  a.product_id === b.product_id &&
  a.menu_item_id === b.menu_item_id &&
  a.option_ids.length === b.option_ids.length &&
  [...a.option_ids].sort().join() === [...b.option_ids].sort().join();

// ---- lookups and pricing (display only: the server prices at placement, §3.3) -----
export function productById(cat: Catalogue, id: number): Product | undefined {
  return cat.products.find((p) => p.id === id);
}
export function productBySlug(cat: Catalogue, slug: string): Product | undefined {
  return cat.products.find((p) => p.slug === slug);
}
export function sizeOf(product: Product, menuItemId: number): Size | undefined {
  return product.sizes.find((s) => s.menu_item_id === menuItemId);
}
export function optionsOf(product: Product, ids: number[]): { group: OptionGroup; option: Option }[] {
  const out: { group: OptionGroup; option: Option }[] = [];
  for (const group of product.option_groups)
    for (const option of group.options) if (ids.includes(option.id)) out.push({ group, option });
  return out;
}
export function unitPrice(line: BasketLine, cat: Catalogue): number {
  const p = productById(cat, line.product_id);
  if (!p) return 0;
  const size = sizeOf(p, line.menu_item_id);
  const base = size?.price_pence ?? 0;
  return base + optionsOf(p, line.option_ids).reduce((s, o) => s + o.option.price_delta_pence, 0);
}
export function lineTotal(line: BasketLine, cat: Catalogue): number {
  return unitPrice(line, cat) * line.qty;
}

// ---- reconcile after a catalogue change --------------------------------------------
/** Drops lines the catalogue no longer supports; returns what was dropped, by name. */
export function reconcileBasket(cat: Catalogue): string[] {
  const dropped: string[] = [];
  const kept: BasketLine[] = [];
  for (const l of basket.lines.value) {
    const p = productById(cat, l.product_id);
    const size = p && sizeOf(p, l.menu_item_id);
    const allOptions = p ? p.option_groups.flatMap((g) => g.options) : [];
    const optionsOk = p && l.option_ids.every((id) => allOptions.some((o) => o.id === id && o.available));
    if (!p || !size || !p.available || !optionsOk) {
      dropped.push(p ? p.name : 'an item');
      continue;
    }
    kept.push(l);
  }
  if (dropped.length) {
    basket.lines.value = kept;
    const names = [...new Set(dropped)];
    basket.notice.value =
      names.length === 1
        ? `${names[0]} is no longer available and was removed from your order.`
        : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]} are no longer available and were removed from your order.`;
  }
  knownVersion = cat.version;
  writeBasket();
  return dropped;
}

// ---- order again -------------------------------------------------------------------
/**
 * Re-adds a past order's lines through the basket, against today's catalogue: a product
 * that is gone or sold out is left out (named), an option that is gone or sold out is
 * dropped from its line, and a required group left empty gets its default. Returns what
 * was added and what was left out, so the screen can say so.
 */
export function reorder(cat: Catalogue, lines: QuoteLineIn[]): { added: number; leftOut: string[]; unknown: number; changed: string[] } {
  let added = 0;
  let unknown = 0;
  const leftOut: string[] = [];
  const changed: string[] = [];
  for (const l of lines) {
    const p = productById(cat, l.product_id);
    if (!p) {
      unknown += 1;
      continue;
    }
    if (!p.available || !sizeOf(p, l.menu_item_id)) {
      leftOut.push(p.name);
      continue;
    }
    const all = p.option_groups.flatMap((g) => g.options);
    let option_ids = (l.option_ids ?? []).filter((id) => all.some((o) => o.id === id && o.available));
    let altered = option_ids.length !== (l.option_ids ?? []).length;
    let ok = true;
    for (const g of p.option_groups) {
      const need = Math.max(g.min_select, g.required ? 1 : 0);
      const mine = g.options.filter((o) => option_ids.includes(o.id));
      if (mine.length >= need) continue;
      const defaults = g.options.filter((o) => o.is_default && o.available && !option_ids.includes(o.id)).map((o) => o.id);
      const fill = defaults.slice(0, need - mine.length);
      if (fill.length < need - mine.length) {
        ok = false;
        break;
      }
      option_ids = [...option_ids, ...fill];
      altered = true;
    }
    if (!ok) {
      leftOut.push(p.name);
      continue;
    }
    basket.add({ product_id: p.id, menu_item_id: l.menu_item_id, qty: l.qty, option_ids });
    added += l.qty;
    if (altered) changed.push(p.name);
  }
  return { added, leftOut: [...new Set(leftOut)], unknown, changed: [...new Set(changed)] };
}

/** "Oat latte, Kyiv cake and Brownie" */
export function listNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

// ---- loading -----------------------------------------------------------------------
function readCachedCatalogue(): Catalogue | null {
  try {
    const raw = localStorage.getItem(CATALOGUE_KEY);
    return raw ? (JSON.parse(raw) as Catalogue) : null;
  } catch {
    return null;
  }
}
function cacheCatalogue(c: Catalogue) {
  try {
    localStorage.setItem(CATALOGUE_KEY, JSON.stringify(c));
  } catch {
    /* fine */
  }
}

/**
 * Words for a config answer that is not 2xx. A network failure, timeout or 5xx already
 * read right; a 4xx (a 422 mid-deploy was seen) must not surface as "check the form":
 * to the customer it is the same thing, the shop cannot be reached right now.
 */
function configError(r: { status: number; error: { error: string; detail?: string } | null }): string {
  if (r.status === 0 || r.status >= 500) return shopError(r);
  if (r.status === 429) return shopError(r);
  // The overview's block already says "We can't reach the shop right now."; this is the line under it.
  return "The ordering system answered with an error. Please try again in a minute, or order at the counter.";
}

let inflight: Promise<void> | null = null;
/** Loads config and catalogue (cached copy first, so the shop paints at once). */
export function loadCatalogue(): Promise<void> {
  if (inflight) return inflight;
  loading.value = true;
  inflight = (async () => {
    if (!catalogue.value) {
      const cached = readCachedCatalogue();
      if (cached) catalogue.value = cached;
    }
    const [cfg, cat] = await Promise.all([shopApi.config(), shopApi.catalogue()]);
    if (cfg.ok && cfg.data) {
      config.value = cfg.data;
      if (basket.dining.value === 'eat_in' && !cfg.data.dining.eat_in) basket.setDining('takeaway');
      if (basket.dining.value === 'takeaway' && !cfg.data.dining.takeaway && cfg.data.dining.eat_in) basket.setDining('eat_in');
    }
    if (cat.ok && cat.data) {
      catalogue.value = cat.data;
      cacheCatalogue(cat.data);
      reconcileBasket(cat.data);
    }
    // The shop is "reachable" when config answered; a stale cached catalogue can still
    // paint the menu, but the customer is told the shop itself could not be reached.
    loadError.value = cfg.ok ? (cat.ok || catalogue.value ? null : shopError(cat)) : configError(cfg);
  })().finally(() => {
    inflight = null;
    loading.value = false;
  });
  return inflight;
}
