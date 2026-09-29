// The ordering API (cafeops `/api/shop/*`, proxied on the same origin: CONTRACT §4).
// Same ApiResult shape and error wording as the Rewards card (scripts/rewards/api.ts),
// and the same device token: a member who joined on /rewards is signed in here.
//
// Dev only: `?mock=1` answers from ./mock.ts instead of the network. The branch is
// behind `import.meta.env.DEV`, so a production build never ships mock.ts.
import { forgetToken, humanError, lastCard, saveToken, type ApiError, type ApiResult } from '../rewards/api';
import type { Catalogue, MeK, MeMember, MePatch, MyOrdersPage, OrderView, PlacedOrder, PlaceOrderBody, Quote, QuoteBody, ShopConfig, SlotsResponse } from './types';

export type { ApiError, ApiResult };
export { humanError };

const MOCK: string | null = import.meta.env.DEV ? new URLSearchParams(location.search).get('mock') : null;
export const isMock = () => MOCK !== null;
export const mockFlags = (): Set<string> => new Set((MOCK ?? '').split(',').filter(Boolean));

const TIMEOUT_MS = 15_000;

// ---- the card on this device -------------------------------------------------------
// Exactly the rewards web card's storage (`sc_card_last` + `sc_card_token:<id>`), so
// one sign-in serves /rewards, /c/<id> and /order.
export const session = {
  cardId(): string | null {
    return lastCard()?.id ?? null;
  },
  token(): string | null {
    return lastCard()?.token ?? null;
  },
  set(cardId: string, token: string): void {
    saveToken(cardId, token);
  },
  clear(): void {
    const id = lastCard()?.id;
    if (id) forgetToken(id);
  },
};

export async function request<T>(
  method: 'GET' | 'POST' | 'PATCH',
  path: string,
  opts: { body?: unknown; headers?: Record<string, string> } = {},
): Promise<ApiResult<T>> {
  if (import.meta.env.DEV && MOCK !== null) {
    try {
      const { mockApi } = await import('./mock');
      const r = await mockApi(method, path, opts.body, MOCK, session.token());
      return shape<T>(r.status, r.body);
    } catch {
      return { status: 0, ok: false, data: null, error: { error: 'network' } };
    }
  }
  const headers: Record<string, string> = { Accept: 'application/json', ...opts.headers };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  const token = session.token();
  if (token) headers['X-Card-Token'] = token;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(path, {
      method,
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      cache: 'no-store',
      credentials: 'same-origin',
      signal: ctrl.signal,
    });
    const body = r.status === 204 ? null : await r.json().catch(() => null);
    return shape<T>(r.status, body);
  } catch {
    return { status: 0, ok: false, data: null, error: { error: ctrl.signal.aborted ? 'timeout' : 'network' } };
  } finally {
    clearTimeout(timer);
  }
}

function shape<T>(status: number, body: unknown): ApiResult<T> {
  const ok = status >= 200 && status < 300;
  if (ok) return { status, ok, data: body as T, error: null };
  const b = (body ?? {}) as Record<string, unknown>;
  const detail = Array.isArray(b.detail)
    ? (b.detail as { msg?: string }[]).map((d) => d.msg ?? '').filter(Boolean).join(' ')
    : typeof b.detail === 'string'
      ? b.detail
      : undefined;
  return { status, ok, data: null, error: { error: typeof b.error === 'string' ? b.error : `http_${status}`, detail } };
}

const q = (params: Record<string, string | undefined>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) s.set(k, v);
  const str = s.toString();
  return str ? `?${str}` : '';
};

export const shopApi = {
  config: () => request<ShopConfig>('GET', '/api/shop/config'),
  catalogue: () => request<Catalogue>('GET', '/api/shop/catalogue'),
  slots: (date?: string) => request<SlotsResponse>('GET', `/api/shop/slots${q({ date })}`),
  quote: (body: QuoteBody) => request<Quote>('POST', '/api/shop/quote', { body }),
  placeOrder: (body: PlaceOrderBody) => request<PlacedOrder>('POST', '/api/shop/orders', { body }),
  order: (code: string, token: string) =>
    request<OrderView>('GET', `/api/shop/orders/${encodeURIComponent(code)}`, { headers: { 'X-Order-Token': token } }),
  cancel: (code: string, token: string) =>
    request<OrderView>('POST', `/api/shop/orders/${encodeURIComponent(code)}/cancel`, { headers: { 'X-Order-Token': token } }),
  me: () => request<MeK>('GET', '/api/shop/me'),
  // Profile (Agent K; backend Agent J). Both carry the card token like everything else.
  myOrders: (page = 1, pageSize = 10) => request<MyOrdersPage>('GET', `/api/shop/me/orders${q({ page: String(page), page_size: String(pageSize) })}`),
  // J answers with the `member` block alone; the mock with the whole /me. Both are handled.
  updateMe: (body: MePatch) => request<MeK | MeMember>('PATCH', '/api/shop/me', { body }),
};

/** Shop-specific wording on top of the loyalty translations. */
export function shopError(r: Pick<ApiResult<unknown>, 'status' | 'error'>): string {
  const code = r.error?.error ?? '';
  if (code === 'shop_closed') return r.error?.detail || 'Online ordering is closed right now.';
  if (code === 'price_changed') return 'Prices changed while you were ordering. Your order has been updated: please check it and try again.';
  if (code === 'slot_full') return 'That collection time has just filled up. Please pick another.';
  if (code === 'bad_transition') return r.error?.detail || 'That order can no longer be changed.';
  if (code === 'sign_in_required') return 'Sign in with your Rewards card to place your order.';
  return humanError(r);
}
