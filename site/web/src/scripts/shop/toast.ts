// A short confirmation ("Added to your order") shown above the bottom bar and read
// out by screen readers through an aria-live region in App.tsx.
import { signal } from '@preact/signals';

export const toast = signal<{ text: string; action?: { label: string; href: string } } | null>(null);
let timer: number | undefined;

export function showToast(text: string, action?: { label: string; href: string }, ms = 3200): void {
  toast.value = { text, action };
  if (timer !== undefined) window.clearTimeout(timer);
  timer = window.setTimeout(() => (toast.value = null), ms);
}

// A toast for the next page when that page is a full load away (the /account island
// sends "Order again" to /order/basket): kept for one visit in sessionStorage and
// shown by the shell on mount.
const STASH_KEY = 'sc.shop.toast.v1';
export function stashToast(text: string): void {
  try {
    sessionStorage.setItem(STASH_KEY, text);
  } catch {
    /* storage off: nothing to show, nothing lost */
  }
}
export function showStashedToast(): void {
  try {
    const t = sessionStorage.getItem(STASH_KEY);
    if (!t) return;
    sessionStorage.removeItem(STASH_KEY);
    showToast(t);
  } catch {
    /* fine */
  }
}
