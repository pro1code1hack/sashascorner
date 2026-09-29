// The order-ahead app. Mounted client-only from pages/order.astro; routes live in
// router.ts, state in store.ts. Checkout, account and status screens (checkout/,
// account/, status/) load lazily.
import { useEffect, useRef } from 'preact/hooks';
import { lazy, Suspense } from 'preact/compat';
import { interceptLinks, restoreScroll, route } from './router';
import { loadCatalogue, online } from './store';
import { loadMember } from './member';
import { showStashedToast, toast } from './toast';
import { Overview } from './views/Overview';
import { Category } from './views/Category';
import { Product } from './views/Product';
import { Basket } from './views/Basket';
import { NotFound } from './views/NotFound';
import { isMock } from './api';
import { Loading } from './ui';

const Checkout = lazy(() => import('./checkout/Checkout'));
const Status = lazy(() => import('./status/Status'));

function Fallback() {
  return (
    <div class="wrap sh-view">
      <Loading what="this page" />
      <div class="sh-skel-grid" aria-hidden="true" style="max-width:760px">
        <span class="sh-skel" style="width:40%;height:2.2rem" />
        <span class="sh-skel" style="width:90%;height:1rem" />
        <span class="sh-skel" style="width:70%;height:1rem" />
      </div>
    </div>
  );
}

export default function App() {
  const root = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  const r = route.value;

  useEffect(() => {
    void loadCatalogue();
    void loadMember();
    showStashedToast();
    if (root.current) interceptLinks(root.current);
    if (isMock()) console.info('[shop] mock mode');
  }, []);

  // A new screen: focus on its heading (not on the first paint, which would steal
  // focus from the page). Back/forward: the position the customer left, once the
  // screen has rendered; a fresh navigation already scrolled to the top (router.ts).
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const id = requestAnimationFrame(() => {
      const h1 = root.current?.querySelector<HTMLElement>('.sh-h1');
      h1?.focus({ preventScroll: true });
      const y = restoreScroll.value;
      if (y !== null) {
        restoreScroll.value = null;
        window.scrollTo({ top: y, left: 0, behavior: 'instant' });
      }
    });
    return () => cancelAnimationFrame(id);
  }, [r.path]);

  let view;
  switch (r.name) {
    case 'overview':
      view = <Overview />;
      break;
    case 'category':
      view = <Category slug={r.params.slug} />;
      break;
    case 'product':
      view = <Product key={`${r.params.slug}:${r.query.get('line') ?? ''}`} slug={r.params.slug} lineParam={r.query.get('line')} />;
      break;
    case 'basket':
      view = <Basket />;
      break;
    case 'checkout':
      view = (
        <Suspense fallback={<Fallback />}>
          <Checkout />
        </Suspense>
      );
      break;
    case 'account':
      // The account moved site-wide (owner, 2026-09-29): /account keeps `?next=checkout`.
      location.replace(`/account${location.search}${location.hash}`);
      view = <Fallback />;
      break;
    case 'status':
      view = (
        <Suspense fallback={<Fallback />}>
          <Status />
        </Suspense>
      );
      break;
    default:
      view = <NotFound />;
  }

  const t = toast.value;
  return (
    <div class="sh-app" ref={root} data-view={r.name}>
      {!online.value && (
        <div class="sh-offline" role="status">
          <span>You're offline. Your order is saved on this device; checkout needs a connection.</span>
        </div>
      )}
      {view}
      <div class="sh-toast-region" aria-live="polite" aria-atomic="true">
        {t && (
          <div class="sh-toast on-dark" key={t.text}>
            <span>{t.text}</span>
            {t.action && <a href={t.action.href}>{t.action.label}</a>}
          </div>
        )}
      </div>
    </div>
  );
}
