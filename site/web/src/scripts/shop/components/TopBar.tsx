// The shop's own bar under the site header: back link or store line on the left, the
// account link and the basket pill on the right. Sticks to the top while scrolling.
import { gbp, plural } from '../format';
import { member, signedIn } from '../member';
import { paths, route } from '../router';
import { basket, catalogue, config } from '../store';

interface Props {
  back?: { href: string; label: string };
  title?: string;
  /** Opens the allergen sheet (overview only). */
  onAllergens?: () => void;
}

export function TopBar({ back, title, onAllergens }: Props) {
  const count = basket.count();
  const sub = basket.subtotal(catalogue.value);
  const onBasket = route.value.name === 'basket';
  // The first name once /me has answered; 'Account' while a token is here but unproven.
  const who = signedIn.value ? member.value?.first_name || 'Account' : 'Sign in';
  const cfg = config.value;
  return (
    <div class="sh-top">
      <div class="sh-top__in wrap">
        <div class="sh-top__lead">
          {back ? (
            <a class="sh-top__back" href={back.href}>
              <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">
                <path d="M12 4l-6 6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              <span>{back.label}</span>
            </a>
          ) : (
            <span class="sh-top__store">
              <strong>{cfg?.cafe.name ?? "Sasha's Corner"}</strong>
              <span class="sh-top__mode">Takeaway · collection</span>
            </span>
          )}
          {title && <span class="sh-top__title">{title}</span>}
        </div>
        <div class="sh-top__actions">
          {onAllergens && (
            <button type="button" class="sh-top__link sh-top__allergens" onClick={onAllergens}>
              Allergens
            </button>
          )}
          <a class="sh-top__link sh-top__account" href={paths.account()} aria-current={route.value.name === 'account' ? 'page' : undefined} aria-label={signedIn.value ? `Your profile, ${who}` : undefined}>
            <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">
              <circle cx="10" cy="7" r="3.4" fill="none" stroke="currentColor" stroke-width="1.8" />
              <path d="M3.5 17.5c.8-3.3 3.4-5 6.5-5s5.7 1.7 6.5 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
            </svg>
            <span>{who}</span>
          </a>
          <a class={['sh-pill', count === 0 && 'is-empty'].filter(Boolean).join(' ')} href={paths.basket()} aria-current={onBasket ? 'page' : undefined}>
            <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
              <path d="M4 6h12l-1 10H5L4 6zM7 6V4.5a3 3 0 016 0V6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
            </svg>
            <span class="sh-pill__text" aria-live="polite" aria-atomic="true">
              {count === 0 ? (
                'My order'
              ) : (
                <>
                  <span class="num">{gbp(sub)}</span>
                  <span class="sh-pill__sep" aria-hidden="true">
                    ·
                  </span>
                  <span class="num">{plural(count, 'item')}</span>
                </>
              )}
            </span>
          </a>
        </div>
      </div>
    </div>
  );
}
