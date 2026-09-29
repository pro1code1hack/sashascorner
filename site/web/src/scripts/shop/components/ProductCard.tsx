// One product in a category grid: photo, badge, NAME, price left, kcal right, the ⓘ
// button, and the count bubble when it is already in the basket. Sold out: greyed,
// not a link. The ⓘ is a sibling of the link (a button inside an anchor is not HTML).
import { gbp, kcal } from '../format';
import { paths } from '../router';
import { basket } from '../store';
import type { Product } from '../types';
import { Photo } from './Photo';

export function ProductCard({ product: p, onInfo }: { product: Product; onInfo?: (p: Product) => void }) {
  const inBasket = basket.lines.value.filter((l) => l.product_id === p.id).reduce((n, l) => n + l.qty, 0);
  const multi = p.sizes.length > 1 && p.sizes.some((s) => s.price_pence !== p.from_price_pence);
  const inner = (
    <>
      <span class="sh-card__media">
        <Photo src={p.photo_url} alt="" name={p.name} class="sh-card__img" sizes="(min-width: 900px) 300px, 50vw" />
        {p.badge && <span class="sh-card__badge">{p.badge}</span>}
        {inBasket > 0 && (
          <span class="sh-card__count num" aria-label={`${inBasket} in your order`}>
            {inBasket}
          </span>
        )}
        {!p.available && <span class="sh-card__soldout">Sold out</span>}
      </span>
      <span class="sh-card__name">{p.name}</span>
      <span class="sh-card__meta">
        <span class="num">
          {multi && <span class="sh-card__from">from </span>}
          {gbp(p.from_price_pence)}
        </span>
        {p.kcal !== null && <span class="num sh-card__kcal">{kcal(p.kcal)}</span>}
      </span>
    </>
  );
  const info = onInfo && (
    <button type="button" class="sh-card__info" aria-label={`About ${p.name}`} onClick={() => onInfo(p)}>
      <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">
        <circle cx="10" cy="10" r="8.2" fill="none" stroke="currentColor" stroke-width="1.6" />
        <path d="M10 9v5M10 6.2v.4" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" />
      </svg>
    </button>
  );
  if (!p.available) {
    return (
      <div class="sh-card is-off">
        <div class="sh-card__link" aria-disabled="true">
          {inner}
        </div>
        {info}
      </div>
    );
  }
  return (
    <div class="sh-card">
      <a class="sh-card__link" href={paths.product(p.slug)}>
        {inner}
      </a>
      {info}
    </div>
  );
}
