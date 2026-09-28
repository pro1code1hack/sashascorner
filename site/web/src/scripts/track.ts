// Swetrix custom events (CONTRACT §9). Cookieless analytics; nothing personal is sent.
//
//   import { track } from '../scripts/track';
//   track('rewards_view');
//
// A no-op unless PUBLIC_SWETRIX_PID is set at build time, so dev and preview builds send
// nothing. The Swetrix script is loaded lazily on the first event, not on page load.
// Importing this module also remembers `?src=` (the QR code's place: table, till,
// cup, flyer-uni...) for the rest of the visit, and every event carries it as `src`.

const PID: string | undefined = import.meta.env.PUBLIC_SWETRIX_PID;
const API_URL: string | undefined = import.meta.env.PUBLIC_SWETRIX_API; // self-hosted only
const SCRIPT = 'https://swetrix.org/swetrix.js';
const SRC_KEY = 'sc_src';
const SRC_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/i;

interface Swetrix {
  init(pid: string, opts?: Record<string, unknown>): void;
  track(e: { ev: string; meta?: Record<string, string> }): void;
}
declare global {
  interface Window {
    swetrix?: Swetrix;
  }
}

function persistSrc(): void {
  try {
    const src = new URLSearchParams(location.search).get('src');
    if (src && SRC_RE.test(src)) sessionStorage.setItem(SRC_KEY, src.toLowerCase());
  } catch {
    /* storage blocked: the URL still has it for this page */
  }
}
if (typeof window !== 'undefined') persistSrc();

/** The visit's `src`: this URL's `?src=`, else the one remembered this session. */
export function currentSrc(): string | undefined {
  if (typeof window === 'undefined') return undefined;
  const fromUrl = new URLSearchParams(location.search).get('src');
  if (fromUrl && SRC_RE.test(fromUrl)) return fromUrl.toLowerCase();
  try {
    return sessionStorage.getItem(SRC_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

// Belt and braces: event props are ours, but refuse anything that looks personal.
const PII_KEY = /name|mail|phone|contact|token|birth|code|card/i;
const PII_VALUE = /@|\d{6,}|[0-9a-f]{8}-[0-9a-f]{4}-/i;

let ready: Promise<Swetrix | null> | null = null;
function load(): Promise<Swetrix | null> {
  if (ready) return ready;
  ready = new Promise((resolve) => {
    const s = document.createElement('script');
    s.src = SCRIPT;
    s.async = true;
    s.defer = true;
    s.onload = () => {
      const sw = window.swetrix;
      if (!sw || !PID) return resolve(null);
      sw.init(PID, API_URL ? { apiURL: API_URL } : {});
      resolve(sw);
    };
    s.onerror = () => resolve(null); // blocked by an ad blocker: fine
    document.head.append(s);
  });
  return ready;
}

export function track(event: string, props?: Record<string, string>): void {
  if (!PID || typeof window === 'undefined') return;
  const meta: Record<string, string> = {};
  for (const [k, v] of Object.entries(props ?? {})) {
    if (typeof v === 'string' && !PII_KEY.test(k) && !PII_VALUE.test(v)) meta[k] = v.slice(0, 100);
  }
  const src = currentSrc();
  if (src && !meta.src) meta.src = src;
  void load().then((sw) => {
    try {
      sw?.track({ ev: event, meta });
    } catch {
      /* analytics must never break a page */
    }
  });
}
