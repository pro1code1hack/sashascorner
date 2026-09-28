// Small DOM and formatting helpers for the scanner.

export function $<T extends Element = HTMLElement>(sel: string, root: ParentNode = document): T {
  const el = root.querySelector<T>(sel);
  if (!el) throw new Error(`staff: missing ${sel}`);
  return el;
}

type Attrs = Record<string, string | number | boolean | null | undefined | ((e: Event) => void)>;

/** h('button', {class: 'x', onclick: fn}, 'text', child) */
export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...kids: (Node | string | null | false)[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (typeof v === 'function') el.addEventListener(k.replace(/^on/, ''), v as EventListener);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
  for (const kid of kids) if (kid !== null && kid !== false) el.append(kid);
  return el;
}

/** Say something to screen readers. */
export function announce(text: string, assertive = false): void {
  const el = document.querySelector<HTMLElement>(assertive ? '[data-live-assertive]' : '[data-live]');
  if (!el) return;
  el.textContent = '';
  window.setTimeout(() => (el.textContent = text), 30);
}

export function showMsg(el: HTMLElement, text: string | null): void {
  el.textContent = text ?? '';
  el.hidden = !text;
}

const MONTH = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'Europe/London' });
const TIME = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' });
const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'Europe/London' });

export const fmt = {
  /** "YYYY-MM-DD" -> "March 2026" */
  since(iso: string): string {
    const d = new Date(`${iso.slice(0, 10)}T12:00:00Z`);
    return Number.isNaN(d.getTime()) ? iso : MONTH.format(d);
  },
  time(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : TIME.format(d);
  },
  day(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : DAY.format(d);
  },
  ago(iso: string): string {
    const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86_400) return `${Math.floor(s / 3600)} h ago`;
    const days = Math.floor(s / 86_400);
    return days === 1 ? 'yesterday' : `${days} days ago`;
  },
  /** ms -> "1:42" */
  clock(ms: number): string {
    const t = Math.max(0, Math.ceil(ms / 1000));
    return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
  },
  gbp(p: number): string {
    return `£${(p / 100).toFixed(2)}`;
  },
  plural(n: number, one: string, many = `${one}s`): string {
    return `${n} ${n === 1 ? one : many}`;
  },
};

export interface PinPad {
  value(): string;
  clear(): void;
  /** Handle a physical key press; true when it was a PIN key. */
  key(e: KeyboardEvent): boolean;
}

/**
 * Wire a keypad (buttons with data-key="0".."9"|"back"|"clear") and its dots.
 * `onSubmit` fires on Enter, or on its own at the sixth digit.
 */
export function pinPad(root: HTMLElement, dots: HTMLElement, onChange: (len: number) => void, onSubmit: (pin: string) => void, max = 6): PinPad {
  let v = '';
  const render = () => {
    dots.querySelectorAll('span').forEach((s, i) => s.classList.toggle('is-on', i < v.length));
    dots.setAttribute('aria-label', v.length ? `${v.length} of up to ${max} digits entered` : 'No digits entered');
    onChange(v.length);
  };
  const press = (k: string) => {
    if (k === 'back') v = v.slice(0, -1);
    else if (k === 'clear') v = '';
    else if (k === 'enter') {
      if (v.length >= 4) onSubmit(v);
      return;
    } else if (/^\d$/.test(k) && v.length < max) v += k;
    render();
    if (v.length === max) onSubmit(v);
  };
  root.addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-key]');
    if (!b || b.disabled) return;
    press(b.dataset.key!);
  });
  render();
  return {
    value: () => v,
    clear() {
      v = '';
      render();
    },
    key(e) {
      if (e.metaKey || e.ctrlKey || e.altKey) return false;
      if (/^\d$/.test(e.key)) press(e.key);
      else if (e.key === 'Backspace') press('back');
      else if (e.key === 'Escape') press('clear');
      else if (e.key === 'Enter') press('enter');
      else return false;
      e.preventDefault();
      return true;
    },
  };
}
