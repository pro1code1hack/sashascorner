/**
 * The data layer.
 *
 * Live (`VITE_LIVE=1` or `VITE_API_BASE`): requests go to the API; auth is one
 * shared password exchanged for a session token (see "auth" below), kept in
 * sessionStorage rather than localStorage because it is a shared laptop.
 *
 * Fixture mode (neither set): every GET through `request()` is answered from
 * `web/fixtures/` -- real responses recorded by `cafeops api-fixtures`, not
 * samples -- via `lib/fixtures`, loaded lazily so they never weigh on the live
 * bundle. Writes answer `offline` and never pretend to have landed.
 */
import { getOperator } from './operator'
import type {
  ProposalsResponse,
  ShelfLifeIn,
  ShelfLifeResponse,
  SupplierTermsIn,
  SupplierTermsResponse,
} from './types'

/**
 * Where the data comes from.
 *
 * `VITE_API_BASE` is an ORIGIN ("https://ops.example.com"), never a path prefix:
 * every request path below already begins "/api/", so a "/api" base would build
 * "/api/api/today" and 404. Same-origin deployments -- the normal case, with the
 * API behind the same Caddy -- leave the base empty and set `VITE_LIVE=1`, which
 * is why liveness cannot simply be "is the base non-empty".
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')
export const LIVE = API_BASE !== '' || import.meta.env.VITE_LIVE === '1'
/** The same decision written so the bundler can fold it: in a live build the
 *  fixture branch in `request()` is dropped, and no fixture chunk is emitted. */
const FIXTURE_MODE = !import.meta.env.VITE_API_BASE && import.meta.env.VITE_LIVE !== '1'

/* ------------------------------------------------------------- auth ---- *
 * One place decides how a request proves who it is: `authHeaders()`.
 *
 * DECISIONS §3: the shared password is traded for a session token at sign-in
 * (`POST /api/auth/session` -> `{token, expires_at}`) and the token is sent as
 * `Authorization: Bearer`. The server stores only its hash and revokes every
 * session when the password changes, so a stale tab gets a 401 and returns to
 * Login. The stored credential carries its kind; `signIn()` falls back to the
 * raw password (`X-API-Key`) only against an older server with no session
 * endpoint (404/405). The raw-password header is still accepted by the server
 * for tools (`cafeops api-fixtures`, curl); a raw password sent as Bearer is not.
 *
 * Kept in sessionStorage, not localStorage: a shared secret on a shared laptop
 * should not outlive the tab.
 */

export type Credential = { kind: 'password' | 'session'; value: string }

const AUTH_STORE = 'cafeops.auth'
/** The pre-v2 key: a raw password. Read once for continuity, then migrated. */
const LEGACY_KEY_STORE = 'cafeops.key'

let memoryCredential: Credential | null = null

export function getCredential(): Credential | null {
  try {
    const raw = sessionStorage.getItem(AUTH_STORE)
    if (raw !== null) {
      const c = JSON.parse(raw) as Partial<Credential>
      if ((c.kind === 'password' || c.kind === 'session') && typeof c.value === 'string') {
        return { kind: c.kind, value: c.value }
      }
    }
    const legacy = sessionStorage.getItem(LEGACY_KEY_STORE)
    if (legacy !== null) return { kind: 'password', value: legacy }
  } catch {
    /* private mode or corrupt value: fall through to memory */
  }
  return memoryCredential
}

export function setCredential(c: Credential): void {
  memoryCredential = c
  try {
    sessionStorage.setItem(AUTH_STORE, JSON.stringify(c))
    sessionStorage.removeItem(LEGACY_KEY_STORE)
  } catch {
    /* private mode; the credential lives in memory for this page only */
  }
}

export function clearCredential(): void {
  memoryCredential = null
  try {
    sessionStorage.removeItem(AUTH_STORE)
    sessionStorage.removeItem(LEGACY_KEY_STORE)
  } catch {
    /* ignore */
  }
}

/** The single function that attaches auth to a request. */
export function authHeaders(c: Credential | null = getCredential()): Record<string, string> {
  if (c === null) return {}
  return c.kind === 'session' ? { Authorization: `Bearer ${c.value}` } : { 'X-API-Key': c.value }
}

/**
 * Fired on `window` when a live request comes back 401: the credential is no
 * longer accepted (password changed, session expired or revoked). The app
 * listens and returns to the login screen with an explanation.
 */
export const SIGNED_OUT_EVENT = 'cafeops:signed-out'

export type SignInOutcome =
  | { kind: 'ok' }
  | { kind: 'wrong' }
  | { kind: 'no_password_set' }
  | { kind: 'rate_limited' }
  | { kind: 'failed'; message: string }

function statusOutcome(status: number): SignInOutcome | null {
  if (status === 401 || status === 403) return { kind: 'wrong' }
  if (status === 503) return { kind: 'no_password_set' }
  if (status === 429) return { kind: 'rate_limited' }
  return null
}

/**
 * Verify the shared password against an AUTHENTICATED endpoint and store the
 * resulting credential. Never trusts an open route: the pre-v2 Unlock checked
 * `/api/meta`, which is unauthenticated, so any password "worked"
 * (shell-agents.md §0.1).
 */
