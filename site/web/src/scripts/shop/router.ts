// History-API routing under /order. Caddy (and the Vite dev rewrite) serve order.html
// for every /order/* path, so `location.pathname` is the real deep link on first load.
import { signal, type Signal } from '@preact/signals';

export interface Route {
  /** The pathname, e.g. "/order/c/hot-drinks" (no .html, no trailing slash). */
  path: string;
  /** Matched pattern parameters: `slug` on category and product routes, `code` on status. */
  params: Record<string, string>;
  query: URLSearchParams;
  /** Which screen `path` resolves to. */
  name: RouteName;
}
export type RouteName = 'overview' | 'category' | 'product' | 'basket' | 'checkout' | 'account' | 'status' | 'notfound';

export const BASE = '/order';

const PATTERNS: { name: RouteName; re: RegExp; keys: string[] }[] = [
  { name: 'overview', re: /^\/order$/, keys: [] },
  { name: 'category', re: /^\/order\/c\/([^/]+)$/, keys: ['slug'] },
  { name: 'product', re: /^\/order\/p\/([^/]+)$/, keys: ['slug'] },
  { name: 'basket', re: /^\/order\/basket$/, keys: [] },
  { name: 'checkout', re: /^\/order\/checkout$/, keys: [] },
  { name: 'account', re: /^\/order\/account$/, keys: [] },
  { name: 'status', re: /^\/order\/status\/([^/]+)$/, keys: ['code'] },
];

export function normalise(pathname: string): string {
  let p = pathname.replace(/\.html$/, '').replace(/\/+$/, '');
  if (p === '' || p === '/order/index') p = BASE;
  return p;
}

function resolve(pathname: string, search: string): Route {
  const path = normalise(pathname);
  const query = new URLSearchParams(search);
  for (const { name, re, keys } of PATTERNS) {
    const m = path.match(re);
    if (!m) continue;
    const params: Record<string, string> = {};
    keys.forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1] ?? '')));
    return { path, params, query, name };
  }
  return { path, params: {}, query, name: 'notfound' };
}

export const route: Signal<Route> = signal<Route>(resolve(location.pathname, location.search));

// Dev-only: keep `?mock=` alive across in-app navigation so a reload stays in mock mode.
const KEEP = import.meta.env.DEV ? ['mock'] : [];
/** Dev only: carries `?mock=` onto a path so a full page load stays in mock mode. */
export function withKept(path: string): string {
  if (!KEEP.length) return path;
  const url = new URL(path, location.origin);
  const cur = new URLSearchParams(location.search);
  for (const k of KEEP) if (cur.has(k) && !url.searchParams.has(k)) url.searchParams.set(k, cur.get(k)!);
  return url.pathname + url.search + url.hash;
}

// ---- scroll: new screens start at the top, Back returns to where you were ----------
// The browser's own restoration fires before the screen has rendered, so it is turned
// off and the position is kept in history.state instead: remembered on every
// navigation away, put back after the popstate render.
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
type HistState = { scroll?: number } | null;
const remember = () => history.replaceState({ ...(history.state as HistState), scroll: window.scrollY }, '', location.href);
/** Set by the shell after a popstate render (App.tsx): where to scroll to, or null for the top. */
export const restoreScroll: Signal<number | null> = signal<number | null>(null);

/** Navigate within the app. Paths outside /order fall back to a full page load. */
export function go(path: string, opts: { replace?: boolean } = {}): void {
  if (!path.startsWith(BASE)) {
    location.assign(path);
    return;
  }
  const full = withKept(path);
  // Outside the shop (the /account island) there is no App to render a route: load it.
  const here = normalise(location.pathname);
  if (here !== BASE && !here.startsWith(BASE + '/')) {
    location.assign(full);
    return;
  }
  if (opts.replace) history.replaceState(history.state, '', full);
  else {
    remember();
    history.pushState(null, '', full);
  }
  route.value = resolve(location.pathname, location.search);
  // A new screen starts at the top; a replace (query tweak) keeps the scroll position.
  if (!opts.replace) window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
}

window.addEventListener('popstate', () => {
  const st = history.state as HistState;
  restoreScroll.value = typeof st?.scroll === 'number' ? st.scroll : 0;
  route.value = resolve(location.pathname, location.search);
});

/** Handle same-origin /order links anywhere in the app without a page load. */
export function interceptLinks(root: HTMLElement): void {
  root.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as HTMLElement).closest<HTMLAnchorElement>('a[href]');
    if (!a || a.target === '_blank' || a.hasAttribute('download') || a.dataset.native !== undefined) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return;
    const path = normalise(url.pathname);
    if (path !== BASE && !path.startsWith(BASE + '/')) return;
    e.preventDefault();
    go(url.pathname + url.search + url.hash);
  });
}

// Paths, in one place, so a rename is one edit.
export const paths = {
  overview: () => BASE,
  category: (slug: string) => `${BASE}/c/${encodeURIComponent(slug)}`,
  product: (slug: string, line?: number) =>
    `${BASE}/p/${encodeURIComponent(slug)}${line === undefined ? '' : `?line=${line}`}`,
  basket: () => `${BASE}/basket`,
  checkout: () => `${BASE}/checkout`,
  // Site-wide since 2026-09-29 (pages/account.astro); /order/account redirects there.
  account: () => withKept('/account'),
  status: (code: string) => `${BASE}/status/${encodeURIComponent(code)}`,
};
