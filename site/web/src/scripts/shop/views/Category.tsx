// /order/c/<slug>: the category strip, the kcal notice and the product grid, with an ⓘ
// card per product. Loading paints the grid's shape, not a spinner.
import { useState } from 'preact/hooks';
import { InfoSheet } from '../components/InfoSheet';
import { ProductCard } from '../components/ProductCard';
import { TabStrip } from '../components/TabStrip';
import { TopBar } from '../components/TopBar';
import { paths } from '../router';
import { catalogue, config, loadCatalogue, loadError } from '../store';
import type { Product } from '../types';
import { Loading, Notice, SkeletonCards } from '../ui';
import { NotFound } from './NotFound';
import { useTitle } from './useTitle';

export function Category({ slug }: { slug: string }) {
  const cat = catalogue.value;
  const c = cat?.categories.find((x) => x.slug === slug);
  const [info, setInfo] = useState<Product | null>(null);
  useTitle(c?.name ?? 'Menu');
  if (!cat) {
    return (
      <>
        <TopBar back={{ href: paths.overview(), label: 'Menu' }} />
        <div class="wrap sh-view">
          {loadError.value ? (
            <Notice tone="error">
              {loadError.value}{' '}
              <button type="button" class="sh-linkbtn" onClick={() => void loadCatalogue()}>
                Try again
              </button>
            </Notice>
          ) : (
            <>
              <Loading what="the menu" />
              <SkeletonCards />
            </>
          )}
        </div>
      </>
    );
  }
  if (!c) return <NotFound what="category" />;
  const products = cat.products.filter((p) => p.category_slug === slug);
  const visible = cat.categories.filter((x) => x.product_count > 0);
  return (
    <>
      <TopBar back={{ href: paths.overview(), label: 'Menu' }} />
      <TabStrip categories={visible} active={slug} />
      <div class="wrap sh-view">
        <header class="sh-cathead">
          <h1 class="sh-h1" tabIndex={-1}>
            {c.name}
          </h1>
          {c.blurb && <p class="sh-cathead__blurb">{c.blurb}</p>}
          {config.value?.kcal_notice && <p class="sh-kcal-notice">{config.value.kcal_notice}</p>}
        </header>
        {products.length === 0 ? (
          <div class="sh-empty">
            <p>Nothing in this category right now.</p>
            <p>
              <a href={paths.overview()}>Back to the menu</a>
            </p>
          </div>
        ) : (
          <ul class="sh-grid">
            {products.map((p) => (
              <li key={p.id}>
                <ProductCard product={p} onInfo={setInfo} />
              </li>
            ))}
          </ul>
        )}
      </div>
      <InfoSheet product={info} open={info !== null} onClose={() => setInfo(null)} />
    </>
  );
}
