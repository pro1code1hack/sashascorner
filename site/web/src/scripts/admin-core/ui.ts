// Toasts, screen-reader announcements and a tiny DOM builder. Shared by every admin page.

/** Say something to screen readers without showing it. */
export function announce(text: string, assertive = false): void {
  const el = document.querySelector<HTMLElement>(assertive ? '[data-live-assertive]' : '[data-live]');
  if (!el) return;
  el.textContent = '';
  // A fresh text node after a tick makes repeated identical messages re-announce.
  window.setTimeout(() => (el.textContent = text), 30);
}

export type ToastTone = 'info' | 'bad';

export interface ToastOptions {
  tone?: ToastTone;
  /** ms before it goes; errors stay until dismissed when 0. Default 4000 (info) / 8000 (bad). */
  timeout?: number;
  /** One action button, e.g. Undo. */
  action?: { label: string; run: () => void };
}

/** Show a short message at the bottom (and announce it). */
export function toast(text: string, opts: ToastOptions = {}): () => void {
  const tone = opts.tone ?? 'info';
  const host = document.querySelector<HTMLElement>('[data-toasts]');
  announce(text, tone === 'bad');
  if (!host) return () => {};
  const el = document.createElement('div');
  el.className = `adm-toast${tone === 'bad' ? ' adm-toast--bad' : ''}`;
  // The live region already speaks it; keep the visual copy out of the tree twice.
  el.setAttribute('aria-hidden', opts.action ? 'false' : 'true');
  const span = document.createElement('span');
  span.textContent = text;
  span.style.flex = '1';
  el.append(span);
  const close = () => {
    el.remove();
    window.clearTimeout(timer);
  };
  if (opts.action) {
    const a = document.createElement('button');
    a.type = 'button';
    a.textContent = opts.action.label;
    a.addEventListener('click', () => {
      close();
      opts.action!.run();
    });
    el.append(a);
  }
  const x = document.createElement('button');
  x.type = 'button';
  x.setAttribute('aria-label', 'Dismiss');
  x.textContent = '×';
  x.addEventListener('click', close);
  el.append(x);
  host.append(el);
  while (host.children.length > 3) host.firstElementChild?.remove();
  const ms = opts.timeout ?? (tone === 'bad' ? 8000 : 4000);
  const timer = ms > 0 ? window.setTimeout(close, ms) : 0;
  return close;
}

export const toastError = (text: string) => toast(text, { tone: 'bad' });

type Child = Node | string | number | null | undefined | false;
type Attrs = Record<string, string | number | boolean | null | undefined | EventListener>;

/**
 * h('button', { class: 'adm-btn', type: 'button', onclick: fn }, 'Save')
 * Attributes set to false/null/undefined are skipped; `true` sets an empty attribute.
 * Strings are always text, never HTML.
 */
export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...kids: (Child | Child[])[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v === null || v === undefined) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v as EventListener);
    else if (k === 'class') el.className = String(v);
    else if (k === 'text') el.textContent = String(v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/** Replace an element's children. */
export function fill(el: Element, ...kids: (Child | Child[])[]): void {
  el.replaceChildren();
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

/** Run an async action from a button: disable it, relabel it, restore it. */
export async function busy<T>(btn: HTMLButtonElement | null, pendingLabel: string, fn: () => Promise<T>): Promise<T> {
  if (!btn) return fn();
  const label = btn.textContent;
  const width = btn.offsetWidth;
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  if (width) btn.style.minWidth = `${width}px`;
  btn.textContent = pendingLabel;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    btn.textContent = label;
    btn.style.minWidth = '';
  }
}

/** Mark fields invalid from an ApiError's `.fields` (keys matched to [name] or [data-field]). */
export function showFieldErrors(form: HTMLElement, fields: Record<string, string>): boolean {
  clearFieldErrors(form);
  let any = false;
  for (const [key, msg] of Object.entries(fields)) {
    const last = key.split('.').pop() ?? key;
    const input =
      form.querySelector<HTMLElement>(`[data-field="${CSS.escape(key)}"]`) ??
      form.querySelector<HTMLElement>(`[name="${CSS.escape(key)}"]`) ??
      form.querySelector<HTMLElement>(`[name="${CSS.escape(last)}"]`);
    if (!input) continue;
    any = true;
    input.setAttribute('aria-invalid', 'true');
    const err = h('p', { class: 'adm-err', id: `err-${Math.random().toString(36).slice(2, 8)}`, 'data-err': true }, msg);
    const wrap = input.closest('.adm-field') ?? input.parentElement;
    wrap?.append(err);
    input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), err.id].filter(Boolean).join(' '));
  }
  const first = form.querySelector<HTMLElement>('[aria-invalid="true"]');
  first?.focus();
  return any;
}

