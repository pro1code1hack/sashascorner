// Shared by the checkout, account and status screens (Agent D): the order token on
// this device, the contact-field rules the loyalty join form uses, and small pieces of
// page furniture. Styles: ./d.css (prefix `sd-`; C's shared components keep `sh-`).
import type { ComponentChildren } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { paths } from '../router';
import { catalogue, config, loadCatalogue } from '../store';
import './d.css';

// ---- the order's access token on this device ---------------------------------------
// `POST /api/shop/orders` answers with an access_token; the status page reads it from
// `?t=` once (the link in a confirmation email) and from here on every reload.
const ORDER_KEY = (code: string) => `sc.shop.order:${code.toUpperCase()}`;
/** Marker for an online-paid order whose basket must be cleared once payment lands. */
const PENDING_KEY = (code: string) => `sc.shop.pending:${code.toUpperCase()}`;

export function saveOrderToken(code: string, token: string): void {
  try {
    localStorage.setItem(ORDER_KEY(code), token);
  } catch {
    /* storage off: the token in the URL still works for this visit */
  }
}
export function loadOrderToken(code: string): string | null {
  try {
    return localStorage.getItem(ORDER_KEY(code));
  } catch {
    return null;
  }
}
export function markPending(code: string): void {
  try {
    localStorage.setItem(PENDING_KEY(code), '1');
  } catch {
    /* fine */
  }
}
export function takePending(code: string): boolean {
  try {
    const had = localStorage.getItem(PENDING_KEY(code)) === '1';
    if (had) localStorage.removeItem(PENDING_KEY(code));
    return had;
  } catch {
    return false;
  }
}

// ---- contact rules (the same ones scripts/rewards/join.ts applies) -----------------
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const phoneDigits = (s: string) => s.replace(/[^\d]/g, '');
export const looksLikePhone = (s: string) =>
  /^\+?[\d\s()-]{7,20}$/.test(s.trim()) && phoneDigits(s).length >= 10 && phoneDigits(s).length <= 15;
export const looksLikeEmail = (s: string) => EMAIL_RE.test(s.trim());

/** "SC-ABC123" from either "ABC123" or "sc-abc123". */
export const displayCode = (code: string) => `SC-${code.replace(/^sc-/i, '').toUpperCase()}`;

/** "Tue 14:20" in Europe/London, from an ISO timestamp; falls back to the raw string. */
export function localTime(iso: string | null | undefined, withDay = false): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  try {
    const time = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' }).format(d);
    if (!withDay) return time;
    const day = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'Europe/London' }).format(d);
    return `${day}, ${time}`;
  } catch {
    return iso;
  }
}

/** Loads config + catalogue once for a screen that may be the first one opened. */
export function useShopData() {
  const [ready, setReady] = useState(!!config.value && !!catalogue.value);
  useEffect(() => {
    if (config.value && catalogue.value) return;
    void loadCatalogue().then(() => setReady(true));
  }, []);
  return ready;
}

// ---- furniture ---------------------------------------------------------------------
/** A screen's top: back link, title, an optional line under it. */
export function PageHead({ back, backLabel = 'Back', title, children }: { back?: string; backLabel?: string; title: string; children?: ComponentChildren }) {
  // The router scrolls a new screen to the top and restores the position on Back;
  // the shell (App.tsx) focuses `.sh-h1`.
  return (
    <header class="sd-head">
      {back && (
        <a class="sd-back" href={back}>
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
            <path d="M12 4l-6 6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          {backLabel}
        </a>
      )}
      <h1 class="sh-h1 sd-title" tabIndex={-1}>
        {title}
      </h1>
      {children}
    </header>
  );
}

/** Uppercase display label above a block of the form. */
export function SectionLabel({ children, id }: { children: ComponentChildren; id?: string }) {
  return (
    <h2 class="sd-label" id={id}>
      {children}
    </h2>
  );
}

/** "Takeaway / collection" said plainly, with the address. */
export function CollectionLine({ short }: { short?: boolean }) {
  const cafe = config.value?.cafe;
  const where = cafe ? `${cafe.address_line}, ${cafe.postcode}` : '23 Commercial Street, DD1 3DD';
  return (
    <p class="sd-collect">
      <strong>Takeaway / collection.</strong> {short ? `Collect from ${where}.` : `Collect from the counter at ${where}. This is not delivery: for Deliveroo or Just Eat see `}
      {!short && <a href="/delivery">cakes &amp; delivery</a>}
      {!short && '.'}
    </p>
  );
}

/** Where a screen has nothing to show yet (empty basket, no order). */
export function EmptyState({ title, children, action }: { title: string; children?: ComponentChildren; action?: ComponentChildren }) {
  return (
    <div class="sd-empty">
      <h2 class="sd-empty__title">{title}</h2>
      {children && <div class="sd-empty__text">{children}</div>}
      {action && <div class="sd-empty__act">{action}</div>}
    </div>
  );
}

/** The statement of an order: lines, subtotal, discount, total. Money as pence in. */
export function Statement({
  lines,
  subtotal,
  discount,
  total,
  discountText,
  gbp,
}: {
  lines: {
    name: string;
    size_label: string;
    qty: number;
    line_total_pence: number;
    options: { name: string; price_delta_pence: number }[];
    problems?: string[];
    /** The allergen sentence for this line (format.ts `allergenText`), when the product is known. */
    allergens?: string | null;
  }[];
  subtotal: number;
  discount: number;
  total: number;
  discountText?: string | null;
  gbp: (p: number) => string;
}) {
  return (
    <div class="sd-stmt">
      <ul class="sd-stmt__lines">
        {lines.map((l, i) => (
          <li key={i} class={l.problems?.length ? 'has-problem' : undefined}>
            <span class="sd-stmt__qty num">{l.qty}×</span>
            <span class="sd-stmt__name">
              {l.name}
              {l.size_label ? ` (${l.size_label})` : ''}
              {l.options.length > 0 && <span class="sd-stmt__opts">{l.options.map((o) => o.name).join(', ')}</span>}
              {l.allergens && <span class="sd-stmt__opts">{l.allergens}</span>}
              {l.problems?.map((p) => (
                <span class="sd-stmt__problem" key={p}>
                  {p}
                </span>
              ))}
            </span>
            <span class="sd-stmt__amt num">{gbp(l.line_total_pence)}</span>
          </li>
        ))}
      </ul>
      <dl class="sd-stmt__sums">
        {(discount > 0 || subtotal !== total) && (
          <>
            <dt>Subtotal</dt>
            <dd class="num">{gbp(subtotal)}</dd>
          </>
        )}
        {discount > 0 && (
          <>
            <dt>{discountText ?? 'Free drink'}</dt>
            <dd class="num">−{gbp(discount)}</dd>
          </>
        )}
        <dt class="sd-stmt__total">Total</dt>
        <dd class="sd-stmt__total num">{gbp(total)}</dd>
      </dl>
    </div>
  );
}

export const accountPath = (next?: 'checkout') => `${paths.account()}${next ? `?next=${next}` : ''}`;
