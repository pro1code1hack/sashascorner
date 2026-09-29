// The horizontally scrolling category strip on a category page. Plain links, the
// current one underlined and centred in the strip before the first paint (a layout
// effect, so the strip never appears at one position and jumps to another).
import { useLayoutEffect, useRef } from 'preact/hooks';
import { paths } from '../router';
import type { Category } from '../types';

export function TabStrip({ categories, active }: { categories: Category[]; active: string }) {
  const ref = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const cur = ref.current?.querySelector<HTMLElement>('[aria-current="page"]');
    const strip = ref.current;
    if (!cur || !strip) return;
    // Centre the current tab in the strip without scrolling the page itself.
    const left = cur.offsetLeft - (strip.clientWidth - cur.offsetWidth) / 2;
    strip.scrollLeft = Math.max(0, left);
  }, [active]);
  return (
    <nav class="sh-tabs" aria-label="Categories" ref={ref}>
      <ul class="sh-tabs__list">
        {categories.map((c) => (
          <li key={c.slug}>
            <a href={paths.category(c.slug)} class="sh-tabs__tab" aria-current={c.slug === active ? 'page' : undefined}>
              {c.name}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
