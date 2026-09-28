// Staff scanner service worker: caches the app shell so the scanner opens fast and
// still shows its "No connection" state when the network drops. It NEVER touches
// /api/* (or /wallet/*): a stamp is either confirmed by the server or it did not
// happen. Registered by /staff with scope "/staff" (Caddy sends
// Service-Worker-Allowed: /staff for this file).
//
//   /staff (the page)   network first, cached copy when offline
//   /_astro/*           cache first: file names carry a content hash
//   icons, manifest,    stale-while-revalidate
//   stamp stickers

const VERSION = 'sc-staff-v2';
const STICKERS = ['1', '2', '3', '4', '5', '6', '7', '8'].map((n) => `/stickers/slot-${n}.svg`).concat('/stickers/reward.svg');
const SHELL = ['/staff', '/staff/manifest.webmanifest', '/brand/logo-reverse.svg', '/favicon.svg', '/icon-192.png', '/icon-512.png', ...STICKERS];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then((c) => c.addAll(SHELL))
      .catch(() => {})
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('sc-staff-') && k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function isPage(url) {
  return url.pathname === '/staff' || url.pathname === '/staff/' || url.pathname === '/staff.html';
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Never the API, never wallet endpoints: let the network answer or fail.
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/wallet/')) return;

  if (req.mode === 'navigate' && isPage(url)) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(VERSION).then((c) => c.put('/staff', copy));
          }
          return res;
        })
        .catch(() => caches.match('/staff').then((r) => r || Response.error())),
    );
    return;
  }

  if (url.pathname.startsWith('/_astro/')) {
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(VERSION).then((c) => c.put(req, copy));
            }
            return res;
          }),
      ),
    );
    return;
  }

  if (SHELL.includes(url.pathname)) {
    event.respondWith(
      caches.match(req).then((hit) => {
        const net = fetch(req)
          .then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(VERSION).then((c) => c.put(req, copy));
            }
            return res;
          })
          .catch(() => hit);
        return hit || net;
      }),
    );
  }
});
