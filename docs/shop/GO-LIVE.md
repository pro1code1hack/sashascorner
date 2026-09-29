# Going live: Order online (click & collect)

Companion to [`../loyalty/GO-LIVE.md`](../loyalty/GO-LIVE.md), which covers the stack
itself (DNS, `.env`, `docker compose`). This page is only what online ordering adds.
Everything below runs from the repo root on the server; `docker compose run --rm api
<cmd>` runs a one-off `cafeops` command against the live database.

## 0. What works with nothing configured

- Customers order at `https://<SITE_DOMAIN>/order`, choose a collection time, and **pay
  at the counter**. Orders appear on the back office's **Online orders › Live orders**
  board (rings while one is unaccepted) and, when Telegram is set up, on Sasha's phone
  with Accept / Start / Ready / Collected buttons.
- Collected orders become sales (channel "Website" on Money › Sales) and stamp the
  customer's Rewards card.
- Customers see their order's status on the page they land on after ordering; it
  updates by itself. No account is needed; signing in with the Rewards card only
  pre-fills their details and lets them use a free drink.

The shop is **off** until somebody turns it on: Online orders › Settings › "Take orders
online". Do that last, after the checks in §3.

## 1. `.env` keys (all optional; each switches one thing on)

| Feature | Keys | Without it |
|---|---|---|
| Card payment online (Stripe Checkout) | `CAFEOPS_STRIPE_SECRET_KEY`, `CAFEOPS_STRIPE_PUBLISHABLE_KEY`, `CAFEOPS_STRIPE_WEBHOOK_SECRET` | only "pay at the counter"; the "pay online" switch stays greyed with the reason |
| Customer updates by email | the `CAFEOPS_SMTP_*` keys (same as card recovery) | no emails; the status page still updates |
| Customer updates by push notification | `CAFEOPS_VAPID_PUBLIC_KEY`, `CAFEOPS_VAPID_PRIVATE_KEY`, `CAFEOPS_VAPID_SUBJECT` (`mailto:…`) | no "notify me" button on the status page |
| Customer updates by text (opt-in per order, ~4p a text) | the `CAFEOPS_TWILIO_*` keys (same as card recovery) | the "text me" checkbox does not appear |
| Owner alerts on Telegram, with action buttons | `CAFEOPS_TELEGRAM_BOT_TOKEN`, `CAFEOPS_TELEGRAM_OWNER_CHAT_ID` | the board and the sidebar badge are the only alert |
| Orders pushed onto the Lightspeed till | the `CAFEOPS_LIGHTSPEED_*` keys plus `CAFEOPS_LIGHTSPEED_BUSINESS_LOCATION_ID`, `…_ONLINE_ORDER_ENDPOINT_ID`, `…_PAYMENT_METHOD_CODE` (see `.env.example`); then Settings › Till → Lightspeed | orders live only in the back office; sales are still recorded here |

Push keys: `docker compose run --rm api cafeops shop vapid-keys` prints a pair. Set them
once and keep them: changing the keys silently drops every existing subscription.

Stripe: in the Stripe dashboard add a webhook endpoint for
`https://<SITE_DOMAIN>/api/shop/stripe/webhook` with the events
`checkout.session.completed` and `charge.refunded`, and paste its signing secret into
`CAFEOPS_STRIPE_WEBHOOK_SECRET`. Use test keys first (`sk_test_…`) and place one order
with Stripe's test card; the order must move from "Waiting for payment" to "New" on the
board within a few seconds. Refunds are started from the order page in the back office
and go through Stripe; a counter payment is refunded from the till.

Payments and the till are chosen per shop in Online orders › Settings › Payments
(provider: Stripe or Lightspeed; Lightspeed records payments but cannot take them online).

## 2. Set up the shop (back office, ten minutes)

1. **Menu items** — every item page has an "Online ordering" section: on sale today /
   shown online / featured, a customer description, allergens (mark **None (confirmed)**
   only when it is true; unlisted allergens tell customers to ask at the counter), a
   photo, and the option groups it uses. The same description and visibility drive the
   public website's menu page.
2. **Online orders › Option groups** — Milk, Extras and Customise are seeded. Adjust
   prices (oat +50p …), add syrups, link an option to a till modifier so stock depletes
   the right milk.
3. **Online orders › Banners & upsells** — the carousel on the shop's front page and
   the "Fancy a cake?" rows.
4. **Online orders › Settings** — ordering hours (seeded from the café's hours),
   lead time and slot size, orders per slot, days ahead, notices, payments, customer
   updates, Telegram.
5. **Categories** — order, photo and blurb from the Categories button on Menu items. A
   category without a photo borrows one from its first product.

## 3. Before switching it on

```bash
docker compose run --rm api cafeops doctor      # "online ordering" line says what is configured
```

Then on the live site, with the shop still off, open `https://<SITE_DOMAIN>/order`: it
must say ordering is taking a break (the message from Settings). Turn it on, place a
counter order for one drink, watch it ring on the board, accept → ready → collected, and
check Money › Transactions shows the receipt as `web:<code>` on the Website channel.
Cancel or collect it before opening for the day; nothing is deleted, cancelled orders
just stay in All orders.

## 4. Every day

- The board (`#/shop`) on the counter laptop or tablet with the sound turned on (the
  browser asks once). It re-rings every 30 seconds until an order is accepted.
- Sold out: Menu items › tick the rows › "Sold out today" (or the switch on the item
  page). Customers see "Sold out" and cannot add it.
- Closing early or a day off: Settings › closures, or simply turn ordering off; existing
  orders stay visible.
- Sasha's phone: the Telegram message's buttons do the same as the board.
