// The ⓘ card: what is in an item, without leaving the grid. Energy, allergens (in the
// same honest words as the item page), dietary marks and the description.
import { allergenText, DIETARY_WORDS, gbp, kcal, word } from '../format';
import { paths } from '../router';
import { config } from '../store';
import type { Product } from '../types';
import { Button, Sheet } from '../ui';

export function InfoSheet({ product: p, open, onClose }: { product: Product | null; open: boolean; onClose: () => void }) {
  const cfg = config.value;
  return (
    <Sheet
      open={open && !!p}
      onClose={onClose}
      title={p?.name ?? ''}
      actions={
        p && p.available ? (
          <Button tone="caramel" href={paths.product(p.slug)} onClick={onClose}>
            Choose options
          </Button>
        ) : (
          <Button tone="ghost" onClick={onClose}>
            Close
          </Button>
        )
      }
    >
      {p && (
        <div class="sh-infocard">
          {p.description && <p>{p.description}</p>}
          {p.note && <p class="sh-item__note">{p.note}</p>}
          <dl class="sh-infocard__facts">
            <dt>Price</dt>
            <dd class="num">
              {p.sizes.length > 1 ? 'from ' : ''}
              {gbp(p.from_price_pence)}
            </dd>
            <dt>Energy</dt>
            <dd class="num">{p.kcal !== null ? kcal(p.kcal) : '—'}</dd>
            <dt>Allergens</dt>
            <dd>{allergenText(p)}</dd>
            {p.dietary.length > 0 && (
              <>
                <dt>Dietary</dt>
                <dd>{p.dietary.map((x) => word(DIETARY_WORDS, x)).join(' · ')}</dd>
              </>
            )}
          </dl>
          {!p.available && <p class="sh-muted">Sold out today.</p>}
          {cfg?.allergen_notice && <p class="sh-muted sh-info__small">{cfg.allergen_notice}</p>}
        </div>
      )}
    </Sheet>
  );
}
