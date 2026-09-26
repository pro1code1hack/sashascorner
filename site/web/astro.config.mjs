// Static output: every page is plain HTML a crawler can read without running
// JavaScript. The two live things -- menu prices and table availability -- are
// fetched from the site API at runtime; the menu is also baked in at build time
// (src/data/menu.json, written by `sashasite menu-export`) so search engines see it.
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
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
const MENU_PAGES = new Set(['/', '/menu', '/order', '/faq']);
const lastmodFor = (url) => {
  const path = new URL(url).pathname.replace(/\/$/, '') || '/';
  const page = path === '/' ? 'index' : path.slice(1);
  const files = [src(`pages/${page}.astro`), src(`pages/${page}/index.astro`), src('data/info.json')];
  if (MENU_PAGES.has(path)) files.push(src('data/menu.json'));
  if (path === '/faq') files.push(src('pages/_facts.ts'));
  const t = Math.max(...files.map(changedAt));
  return t ? new Date(t).toISOString() : undefined;
};
const NOT_IN_SITEMAP = [/\/book\/manage/, /\/admin/, /\/404$/];

export default defineConfig({
  // Canonical origin. Unconfirmed -- change once the domain is registered.
  site: process.env.SITE_PUBLIC_URL ?? 'https://sashascorner.co.uk',
  trailingSlash: 'never',
  build: { format: 'file' },
  integrations: [
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
      proxy: { '/api': process.env.SITE_API_URL ?? 'http://127.0.0.1:8100' },
    },
  },
});
