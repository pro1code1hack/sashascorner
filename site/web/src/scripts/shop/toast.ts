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
