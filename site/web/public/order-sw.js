// Service worker for order-ahead notifications ("your order is ready"). Registered by
// the status page (scripts/shop/checkout/notify.ts) with scope /order/ only; it does no
// caching and never intercepts fetches. Served at /order-sw.js (site root), which the
// /order/* rewrites in Caddy and the Vite dev server do not touch.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : '' };
  }
  const title = data.title || "Sasha's Corner";
  const options = {
    body: data.body || 'Your order has an update.',
    tag: data.tag || data.code || 'sc-order',
    renotify: true,
    icon: data.icon || '/apple-touch-icon.png',
    badge: data.badge || '/favicon-32.png',
    data: { url: data.url || '/order' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || '/order', self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      const open = list.find((c) => c.url === url || c.url.startsWith(url.split('?')[0]));
      if (open) return open.focus().then(() => ('navigate' in open && open.url !== url ? open.navigate(url) : open));
      return self.clients.openWindow(url);
    }),
  );
});
