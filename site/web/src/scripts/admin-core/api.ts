// The admin's one way to talk to the API.
//
// - Session: the `sc_admin` cookie (HttpOnly, Path=/api) rides along on its own;
//   nothing is kept in JS.
// - CSRF: every non-GET request carries `X-Admin: 1` (the server 403s without it).
// - 401 on any admin call except the sign-in itself sends the owner back to
//   /admin?next=<this page>. Pass `{ quiet401: true }` to handle it yourself.
// - Errors become `ApiError` with a sentence the owner can read (`.message`) and,
//   for 422, per-field messages (`.fields`, keyed by dotted path without "body").
// - `?mock=1` (kept in sessionStorage by the layout) routes every call to the
//   in-page mock in mock.ts, which follows site/ADMIN.md.

import { mockFetch } from './mock';

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly fields: Record<string, string>;
  constructor(status: number, message: string, detail: unknown, fields: Record<string, string> = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.fields = fields;
  }
}

export function isMock(): boolean {
  try {
    return sessionStorage.getItem('sc-adm-mock') === '1';
  } catch {
    return new URLSearchParams(location.search).get('mock') === '1';
  }
}

/** Headers every write needs. Exported for callers that must use XHR (uploads with progress). */
export function adminHeaders(json = true): Record<string, string> {
  const h: Record<string, string> = { 'X-Admin': '1', Accept: 'application/json' };
  if (json) h['Content-Type'] = 'application/json';
  return h;
}

/** Where a 401 sends the owner. Exported so XHR callers can do the same. */
export function goToSignIn(): void {
  const here = location.pathname + location.search;
  if (location.pathname.replace(/\/$/, '') === '/admin') {
    location.reload();
    return;
  }
  location.assign(`/admin?next=${encodeURIComponent(here)}`);
}

/** FastAPI 422 detail -> { "party": "must be at least 1", ... }. */
export function fieldErrors(detail: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!Array.isArray(detail)) return out;
  for (const d of detail as { loc?: unknown[]; msg?: string }[]) {
    const loc = (d.loc ?? []).filter((p) => p !== 'body' && p !== 'query').map(String);
    const key = loc.join('.') || '_';
    const msg = String(d.msg ?? 'Not valid').replace(/^Value error, /, '');
    if (!out[key]) out[key] = msg.charAt(0).toUpperCase() + msg.slice(1);
  }
  return out;
}

function detailText(detail: unknown): string | null {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const m = (detail as Record<string, unknown>).message ?? (detail as Record<string, unknown>).detail;
    if (typeof m === 'string') return m;
  }
  return null;
}

/** A sentence for the owner, by status. The server's own words win when it sends a string. */
export function friendlyMessage(status: number, detail: unknown): string {
  const said = detailText(detail);
  switch (status) {
    case 0:
      return "Couldn't reach the server. Check the connection and try again.";
    case 408:
      return 'The server took too long to answer. Check the connection and try again: nothing was lost.';
    case 401:
      return 'You have been signed out. Sign in again.';
    case 403:
      return 'The server refused that. Reload the page and try again.';
    case 404:
      return said ?? "That isn't there any more. Reload the page.";
    case 409:
      return said ?? 'That clashes with something already saved.';
    case 413:
      return 'That file is too big.';
    case 422: {
      const f = fieldErrors(detail);
      const first = Object.entries(f)[0];
      if (first) return first[0] === '_' ? first[1] : `${label(first[0])}: ${first[1]}`;
      return said ?? 'Some of that is not valid. Check the highlighted fields.';
    }
    case 429:
      return 'Too many tries. Wait a minute, then try again.';
    case 503:
      return said ?? 'The server is not set up for this yet.';
    default:
      if (status >= 500) return 'Something went wrong on the server. Try again in a moment.';
      return said ?? `That didn't work (error ${status}).`;
  }
}

function label(path: string): string {
  const last = path.split('.').pop() ?? path;
  return last.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

export interface RequestOptions {
  /** Don't redirect on 401; throw an ApiError(401) instead. */
  quiet401?: boolean;
  signal?: AbortSignal;
  /** Give up after this many ms (default 20s), so a dropped connection can't leave a
   *  button on "Saving…" for ever. The caller's own `signal` still aborts silently. */
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 20_000;

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export async function request<T>(method: Method, path: string, body?: unknown, opts: RequestOptions = {}): Promise<T> {
  let status: number;
  let data: unknown = null;

  if (isMock()) {
    const r = await mockFetch(method, path, body);
    status = r.status;
    data = r.body;
  } else {
    const isForm = typeof FormData !== 'undefined' && body instanceof FormData;
    const timer = new AbortController();
    const timeout = window.setTimeout(() => timer.abort(), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    opts.signal?.addEventListener('abort', () => timer.abort(), { once: true });
    const init: RequestInit = {
      method,
      credentials: 'same-origin',
      headers: method === 'GET' ? { Accept: 'application/json' } : adminHeaders(!isForm && body !== undefined),
      signal: timer.signal,
    };
    if (body !== undefined) init.body = isForm ? (body as FormData) : JSON.stringify(body);
    let res: Response;
    let text: string;
    try {
      res = await fetch(path, init);
      text = await res.text();
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') {
        // The caller cancelled (a newer request superseded this one): stay silent.
        if (opts.signal?.aborted) throw e;
        throw new ApiError(408, friendlyMessage(408, null), null);
      }
      throw new ApiError(0, friendlyMessage(0, null), null);
    } finally {
      window.clearTimeout(timeout);
    }
    status = res.status;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
  }

  if (status >= 200 && status < 300) return data as T;

  const detail = data && typeof data === 'object' && 'detail' in (data as object) ? (data as { detail: unknown }).detail : data;
  if (status === 401 && !opts.quiet401) {
    goToSignIn();
    // Leave the caller waiting: the page is going away.
    return new Promise<T>(() => {});
  }
  // A 404/405 on an /api/admin route usually means the backend isn't there yet.
  if ((status === 404 || status === 405) && typeof detail === 'string' && /^(not found|method not allowed)$/i.test(detail)) {
    throw new ApiError(status, 'The server does not know this yet. Is the backend up to date?', detail);
  }
  throw new ApiError(status, friendlyMessage(status, detail), detail, status === 422 ? fieldErrors(detail) : {});
}

const q = (params?: Record<string, string | number | undefined | null>) => {
  if (!params) return '';
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : '';
};

export const api = {
  get: <T>(path: string, params?: Record<string, string | number | undefined | null>, opts?: RequestOptions) =>
    request<T>('GET', path + q(params), undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>('POST', path, body ?? {}, opts),
  put: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>('PUT', path, body ?? {}, opts),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>('PATCH', path, body ?? {}, opts),
  del: <T>(path: string, opts?: RequestOptions) => request<T>('DELETE', path, undefined, opts),
};

/** Message from anything thrown, for a toast or an inline error. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error && e.message) return e.message;
  return 'Something went wrong. Try again.';
}
