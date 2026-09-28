// The scanner's one way to talk to the staff API (CONTRACT §5).
//
// - Every call carries `X-Staff-Device`; all but pairing and login also carry
//   `Authorization: Bearer <session>`.
// - Errors are `{error, detail}`; they become `StaffApiError` whose `.message` is
//   the server's `detail` (the sentence staff read) and `.code` the `error` code.
// - A 401 on a signed-in call means the session is over: the app is told through
//   the `staff:session-ended` event and goes back to the PIN pad. A wrong manager
//   PIN is not a session ending, so a 401/403 whose code mentions "pin" is left to
//   the caller.
// - A device the server no longer knows (revoked, unknown) fires
//   `staff:device-revoked`: the app forgets its token and shows pairing.
// - No network is `status 0` with `.offline = true`. Nothing is queued: a stamp
//   that did not reach the server did not happen, and the screen says so.
// - `?mock=1` routes every call to the in-page mock (mock.ts, loaded only then).

import { device, isMockMode, session } from './store';

export class StaffApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly offline: boolean;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'StaffApiError';
    this.status = status;
    this.code = code;
    this.offline = status === 0;
  }
}

type Method = 'GET' | 'POST';

interface Opts {
  /** Send the session bearer (default true). */
  auth?: boolean;
  signal?: AbortSignal;
}

const TIMEOUT_MS = 12_000;
const DEVICE_CODES = /device/i;

function fallbackText(status: number): string {
  if (status === 0) return 'No connection. Nothing was saved: try again when the connection is back.';
  if (status === 401) return 'Your session has ended. Enter your PIN again.';
  if (status === 403) return 'This device or user is not allowed to do that.';
  if (status === 404) return 'Not found.';
  if (status === 429) return 'Too many tries. Wait a few minutes, then try again.';
  if (status >= 500) return 'Something went wrong on the server. Nothing was saved: try again.';
  return `That didn't work (error ${status}).`;
}

function asText(v: unknown): string | null {
  if (typeof v === 'string' && v.trim()) return v;
  // FastAPI 422: [{loc, msg}]
  if (Array.isArray(v) && v[0] && typeof v[0].msg === 'string') return String(v[0].msg).replace(/^Value error, /, '');
  return null;
}

let mockFetch: ((method: Method, path: string, body: unknown, headers: Record<string, string>) => Promise<{ status: number; body: unknown }>) | null = null;

export async function request<T>(method: Method, path: string, body?: unknown, opts: Opts = {}): Promise<T> {
  const auth = opts.auth ?? true;
  const headers: Record<string, string> = { Accept: 'application/json' };
  const dev = device.token();
  if (dev) headers['X-Staff-Device'] = dev;
  const s = session.get();
  if (auth && s) headers.Authorization = `Bearer ${s.token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let status: number;
  let data: unknown = null;

  if (isMockMode()) {
    if (!mockFetch) mockFetch = (await import('./mock')).mockFetch;
    const r = await mockFetch(method, path, body, headers);
    status = r.status;
    data = r.body;
  } else {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      throw new StaffApiError(0, 'offline', fallbackText(0));
    }
    const timer = new AbortController();
    const t = window.setTimeout(() => timer.abort(), TIMEOUT_MS);
    opts.signal?.addEventListener('abort', () => timer.abort(), { once: true });
    try {
      const res = await fetch(path, {
        method,
        headers,
        credentials: 'omit',
        cache: 'no-store',
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: timer.signal,
      });
      status = res.status;
      const text = await res.text();
      if (text) {
        try {
          data = JSON.parse(text);
        } catch {
          data = text;
        }
      }
    } catch (e) {
      if (opts.signal?.aborted) throw e;
      throw new StaffApiError(0, 'offline', fallbackText(0));
    } finally {
      window.clearTimeout(t);
    }
  }

  if (status >= 200 && status < 300) return data as T;

  const obj = data && typeof data === 'object' && !Array.isArray(data) ? (data as Record<string, unknown>) : {};
  const code = typeof obj.error === 'string' ? obj.error : '';
  const message = asText(obj.detail) ?? asText(obj.message) ?? fallbackText(status);
  const err = new StaffApiError(status, code, message);

  if ((status === 401 || status === 403) && DEVICE_CODES.test(code)) {
    window.dispatchEvent(new CustomEvent('staff:device-revoked', { detail: message }));
  } else if (status === 401 && auth && !/pin/i.test(code)) {
    window.dispatchEvent(new CustomEvent('staff:session-ended', { detail: message }));
  }
  throw err;
}

export const api = {
  get: <T>(path: string, opts?: Opts) => request<T>('GET', path, undefined, opts),
  post: <T>(path: string, body: unknown = {}, opts?: Opts) => request<T>('POST', path, body, opts),
};

export function errorText(e: unknown): string {
  if (e instanceof StaffApiError) return e.message;
  if (e instanceof Error && e.message) return e.message;
  return 'Something went wrong. Try again.';
}
