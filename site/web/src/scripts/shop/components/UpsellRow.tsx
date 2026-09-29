// "Fancy a cake?": products in a row with ADD. A product whose options all have
// defaults is added straight away; one that needs a choice opens its page.
import { gbp, kcal } from '../format';
import { go, paths } from '../router';
import { basket, type BasketLine } from '../store';
import { showToast } from '../toast';
import type { Catalogue, Product, Upsell } from '../types';
import { Photo } from './Photo';

/** A line with the default size and every default option; null when a required group has no default. */
export function quickLine(p: Product): BasketLine | null {
  const size = p.sizes.find((s) => s.code === p.default_size) ?? p.sizes[0];
  if (!size) return null;
  const option_ids: number[] = [];
  for (const g of p.option_groups) {
    const defaults = g.options.filter((o) => o.is_default && o.available).map((o) => o.id);
    const need = Math.max(g.min_select, g.required ? 1 : 0);
    if (defaults.length < need) return null;
    option_ids.push(...(g.kind === 'single' ? defaults.slice(0, 1) : defaults.slice(0, g.max_select ?? defaults.length)));
  }
  return { product_id: p.id, menu_item_id: size.menu_item_id, qty: 1, option_ids };
}

export function UpsellRow({ upsell, cat, exclude }: { upsell: Upsell; cat: Catalogue; exclude?: number }) {
  const products = upsell.product_ids
    .map((id) => cat.products.find((p) => p.id === id))
    .filter((p): p is Product => !!p && p.available && p.id !== exclude);
  if (products.length === 0) return null;
  const add = (p: Product) => {
    const line = quickLine(p);
    if (!line) {
      go(paths.product(p.slug));
      return;
    }
    basket.add(line);
    showToast(`${p.name} added to your order.`, { label: 'View order', href: paths.basket() });
  };
  return (
    <section class="sh-upsell" aria-labelledby={`up-${slug(upsell.heading)}`}>
      <h2 id={`up-${slug(upsell.heading)}`} class="sh-h2">
        {upsell.heading}
      </h2>
      <ul class="sh-upsell__list">
        {products.map((p) => (
          <li class="sh-upsell__item" key={p.id}>
            <a class="sh-upsell__link" href={paths.product(p.slug)}>
              <Photo src={p.photo_url} alt="" name={p.name} class="sh-upsell__img" />
              <span class="sh-upsell__text">
                <span class="sh-upsell__name">{p.name}</span>
                <span class="sh-upsell__meta num">
                  {gbp(p.from_price_pence)}
                  {p.kcal !== null && <span> · {kcal(p.kcal)}</span>}
                </span>
              </span>
            </a>
            <button type="button" class="sh-btn sh-btn--ghost sh-btn--sm" onClick={() => add(p)} aria-label={`Add ${p.name}`}>
              Add
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
