// Talks to the loyalty API (cafeops, proxied on the same origin: CONTRACT §4, §8).
// Also keeps the card token on this device.
//
// Dev only: `?mock=<scenario>` answers from ./mock.ts instead of the network, so every
// state can be rendered without the backend. `import.meta.env.DEV` is false in a
// production build, the branch is dropped and mock.ts is never shipped.

export interface ApiError {
  error: string;
  detail?: string;
}
export interface ApiResult<T> {
  /** HTTP status; 0 when the request never got an answer. */
  status: number;
  ok: boolean;
  data: T | null;
  error: ApiError | null;
}

const MOCK: string | null = import.meta.env.DEV ? new URLSearchParams(location.search).get('mock') : null;
export const isMock = () => MOCK !== null;

export async function api<T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  opts: { body?: unknown; token?: string } = {},
): Promise<ApiResult<T>> {
  if (import.meta.env.DEV && MOCK !== null) {
    try {
      const { mockApi } = await import('./mock');
      const r = await mockApi(method, path, opts.body, MOCK);
      return shape<T>(r.status, r.body);
    } catch {
      return { status: 0, ok: false, data: null, error: { error: 'network' } };
    }
  }
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (opts.token) headers['X-Card-Token'] = opts.token;
  try {
    const r = await fetch(path, {
      method,
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      cache: 'no-store',
      credentials: 'same-origin',
    });
    const body = r.status === 204 ? null : await r.json().catch(() => null);
    return shape<T>(r.status, body);
  } catch {
    return { status: 0, ok: false, data: null, error: { error: 'network' } };
  }
}

function shape<T>(status: number, body: unknown): ApiResult<T> {
  const ok = status >= 200 && status < 300;
  if (ok) return { status, ok, data: body as T, error: null };
  const b = (body ?? {}) as Record<string, unknown>;
  // FastAPI's own 422s carry `detail` as a list of {msg}; ours carry {error, detail}.
  const detail = Array.isArray(b.detail)
    ? (b.detail as { msg?: string }[]).map((d) => d.msg ?? '').filter(Boolean).join(' ')
    : typeof b.detail === 'string'
      ? b.detail
      : undefined;
  return { status, ok, data: null, error: { error: typeof b.error === 'string' ? b.error : `http_${status}`, detail } };
}

// ---- the card token on this device ------------------------------------------------
// The web-card bearer arrives once in the URL fragment (#t=...), which never reaches a
// server log. It is moved into localStorage straight away and cut from the address bar.
const TOKEN_KEY = (id: string) => `sc_card_token:${id}`;
const LAST_KEY = 'sc_card_last';

export function saveToken(cardId: string, token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY(cardId), token);
    localStorage.setItem(LAST_KEY, cardId);
  } catch {
    /* private mode with storage off: the card still works for this visit */
  }
}
export function loadToken(cardId: string): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY(cardId));
  } catch {
    return null;
  }
}
export function forgetToken(cardId: string): void {
  try {
    localStorage.removeItem(TOKEN_KEY(cardId));
    if (localStorage.getItem(LAST_KEY) === cardId) localStorage.removeItem(LAST_KEY);
  } catch {
    /* nothing to forget */
  }
}
/** The card this device last opened, if its token is still here. */
export function lastCard(): { id: string; token: string } | null {
  try {
    const id = localStorage.getItem(LAST_KEY);
    const token = id ? localStorage.getItem(TOKEN_KEY(id)) : null;
    return id && token ? { id, token } : null;
  } catch {
    return null;
  }
}

export const CARD_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// ---- device ------------------------------------------------------------------------
export type Platform = 'ios' | 'android' | 'desktop';
export function platform(): Platform {
  if (import.meta.env.DEV) {
    const d = new URLSearchParams(location.search).get('device'); // dev screenshots only
    if (d === 'ios' || d === 'android' || d === 'desktop') return d;
  }
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return 'ios';
  if (/Android/.test(ua)) return 'android';
  return 'desktop';
}
