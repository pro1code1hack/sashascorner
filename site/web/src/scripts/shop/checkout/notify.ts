// Order updates (owner addition, 2026-09-29): free channels first, SMS by opt-in.
// The config and order fields are read defensively -- when the API doesn't ship them
// yet, every control here stays hidden.
import type { OrderView, ShopConfig } from '../types';

export interface PushConfig {
  enabled: boolean;
  vapid_public_key: string | null;
}
/** What `GET /api/shop/config` adds on top of C's typed shape. */
export type ShopConfigX = ShopConfig & { push?: PushConfig | null; sms_notify?: string | null };
/** What the order read adds. */
export type OrderViewX = OrderView & { notify?: { email?: boolean | string | null; push_subscribed?: boolean; sms?: boolean | null } | null };

export const smsOffered = (cfg: ShopConfig | null): boolean => {
  const v = (cfg as ShopConfigX | null)?.sms_notify;
  return typeof v === 'string' && v !== 'off';
};
export const pushConfig = (cfg: ShopConfig | null): PushConfig | null => {
  const p = (cfg as ShopConfigX | null)?.push;
  return p && p.enabled && p.vapid_public_key ? p : null;
};

export const SW_URL = '/order-sw.js';
export const SW_SCOPE = '/order/';

export const pushSupported = (): boolean =>
  typeof window !== 'undefined' && 'Notification' in window && 'PushManager' in window && 'serviceWorker' in navigator && window.isSecureContext;

/** iOS Safari only offers push once the page is installed to the Home Screen. */
export const iosNeedsInstall = (): boolean => {
  const ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const standalone = (navigator as Navigator & { standalone?: boolean }).standalone === true || matchMedia('(display-mode: standalone)').matches;
  return ios && !standalone && !('PushManager' in window);
};

function vapidKey(base64: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  const existing = await navigator.serviceWorker.getRegistration(SW_SCOPE);
  if (existing) return existing;
  return navigator.serviceWorker.register(SW_URL, { scope: SW_SCOPE });
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration(SW_SCOPE);
  return reg ? reg.pushManager.getSubscription() : null;
}

type Outcome = { ok: true } | { ok: false; reason: 'denied' | 'unsupported' | 'failed'; detail?: string };

const orderUrl = (code: string, token: string) => `/api/shop/orders/${encodeURIComponent(code)}/push?t=${encodeURIComponent(token)}`;

/** Ask permission, subscribe this device, and tell the API. */
export async function subscribeToOrder(code: string, token: string, cfg: PushConfig): Promise<Outcome> {
  if (!pushSupported()) return { ok: false, reason: 'unsupported' };
  try {
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') return { ok: false, reason: 'denied' };
    const reg = await registration();
    const sub = (await reg.pushManager.getSubscription()) ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKey(cfg.vapid_public_key!) }));
    const j = sub.toJSON();
    const r = await fetch(orderUrl(code, token), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Order-Token': token },
      body: JSON.stringify({ endpoint: sub.endpoint, keys: { p256dh: j.keys?.p256dh ?? '', auth: j.keys?.auth ?? '' } }),
      credentials: 'same-origin',
    });
    if (!r.ok) return { ok: false, reason: 'failed', detail: `HTTP ${r.status}` };
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: 'failed', detail: e instanceof Error ? e.message : String(e) };
  }
}

/** Undo: tell the API, then drop the browser subscription. */
export async function unsubscribeFromOrder(code: string, token: string): Promise<Outcome> {
  try {
    const sub = await currentSubscription();
    // The API reads the endpoint from the query string; without it every device on the
    // order would be unsubscribed, not just this one.
    const url = new URL(orderUrl(code, token), location.origin);
    if (sub) url.searchParams.set('endpoint', sub.endpoint);
    await fetch(url.toString(), {
      method: 'DELETE',
      headers: { Accept: 'application/json', 'X-Order-Token': token },
      credentials: 'same-origin',
    });
    if (sub) await sub.unsubscribe();
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: 'failed', detail: e instanceof Error ? e.message : String(e) };
  }
}