export async function signIn(password: string): Promise<SignInOutcome> {
  try {
    // 1. The session endpoint (DECISIONS §3). Absent today -> 404/405.
    const s = await fetch(`${API_BASE}/api/auth/session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // The operator name only labels the audit row (DECISIONS §6); it is not a login.
      body: JSON.stringify({ password, actor: getOperator() }),
    })
    if (s.ok) {
      const body = (await s.json()) as { token?: unknown }
      if (typeof body.token === 'string' && body.token !== '') {
        setCredential({ kind: 'session', value: body.token })
        return { kind: 'ok' }
      }
      return { kind: 'failed', message: 'The server accepted the password but sent no session.' }
    }
    const early = statusOutcome(s.status)
    if (early !== null) return early
    if (s.status !== 404 && s.status !== 405) {
      return { kind: 'failed', message: `The server answered ${s.status}.` }
    }

    // 2. Fallback while sessions do not exist: the raw password against a
    //    cheap authenticated GET. /api/suppliers is on the guarded router.
    const candidate: Credential = { kind: 'password', value: password }
    const r = await fetch(`${API_BASE}/api/suppliers`, { headers: authHeaders(candidate) })
    // A 200 must be the API's JSON, not an HTML fallback page from a static
    // server with no API behind it: that would "unlock" on any password.
    if (r.ok && (r.headers.get('content-type') ?? '').includes('application/json')) {
      setCredential(candidate)
      return { kind: 'ok' }
    }
    if (r.ok) return { kind: 'failed', message: 'No API answered at this address.' }
    return statusOutcome(r.status) ?? { kind: 'failed', message: `The server answered ${r.status}.` }
  } catch (e) {
    return {
      kind: 'failed',
      message: `Could not reach the server. ${e instanceof Error ? e.message : String(e)}`,
    }
  }
}

/** Revoke the session if there is one, forget the credential, reload. */
export async function signOut(): Promise<void> {
  const c = getCredential()
  if (LIVE && c?.kind === 'session') {
    try {
      await fetch(`${API_BASE}/api/auth/session`, { method: 'DELETE', headers: authHeaders(c) })
    } catch {
      /* signing out locally is what matters */
    }
  }
  clearCredential()
  location.reload()
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly payload: unknown,
    message: string,
  ) {
    super(message)
  }
}

/** A raw request with auth attached. Throws ApiError on a non-2xx answer. */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (FIXTURE_MODE && !LIVE && (init?.method ?? 'GET').toUpperCase() === 'GET') {
    const { fixtureResponse } = await import('./fixtures')
    return (await fixtureResponse(path)) as T
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  })
  const text = await res.text()
  let payload: unknown = null
  try {
    payload = text === '' ? null : JSON.parse(text)
  } catch {
    payload = text
  }
  if (!res.ok) {
    if (res.status === 401 && LIVE && getCredential() !== null) {
      clearCredential()
      window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: { reason: 'rejected' } }))
    }
    throw new ApiError(res.status, payload, `${res.status} on ${path}`)
  }
  return payload as T
}

/* ---------------------------------------------------------------- reads --- */

export const api = {
  /** Template proposals waiting for a human (spec 6). Reading writes nothing. */
  proposals: (): Promise<ProposalsResponse> => request('/api/proposals'),
}

/* ------------------------------------------------------------ the writes --- */

/**
 * A refused confirmation. The backend writes these messages to be shown to the
 * person who typed the number -- "a 1-day life against a 2-day transit buffer
 * leaves nothing usable on arrival" is the whole explanation -- so they are
 * surfaced verbatim rather than replaced with a generic failure.
 */
export type WriteResult<T> =
  | { kind: 'ok'; data: T }
  | { kind: 'refused'; message: string }
  | { kind: 'offline'; message: string }
  | { kind: 'failed'; status: number | null; message: string }

function refusalMessage(payload: unknown): string | null {
  if (payload === null || typeof payload !== 'object') return null
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (detail !== null && typeof detail === 'object') {
    const m = (detail as { message?: unknown }).message
    if (typeof m === 'string') return m
  }
  // FastAPI's own validation errors arrive as a list of {loc, msg}.
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => (d && typeof d === 'object' ? (d as { msg?: unknown }).msg : null))
      .filter((m): m is string => typeof m === 'string')
    if (parts.length) return parts.join('; ')
  }
  return null
}

/**
 * A write with the refusal handling every screen needs. Exported as `apiWrite`
 * for screen builders; `method` defaults to POST.
 */
async function write<T>(
  path: string,
  body: unknown,
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST',
): Promise<WriteResult<T>> {
  if (!LIVE) {
    return {
      kind: 'offline',
      message:
        'This screen is reading recorded fixtures, so there is nothing to write to. ' +
        'Point it at the live API to confirm terms.',
    }
  }
  try {
    const data = await request<T>(path, {
      method,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    return { kind: 'ok', data }
  } catch (e) {
    if (e instanceof ApiError) {
      const msg = refusalMessage(e.payload)
      // 409 belongs here too: "a template of that name already exists" is the
      // backend declining for a reason the reader can act on, not a transport
      // failure. Rendering it as "the request did not land" would be wrong.
      if (msg !== null && (e.status === 422 || e.status === 404 || e.status === 409)) {
        return { kind: 'refused', message: msg }
      }
      return { kind: 'failed', status: e.status, message: msg ?? e.message }
    }
    return { kind: 'failed', status: null, message: e instanceof Error ? e.message : String(e) }
  }
}

/** Record terms confirmed with a supplier, clearing the invented-terms flag. */
export function confirmSupplierTerms(
  supplierId: number,
  body: SupplierTermsIn,
): Promise<WriteResult<SupplierTermsResponse>> {
  return write(`/api/suppliers/${supplierId}/confirm`, body)
}

/** Record a shelf life somebody checked. Changes order size from the next run. */
export function confirmShelfLife(
  ingredientId: number,
  body: ShelfLifeIn,
): Promise<WriteResult<ShelfLifeResponse>> {
  return write(`/api/ingredients/${ingredientId}/shelf-life`, body)
}

export { write as apiWrite }