export function clearFieldErrors(form: HTMLElement): void {
  form.querySelectorAll('[data-err]').forEach((e) => {
    const id = e.id;
    form.querySelectorAll(`[aria-describedby~="${id}"]`).forEach((i) => {
      const rest = (i.getAttribute('aria-describedby') ?? '').split(' ').filter((x) => x && x !== id);
      if (rest.length) i.setAttribute('aria-describedby', rest.join(' '));
      else i.removeAttribute('aria-describedby');
    });
    e.remove();
  });
  form.querySelectorAll('[aria-invalid="true"]').forEach((i) => i.removeAttribute('aria-invalid'));
}

/**
 * A right-hand panel (full screen on phones). Traps focus, closes on Esc,
 * returns focus to whatever opened it.
 */
export function openDrawer(opts: { title: string; body: Node; foot?: Node; onClose?: () => void; label?: string }): { close: () => void; el: HTMLElement } {
  const opener = document.activeElement as HTMLElement | null;
  const titleId = `dr-${Math.random().toString(36).slice(2, 8)}`;
  const closeBtn = h('button', { type: 'button', class: 'adm-drawer-close', 'aria-label': 'Close' }, '×');
  const el = h(
    'div',
    { class: 'adm-drawer', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId },
    h('div', { class: 'adm-drawer-head' }, h('h2', { id: titleId }, opts.title), closeBtn),
    h('div', { class: 'adm-drawer-body' }, opts.body),
    opts.foot ? h('div', { class: 'adm-drawer-foot' }, opts.foot) : null,
  );
  const scrim = h('div', { class: 'adm-scrim', 'aria-hidden': 'true' });
  Object.assign(scrim.style, { position: 'fixed', inset: '0', zIndex: '45', background: 'rgb(31 38 51 / .18)' });
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    } else if (e.key === 'Tab') {
      const f = [...el.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter(
        (x) => !x.hasAttribute('disabled') && x.offsetParent !== null,
      );
      if (!f.length) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKey);
    el.remove();
    scrim.remove();
    document.documentElement.style.overflow = '';
    opts.onClose?.();
    if (opener && document.contains(opener)) opener.focus();
  }
  closeBtn.addEventListener('click', close);
  scrim.addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  document.body.append(scrim, el);
  document.documentElement.style.overflow = 'hidden';
  window.setTimeout(() => (el.querySelector<HTMLElement>('input, select, textarea, button:not(.adm-drawer-close)') ?? closeBtn).focus(), 0);
  return { close, el };
}

/** "Tap again to …" — arms for 4s, fires on the second press (design-system §5.1). */
export function confirmTwice(btn: HTMLButtonElement, armedLabel: string, run: () => void): void {
  let armed = 0;
  const original = btn.textContent ?? '';
  btn.addEventListener('click', () => {
    if (armed) {
      window.clearTimeout(armed);
      armed = 0;
      btn.textContent = original;
      run();
      return;
    }
    btn.textContent = armedLabel;
    announce(armedLabel);
    armed = window.setTimeout(() => {
      armed = 0;
      btn.textContent = original;
    }, 4000);
  });
}
