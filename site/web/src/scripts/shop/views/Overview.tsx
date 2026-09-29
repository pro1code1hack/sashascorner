// /order: hero (copy from config), banners, Takeaway / Eat in, category tiles.
import { useState } from 'preact/hooks';
import { session } from '../api';
import { Carousel } from '../components/Carousel';
import { Photo } from '../components/Photo';
import { TopBar } from '../components/TopBar';
import { paths } from '../router';
import { basket, catalogue, config, loadCatalogue, loadError, loading, online } from '../store';
import type { Dining } from '../types';
import { Button, Loading, Notice, Segmented, Sheet, SkeletonTiles } from '../ui';
import { useTitle } from './useTitle';

export function Overview() {
  useTitle(null);
  const cfg = config.value;
  const cat = catalogue.value;
  const [allergens, setAllergens] = useState(false);
  const signedIn = !!session.token();

  const diningOptions: { value: Dining; label: string }[] = [
    { value: 'takeaway', label: 'Takeaway' },
    { value: 'eat_in', label: 'Eat in' },
  ];
  const bothDining = cfg ? cfg.dining.takeaway && cfg.dining.eat_in : true;

  return (
    <>
      <TopBar onAllergens={cfg ? () => setAllergens(true) : undefined} />
      <section class="sh-hero">
        <div class="wrap sh-hero__in">
          <p class="label sh-hero__label">Order ahead · takeaway and collection</p>
          <h1 class="sh-h1 sh-hero__title" tabIndex={-1}>
            {cfg?.hero_title ?? 'Order ahead. Collect from Commercial Street.'}
          </h1>
          <p class="sh-hero__sub">{cfg?.hero_subtitle ?? "Takeaway made to order. Skip the queue: pay here or at the counter, we'll have it ready."}</p>
          <p class="sh-hero__note">
            This is collection from {cfg?.cafe.address_line ?? '23 Commercial Street'}, not delivery. For delivery, see{' '}
            <a href="/delivery" data-native>
              Deliveroo and Just Eat
            </a>
            .
          </p>
          {cfg && (
            <button type="button" class="sh-btn sh-btn--sm sh-hero__allergens" onClick={() => setAllergens(true)}>
              Allergies or intolerances?
            </button>
          )}
          <p class="sh-hero__account">
            {signedIn ? (
              <a href={paths.account()}>Your Rewards card is signed in. Stamps count online too.</a>
            ) : (
              <a href={paths.account()}>Sign in or join Rewards to collect stamps on online orders.</a>
            )}
          </p>
        </div>
      </section>

      <div class="wrap sh-view">
        {cfg && !cfg.enabled && <Notice tone="warn">{cfg.closed_message}</Notice>}
        {cfg && cfg.enabled && !cfg.open_now && (
          <Notice tone="info">
            Online orders are closed right now{cfg.next_open_local ? `: back ${cfg.next_open_local.toLowerCase()}` : ''}. You can still browse the menu.
          </Notice>
        )}
        {basket.notice.value && (
          <Notice tone="warn" onDismiss={() => (basket.notice.value = null)}>
            {basket.notice.value}
          </Notice>
        )}
        {loadError.value && !loading.value && (
          <div class="sh-unreachable" role="alert">
            <p class="sh-unreachable__title">We can't reach the shop right now.</p>
            <p class="sh-muted">
              {online.value ? loadError.value : 'Your device is offline.'}
              {cat ? ' The menu below is the last one this device saw; prices are checked again when you order.' : ''}
            </p>
            <p>
              <Button tone="caramel" size="sm" onClick={() => void loadCatalogue()}>
                Try again
              </Button>
              <a class="sh-unreachable__alt" href="/menu" data-native>
                See the menu on the website
              </a>
            </p>
          </div>
        )}

        {cat && cat.banners.length > 0 && <Carousel banners={cat.banners} />}

        {cfg && (
          <div class="sh-dining">
            {bothDining ? (
              <Segmented label="Takeaway or eat in" options={diningOptions} value={basket.dining.value} onChange={(v) => basket.setDining(v)} />
            ) : (
              <p class="sh-dining__one">{cfg.dining.eat_in ? 'Eat in' : 'Takeaway'} only</p>
            )}
          </div>
        )}

        {cat ? (
          <section aria-labelledby="sh-cats">
            <h2 id="sh-cats" class="sh-h2">
              Menu
            </h2>
            <ul class="sh-cats">
              {cat.categories
                .filter((c) => c.product_count > 0)
                .map((c) => (
                  <li key={c.slug}>
                    <a class={['sh-cat', c.photo_is_fallback && c.photo_url && 'sh-cat--borrowed'].filter(Boolean).join(' ')} href={paths.category(c.slug)}>
                      <Photo src={c.photo_url} alt="" name={c.name} class="sh-cat__img" sizes="(min-width: 900px) 320px, 50vw" borrowed={!!c.photo_is_fallback} />
                      <span class="sh-cat__band">{c.name}</span>
                    </a>
                  </li>
                ))}
            </ul>
          </section>
        ) : (
          !loadError.value && (
            <section aria-label="Menu">
              <h2 class="sh-h2">Menu</h2>
              <Loading what="the menu" />
              <SkeletonTiles />
            </section>
          )
        )}
      </div>

      <Sheet
        open={allergens}
        onClose={() => setAllergens(false)}
        title="Allergies and intolerances"
        wide
        actions={
          <Button tone="caramel" onClick={() => setAllergens(false)}>
            Got it
          </Button>
        }
      >
        <p>{cfg?.allergen_notice}</p>
        <p class="sh-muted">Each item lists what it contains under Dietary info.</p>
      </Sheet>
    </>
  );
}
