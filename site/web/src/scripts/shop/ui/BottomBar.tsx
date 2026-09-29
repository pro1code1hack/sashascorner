// The olive bar pinned to the bottom of the viewport (Black Sheep's charcoal CTA bar).
// Its measured height goes into --sh-bar-h on the app root, so the page's bottom
// padding and the toast follow it when text is zoomed or the label wraps; the bar
// itself pads by --safe-b (shop.css) for a phone's home indicator.
import type { ComponentChildren } from 'preact';
import { useLayoutEffect, useRef } from 'preact/hooks';

interface Props {
  children: ComponentChildren;
  /** aria-label for the region. */
  label?: string;
}

export function BottomBar({ children, label = 'Order actions' }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    const root = el?.closest<HTMLElement>('.sh-app');
    if (!el || !root) return;
    const set = () => root.style.setProperty('--sh-bar-h', `${Math.ceil(el.getBoundingClientRect().height)}px`);
    set();
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(set);
    ro?.observe(el);
    return () => {
      ro?.disconnect();
      root.style.removeProperty('--sh-bar-h');
    };
  }, []);
  return (
    <div class="sh-bar on-dark" role="region" aria-label={label} ref={ref}>
      <div class="sh-bar__in">{children}</div>
    </div>
  );
}
