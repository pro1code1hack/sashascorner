// Tiny DOM helpers: the admin builds its lists in script, so every text value
// goes through textContent (never innerHTML) and cannot inject markup.

type Attrs = Record<string, string | number | boolean | null | undefined | ((e: Event) => void)>;
export type Child = Node | string | null | undefined | false | Child[];

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...kids: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (typeof v === 'function') el.addEventListener(k.replace(/^on/, ''), v as EventListener);
    else if (k === 'class') el.className = String(v);
    else if (k === 'style') el.style.cssText = String(v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
  append(el, kids);
  return el;
}

function append(el: Node, kids: Child[]) {
  for (const k of kids) {
    if (k === null || k === undefined || k === false) continue;
    if (Array.isArray(k)) append(el, k);
    else el.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
  }
}

export function $(sel: string, root: ParentNode = document): HTMLElement {
  const el = root.querySelector<HTMLElement>(sel);
  if (!el) throw new Error(`admin: missing ${sel}`);
  return el;
}

/** Announce to screen readers through the page's polite live region. */
export function announce(text: string) {
  const live = document.getElementById('adm-live');
  if (!live) return;
  live.textContent = '';
  // A fresh text node after a tick makes repeated identical messages re-announce.
  setTimeout(() => (live.textContent = text), 60);
}

export const pct = (v: number) => `${Math.round(v * 1000) / 10}%`;

export function kb(bytes: number): string {
  if (!bytes) return '';
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`;
}

export function slotAnchor(key: string) {
  return `slot-${key.replace(/[^a-z0-9]+/gi, '-')}`;
}

const PAGE_NAMES: Record<string, string> = {
  '/': 'Home',
  '/about': 'About',
  '/menu': 'Menu',
  '/order': 'Order',
  '/visit': 'Visit',
  '/book': 'Book a table',
};
export const PAGE_ORDER = ['/', '/about', '/menu', '/order', '/visit', '/book'];

export function pageName(path: string) {
  return PAGE_NAMES[path] ?? path.replace(/^\//, '').replace(/[-/]/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/** CSS aspect string ("4/5", "16 / 9", "1.5") as a width / height number. */
export function ratio(aspect: string): number {
  const [w, hh] = aspect.split('/').map((s) => parseFloat(s));
  if (!w) return 4 / 3;
  return hh ? w / hh : w;
}

/** The narrower frame a phone is likely to show for the same slot. */
export function phoneAspect(aspect: string): string {
  const r = ratio(aspect);
  if (r > 1.05) return '4/5';
  if (r > 0.7) return '9/16';
  return '9/19';
}
