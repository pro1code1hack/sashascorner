// Static output: every page is plain HTML a crawler can read without running
// JavaScript. The two live things -- menu prices and table availability -- are
// fetched from the site API at runtime; the menu is also baked in at build time
// (src/data/menu.json, written by `sashasite menu-export`) so search engines see it.
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import preact from '@astrojs/preact';
import { execFileSync } from 'node:child_process';
import { existsSync, statSync } from 'node:fs';

// Sitemap <lastmod>: when the page's own source or the data it renders last changed
// (git commit time, else file mtime). Not the build time -- a lastmod that moves on
// every deploy teaches Google to ignore it.
const src = (p) => new URL(`./src/${p}`, import.meta.url);
const git = (...args) =>
  execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
const changedAt = (file) => {
  if (!existsSync(file)) return 0;
  const modified = statSync(file).mtimeMs;
  try {
    // Committed and unchanged: the commit time. Untracked or edited: the file's mtime.
    if (git('status', '--porcelain', '--', file.pathname)) return modified;
    const committed = git('log', '-1', '--format=%cI', '--', file.pathname);
    return committed ? Date.parse(committed) : modified;
  } catch {
    return modified; // no git (e.g. a tarball build): mtime is the best we have
  }
};
const MENU_PAGES = new Set(['/', '/menu', '/delivery', '/faq', '/matcha-dundee', '/bubble-tea-dundee', '/kyiv-cake']);
const lastmodFor = (url) => {
  const path = new URL(url).pathname.replace(/\/$/, '') || '/';
  const page = path === '/' ? 'index' : path.slice(1);
  const files = [src(`pages/${page}.astro`), src(`pages/${page}/index.astro`), src('data/info.json')];
  if (MENU_PAGES.has(path)) files.push(src('data/menu.json'));
  if (path === '/faq') files.push(src('pages/_facts.ts'));
  if (path === '/events') files.push(src('data/events.json'));
  const t = Math.max(...files.map(changedAt));
  return t ? new Date(t).toISOString() : undefined;
};
// /order/checkout, /order/account and /order/status/<code> are routes inside the order-ahead
// app (one static page, /order): a basket, a sign-in and a private order are not for search.
const NOT_IN_SITEMAP = [/\/book\/manage/, /\/admin/, /\/404$/, /\/c(\/|$)/, /\/staff(\/|$)/, /\/order\/(checkout|account|status)(\/|$)/];

// ---- dev proxy to cafeops (Rewards, the staff scanner, Apple Wallet's web service) ----
// CAFEOPS_API_URL wins. Without it, `astro dev` looks for a running cafeops API on the
// ports it is usually started on and uses the first that answers its health check with
// cafeops' own shape: port 8000 can be held by something else entirely (Docker Desktop
// keeps a listener there that accepts and never replies), and a join form proxied to it
// just spins. Nothing found: 8000, start.sh's port, which may simply not be up yet.
const IS_DEV = process.argv.includes('dev');
async function findCafeops() {
  if (process.env.CAFEOPS_API_URL) return process.env.CAFEOPS_API_URL;
  const fallback = 'http://127.0.0.1:8000';
  if (!IS_DEV) return fallback;
  const probe = async (base) => {
    try {
      const r = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(800) });
      const body = await r.json();
      return body && typeof body === 'object' && 'database_dialect' in body ? base : null;
    } catch {
      return null;
    }
  };
  const bases = [8000, 8001, 8002].map((p) => `http://127.0.0.1:${p}`);
  const found = (await Promise.all(bases.map(probe))).find(Boolean);
  console.info(
    found
      ? `[cafeops proxy] /api/loyalty, /api/staff, /api/shop, /wallet -> ${found} (set CAFEOPS_API_URL to choose)`
      : `[cafeops proxy] no cafeops API answering on ${bases.join(', ')}; using ${fallback}. Start it with \`uv run cafeops serve\` or set CAFEOPS_API_URL.`,
  );
  return found ?? fallback;
}
const CAFEOPS_API = await findCafeops();
// A target that is down or never answers becomes a JSON 502 the pages can put into words
// (scripts/rewards/api.ts `humanError`), and a line in this terminal saying which URL.
const cafeopsProxy = () => ({
  target: CAFEOPS_API,
  proxyTimeout: 20_000,
  configure(proxy) {
    proxy.on('error', (err, req, res) => {
      console.error(
        `[cafeops proxy] ${req.method} ${req.url} -> ${CAFEOPS_API} failed (${err.code ?? err.message}). ` +
          'Is the cafeops API running there? Set CAFEOPS_API_URL to point at it.',
      );
      if (res && typeof res.writeHead === 'function' && !res.headersSent && !res.writableEnded) {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'api_unreachable', detail: `The cafeops API at ${CAFEOPS_API} is not answering.` }));
      }
    });
  },
});

export default defineConfig({
  // Canonical origin. Unconfirmed -- change once the domain is registered.
  site: process.env.SITE_PUBLIC_URL ?? 'https://sashascorner.co.uk',
  trailingSlash: 'never',
  build: { format: 'file' },
  integrations: [
    // The order-ahead app (pages/order.astro, scripts/shop/) is a Preact island.
    preact(),
    sitemap({
      filter: (page) => !NOT_IN_SITEMAP.some((re) => re.test(new URL(page).pathname)),
      serialize: (item) => ({ ...item, lastmod: lastmodFor(item.url) }),
    }),
  ],
  vite: {
    // Pre-bundle three and the add-ons the diorama imports lazily. Discovered late,
    // Vite re-optimises mid-session and the open page 504s on the stale bundle.
    optimizeDeps: {
      include: [
        'three',
        'three/addons/geometries/RoundedBoxGeometry.js',
        'three/addons/loaders/SVGLoader.js',
        'three/addons/utils/BufferGeometryUtils.js',
        'three/addons/environments/RoomEnvironment.js',
      ],
    },
    server: {
      // Rewards, the staff scanner, order-ahead (docs/shop/CONTRACT.md §4) and Apple
      // Wallet's web service are cafeops routes (docs/loyalty/CONTRACT.md §8); everything
      // else under /api is the site API. Vite matches keys in order, so the specific
      // prefixes come first.
      proxy: {
        '/api/loyalty': cafeopsProxy(),
        '/api/staff': cafeopsProxy(),
        '/api/shop': cafeopsProxy(),
        // Menu photos the shop shows are cafeops uploads (content-addressed, /media/<sha>.<ext>).
        '/media': cafeopsProxy(),
        '/wallet': cafeopsProxy(),
        '/api': process.env.SITE_API_URL ?? 'http://127.0.0.1:8100',
      },
    },
    // Dev twin of Caddy's rewrites: every web card (`/c/* -> /c.html`) and every route of
    // the order-ahead app (`/order/* -> /order.html`; /order itself is the page) is one
    // static page whose script reads the rest of the path.
    plugins: [
      {
        name: 'sc-dev-rewrites',
        apply: 'serve',
        configureServer(server) {
          server.middlewares.use((req, _res, next) => {
            if (req.url && /^\/c\/[^/?#]+/.test(req.url)) req.url = '/c' + req.url.replace(/^\/c\/[^?#]*/, '');
            else if (req.url && /^\/order\/[^?#]+/.test(req.url)) req.url = '/order' + req.url.replace(/^\/order\/[^?#]*/, '');
            next();
          });
        },
      },
    ],
  },
});
