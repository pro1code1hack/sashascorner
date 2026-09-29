// /order/basket: "My order". Dining, lines with edit / duplicate / remove, total,
// basket upsells, and the bar: Cancel order · CHECKOUT £x.
import { useState } from 'preact/hooks';
import { TopBar } from '../components/TopBar';
import { UpsellRow } from '../components/UpsellRow';
import { allergenText, gbp, lineTitle, optionSummary, plural } from '../format';
import { go, paths } from '../router';
import { basket, catalogue, config, lineTotal, loadCatalogue, loadError, online, productById } from '../store';
import type { Dining } from '../types';
import { BottomBar, Button, Loading, Notice, Segmented, Sheet, SkeletonLines, Stepper } from '../ui';
import { useTitle } from './useTitle';
import { needsSignIn, signedIn } from '../member';
import { SignInWall, accountHref } from '../account/SignInWall';

export function Basket() {
  useTitle('My order');
  const cat = catalogue.value;
  const cfg = config.value;
  const lines = basket.lines.value;
  const [confirm, setConfirm] = useState(false);
  const total = basket.subtotal(cat);
  const count = basket.count();
  const closed = cfg ? !cfg.enabled : false;
  const both = cfg ? cfg.dining.takeaway && cfg.dining.eat_in : true;
  // The sign-in wall: an account is needed to place an order, not to fill a basket.
  void signedIn.value;
  const wall = !closed && !!cat && lines.length > 0 && needsSignIn();
  const diningOptions: { value: Dining; label: string }[] = [
    { value: 'takeaway', label: 'Takeaway' },
    { value: 'eat_in', label: 'Eat in' },
  ];

  return (
    <>
      <TopBar back={{ href: paths.overview(), label: 'Menu' }} />
      <div class="wrap sh-view sh-basket">
        <h1 class="sh-h1" tabIndex={-1}>
          My order
        </h1>
        <p class="sh-muted">Takeaway and collection from {cfg?.cafe.address_line ?? '23 Commercial Street'}.</p>
        {closed && cfg && <Notice tone="warn">{cfg.closed_message}</Notice>}
        {basket.notice.value && (
          <Notice tone="warn" onDismiss={() => (basket.notice.value = null)}>
            {basket.notice.value}
          </Notice>
        )}

        {cfg && (
          <div class="sh-dining">
            {both ? (
              <Segmented label="Takeaway or eat in" options={diningOptions} value={basket.dining.value} onChange={(v) => basket.setDining(v)} />
            ) : (
              <p class="sh-dining__one">{cfg.dining.eat_in ? 'Eat in' : 'Takeaway'} only</p>
            )}
          </div>
        )}

        {!cat ? (
          loadError.value ? (
            <Notice tone="error">
              {loadError.value}{' '}
              <button type="button" class="sh-linkbtn" onClick={() => void loadCatalogue()}>
                Try again
              </button>
            </Notice>
          ) : (
            <>
              <Loading what="your order" />
              <SkeletonLines count={Math.max(1, Math.min(4, lines.length))} />
            </>
          )
        ) : lines.length === 0 ? (
          <div class="sh-empty">
            <p>Your order is empty.</p>
            <p>
              <Button href={paths.overview()} tone="caramel">
                See the menu
              </Button>
            </p>
          </div>
        ) : (
          <>
            <ul class="sh-lines" aria-label="Items in your order">
              {lines.map((l, i) => {
                const p = productById(cat, l.product_id);
                const summary = optionSummary(l, cat);
                return (
                  <li class="sh-line" key={`${i}-${l.product_id}-${l.menu_item_id}`}>
                    <div class="sh-line__main">
                      <p class="sh-line__title">{lineTitle(l, cat)}</p>
                      {summary && <p class="sh-line__opts">{summary}</p>}
                      {p && <p class="sh-line__allergens">{allergenText(p)}</p>}
                      <div class="sh-line__tools">
                        <Stepper value={l.qty} max={basket.MAX_QTY} onChange={(n) => basket.update(i, { ...l, qty: n })} minLabel="Remove" onMin={() => basket.remove(i)} label={`Quantity of ${lineTitle(l, cat)}`} />
                        {p && (
                          <a class="sh-iconbtn" href={paths.product(p.slug, i)} aria-label={`Edit ${lineTitle(l, cat)}`} title="Edit">
                            <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">
                              <path d="M3 17h4l9-9-4-4-9 9v4zM11 5l4 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
                            </svg>
                          </a>
                        )}
                        <button type="button" class="sh-iconbtn" aria-label={`Add another ${lineTitle(l, cat)}`} title="Duplicate" onClick={() => basket.duplicate(i)}>
                          <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">
                            <rect x="7" y="7" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.8" />
                            <path d="M13 7V3H3v10h4" fill="none" stroke="currentColor" stroke-width="1.8" />
                          </svg>
                        </button>
                        <button type="button" class="sh-iconbtn" aria-label={`Remove ${lineTitle(l, cat)}`} title="Remove" onClick={() => basket.remove(i)}>
                          <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">
                            <path d="M4 6h12M8 6V4h4v2M6 6l1 11h6l1-11" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
                          </svg>
                        </button>
                      </div>
                    </div>
                    <p class="sh-line__price num">{gbp(lineTotal(l, cat))}</p>
                  </li>
                );
              })}
            </ul>
            <dl class="sh-total">
              <dt>Total</dt>
              <dd class="num">{gbp(total)}</dd>
            </dl>
            <p class="sh-muted sh-total__note">
              {online.value ? 'Rewards discounts and the collection time are chosen at checkout.' : 'Checkout needs a connection. Your order is saved on this device.'}
            </p>
            {wall && <SignInWall />}
            {cat.basket_upsells.map((u) => (
              <UpsellRow upsell={u} cat={cat} key={u.heading} />
            ))}
          </>
        )}
      </div>

      {lines.length > 0 && (
        <BottomBar label="Order actions">
          <Button tone="paper" onClick={() => setConfirm(true)}>
            Cancel order
          </Button>
          <Button tone="caramel" class="sh-bar__cta" disabled={closed || !cat || !online.value} onClick={() => go(wall ? accountHref('signin') : paths.checkout())}>
            <span class="sh-bar__label">{wall ? 'Sign in to order' : 'Checkout'}</span>
            {!wall && <span class="num">{gbp(total)}</span>}
            <span class="sr-only">, {plural(count, 'item')}</span>
          </Button>
        </BottomBar>
      )}

      <Sheet
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Cancel this order?"
        actions={
          <>
            <Button tone="ghost" onClick={() => setConfirm(false)}>
              Keep it
            </Button>
            <Button
              tone="caramel"
              onClick={() => {
                basket.clear();
                setConfirm(false);
                go(paths.overview());
              }}
            >
              Cancel order
            </Button>
          </>
        }
      >
        <p>Everything in your order will be removed. Nothing has been sent to the café yet.</p>
      </Sheet>
    </>
  );
}
