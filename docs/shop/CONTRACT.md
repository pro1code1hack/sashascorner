# Sasha's Corner — Order online (click & collect) — build contract

**Binding for every agent on this build. Written 2026-09-28 by the integrator.**
Owner's ask: clone Black Sheep Coffee's online ordering (vmos.io) for Sasha's Corner —
customers order ahead for **takeaway / collection** (say so explicitly), an **admin panel**
in the Café Ops back office, and an updated **landing page**. Customer accounts are the
**loyalty (Rewards) card** — no second account system. More customisable than the
reference: everything a customer sees is editable in the admin.

Read first: `CLAUDE.md` (no tests; verify by running), `ARCHITECTURE.md` §1 and §8E,
`docs/design/specs/DECISIONS.md` §25–27, `docs/loyalty/CONTRACT.md` §2–§4,
`site/BRIEF.md` (facts, design system), `docs/design/specs/FRONTEND-KIT.md` (back office).

**The working tree has uncommitted work from other sessions** (menu, sales dashboard,
browser agents, sale.source migration). Never `git stash`, `git checkout --`, revert or
reformat files you do not own. Touch shared files only at the insertion points in §9.

---

## 0. What the reference does (measured 2026-09-28)

`blacksheepcoffee.vmos.io` — Montserrat, ink `rgb(65,64,66)`, square corners, white
ground, dark charcoal CTA bars pinned to the bottom of the viewport.

1. **Menu overview** — top bar: store name + "Change" · "Allergens" · "Login" · basket
   pill "£4.59 / 1 item". Promo image carousel (dots). **Takeaway / Eat In** toggle
   (segmented, tick icon on the chosen one). Category grid: big image tiles with a
   dark band carrying the category name in caps (uppercase display face).
2. **Category page** — horizontally scrolling category tab strip (active underlined),
   a kcal notice line, then a 4-up product grid: image, badge stickers ("I'm back!",
   "CBD blend"), name in caps, price left, kcal right, an ⓘ button, and a count
   bubble when the item is already in the basket.
3. **Item customisation** (full-screen route) — left column: photo, NAME, description,
   tabs *Dietary info / Ingredients / Nutrition*, Energy, Allergen icons. Right column:
   option groups as tile rows — **SIZE** (Regular / Large +£0.30 +69 kcal), **CUP**
   (Takeaway cup / Own cup), **COFFEE BEAN** (image tiles), **MILK** (Whole 182 kcal /
   Skimmed / Oat / Almond £0.49 …), **EXTRAS AND SYRUPS** (collapsed, "Add extras and
   syrups"), **CUSTOMISE** (checkboxes: chocolate dusting, extra hot, single shot …),
   upsell rows **FANCY A PASTRY? / FANCY A WAFFLE? / ADD A SNACK** (products with price
   and kcal). Sticky bottom bar: − 1 + stepper · **ADD TO ORDER · £4.59**.
4. **Basket "MY ORDER"** — dining toggle again, lines "Pumpkin Spice Latte (Regular)"
   with option summary "Takeaway cup £0.00, Blue Volcano, Whole milk", edit / delete /
   duplicate icon buttons, price; Total; **FANCY A SNACK?** upsell rows with ADD;
   bottom bar: *Cancel order* (ghost) · **CHECKOUT £9.18**.
5. **Allergy notice modal** before checkout: "Any allergies or intolerances?" +
   disclaimer text + CONTINUE.
6. **Account** — tabs *Add loyalty / Create account*; dark panel "Join our loyalty and
   order online!"; form: display name, email, phone (+44), date of birth (3 selects),
   password, referral code, marketing checkbox, T&C line, CREATE ACCOUNT. Then order
   summary → collection time → payment → confirmation with order number and status.

We clone the **flow and information architecture**, not the brand: the site's own
design system (`site/web/src/styles/global.css`: paper/olive/caramel, Saira + Jost)
applies. **No password**: our account is the Rewards card (email or mobile + one-time
code to get it back), so the account form is the loyalty join form.

---

## 1. Where things live

| Piece | Location | Owner (agent) |
|---|---|---|
| Tables, migration, services, public API, payments, notifications | `cafeops/db/models/shop.py`, `migrations/versions/f2a3b4c5d6e7_online_ordering.py`, `cafeops/services/shop/`, `cafeops/api/areas/shop.py` + `shop_schemas.py` + `shop_views.py`, `cafeops/config.py` (§9) | **A — backend** |
| Admin API (back office) | `cafeops/api/areas/shop_admin.py` + `shop_admin_schemas.py` + `shop_admin_views.py`, `cafeops/services/shop/admin.py` | **B — admin API** |
| Public ordering app: shell, catalogue, item, basket | `site/web/src/scripts/shop/**` (except `checkout/`, `account/`), `site/web/src/pages/order.astro`, `site/web/src/styles/shop.css` | **C — shop app** |
| Checkout, account (join / sign in), order status, landing + header changes, Caddy/Vite rewrites | `site/web/src/scripts/shop/checkout/**`, `site/web/src/scripts/shop/account/**`, `site/web/src/scripts/shop/status/**`, `site/web/src/pages/index.astro`, `Header.astro`, `Hero.astro`, `pages/delivery.astro`, `site/deploy/Caddyfile.site`, `site/web/astro.config.mjs` | **D — checkout & landing** |
| Back-office screens | `web/src/screens/shop/**`, `web/src/lib/shop-api.ts`, `web/src/lib/types/shop.ts`, `web/src/routes.tsx` (§9) | **E — admin screens** |

Python: 3.12, `uv run`, ruff + mypy strict on `services/`. Money **integer pence**,
quantities **int** (a basket line is whole units), timestamps **UTC tz-aware**, local
day/time via `settings.tz` (Europe/London). No SQLite-specific SQL. All DB access sync,
through `in_session` in routes. Enum columns are `enum_col(...)` VARCHAR without CHECK
(adding a member is code only).

Dev: back office API `uv run cafeops serve --port 8000` (repo root), back office UI
`web/`: `npx vite --port 5178`, site API `site/backend`: `uv run sashasite serve --port 8100`,
site `site/web`: `npx astro dev --port 4321` (proxies `/api/shop` to cafeops — D adds it).
`./start.sh` runs all four; check `lsof -nP -iTCP:8000` before starting your own.

---

## 2. Data model (Agent A implements exactly this; B and E code against it)

All tables prefixed `shop_`. `id` integer PK unless stated. `created_at`/`updated_at`
`UTCDateTime` where sensible. Media photos reuse `media_asset` (`photo_asset_id` FK,
`ondelete SET NULL`) and `services/media_store.store_image` / `media_url` (`/media/<sha>.<ext>`).

### 2.1 `shop_settings` — one row, `id = 1`, created by the migration
| column | type | default / meaning |
|---|---|---|
| `enabled` | bool | `False`. Off: the shop shows `closed_message` and takes no orders |
| `closed_message` | str(300) | "Online ordering is taking a short break. Order at the counter." |
| `hero_title` | str(120) | "Order ahead. Collect from Commercial Street." |
| `hero_subtitle` | str(300) | "Takeaway made to order. Skip the queue: pay here or at the counter, we'll have it ready." |
| `takeaway_enabled` | bool | `True` |
| `eat_in_enabled` | bool | `True` |
| `default_dining` | enum `DiningOption` TAKEAWAY/EAT_IN | TAKEAWAY |
| `lead_minutes` | int | 10 — earliest collection = now + lead, rounded up to slot |
| `slot_minutes` | int | 10 |
| `max_orders_per_slot` | int | 6 (0 = unlimited) |
| `days_ahead` | int | 0 — how many days ahead a customer may schedule (0 = today only) |
| `hours` | JSON | `[{"weekday":0,"open":"09:00","close":"19:00"}, …]` Mon=0…Sun=6; a missing weekday = closed. Seeded Mon–Sat 09:00–19:00, Sun 09:00–17:00 |
| `last_order_minutes_before_close` | int | 20 |
| `closures` | JSON | `[{"date":"2026-12-25","note":"Christmas"}]` |
| `pay_at_counter` | bool | `True` |
| `pay_online` | bool | `False` — only honoured when Stripe env is configured |
| `kcal_notice` | str(200) | "Adults need around 2000 kcal a day." |
| `allergen_notice` | text | the Black Sheep-style disclaimer, editable |
| `collection_note` | str(300) | "Collect from the counter at 23 Commercial Street. Say your name or show the order code." |
| `terms_url` | str(300) | "/privacy" |
| `loyalty_stamps_online` | bool | `True` — collected orders stamp the member's card |
| `notify_telegram` | bool | `True` |
| `updated_at` | | |

### 2.2 `shop_banner` (promo carousel)
`title` str(120), `subtitle` str(300) null, `photo_asset_id` FK null, `link_href` str(300)
null (a shop path like `/order/c/hot-matcha` or a site page), `active` bool, `sort_order`
int, `starts_on` date null, `ends_on` date null.

### 2.3 `shop_category`
Keyed to ops `menu_category.name`. `ops_name` str(80) unique NOT NULL, `name` str(80)
(display, defaults to ops name), `slug` str(80) unique, `blurb` str(300) null,
`photo_asset_id` null, `sort_order` int, `visible` bool default True, `updated_at`.
`services/shop/catalog.sync_categories(session)` inserts a row for every
`menu_category` missing one (slug via slugify, sort from `menu_category.sort_order`) and
never deletes; called by the admin GET and the migration's data step.

### 2.4 `shop_product`
One per **product name** (the ops `menu_item.name`; sizes are the `menu_item` rows with
that name). `item_name` str(200) unique NOT NULL, `category_ops_name` str(80) null (from
`menu_item.category` at sync; admin may move it), `display_name` str(200) null (override),
`description` str(600) null, `kcal` int null, `allergens` JSON list of str
(`["milk","gluten","nuts","soya","egg","sesame","sulphites"]` vocabulary in
`domain/shop.py: ALLERGENS`), `dietary` JSON list (`["vegan","vegetarian","gluten-free","decaf-available","contains-caffeine"]`),
`default_size` str(4) null (the size code the item page pre-selects; null = the cheapest),
`ingredients_text` str(600) null, `note` str(300) null (an italic caveat under the
description: "Dairy-free option not available: the pumpkin sauce contains dairy"),
`kcal_by_size` JSON null (`{"S": 244, "M": 339, "XL": 420}`; the client shows the
delta vs the default size on each size tile, like "+81 kcal"), `nutrition` JSON null
(`{"energy_kj": "1418", "fat_g": "12", "saturates_g": "7", "carbs_g": "40", "sugars_g": "36",
"protein_g": "9", "salt_g": "0.4"}` — strings, shown on a Nutrition tab; a missing
value renders "—", never 0), `photo_asset_id` null (falls back to
`menu_item.photo_asset_id`), `badge` str(30) null ("New", "Back for autumn"),
`sort_order` int, `visible` bool True, `available` bool True (False = sold out today,
shown greyed "Sold out"), `featured` bool False, `updated_at`.
`catalog.sync_products(session)` upserts a row for every active `menu_item` name (never
deletes; a name with no active rows is served as `visible=False`).

### 2.5 `shop_option_group` and `shop_option`
Group: `name` str(80) ("Milk"), `prompt` str(160) null ("Choose your milk"), `kind`
enum `OptionKind` SINGLE/MULTI, `layout` enum `OptionLayout` TILES/PHOTO_TILES/CHECKLIST
(TILES: the name + price/kcal tile; PHOTO_TILES: the coffee-bean style image card with
name and tagline; CHECKLIST: the "Customise" checkbox rows), `required` bool, `min_select` int 0, `max_select` int null,
`collapsed` bool False (Black Sheep's "Add extras and syrups"), `applies_to_categories`
JSON list of `shop_category.slug` (empty = only explicit attachments), `sort_order`,
`active` bool, `updated_at`.
Option: `group_id` FK CASCADE, `name` str(80), `description` str(120) null (the tagline
under a tile: "Double the caffeine!"), `price_delta_pence` int 0, `kcal` int null,
`is_default` bool, `available` bool True, `sort_order`, `modifier_id` FK `modifier.id`
null (when set, the ops modifier id goes into `sale.applied_modifiers` so stock depletes
the right milk/syrup), `photo_asset_id` null.
Attachment: `shop_product_option_group(product_id FK CASCADE, group_id FK CASCADE,
sort_order)` PK(product_id, group_id). Effective groups for a product = explicit
attachments ∪ groups whose `applies_to_categories` contains the product's category slug,
ordered by sort_order. **Size is not an option group**: sizes come from `menu_item`
rows (S/M/XL/ONE) and their own prices.

Migration seeds three groups so the shop is usable on day one, all `active`:
*Milk* (SINGLE, required, applies to every DRINKS category: Whole milk default, Skimmed,
Oat +£0.50, Almond +£0.50, Coconut +£0.50, Soya +£0.50 — link `modifier_id` where an
active `modifier` row's name matches case-insensitively, else null), *Extras* (MULTI,
collapsed: Extra shot +£0.60, Extra syrup pump +£0.50, Whipped cream +£0.50, Decaf £0),
*Customise* (MULTI: Extra hot, No lid, Cinnamon dusting, Chocolate dusting, all £0).

### 2.6 `shop_upsell`
`placement` enum `UpsellPlacement` ITEM_PAGE/BASKET, `heading` str(80) ("Fancy a pastry?"),
`product_ids` JSON list of `shop_product.id`, `sort_order`, `active`. Seed one BASKET
upsell "Fancy a cake?" with the first four visible products in *Cakes & bakes*.

### 2.7 `shop_order`, `shop_order_line`, `shop_order_event`
Order: `code` str(8) unique — 6 chars from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, shown
as `SC-XXXXXX`; `status` enum `OrderStatus`: `PENDING_PAYMENT`, `NEW`, `ACCEPTED`,
`PREPARING`, `READY`, `COLLECTED`, `CANCELLED`, `REJECTED`; `dining` DiningOption;
`asap` bool; `requested_at` UTCDateTime (the slot start; for ASAP the computed one);
`placed_at`, `accepted_at`, `ready_at`, `collected_at`, `cancelled_at` null;
`cancel_reason` str(300) null; `cancelled_by` str(40) null ("customer"/"staff");
`customer_name` str(80), `customer_phone` str(20) null (E.164), `customer_email`
str(254) null (one of phone/email required), `member_id` FK `loyalty_member` null,
`card_id` FK `loyalty_card` null, `note` str(300) null (customer's note), `allergy_ack`
bool NOT NULL, `subtotal_pence`, `discount_pence` (reward), `total_pence`,
`reward_id` FK `loyalty_reward` null (a free drink used), `payment_method` enum
`PaymentMethod` COUNTER/ONLINE, `payment_status` enum `PaymentStatus`
UNPAID/PAID/REFUNDED/FAILED, `payment_ref` str(120) null (Stripe checkout session id),
`paid_at` null, `sale_receipt_id` str(80) null (`web:<code>`, written at COLLECTED),
`stamp_event_id` FK `loyalty_stamp_event` null, `access_token` str(64) unique (customer's
status page bearer), `client_ip_hash` str(64) null, `user_agent` str(200) null,
`staff_note` str(300) null. Indexes on `status`, `requested_at`, `placed_at`, `member_id`.
Line: `order_id` FK CASCADE, `product_id` FK null (SET NULL), `menu_item_id` FK
`menu_item` NOT NULL (the size row), `name` str(200) snapshot, `size_label` str(20)
("Small"/"Medium"/"Large"/""), `qty` int ≥1, `unit_price_pence` (size price + option
deltas), `options` JSON `[{"group":"Milk","name":"Oat milk","price_delta_pence":50,"modifier_id":3}]`,
`line_total_pence`, `sort_order`.
Event: `order_id` FK CASCADE, `at`, `kind` str(40) (`placed`, `paid`, `accepted`,
`preparing`, `ready`, `collected`, `cancelled`, `rejected`, `note`, `notified`,
`stamped`, `sale_recorded`), `detail` str(400) null, `actor` str(80) ("customer",
"staff:<name>", "system", "stripe").

### 2.8 Enum additions (Agent A, `cafeops/db/models/enums.py`)
`SaleChannel.WEB = "WEB"`; `SaleSource.ONLINE = "ONLINE"`. Do **not** add WEB to
`MANUAL_SALE_CHANNELS`. Grep `SaleChannel.` in `api/areas/finance*`, `services/finance/`,
`bot/` and add a label for WEB ("Online orders") wherever channels are enumerated for
display (minimal edits, only label maps/lists — leave logic alone).

---

## 3. Rules (invariants for this build)

1. **A collected order is a sale.** Moving to `COLLECTED` writes one `sale` row per line
   (`lightspeed_receipt_id = "web:<code>"`, `lightspeed_line_id = "web:<code>:<n>"`,
   `channel=WEB`, `source=ONLINE`, `recorded_by="online shop"`, `sold_at=collected_at`,
   `applied_modifiers` = the option `modifier_id`s, `expanded_at=NULL` so the nightly
   expansion depletes stock as usual; the free-drink line has `gross_pence=0`). Never
   before COLLECTED, never twice (`sale_receipt_id` guards). Nothing writes
   `stock_movement` (invariant 12).
2. **Cancel/reject after COLLECTED is refused.** Cancelling before it touches no sale.
3. **Prices are computed on the server**, from `menu_item.price_pence` and
   `shop_option.price_delta_pence` at placement. The client's totals are display only;
   `POST /api/shop/orders` recomputes and refuses if the client total differs (409
   `price_changed`, with the fresh basket) so a stale tab can't underpay.
4. **Ordering hours are the shop's own** (`shop_settings.hours`, not the site's café
   hours): a slot is offered only inside them, not in `closures`, ≥ `lead_minutes` from
   now, ≤ `close − last_order_minutes_before_close`, within `days_ahead`, and under
   `max_orders_per_slot` (counting orders not CANCELLED/REJECTED in that slot).
5. **Loyalty**: a member is identified by `X-Card-Token` (the Rewards card's `auth_token`,
   same header the loyalty API uses). With a valid token the order gets `member_id`,
   `card_id`, and pre-filled name/contact. On COLLECTED, if `loyalty_stamps_online` and
   the card's programme is STAMPS, stamp once per eligible line unit through
   `services/loyalty/stamping.apply_units` (respect `max_stamps_per_scan` as the per-order
   cap; reason PURCHASE; actor "online shop"); POINTS programmes use `add_spend`. Record
   `stamp_event_id`. A reward (`loyalty_reward` unredeemed, unexpired, on this card) may
   be applied at checkout to the priciest eligible drink line (respecting
   `reward_max_price_pence`); the reward is marked redeemed **at COLLECTED** (via the
   existing redeem service pattern: `redeemed_at`, `redeemed_menu_item_id`, `sale_id`),
   and released if the order is cancelled.
   **§3.5 addendum (owner decision, 2026-09-29; Agent J):** placing an order needs a
   signed-in Rewards card. `shop_settings.guest_orders` (bool, default `False`) relaxes it.
   While it is off, `POST /api/shop/orders` without a valid `X-Card-Token` answers
   `401 {"error":"sign_in_required","detail":"Sign in with your Rewards card to place an
   order — it takes a moment and every order earns stamps."}` before any other check; a
   voided card counts as no card. Browsing, `GET /catalogue`, `GET /slots` and
   `POST /quote` stay open to guests, so prices are never hidden. `GET /config` carries
   `require_account: boolean` (= `not guest_orders`) for the checkout to branch on. With
   a valid token the order is linked to the member exactly as before (this rule).
6. **Payment**: `COUNTER` orders are `NEW` immediately with `payment_status=UNPAID`; staff
   mark paid at collection (`COLLECTED` implies paid: set `PAID`, `paid_at`). `ONLINE`
   orders start `PENDING_PAYMENT`; the Stripe Checkout Session's success webhook
   (`checkout.session.completed`) moves them to `NEW`/`PAID`. A PENDING_PAYMENT order
   older than 30 min is expired to CANCELLED by the scheduler job. Refunds are not
   automated: cancelling a PAID order sets `payment_status` unchanged and adds an event
   "refund needed" — staff refund in Stripe. Stripe via plain `httpx` (no SDK):
   `POST https://api.stripe.com/v1/checkout/sessions` with basic auth on the secret key,
   `line_items` from the order, `success_url = <site>/order/status/<code>?t=<access_token>&paid=1`,
   `cancel_url = <site>/order/checkout?resume=<code>`; webhook signature check per
   Stripe's `t=…,v1=…` HMAC-SHA256 scheme with `CAFEOPS_STRIPE_WEBHOOK_SECRET`. Without
   `CAFEOPS_STRIPE_SECRET_KEY` the API reports `pay_online: false` regardless of settings.
7. **Notifications** (best effort, background tasks, never fail the request): new order
   → Telegram to `settings.telegram_owner_chat_id` via the Bot API (`httpx`, like
   `site/backend/sashasite/notify.py`; write `services/shop/notify.py`) — Russian, like the
   bot: "🛍 Новый онлайн-заказ SC-ABC123 · Имя · к 14:20 · с собой · 2 позиции · £9.10 ·
   оплата на кассе". READY → customer email/SMS via `services/loyalty/messaging.deliver`
   when SMTP/Twilio configured ("Your order SC-ABC123 is ready to collect").
8. **Every status change is an event row** and bumps `updated_at`. Status transitions:
   PENDING_PAYMENT→NEW|CANCELLED; NEW→ACCEPTED|PREPARING|READY|REJECTED|CANCELLED;
   ACCEPTED→PREPARING|READY|CANCELLED; PREPARING→READY|CANCELLED; READY→COLLECTED|CANCELLED;
   anything else 409 `bad_transition`. Customer may cancel only while NEW (and
   PENDING_PAYMENT).
9. **Public API is rate-limited** like loyalty (`services/auth.RateLimiter`): 10 orders
   per IP per hour, 60 catalogue reads a minute is plenty.
10. Errors on `/api/shop/*` use the loyalty shape `{"error": code, "detail": sentence}`
    (register the prefix in `loyalty_edge.LOYALTY_PREFIXES` or add a sibling handler);
    admin routes use the back office's normal FastAPI detail shape.

---

## 3b. Payment providers and the POS sink (owner extension, 2026-09-29; Agent A)

1. **Payments are a provider layer**, not a Stripe module: `cafeops/integrations/payments/providers/`
   (`base.py` protocol + frozen results, `registry.py`, `stripe.py`, `lightspeed.py`; the
   path is one level below the contract's because `integrations/payments/base.py` was
   already the payment-REPORTS protocol -- `integrations/payments/registry.py` re-exports
   the registry at the contract's path). `PaymentProvider`: `key`, `display_name`,
   `configured()`, `create_checkout(order, customer, *, success_url, cancel_url) -> Checkout(url, ref)`,
   `parse_webhook(headers, body) -> PaymentEvent(kind "paid"|"failed"|"refunded"|"ignored", ref, amount_pence, raw_id)`,
   `refund(order) -> RefundResult(ok, ref, detail)`, `ensure_customer(member) -> str`,
   `saved_methods(provider_customer_id) -> list[SavedMethod(label, ref, is_default)]`.
   `registry.provider(key)` / `registry.active(key)` (configured only) /
   `registry.available() -> [{key, display_name, configured}]`.
   Chosen by `shop_settings.payment_provider` (str(20), default `"stripe"`; the admin PUT
   validates against `registry.keys()`). `GET /api/shop/config` `pay.online` is true only
   when `pay_online` is set AND the chosen provider is `configured()`; `pay.provider` names it.
   Webhook: `POST /api/shop/payments/{provider_key}/webhook` (`/api/shop/stripe/webhook`
   kept as an alias). Pay-at-counter is not a provider (the COUNTER `payment_method` path).
2. **Lightspeed** (`providers/lightspeed.py`, `pos/lightspeed.py`) is written against the
   documented K-Series Order & Pay API (`POST /o/op/1/order/toGo`, `POST /o/op/1/pay`,
   `GET /o/op/1/onlineOrderReadiness`; api-docs.lsk.lightspeed.app read 2026-09-29) and is
   **untested against the live API** (no credentials). Finding: K-Series exposes no hosted
   online payment, payment link or card-on-file to API integrators -- `/pay` and the order's
   `payment` object only RECORD a payment taken elsewhere -- so the Lightspeed payment
   provider's `configured()` is always False (honest), `create_checkout` raises, and
   `ensure_customer` returns the member's `lightspeed_customer_id` (there is no
   create-customer endpoint; a customer is created implicitly by a pushed order's
   `customerInfo`). Config: `CAFEOPS_LIGHTSPEED_BUSINESS_LOCATION_ID`,
   `CAFEOPS_LIGHTSPEED_ONLINE_ORDER_ENDPOINT_ID`, `CAFEOPS_LIGHTSPEED_PAYMENT_METHOD_CODE`
   plus the existing four credentials (scope `orders-api` needed on the refresh token).
   Mapping to confirm on the first live call: `items[].sku` <- `menu_item.lightspeed_id`,
   `modifiers[].modifierId` <- `modifier.lightspeed_modifier_id`.
3. **POS sink**: `cafeops/integrations/pos/` (`base.py` `PosOrderSink`: `push_order`,
   `mark_paid`, `cancel` -> `PosPushResult(ok, external_ref, detail)`; `null.py` default,
   `lightspeed.py`, `registry.py` `sink(key)` / `available()`). `shop_settings.pos_sink`
   (str(20), default `"none"`). `services/shop/pos.push_order_to_pos(order_id)` runs after
   commit when an order becomes NEW (background), writes `pos_pushed` / `pos_failed` and
   `shop_order.pos_ref` (str(80)). The COLLECTED sale rows are unchanged; **double-count
   risk**: a pushed order that the Lightspeed sales sync later imports lands as a second
   `sale` (EPOS) beside `web:<code>` -- dedupe in `ingest_sales` is out of scope and
   documented in `pos/lightspeed.py`. K-Series has no cancel endpoint (void on the till);
   `mark_paid` needs the account identifier its order webhook returns, which we do not
   receive, so it reports "not possible" today.
4. **Account link**: `shop_payment_customer(id, member_id FK loyalty_member NOT NULL,
   provider str(20), provider_customer_id str(120), default_method_label str(60) null,
   created_at, updated_at, UNIQUE(member_id, provider))`. `ensure_customer` runs when a
   signed-in member starts an online checkout (a failure there is logged and the customer
   pays as a guest); `GET /api/shop/me` returns `payment: {provider, saved_methods}|null`.
   Refs and labels only, never card numbers.
5. Config: `stripe_*` as §9; no env override for the provider (the DB setting rules).
6. Migration: `f2a3b4c5d6e7` was already applied to the live cafeops.db, so these columns
   and the table are `f3b4c5d6e7f8_payment_providers.py` on top of it.

## 3c. Customer notifications (owner extension, 2026-09-29; Agent A)

1. **Three channels, cost-ordered**, all in `services/shop/notify.py`:
   `notify_customer(session, order, kind)` for `accepted`, `ready`, `cancelled`, `rejected`
   (with the reason) and `delayed` (staff-triggered, optional). Every attempt is an order
   event: `notified` ("email: ready", "push: ready (2 devices)", "sms: ready") or
   `notify_failed` with the reason ("email: ready not sent, SMTP is not configured").
   - **Email** (free): `messaging.send_email` when SMTP is configured and the order has an
     email; every kind; `shop_settings.email_notify` (bool, default true).
   - **Web push** (free): `shop_push_subscription(id, order_id FK CASCADE, endpoint str(500),
     p256dh str(200), auth str(100), created_at, expired_at null)`;
     `POST /api/shop/orders/{code}/push?t=…` `{endpoint, keys:{p256dh, auth}}` -> 201
     `{subscribed, devices}`; `DELETE …/push[?endpoint=]`. Sent with `pywebpush`; VAPID keys
     from `CAFEOPS_VAPID_PUBLIC_KEY` / `CAFEOPS_VAPID_PRIVATE_KEY` / `CAFEOPS_VAPID_SUBJECT`
     (`cafeops shop vapid-keys` prints a pair). `GET /api/shop/config` has
     `push: {enabled, vapid_public_key}`. 404/410 from the push service sets `expired_at`.
     Payload `{title, body, url: "<site>/order/status/<code>?t=…", code, status}`.
     `shop_settings.push_notify` (bool, default true).
   - **SMS** (paid): `messaging.send_sms` only when Twilio is configured, the customer
     opted in on the order (`shop_order.sms_opt_in` bool default false; `POST /api/shop/orders`
     takes `sms_opt_in`) and `shop_settings.sms_notify` (str(12) `"off"|"ready"|"all"`,
     default `"ready"`) allows the kind.
   - `GET /api/shop/orders/{code}` returns `notify: {email, push_subscribed, sms}`.
2. Hooked into `services/shop/orders.transition` for ACCEPTED, READY and a staff-initiated
   CANCELLED / REJECTED (a customer's own cancel and the scheduler's expiry send nothing).
   The notice is queued on the session and fired on its `after_commit` (a thread with its
   own session), so it describes a status that exists and never fails the request; the
   admin's status route gets this for free. The owner's Telegram ping is unchanged.
3. Migration: `f3b4c5d6e7f8` was already on the live DB, so this is
   `f4c5d6e7f8a9_customer_notifications.py` on top.

## 4. Public API — `cafeops/api/areas/shop.py`, `open_router`, prefix `/api/shop`

All GETs cacheable 30 s. Money pence. Times ISO-8601 UTC with offset; `local` fields
are display strings in Europe/London.

```ts
GET /api/shop/config
{ enabled: boolean, closed_message: string, hero_title, hero_subtitle,
  dining: { takeaway: boolean, eat_in: boolean, default: 'takeaway'|'eat_in' },
  pay: { counter: boolean, online: boolean },
  kcal_notice, allergen_notice, collection_note, terms_url,
  open_now: boolean, next_open_local: string|null,       // "Tomorrow 09:00"
  cafe: { name, address_line, postcode, phone },         // constants from BRIEF
  loyalty: { program_name, stamps_required, reward_text } }

GET /api/shop/catalogue
{ generated_at, version: string,                         // hash of the payload; the client caches on it
  banners: [{ id, title, subtitle, photo_url, link_href }],
  categories: [{ id, slug, name, blurb, photo_url, product_count }],
  products: [{ id, slug, name, category_slug, description, note, kcal, kcal_by_size, nutrition,
               allergens: string[], dietary: string[], ingredients_text, photo_url, badge, available, featured,
               from_price_pence, default_size: 'S'|'M'|'XL'|'ONE',
               sizes: [{ menu_item_id, code:'S'|'M'|'XL'|'ONE', label, price_pence, kcal }],
               option_groups: [{ id, name, prompt, kind:'single'|'multi', layout:'tiles'|'photo_tiles'|'checklist',
                                 required, min_select, max_select, collapsed,
                                 options: [{ id, name, description, price_delta_pence, kcal, is_default, available, photo_url }] }],
               upsells: [{ heading, product_ids: number[] }] }],   // ITEM_PAGE placement
  basket_upsells: [{ heading, product_ids: number[] }] }

GET /api/shop/slots?date=YYYY-MM-DD          // default today (local)
{ date, open: boolean, reason: string|null, asap: { available: boolean, at: string, local: "12:40" },
  slots: [{ at: string, local: "12:50", available: boolean }], days: [YYYY-MM-DD…] }

POST /api/shop/quote                          // never writes; prices a basket
{ dining, lines: [{ product_id, menu_item_id, qty, option_ids: number[] }], reward: boolean }
-> { lines: [{ …, name, size_label, unit_price_pence, line_total_pence, options:[{group,name,price_delta_pence}] , problems: string[] }],
     subtotal_pence, discount_pence, total_pence, reward: { applied: boolean, line_index: number|null, text: string|null }, problems: string[] }
   (X-Card-Token optional; `reward` only honoured with a token whose card has one)

POST /api/shop/orders
{ dining, asap: boolean, requested_at: string|null, lines: [...as quote...], reward: boolean,
  customer: { name, phone?, email? }, note?, allergy_ack: true, payment: 'counter'|'online',
  expected_total_pence, website: "" }        // honeypot
-> 201 { code, status, access_token, total_pence, requested_at, requested_local,
         payment: { method, status, checkout_url: string|null } }   // online: redirect to checkout_url
   409 price_changed | 409 slot_full | 422 … | 403 shop_closed | 429

GET /api/shop/orders/{code}?t=<access_token>     (or X-Order-Token header)
{ code, status, status_label, status_step: 0..4, dining, asap, requested_at, requested_local,
  placed_at, ready_at, collected_at, customer: { name }, lines: [{ name, size_label, qty,
  unit_price_pence, line_total_pence, options: [{group,name,price_delta_pence}] }],
  subtotal_pence, discount_pence, total_pence, payment: { method, status },
  collection_note, cancel_allowed: boolean, events: [{ at, kind, detail }] }

POST /api/shop/orders/{code}/cancel?t=…      -> the order (customer, NEW only)
GET  /api/shop/me                            // X-Card-Token -> { first_name, email, phone, card_id,
                                             //   stamps_current, stamps_required, reward: {id, text}|null,
                                             //   recent_orders: [{code, status, placed_at, total_pence}] }
POST /api/shop/stripe/webhook                // raw body + Stripe-Signature
```

Account creation / sign-in are the **existing loyalty routes** (`POST /api/loyalty/join`,
`POST /api/loyalty/recover`, `POST /api/loyalty/recover/verify`, `GET /api/loyalty/card/{id}`);
the shop stores the returned `card_id` + `token` exactly where the rewards web card does
(Agent D reads `site/web/src/scripts/rewards/webcard.ts` for the localStorage key and
reuses it, so a member who joined on /rewards is signed in on /order and vice-versa).

---

## 5. Admin API — `cafeops/api/areas/shop_admin.py`, `router` (ApiAuth), prefix `/api/shop-admin`

```ts
GET  /api/shop-admin/summary   { enabled, open_now, counts: { new, accepted, preparing, ready, today_collected, today_cancelled },
                                 today_revenue_pence, next_due: [{code, requested_local, customer_name, status}], stripe_configured, telegram_configured }
GET  /api/shop-admin/orders?status=live|today|all&from&to&q&page&page_size   // live = PENDING_PAYMENT..READY
     { items: [OrderAdmin], total, page, page_size }
GET  /api/shop-admin/orders/{id}   OrderAdmin (order + lines + events + customer contact + member link)
POST /api/shop-admin/orders/{id}/status { status, reason?, by }     // by = operator name, required
POST /api/shop-admin/orders/{id}/note   { staff_note, by }
POST /api/shop-admin/orders/{id}/paid   { by }                       // COUNTER: mark paid before collection

GET  /api/shop-admin/settings          -> ShopSettings (every 2.1 column)
PUT  /api/shop-admin/settings          { any subset } -> ShopSettings   (validates hours/closures)

GET  /api/shop-admin/catalogue         // runs sync_categories + sync_products first
     { categories: [CategoryAdmin], products: [ProductAdmin], option_groups: [GroupAdmin], upsells: [UpsellAdmin], banners: [BannerAdmin],
       ops: { items_without_category: number, menu_items_active: number } }
PUT  /api/shop-admin/categories/{id}   { name?, blurb?, visible?, sort_order? }
POST /api/shop-admin/categories/order  { ids: number[] }
POST /api/shop-admin/categories/{id}/photo   (raw image body, like menu photo upload) / …/photo/clear
PUT  /api/shop-admin/products/{id}     { display_name?, description?, note?, kcal?, kcal_by_size?, nutrition?, allergens?, dietary?, ingredients_text?, badge?, visible?, available?, featured?, category_ops_name?, sort_order?, default_size?, option_group_ids? }
POST /api/shop-admin/products/order    { category_slug, ids: number[] }
POST /api/shop-admin/products/{id}/photo | /photo/clear
POST /api/shop-admin/products/bulk     { ids, available?: boolean, visible?: boolean }     // "86" several at once
POST /api/shop-admin/option-groups     { name, prompt?, kind, layout, required, min_select, max_select?, collapsed, applies_to_categories, options: [{ id?, name, description?, price_delta_pence, kcal?, is_default, available, modifier_id?, sort_order }] } -> GroupAdmin
POST /api/shop-admin/options/{id}/photo | /photo/clear                                 // PHOTO_TILES option images
PUT  /api/shop-admin/option-groups/{id}  (same, options replaced wholesale by id; options carry id? for update)
DELETE /api/shop-admin/option-groups/{id}
POST /api/shop-admin/option-groups/order { ids }
POST /api/shop-admin/upsells | PUT …/{id} | DELETE …/{id}
POST /api/shop-admin/banners | PUT …/{id} | DELETE …/{id} | POST …/{id}/photo | /order
GET  /api/shop-admin/modifiers         [{ id, name, price_pence }]     // for linking options
```

`OrderAdmin`: every order column plus `lines`, `events`, `member: {id, first_name, stamps_current}|null`,
`requested_local`, `placed_local`, `minutes_until_due` (negative when late).

---

## 6. Public app — `site/web`, URL map (Agent C shell + catalogue, Agent D checkout/account/status)

Static Astro page `pages/order.astro` mounts a **Preact** app (add `@astrojs/preact` +
`preact`; Agent C installs and edits `astro.config.mjs` integrations only — D edits its
`vite.server.proxy` and dev-rewrite plugin). History routing under `/order`; Caddy
rewrites `/order/*` to `/order.html` (D, mirroring the `/c/*` block, plus a Vite dev
rewrite). The old `/order` page (whole cakes, delivery apps) moves to `/delivery` (D),
and every link to it is updated (`grep -rn '"/order' site/web/src`).

| Path | Screen | Owner |
|---|---|---|
| `/order` | Overview: hero (title/subtitle from config, "Takeaway · collection only" said plainly), banner carousel, dining toggle, category tiles, "Sign in / Join Rewards" link, basket pill | C |
| `/order/c/<category-slug>` | Category: tab strip, kcal notice, product grid | C |
| `/order/p/<product-slug>` | Item customisation (two columns ≥ 900px, stacked below): size tiles, option groups, upsells, sticky add bar; `?line=<n>` edits a basket line | C |
| `/order/basket` | My order: dining toggle, lines (edit/remove/duplicate), total, basket upsells, Cancel order / Checkout | C |
| `/order/checkout` | Allergy notice (first time), collection time (ASAP / pick a slot), your details (pre-filled from the card), Rewards panel (use free drink), payment choice, note, T&C, Place order | D |
| `/order/account` | Tabs *Sign in* (contact → code) / *Join Rewards* (loyalty join form) | D |
| `/order/status/<code>` | Confirmation & live status (poll every 20 s): step bar NEW→ACCEPTED→PREPARING→READY→COLLECTED, code big, collection note, lines, cancel while NEW | D |

Shared modules (C owns, D consumes — **C writes these first, within its first 20
minutes, and keeps their exported signatures stable**):

```ts
// scripts/shop/api.ts
export const shopApi: { config(), catalogue(), slots(date?), quote(body), placeOrder(body), order(code, token), cancel(code, token), me() }
// each returns ApiResult<T> like scripts/rewards/api.ts (status, ok, data, error); attaches X-Card-Token from the card store
// scripts/shop/store.ts  (signals; persisted in localStorage 'sc.shop.basket.v1')
export const basket: { lines: Signal<BasketLine[]>; dining: Signal<'takeaway'|'eat_in'>; add(line); update(index, line); remove(index); duplicate(index); clear(); count(): number; subtotal(catalogue): number }
export interface BasketLine { product_id: number; menu_item_id: number; qty: number; option_ids: number[]; }
export const catalogue: Signal<Catalogue|null>; export function loadCatalogue(): Promise<void>
export const config: Signal<ShopConfig|null>
export const session: { cardId(): string|null; token(): string|null; set(cardId, token): void; clear(): void }   // same storage as the rewards web card
// scripts/shop/router.ts
export const route: Signal<{ path: string; params: Record<string,string>; query: URLSearchParams }>; export function go(path: string, opts?: {replace?: boolean}): void
// scripts/shop/format.ts  gbp(pence), kcal(n), optionSummary(line, catalogue): string, lineTitle(line, catalogue): string  ("Iced latte (Medium)")
// scripts/shop/ui/*.tsx   Button, Segmented (dining), Tile, Stepper, BottomBar, Sheet (modal), Field, Notice — brand-styled
```

**Design**: `site/BRIEF.md` design system, `global.css` tokens only. Uppercase display
labels are fine here (Saira 600, `font-stretch 88%`) — this is a Black Sheep-style
ordering UI. Category tiles: photo with an olive-900 band and the name in caps; no
photo → a sage/oat placeholder with the category initial. Sticky bottom bars are
olive-900 with paper text; primary buttons caramel. Everything works at 375 px, no
horizontal scroll, 44 px targets, visible focus, `prefers-reduced-motion`. No emoji.
**Say "takeaway / collection" explicitly** on the overview, in checkout and on the
status page: this is not delivery. Link to `/delivery` for Deliveroo / Just Eat.

**Landing (`index.astro`, D):** a new section after the hero — "Order ahead for
takeaway" with the three-step line (Choose · Pay here or at the counter · Collect at
23 Commercial Street), a caramel button to `/order`, and the Rewards tie-in ("Stamps
count online too"). Header (D): nav "Order online" → `/order` replaces "Cakes & delivery";
keep "Book a table". The `tile--order` on the landing food section now points to
`/delivery` with "Delivery" wording.

---

## 7. Back office — `web/` (Agent E)

New sidebar group **Online orders** (after *Customers*, before *Money*) with routes:

| Hash | Label | Screen |
|---|---|---|
| `#/shop` | Live orders | board of columns New / Accepted / Preparing / Ready (cards are fine here: each is an order with code, due time, customer, lines summary, dining, payment, next-action button), a "Later today" list, polling every 15 s, `badge: 'shop_new'` (E adds `shop_new` to the shell badge type as optional; B adds it to `GET /api/shell` badges **only if trivial** — otherwise the screen counts itself) |
| `#/shop/orders` | All orders | table with filters (status, dates, search), row → `#/shop/orders/<id>` page: statement layout (lines, discount, total), timeline, customer + member link (`#/loyalty/members/<id>` if that route exists), actions: accept / start / ready / collected / cancel (reason) / mark paid / staff note |
| `#/shop/menu` | Shop menu | categories (order, visibility, photo, blurb) and products (search, category chips, availability toggle "Sold out today", visible, badge, description, kcal, allergens, dietary, photo, option groups attached, featured); drawers, no modals |
| `#/shop/options` | Option groups | list + drawer editor (options table with price delta, kcal, default, available, linked modifier), applies-to categories chips |
| `#/shop/promos` | Banners & upsells | banner list with photo/link/dates; upsell lists per placement with product pickers |
| `#/shop/settings` | Settings | on/off with closed message, hero copy, dining, hours per weekday + closures, lead/slot/capacity/days ahead, payment (counter / online with "Stripe not configured" note), notices, loyalty stamps online, Telegram |

Rules: `FRONTEND-KIT.md` (tokens only, `request`/`apiWrite`, `useOperator` for `by`,
`PageHeader` + `PageBody`, drawers not modals, no KPI grids, 375/1180/1440). Types in
`web/src/lib/types/shop.ts` mirror §5 literally. Fixture mode: screens render an
`Empty` "needs the live API" like the Website screens (`LIVE` from `lib/api`).

---

## 8. Verification (each agent, before reporting)

- Python: `uv run ruff check <your files> && uv run ruff format <your files> && uv run mypy cafeops/services/shop` — zero errors, zero `type: ignore`.
- Migration: `cp cafeops.db /tmp/shop-test.db` is **wrong** (WAL) — use
  `sqlite3 cafeops.db ".backup '/private/tmp/…/shop-test.db'"`, then
  `CAFEOPS_DATABASE_URL=sqlite+pysqlite:////private/tmp/…/shop-test.db uv run alembic upgrade head` and `downgrade -1`. Then upgrade the real `cafeops.db` (the other sessions' pending migrations are already applied to it: head is `e6f2b1c40a53`).
- API: start `uv run cafeops serve --port 8000` and curl every route you built; paste
  one real response per route into your report. Place an order end to end with curl.
- Site: `cd site/web && npm run build` clean; look at every screen in Chrome (headless
  is fine: `--remote-debugging-port`, or the `claude-in-chrome` tools) at 375 and 1180;
  zero console errors.
- Back office: `cd web && npx tsc --noEmit && npx vite build` clean; view each screen live.
- Report: what you built, the exact files, anything you could not verify, and every
  contract deviation you had to make (with why). Never claim a screen works you did not open.

---

## 9. Shared-file insertion points (the only edits allowed outside your ownership)

- `cafeops/db/models/__init__.py`: A exports the shop models.
- `cafeops/db/models/enums.py`: A adds `SaleChannel.WEB`, `SaleSource.ONLINE`.
- `cafeops/config.py`: A adds `stripe_secret_key`, `stripe_publishable_key`,
  `stripe_webhook_secret` (all `str | None = None`) and `shop_public_url` (defaults to
  `site_public_url`). `.env.example`: A documents them under a "Online ordering" block.
- `cafeops/api/areas/__init__.py` + `cafeops/api/app.py`: A includes `areas.shop.open_router`
  (and registers the error prefix); B includes `areas.shop_admin.router` in the `for area in (…)` tuple.
- `cafeops/jobs/`: A adds the 30-minute PENDING_PAYMENT expiry job where the other
  scheduled jobs are registered (one `add_job` line + one function).
- `web/src/routes.tsx`: E adds the six routes + the nav group; nothing else.
- `web/src/lib/shell-api.ts` badges type: E adds optional `shop_new?: number`.
- `site/web/astro.config.mjs`: C adds the preact integration; D adds the `/api/shop`
  proxy entry (to cafeops, before `/api`) and the `/order/*` dev rewrite.
- `site/web/src/components/Header.astro`, `Hero.astro`, `pages/index.astro`: D only.
- `site/deploy/Caddyfile.site`: D adds `handle /api/shop/*` → `api:8000` and the
  `/order/*` rewrite block.
- `docs/shop/CONTRACT.md`: append your deviations under "## 10. Agents' additions"
  (create the heading if absent), never rewrite sections above.

Anything else you believe needs changing in a file you do not own: describe it in your
report; do not make the edit.


### 10.B Admin API (Agent B) — 2026-09-29

Files: `cafeops/api/areas/shop_admin.py`, `shop_admin_schemas.py`, `shop_admin_views.py`,
`cafeops/services/shop/admin.py`; router registered in `api/areas/__init__.py` + `api/app.py`.

Additions to §5 (none of these remove anything):

- **Enum strings are the database member names** on admin routes: `status` `NEW|ACCEPTED|…`,
  `dining` `TAKEAWAY|EAT_IN`, `payment_method` `COUNTER|ONLINE`, `payment_status`
  `UNPAID|PAID|REFUNDED|FAILED`, option `kind` `SINGLE|MULTI`, `layout`
  `TILES|PHOTO_TILES|CHECKLIST`, upsell `placement` `ITEM_PAGE|BASKET`.
- `OrderAdmin` also carries `code_display` ("SC-XXXXXX"), `allowed_transitions: string[]`
  (what `POST …/status` accepts from here, from `domain/shop.TRANSITIONS`), `pos_ref`, and
  `member.stamps_current` (max over the member's live cards).
- `GET /orders?status=` accepts `live | today | all` **or a status name** (`COLLECTED`);
  `from`/`to` are local dates on `placed_at`; `q` matches code (with or without `SC-`), name,
  email, phone. `live` sorts by `requested_at` asc, everything else by `placed_at` desc.
- `POST /orders/{id}/paid` refuses (409) an already-paid order, a cancelled/rejected one, and a
  `PENDING_PAYMENT` one (Stripe confirms that, not the counter). `COLLECTED` still implies paid
  through `services/shop/orders._collect`.
- `POST /orders/{id}/status` and `/note` write through `services/shop/orders.transition`,
  `mark_paid`, `add_staff_note`; a `ShopError` becomes `{"detail": sentence}` with its status.
- `GET /settings` and `GET /summary` also return `stripe_configured` and `telegram_configured`;
  `PUT /settings` refuses (422) turning off both dining modes or both ways to pay.
- `GET /catalogue` also returns `allergens` / `dietary` (the vocabularies) and, per product,
  `name` (display or ops), `slug` (`catalog.product_slug`), `category_slug`, `photo_is_own`,
  `ops_active`, `from_price_pence`, `sizes[]` (every `menu_item` row with that name,
  `active` flag, `kcal` from `kcal_by_size`), `effective_option_group_ids`; per category
  `product_count`; per group `product_ids` (explicit attachments) and per option
  `modifier_name` + `photo_url`.
- `POST /categories/order`, `/products/order`, `/option-groups/order`, `/banners/order` return
  the whole `CatalogueAdmin` (the screen re-renders from it). Ids not listed keep their
  relative order after the listed ones.
- Option groups: on `PUT`, an option list where every `sort_order` is 0 is taken as
  positional. A `SINGLE` group gets `max_select = 1`; two defaults on it are refused (422).
- `POST /products/bulk` returns `{ changed, products: ProductAdmin[] }`.
- `DELETE` routes return `{ id, deleted: true }`.
- Photo routes take the raw image body (`X-Operator` optional) and return
  `{ asset_id, photo_url, width, height, bytes, content_type }`; `/photo/clear` returns the
  same shape with nulls.
- Payments/POS extension (owner, 2026-09-29): `GET/PUT /settings` carry `payment_provider`
  (validated against `integrations/payments/registry.keys()`) and `pos_sink` (`none |
  lightspeed`); `GET /summary` carries `payment_providers` and `pos_sinks` as
  `[{ key, display_name, configured }]`. `pos_sinks` is built in `services/shop/admin.py`:
  `none` is always configured, `lightspeed` reports `integrations/pos/lightspeed.LightspeedSink`'s
  own `display_name` / `configured()` (TODO: read the key list from `integrations/pos/registry`
  once A adds it). `pos_pushed` / `pos_failed` appear
  in `OrderAdmin.events` like any other event.
- Not done: `shop_new` in `GET /api/shell` badges — it needs edits in three shell files
  (`services/shell_status.py`, `shell_schemas.py`, `shell_views.py`), so the Live orders
  screen counts from `GET /api/shop-admin/summary.counts.new` instead (§7 allows this).

---

## 10. Agents' additions

### Agent C — public ordering app (shell, catalogue, item, basket), 2026-09-29

- **Shared modules shipped as in §6, with these additions** (all additive, signatures kept):
  - `router.ts`: `route.value` also carries `name: 'overview'|'category'|'product'|'basket'|'checkout'|'account'|'status'|'notfound'`;
    `paths.*` builders (`paths.product(slug, line?)`, `paths.status(code)` …); `interceptLinks(root)` makes every
    same-origin `/order…` `<a>` an in-app navigation (opt out with `data-native`); `go()` outside `/order` does a full load.
    In dev, `go()` carries `?mock=` across navigations.
  - `store.ts`: `basket.setDining(d)`, `basket.notice` (Signal<string|null>, the "removed from your order" sentence),
    `basket.MAX_QTY` (20), `loadError`, and the pure helpers `productById/productBySlug/sizeOf/optionsOf/unitPrice/lineTotal/reconcileBasket`.
    `basket.add()` merges an identical product+size+options line into one line. The catalogue is cached in
    localStorage `sc.shop.catalogue.v1` for first paint, then refetched; lines whose product/size is gone, product
    sold out, or option gone/unavailable are dropped with a notice.
  - `api.ts`: `request()`, `shopError()` (shop codes on top of `humanError`), `isMock()`, `mockFlags()`; `session` wraps
    the rewards card storage (`sc_card_last` + `sc_card_token:<id>`). `order()`/`cancel()` send the bearer as `X-Order-Token`.
  - `format.ts` adds `delta(pence)`, `kcalDelta(n)`, `plural()`, `initial()`; `gbp` handles negatives (`-£0.20`).
  - `ui/`: `Button` has tones `caramel|ink|ghost|paper|link` and `busy`; `Tile` takes native radio/checkbox inputs;
    `Stepper` has `onMin` (minus at the minimum removes the line) and `onDark`; `Sheet` is a native `<dialog>`.
  - `toast.ts` (`showToast(text, action?)`) and `types.ts` (every §4 shape as TS) are extra files under `scripts/shop/`.
- **Size tiles** show the default size's absolute price and, on the others, the price delta and kcal delta vs the
  default (`kcal_by_size`, else `sizes[].kcal`). Single-size products show no Size group.
- **Allergens**: the top-bar "Allergens" button opens a sheet with `allergen_notice`; under 480px it is a button in the
  hero instead (the bar has no room next to the basket pill).
- **Closed shop** (`enabled=false`): the overview and basket show `closed_message`; browsing continues; the basket's
  Checkout button is disabled. `open_now=false` shows an info notice with `next_open_local`; checkout (D) decides slots.
- **Mock** `?mock=1[,closed,shut,takeaway,online,slow,down,signedin,reward,empty]` (`scripts/shop/mock.ts`, dev only)
  builds the catalogue from `src/data/menu.json`, so categories, items and prices are real; photos, kcal, option groups
  and descriptions are invented. Placed orders advance one status every 40 s.
- `site/web/tsconfig.json` gained `"jsx": "react-jsx", "jsxImportSource": "preact"` (needed for `astro check`/editors).
- `npm install` needed `--legacy-peer-deps`: the pre-existing `typescript@7` vs `@astrojs/check@0.9` peer conflict, unrelated to preact.
- Not verified live: Agent A's `/api/shop/*` did not exist at the time of writing, so every screen was verified in mock
  mode only. Required-group validation (the "Choose one" error) could not be triggered through the UI because every
  seeded required group has a default; the code path exists (`views/Product.tsx: groupError`).

### Agent D — checkout, account, status, landing, plumbing (2026-09-29)

Files: `site/web/src/scripts/shop/checkout/{Checkout.tsx,common.tsx,d.css}`,
`account/Account.tsx`, `status/Status.tsx`; `site/web/astro.config.mjs` (proxy + rewrite +
sitemap), `site/deploy/Caddyfile.site`; `pages/delivery.astro` (was `order.astro`),
`pages/index.astro`, `components/Header.astro`, `components/Footer.astro` (one link),
`pages/llms.txt.ts`, `pages/kyiv-cake.astro`, `pages/_facts.ts`.

Deviations and decisions:

- **Shared D styles live in `scripts/shop/checkout/d.css`** (prefix `sd-`), imported by
  `checkout/common.tsx`; account and status import their furniture from `checkout/common`.
  §1 gives D `checkout/**`, `account/**`, `status/**` and no stylesheet, and `shop.css` is C's.
- **Order token storage**: `localStorage['sc.shop.order:<CODE>']` holds the access token; the
  status page reads `?t=` once, stores it, and strips `t` and `paid` from the address bar
  with `history.replaceState`. `sc.shop.pending:<CODE>` marks an online-paid order whose
  basket is cleared only once the status page sees it past `PENDING_PAYMENT` (or `?paid=1`),
  so a Stripe page that is cancelled brings the basket back at `/order/checkout?resume=<code>`
  with a notice. Counter orders clear the basket on the 201.
- **Allergy acknowledgement** is per browser session: `sessionStorage['sc.shop.allergy_ack.v1']`.
  Closing the sheet without CONTINUE goes back to the basket, since the notice is a must-see.
- **Contact field on checkout is one input** ("Mobile or email"), classified by the same rules
  the loyalty join form uses; the body sends `phone` or `email` accordingly.
- **Payment choice** shows only the methods `config.pay` allows and preselects the first; with
  none allowed the checkout says so and the bar stays disabled.
- **Reward toggle**: `reward: true` is sent only when `/api/shop/me` reports a reward; when the
  quote answers `applied: false` the toggle line shows the quote's `reward.text` (or "no
  eligible drink in this order") rather than silently dropping it.
- **Sign-in on `/order/account` is the loyalty recover flow** (`POST /api/loyalty/recover` →
  `/recover/verify`) and join is `POST /api/loyalty/join` with `src: "order"`; both store the
  card through `session.set` (C's `api.ts`), i.e. `sc_card_last` + `sc_card_token:<id>`.
  `?next=checkout` returns to checkout after either. A 409 `already_member` on join switches
  to Sign in with the contact prefilled. `ask_staff` delivery shows the "ask at the till" state.
- **Recent orders** on the account page link to the status page only when this device holds
  that order's token (the `/me` shape carries no token); otherwise the code is plain text.
- **Status page** hides the 5-step bar for CANCELLED / REJECTED / PENDING_PAYMENT and shows a
  state line instead; polls every 20 s only while the tab is visible and the status is not
  final. `?paid=1` shows "Payment received"; while the webhook has not landed it says so and
  keeps polling.
- **Landing section** sits after the stats band (which continues the hero's olive ground),
  not between hero and stats. Section CSS is page-scoped in `index.astro`.
- **`/delivery`**: the food tile on the landing now reads "Delivery · whole cake £25.00" and the
  `#ahead` / `#takeaway` anchors are unchanged. `slots.json` / `slots.toml` still list the
  slot pages as `/order` (backend registry, not D's to edit) — the slot keys `order.*` are
  unchanged so the photos still render.
- **Footer**: one new "Order online" link and the old link retargeted to `/delivery` (Footer is
  integrator-owned per `site/BRIEF.md`; the contract asked for every `/order` link to move).
- **Not verified live**: A's `open_router` was not yet included in `api/app.py` while D ran, so
  the end-to-end order was placed against C's mock (`?mock=1`) only; live verification is
  listed in D's report as pending.
- **Typing note for C**: `ui/Button.tsx` and `ui/Field.tsx` extend `JSX.HTMLAttributes`, which in
  preact 10.29 lacks `disabled`/`type`/`autocomplete`; `npx tsc --noEmit` reports them at every
  call site (D's included). `JSX.ButtonHTMLAttributes<HTMLButtonElement>` /
  `JSX.InputHTMLAttributes<HTMLInputElement>` would clear it. Vite builds regardless.

#### 10.B.1 Insights + online orders in the money statistics (owner addition, 2026-09-29)

`GET /api/shop-admin/insights?days=30` (or `from`/`to`, local dates, inclusive; `days` 1–366).
Service: `cafeops/services/shop/insights.py` (read-only). An order belongs to the local day of
its `requested_at`. "Orders" = every status but PENDING_PAYMENT; **only COLLECTED orders count
towards `revenue_pence`, `avg_basket_pence`, `items`, `top_products`, `top_options`**;
`cancelled` / `rejected` are counted separately. Money integer pence; shares are % (0–100, 1 dp);
`null` where there is no data, never 0.

```ts
{ period: {from, to, days}, previous: {from, to},
  figures: { orders, revenue_pence, avg_basket_pence: number|null, items, members_share: number|null,
             online_paid_share: number|null, cancelled, rejected, avg_minutes_to_ready: number|null,
             vs_previous: { orders_pct, revenue_pct, avg_basket_pct }   // null when the previous window has no orders },
  per_day: [{ date, orders, revenue_pence, cancelled }],                // one per day of the window; cancelled = cancelled + rejected
  by_hour: [{ hour: 0..23, orders }],                                   // 24 rows, local hour of requested_at
  by_weekday: [{ weekday: 0..6, orders, revenue_pence }],               // 7 rows, Mon = 0
  top_products: [{ product_id: number|null, name, qty, revenue_pence }],// top 12 by revenue
  top_options: [{ group, name, qty }],                                  // top 10 by qty
  dining: { takeaway, eat_in }, payment: { counter, online },           // placed orders in the window
  status_now: { new, accepted, preparing, ready },                      // not windowed
  caveats: string[] }
```

Money statistics: collected orders already reach `sale` as `channel=WEB, source=ONLINE`, so
`GET /api/finance/sales/insights` lists `WEB` in `options.channels` and `by_channel` as soon as
one exists (verified: `{'key': 'WEB', 'gross_pence': 2800, 'qty': '8', 'receipts': 3}`), and
`?channel=WEB` filters. The label "Website" lives in the dashboard (`web/src/screens/money/
TillInsights.tsx` `CHANNEL_ORDER` / `CHANNEL_LABEL` / `CHANNEL_COLOR`, added by another session
before B got there). B's only finance edit: the `WEB` / `ONLINE` mentions in the
`GET /api/finance/receipts` query descriptions (`api/areas/finance.py`).

#### 10.B.2 Shop fields on the Menu item page (owner decision, 2026-09-29)

`GET /api/shop-admin/products/by-menu-item/{menu_item_id}` → `ProductAdmin` for the product
whose `item_name` is that menu item's name (any size id works), plus `menu_item_id`,
`effective_option_groups: [{ id, name, kind }]` (explicit attachments first, then the
category's) and `shop_url_path: "/order/p/<slug>"`. Runs `catalog.sync_products` when the
row is missing; a name the sync skips (no active size) gets a row created so the editor still
works; 404 only when the menu item does not exist.
`PUT /api/shop-admin/products/by-menu-item/{menu_item_id}` takes the same body as
`PUT /products/{id}` and returns the same expanded shape.

#### D — order updates (owner addition, 2026-09-29)

- `scripts/shop/checkout/notify.ts` reads `config.push` / `config.sms_notify` and the order's
  `notify` defensively (typed as `ShopConfigX` / `OrderViewX` on top of C's `types.ts`, which
  D does not edit). Nothing renders until A ships the fields.
- **Checkout**: "Text me when it's ready (one message)" appears only when `sms_notify` is a
  string other than `"off"` **and** the contact is a mobile; `sms_opt_in` is sent only then
  (a 422 was observed when it was sent to a schema without the field). "We'll email you
  updates" shows when the contact is an email.
- **Status page**: `Get a notification when it's ready` when `config.push.enabled` with a
  VAPID key and `Notification` + `PushManager` + a service worker are available; registers
  `/order-sw.js` with scope `/order/`, subscribes with the key, `POST …/push?t=`; "You'll get a
  notification on this device" + Undo (`DELETE …/push?t=`, then `unsubscribe()`). Denied
  permission and failures say so in one line; the reason goes to `console.info('[shop] push…')`.
  iOS Safari without `PushManager` (not installed) gets the Home Screen hint. Hidden on final
  and PENDING_PAYMENT orders. `Updates: email · text · this device` line from `notify`.
- `site/web/public/order-sw.js` (site root): `push` → `showNotification(title, {body, tag:
  code, data:{url}})`, `notificationclick` → focus or open `data.url`. Not touched by the
  `/order/*` rewrites (Caddy matcher needs the slash; Vite regex requires `/order/`); served
  `text/javascript` at `/order-sw.js` in dev and copied to `dist/` by the build.
- Verified live with a fetch-patched config (push on): button shown, worker registered at
  `http://localhost:4321/order-sw.js`, permission granted, subscribe path ran and reported
  failure honestly (A's `/push` route not yet live). With the real config (no push) the block
  is hidden.

### A — backend (2026-09-29)

- **Files**: `cafeops/db/models/shop.py`; migrations `f2a3b4c5d6e7_online_ordering.py`,
  `f3b4c5d6e7f8_payment_providers.py`, `f4c5d6e7f8a9_customer_notifications.py`;
  `cafeops/domain/shop.py`; `cafeops/services/shop/{__init__,errors,catalog,slots,pricing,orders,payments,notify,push,pos,commands}.py`;
  `cafeops/integrations/payments/providers/{__init__,base,registry,stripe,lightspeed}.py`
  + `integrations/payments/registry.py` (re-export); `cafeops/integrations/pos/{__init__,base,null,lightspeed,registry}.py`;
  `cafeops/api/areas/shop.py` + `shop_schemas.py` + `shop_views.py`; `cafeops/jobs/shop_jobs.py`.
  Shared insertion points: `db/models/__init__.py`, `db/models/enums.py` (WEB, ONLINE),
  `config.py`, `.env.example`, `api/areas/__init__.py`, `api/app.py` (router + a `ShopError`
  handler that merges `extra` into the body), `api/areas/loyalty_edge.py` (`/api/shop` in
  `LOYALTY_PREFIXES` for the 422 shape), `jobs/scheduler.py` (`shop_expire_pending`, 5 min),
  `cli.py` (`cafeops shop`), labels for WEB in `bot/formatters.py`, `finance_schemas.py`,
  `services/finance/sales_insights.py`; `pyproject.toml` (`pywebpush`).
- **Enum names**: the contract's `PaymentMethod` clashes with `enums.PaymentMethod`
  (finance), so the shop's is `ShopPaymentMethod` in `db/models/shop.py`, with
  `PaymentMethod = ShopPaymentMethod` there as an alias. `OrderStatus`, `PaymentStatus`,
  `DiningOption`, `OptionKind`, `OptionLayout`, `UpsellPlacement` are as written.
- **`ShopError(LoyaltyError)`** (`services/shop/errors.py`) carries `extra`; the app renders
  `{"error", "detail", ...extra}`. 422 `basket_problem` also carries the fresh `quote`.
- **`POST /api/shop/orders` 422 `basket_problem`** (not in §4): a line that cannot be
  priced (sold out, size gone, option gone, required choice missing) refuses the order
  with the first problem and the quote, so the client shows which line.
- **Required option groups auto-fill their default** (Milk -> Whole milk) when the client
  sends nothing for them; a required group with no default is a problem on the line.
- **Reward hold**: nothing on `loyalty_reward` marks a hold; a reward referenced by a live
  order (PENDING_PAYMENT..READY) is simply not offered to another quote. Cancel releases it
  by doing nothing (event `reward_released`). If the reward was used at the till before
  COLLECTED, the discount is withdrawn at COLLECTED (`reward_unavailable` event, total
  corrected; the counter sees the new total) rather than refusing collection.
- **Reward redemption at COLLECTED** sets `redeemed_at`, `redeemed_menu_item_id`,
  `sale_id` on the reward, pointing at the shop's own `web:<code>:<n>` sale (gross 0 on a
  qty-1 line, `line_total - discount` otherwise) -- NOT via `redeem.redeem()`, which would
  write a second £0 loyalty sale and deplete the drink twice.
- **Stamps at COLLECTED**: one per eligible unit by the programme's own rule
  (`programs.program_rule`, the till's), minus one for a free drink, capped at
  `max_stamps_per_scan`; POINTS cards get `points_for_spend(total)`. `apply_units` with
  note `online order SC-…`.
- **Sale rows** carry `note = "online order SC-…"` and `recorded_by = "online shop"`.
- **Slots**: an ASAP order takes the first slot with room today (not just now+lead); a
  chosen time is floored to its slot and must be an offered slot with room
  (409 `slot_unavailable` / `slot_full`). `GET /slots.date` is the `SlotsView.day` field
  internally (a dataclass field cannot be named `date`).
- **`GET /api/shop/config.next_open_local`** is "Today HH:MM" when open now (the earliest
  slot), "Tomorrow HH:MM" / "Mon HH:MM" otherwise; `pay.provider` added; `push` block (§3c).
- **Rate limits**: orders 5/min and 10/hour per IP; catalogue/config/slots/quote 60/min;
  order status, me and push 30/min.
- **Customer-visible events** on `GET /orders/{code}` exclude `system` rows except
  `refund_needed` and `reward_unavailable`; the admin sees all of them.
- **Shop enabled for verification**: `shop_settings.enabled` was switched on and the hours
  widened to test at night; both were restored to the seeded values afterwards.
- **Unverified**: Stripe against the live API (checkout, refund, customers; the signature
  scheme and webhook parsing are exercised with a self-signed payload); Lightspeed Order
  & Pay (no credentials; see §3b.2); real push delivery (only a fake endpoint, which
  fails and is recorded as `notify_failed`); email/SMS sends (SMTP/Twilio unset here).

### 10.E Back-office screens (Agent E) — 2026-09-29

Files: `web/src/screens/shop/{LiveOrdersScreen,OrdersScreen,OrderPage,InsightsScreen,OptionsScreen,PromosScreen,SettingsScreen,OnlineSection,CategoriesDrawer,MenuScreen,SoundToggle}.tsx`,
`shared.tsx`, `alert.ts`, `menu-list.tsx`; `web/src/lib/shop-api.ts`, `web/src/lib/types/shop.ts`.
Insertion points: `web/src/routes.tsx` (seven routes, nav group **Online orders** after Customers),
`web/src/lib/shell-api.ts` (`badges.shop_new?`), `web/src/screens/menu/ItemPage.tsx` (one import,
`<OnlineSection/>` after Sizes & prices, `<ShopPreview/>` in the side column),
`web/src/screens/menu/MenuItemsScreen.tsx` (Online column/filter/bulk, Categories button + drawer,
the list and its drawers wrapped in one flex row so a drawer sits beside the list),
`web/src/screens/money/TillInsights.tsx` + `TransactionsScreen.tsx` (`WEB` → "Website", colour
`var(--color-seq-3)`: no chart token exists for a web channel; pick one with the integrator).

§7 table as built (owner decisions of 2026-09-29 applied):

| Hash | Label | Notes |
|---|---|---|
| `#/shop` | Live orders | kitchen tickets in four columns + "Later today" (due > 60 min out); polls every 15 s **in background tabs too** (`refetchIntervalInBackground`); chime + title flash + Notification (`alert.ts`) |
| `#/shop/orders` | All orders | server paging; status scope `all / live / today` only (the per-status filter B added is not exposed) |
| `#/shop/orders/<id>` | one order | statement, actions gated on `allowed_transitions`, cancel/reject with a reason, mark paid, staff note, customer + `#/loyalty/members/<id>`, payment (`pos_ref`), timeline (`pos_pushed`, `pos_failed`, `notified`, `notify_failed` named) |
| `#/shop/insights` | Insights | 7 / 30 / 90 days, `GET /insights?days=` |
| ~~`#/shop/menu`~~ | — | **retired**: redirects to `#/menu?view=list`. The shop menu is Menu items: Online column, "Online" filter, bulk Sold out today / Back on sale (list view, selected rows), "Categories" drawer (order, visibility, name, blurb, photo); each item's page has "Online ordering" (`by-menu-item`) and an "In the shop" preview |
| `#/shop/options` | Option groups | list + drawer; one PUT with the options; option photos after the group is saved |
| `#/shop/promos` | Banners & upsells | banners (photo after create), upsells per placement with a product picker |
| `#/shop/settings` | Settings | one Save per section; Payments (provider select + pay toggles + Till/POS sink), Customer updates (email / push / SMS Segmented) |

Deviations / notes:
- `nextAction()` shows the board's one button only when the server's `allowed_transitions` agrees.
- The "Later today" threshold (60 min) and the ticket's fresh edge (60 s) are the screen's own numbers.
- Sound: `cafeops.shop.sound` (localStorage) and `cafeops.shop.seen` (sessionStorage); the chime is Web Audio (880/660 Hz), 3× per ring, repeat every 30 s while anything is NEW, "Snooze 5 min".
- `shop_new` in `GET /api/shell` is not sent by B; the sidebar badge stays empty and the board counts itself.
- `pos_sink` uses `"none"` for no push (B), not `""`.
- Customer-update fields are typed optional (`email_notify`, `push_notify`, `sms_notify`; `smtp_configured`, `sms_configured`, `push_configured`; order `sms_opt_in`, `push_subscribed` / `push_subscription_id`) and render only when A ships them; until then the section saves them and the server ignores unknown keys or refuses (shown verbatim).
- Verified live against `cafeops serve --port 8001` with `npx vite --port 5179` (5178 was taken); 375 px checked through 375-wide same-origin iframes because the shared Chrome window ignored resizes.

### G — one menu (2026-09-29)

DECISIONS.md 29. The public website's menu page (`site/web/src/pages/menu.astro`, built
from `sashasite menu-export` and refreshed from `/api/menu`) is now presented from the
**same rows the shop uses**, read read-only by `sashasite` with Core `text()` selects
(`site/backend/sashasite/menu_source.py`; it never imports `cafeops`):

| Website menu shows | From |
|---|---|
| item description | `shop_product.description` (else `menu_item.note` only where the site's old row said `use_ops_note`; else none) |
| item shown / hidden | `shop_product.visible` |
| signature mark (★) | `shop_product.featured` |
| item order in its category | `shop_product.sort_order` (ties: the board's order, then name) |
| category name, blurb, shown / hidden, order | `shop_category.name`, `blurb`, `visible`, `sort_order`, keyed by `ops_name` = `menu_category.name` |
| category slug, item id | the website's own (board slug, else slugified name): stable URLs and photo slots, **not** `shop_category.slug` |
| item name, sizes, prices, seasons | `menu_item` / `season`, as before; `shop_product.display_name` is shop-only |

Consequences for the §2.3–2.4 fields: editing `description`, `visible`, `featured` or
`sort_order` on a product (the item page's "Online ordering" section, the list's bulk
actions) and `name`/`blurb`/`visible`/`sort_order` on a category (the Categories drawer)
changes the website menu too, within a minute. `available` (sold out today), `badge`,
`note`, kcal, allergens, photos and option groups stay shop-only.

- **Site admin:** `PUT /api/admin/menu/categories/{slug}`, `PUT /api/admin/menu/items/{key}`,
  `POST /api/admin/menu/order` answer 410 `"Edit the menu in Café Ops › Menu items (<edit_href>)"`.
  `GET /api/admin/menu` stays (read-only) with `overlay: "shop"|"site"|"none"` and per-row
  `edit_href` (`{SITE_OPS_URL}/#/menu/<menu_item_id>`, `#/menu?view=list` for categories).
- **Site tables** `site_menu_category_meta` / `site_menu_item_meta` are kept, not written;
  used as the overlay only when the shop tables are absent (a warning says so).
- **Carry-over:** `cafeops shop adopt-website-menu [--dry-run]`
  (`cafeops/services/shop/website_menu.py`) copies the site rows' description / signature /
  hidden / blurb into shop rows whose field is still empty or default, after
  `catalog.sync_categories` + `sync_products`. Idempotent; never overwrites a back-office
  value; never touches the site tables. Run once on 2026-09-29 (222 descriptions, 8 featured,
  1 hidden, 15 blurbs).
- **Back office:** Website › Website menu removed from the nav; `#/website/menu` redirects
  to `#/menu?view=list` (`screens/website/MenuRedirect.tsx`); `SiteMenuScreen.tsx` and
  `sitemenu-types.tsx` deleted; `sitemenu-rows.tsx` keeps only `Mover`/`MoveButtons`/`domId`
  (imported by `CategoriesDrawer`, `OptionsScreen`, `PromosScreen`). `OnlineSection.tsx`:
  toggles read "Shown online and on the website menu" / "Featured · Signature on the
  website", the description hint says it is the website line too, and the preview has
  "View on the website menu" (`{public_url}/menu#<slug>`). Website › Today's menu note links
  to `#/menu`.

### F — completion (2026-09-29)

Files: `cafeops/services/shop/{notify,orders,payments,pricing,catalog,admin}.py`,
`cafeops/bot/shop_handlers.py` (new), `cafeops/domain/shop.py`, `cafeops/db/models/shop.py`,
`cafeops/integrations/payments/providers/{base,stripe}.py`, `cafeops/api/areas/shop*.py`,
migration `g5d6e7f8a9b0_shop_table_and_payment_intent.py` (on `f4c5d6e7f8a9`; applied to
the live DB). One-line insertions: `bot/handlers/__init__.py` (import + `shop_handlers.router`
in `ALL_ROUTERS`, so the owner gate covers it), `integrations/pos/lightspeed.py`
(`TABLE <n>` in the K-Series order note).

- **Telegram order buttons.** The owner's "🛍 Новый онлайн-заказ …" message is sent with
  `reply_markup` (plain Bot API JSON from `notify.order_keyboard(order)`): row 1 the
  progress moves allowed from the order's status (`✅ Принять` · `👨‍🍳 Готовим` · `🔔 Готов` ·
  `📦 Выдан`), row 2 `✖️ Отклонить`; callback data `shop:<order_id>:<action>` with
  `action ∈ accept|start|ready|collected|reject` (`notify.TELEGRAM_ACTIONS`). Labels are
  Russian like the rest of the bot (the brief named them in English). `bot/shop_handlers.py`
  (`ShopOrderCB`, prefix `shop`) runs `notify.telegram_action` through `run_sync`: it is
  `orders.transition` by `telegram:<first name>`, then `edit_text` to
  `notify.order_message_text` (the order + `Статус: принят · 14:21 · Sasha`) with only the
  still-allowed buttons; COLLECTED writes the sale as on the web. A refusal is
  `callback.answer(text)` only: a stale keyboard answers `SC-XXXXXX уже выдан; кнопка
  устарела.`, an unknown order the 404 sentence. The outgoing `sendMessage` body is logged at
  DEBUG (`cafeops.shop.notify`, no token in it). `orders.transition` now queues the
  customer's cancelled/rejected notice for any actor that is not `customer` or `system`
  (was `staff:` only), so a Telegram reject tells the customer too.
- **Refunds.** `POST /api/shop-admin/orders/{id}/refund {by, reason?}` → `OrderAdmin`.
  PAID ONLINE: `provider.refund(order)`; success sets `payment_status=REFUNDED` and writes
  `refunded` ("£4.60 refunded with Stripe (reason): re_…"); a provider failure writes
  `refund_failed` (committed) and answers 409 with the provider's sentence. COUNTER → 409
  "SC-… was paid at the counter; refund it from the till."; already refunded / not paid →
  409. `OrderAdmin.refundable` (ONLINE and PAID) and `OrderAdmin.payment_intent`.
  `shop_order.payment_intent str(120)` is stored by the webhook (`checkout.session.completed`
  carries it; `PaymentEvent.payment_intent`), and `charge.refunded` — which names the
  payment, not the session — now finds the order by it. Stripe `refund()` posts
  `/v1/refunds {payment_intent}` straight from the stored id (falls back to reading the
  checkout session for orders paid before this change), idempotency key `refund-<code>`.
  Verified with a signed webhook and a captured `httpx.post`; not against live Stripe.
- **Allergens: unknown vs none.** `domain/shop.ALLERGENS` gains `"none"` (confirmed: no
  allergens). Catalogue products (public and admin) carry
  `allergens_state: 'unknown' | 'none' | 'listed'` — `unknown` is the empty list, which is
  not a claim and must not render as "no listed allergens". `allergens` unchanged.
  `PUT /products/{id}` with `"none"` beside another allergen → 422.
- **Category tile photo fallback.** A public category with no photo of its own gets
  `photo_url` from the first visible product in it (in shop order) that has one (own
  photo, else the menu item's) and `photo_is_fallback: true`; own photo → `false`.
- **Eat-in table.** `shop_order.table str(20)`; `POST /api/shop/orders` takes `table`
  (whitespace-collapsed, stored only when `dining = eat_in`, else dropped); on
  `GET /api/shop/orders/{code}` (`table`), `OrderAdmin.table`, the Telegram text
  ("в кафе, стол 4") and the POS note.
- **Order again.** `GET /api/shop/me` `recent_orders[].lines: [{product_id, menu_item_id,
  qty, option_ids}]` + `reorder_complete`. A line is kept only when its product and size
  are in the current catalogue and every option resolves; otherwise it is dropped and
  `reorder_complete: false`. Order lines now store `option_id` in their `options` JSON
  (`pricing.QuotedOption.option_id`); older lines are matched by group + option name.
- Not verified: a real Telegram round trip (token unset — taps were driven through the
  recording dispatcher of `bot-preview`), live Stripe refunds.

### H — public app completion and polish (2026-09-29)

Files: `site/web/src/scripts/shop/{App,types,store,router,format,mock}.ts(x)`,
`ui/{Sheet,BottomBar,Skeleton(new),index}.tsx`, `components/{Photo,ProductCard,TabStrip,TopBar,InfoSheet(new)}.tsx`,
`views/{Overview,Category,Product,Basket}.tsx`, `checkout/{Checkout,common}.tsx` + `d.css`,
`account/Account.tsx`, `status/Status.tsx`, `site/web/src/styles/shop.css`, `site/web/src/pages/order.astro`.
Coded against F's names as shipped (`allergens_state`, `photo_is_fallback`, `table`,
`recent_orders[].lines` + `reorder_complete`); every one is optional in `types.ts`, so an
older API still renders.

- **Allergens** (`format.ts: allergenState / allergenText`): `unknown` → "Allergens not
  listed yet — ask at the counter", `none` → "No allergens declared", `listed` → "Contains
  milk, gluten". Without `allergens_state`, an empty list is treated as `unknown` (never
  "no listed allergens"). Same sentence on the item page (Dietary info), the ⓘ card, every
  basket line (`.sh-line__allergens`) and the checkout statement (`Statement` lines take an
  optional `allergens` string).
- **ⓘ card** (`components/InfoSheet.tsx`): the grid's ⓘ is a sibling of the card link
  (`.sh-card > .sh-card__link + .sh-card__info`; a button inside an anchor is not HTML),
  opening a sheet with description, note, price, energy, allergens, dietary and the notice.
- **Order again** (`store.ts: reorder(cat, lines)`): re-adds through `basket.add` against
  today's catalogue; a product gone/sold out or size gone is left out by name, an unknown
  product id (or the server's `reorder_complete: false`) is "One item from that order is no
  longer on the menu", an option gone/sold out is dropped and a required group refilled with
  its default (the line is then flagged "an option has changed, so please check it").
  Nothing addable → a warn notice on the account page; otherwise → the basket with
  `basket.notice` or a toast. Verified live with the `Test Join Agent` card.
- **Eat-in table**: checkout shows "Table number (optional)" only when dining is eat in,
  sends `table` (trimmed, ≤20); the status page says "Table 4 · eat in" (else "Eat in ·
  collect from the counter"). Verified live (order rows deleted afterwards).
- **Borrowed category photos**: `.sh-cat--borrowed` zooms 8% and dims (`brightness .82
  saturate .85`) under the band; `Photo` takes `borrowed`.
- **Loading / error / offline**: `ui/Skeleton.tsx` (tiles, cards, item, lines; shimmer off
  under reduced motion; one `sr-only` "Loading …" status each) replaces every "Loading…"
  line, including the lazy-route fallback and the checkout's "Pricing your order…".
  `store.loading` and `store.online` signals; `loadError` now reflects **config** first
  (a cached catalogue can paint while the shop itself is unreachable). Overview shows a
  "We can't reach the shop right now" block (`role=alert`, Try again, link to `/menu`);
  category/item/basket show the sentence + Try again. `online`/`offline` events drive a
  plain banner at the top of the app, disable Checkout and Place order with words, and
  retry the load when back online. The basket stays editable offline.
- **Router**: `history.scrollRestoration = 'manual'`; `go()` remembers `scrollY` in
  `history.state` before pushing, popstate sets `restoreScroll`, and `App` scrolls there
  after the render (forward/new screens start at the top). `PageHead` no longer scrolls.
- **Sheet**: unique `aria-labelledby` per instance (was one id for all), first action focused
  on open (Tab order starts inside; native `<dialog>` keeps the trap, Esc and focus return),
  slide-up animation off under reduced motion. `BottomBar` measures itself into
  `--sh-bar-h` (ResizeObserver) so page padding and the toast follow a taller bar at 200%
  text zoom; the CTA wraps its label/price instead of overflowing. `TabStrip` centres the
  current tab in a layout effect (no post-paint jump). Basket pill text and the toast region
  are `aria-live=polite aria-atomic`. Tap targets: segmented options, small buttons,
  carousel dots and the join form's Email/Mobile switch are now ≥44px. `overflow-wrap:
  anywhere` on product, line, tile, band and upsell names; `minmax(0, 1fr)` on the item
  page columns.
- **SEO** (`order.astro`): title/description as before plus `extraJsonLd` `@graph` of a
  `WebPage` (`potentialAction: OrderAction` → `/order`) and a `Restaurant` with the same
  `@id` as Base's café entity carrying an `OrderAction` with
  `deliveryMethod: http://purl.org/goodrelations/v1#DeliveryModePickUp` and an `EntryPoint`
  to `/order`. Deep routes unchanged (client-rendered, not in the sitemap).
- **Mock** (`?mock=`): products carry `allergens_state` (drinks/cakes listed, third item of
  other categories `none`, rest `unknown`), every second category photo is `photo_is_fallback`,
  orders keep `table`, `/me` returns `lines` + `reorder_complete`, and the `reorder` flag adds
  a past order with a gone and a sold-out line.
- Verified in headless Chrome (CDP, real 375×812 and 1180×900 viewports, DPR 2) live and in
  mock: zero console errors from the app. The only console lines are the site footer's
  `/api/info` 502 when the site API on 8100 is down (not the shop) and, under emulated
  offline, the Astro dev toolbar's own fetches. Shop hours were widened for ten minutes to
  place the two live test orders and restored to the seeded values; both orders deleted.
- Not verified: a real screen reader; the sheet's focus return after a mouse click (driven by
  `el.click()` here, which leaves nothing focused to return to); Safari.

### I — back-office completion (2026-09-29)

Files: `web/src/screens/shop/{OrderPage,LiveOrdersScreen,OrdersScreen,InsightsScreen,SettingsScreen,OptionsScreen,OnlineSection}.tsx`,
`shared.tsx`, new `csv.ts`; `web/src/lib/shop-api.ts` (`orderWrites.refund`, `fetchAllOrders`, `ORDERS_PAGE_MAX`),
`web/src/lib/types/shop.ts` (`table`, `payment_intent`, `refundable`, `days_ahead?` on `OrderAdmin`; `allergens_state`,
`photo_is_fallback?` on `ProductAdmin`; `AllergensState`, `ALLERGENS_NONE`, `OrderRefundIn`). No edits outside those.

- **Order page**: "Refund" (`danger-soft`, then a reason field and a ConfirmTwice "Refund £x") shows only when
  `refundable`; `POST /orders/{id}/refund {by, reason?}`; the provider's refusal is shown verbatim (verified:
  "Stripe is not configured (CAFEOPS_STRIPE_SECRET_KEY)."). "Print ticket" = `window.print()` with a `@media print`
  stylesheet inside the page (`PRINT_CSS`): everything is `visibility: hidden`, the `#shop-print-ticket` block (code,
  ASAP / day / due time, customer, dining, table, lines with options, note, items · payment · total) is laid over the
  sheet at ≤ 92 mm. `table` shows in the statement line, the Customer panel and the ticket. "Copy code" copies
  `code_display` (clipboard API; says "Copied" for 2 s). The Rewards row links `#/loyalty/members/<id>` as before.
  Timeline names `refund_needed`, `refunded`, `refund_failed`, `reward_released`, `reward_unavailable`, `expired`.
- **Live board**: tickets are focusable (`tabIndex=0`); Enter on the ticket itself fires its next action, Esc closes
  a pending reject / cancel and disarms the ConfirmTwice (re-keyed). Each column has an `sr-only` `role=status
  aria-live=polite` line that names tickets as they arrive ("Order SC-… for Anna, due 14:20, in New"); the first
  render is not announced. Eat-in tickets show "Table N" beside the dining pill and in the aria-label. "Later today"
  becomes "Later" when any queued order is for another day; rows then lead with "Tomorrow" / the weekday (within a
  week) / "Thu 8 Oct". `days_ahead` is read from the server when present, else worked out from `requested_at` in
  Europe/London (`shared.daysAhead`); F has not added it to `OrderAdmin` as of this writing.
- **All orders**: "Download CSV" pulls every page of the current filter (`page_size=200`, looping on `total`) and
  builds the file client-side: UTF-8 with BOM, CRLF, columns `Code, Placed, Requested, Status, Customer, Dining,
  Table, Lines, Subtotal, Discount, Total, Payment method, Payment status, Member`, money as `£x.xx` text. File name
  `online-orders_<scope>[_from-D][_to-D]_<today>.csv`. The existing Transactions export is server-side, so there
  was no client column set to mirror; the Transactions button wording ("Export CSV") is server output, this one is
  "Download CSV" because nothing leaves the browser. Docked columns rebalanced (payment column joins at ≥ 1280,
  otherwise under the status tag) so names are not squeezed at 1180.
- **Online section**: "None (confirmed)" chip = the `"none"` token, exclusive (ticking it clears the rest; ticking
  an allergen clears it). The vocabulary from `/catalogue.allergens` is filtered of `"none"` so it never appears as
  a plain chip. The state is worked out from the draft with the same rule as `domain/shop.allergens_state` and said
  in words under the chips, "(after saving)" while it differs from the saved state; unknown reads "Allergens aren’t
  listed yet; customers are told to ask at the counter." Verified against the API: `["none"]` saves as state `none`,
  `["none","milk"]` is refused with the domain sentence.
- **Insights**: "Download CSV" of `per_day` (`Date, Orders, Cancelled or rejected, Taken (collected orders)`; a day
  with no orders leaves Taken empty, never £0.00). Every null figure is now words in place of the number ("no
  collected orders", "nothing made yet", "no orders"), no dashes; the cancellations share says "no orders" instead
  of "—".
- **Settings**: "Copy Monday to all weekdays" (Tuesday–Friday take Monday's row; disabled with "already match" when
  they do). Closures: the date picker has `min=today`, a duplicate date is refused in place, Enter in the note adds
  the closure, past closures are greyed and say "past", dates print as "Mon 25 Dec 2026". The Payments notes were
  checked against the live summary (`stripe_configured=false`, providers listed as "— not configured", Pay online
  disabled with the .env sentence; `smtp/sms/push_configured=false` render "isn't set up on the server yet").
- **Polish**: ticket note label is sentence case ("Note:"), a "Keep it" button beside the ticket's reject / cancel,
  Later rows get a code/time line at 375 instead of a broken 3-column grid, option-group list drops the "Applies to"
  column beside the drawer below 1280 (it moves under the name), closures add-row is a stacked grid at 375.
- **Verification**: `npx tsc --noEmit` and `npx vite build` clean; live in Chrome against a private API on 8002
  (this session's own test password in the env, so the owner's `.env` password was never read) with Vite on 5180;
  375 and 1180 checked through same-origin iframes (the shared Chrome window ignores resizes); zero app console
  errors. F's migration `g5d6e7f8a9b0` (`shop_order.table`, `payment_intent`) was applied here with
  `alembic upgrade head` because `POST /api/shop/orders` 500'd without it. Test orders SC-M6YZK6 / SC-WB4KVR /
  SC-PZL5KR and their `web:` sales were deleted afterwards.
- **Not verified**: a real print (only simulated by swapping `@media print` for `screen`); clipboard contents (the
  button state was checked, the clipboard read blocks on a permission prompt in this Chrome); a successful refund
  (no provider configured); `photo_is_fallback` is typed but not rendered (the preview already says "No photo yet").

### J — sign-in required to order (2026-09-29)

Files: migration `h6e7f8a9b0c1_shop_guest_orders.py` (on `g5d6e7f8a9b0`, applied to the live DB
after an upgrade/downgrade/upgrade on a `.backup` copy), `cafeops/db/models/shop.py`
(`ShopSettings.guest_orders`), `cafeops/services/shop/{orders,admin}.py`,
`cafeops/services/loyalty/join.py` (+ a two-line delegation in `backoffice.py`),
`cafeops/api/areas/{shop,shop_views,shop_schemas,shop_admin_schemas,shop_admin_views}.py`,
`web/src/lib/types/shop.ts`, `web/src/screens/shop/{SettingsScreen,OrderPage}.tsx`.

- **The rule** is §3.5's addendum above, enforced in `services/shop/orders.place_order`
  (`SIGN_IN_REQUIRED_DETAIL`), so the bot, the API and anything else placing an order
  share it. Verified: `POST /orders` without a token and with a bad token both answer
  the 401; with a valid token 201; `POST /quote` as a guest 200.
- **Admin**: `GET`/`PUT /api/shop-admin/settings` carry `guest_orders`. `#/shop/settings`
  › Ordering has the Toggle "Guests may order without signing in" with the note
  "Off: customers sign in with their Rewards card at checkout, so every order has a
  verified contact and earns stamps." Verified: `PUT {guest_orders: true}` → `GET /config`
  `require_account: false` → a guest order 201 (`SC-4DUZWK`, deleted since) → restored to
  `false`. The existing guest contact rule (`contact_required`: a phone or an email) still
  applies to guest orders.
- **Order page / ticket**: the Customer panel's Rewards row says "Guest" (was "not a
  member") and the print ticket's name line adds " · Guest", both only when `member` is
  null; a member's ticket is unchanged.
- **Profile** (`GET /api/shop/me`): gains `member: {first_name, email, phone, birthday_day,
  birthday_month, marketing_opt_in, member_since}`; `recent_orders` is unchanged (it was
  already limited to 10, not the 5 §4 sketches — left as F shipped it).
- **`GET /api/shop/me/orders?page=&page_size=`** (`page ≥ 1`, `page_size` 1–50, default 10;
  99 → 422): `{items: [{code, status, status_label, placed_at, placed_local ("29 Sep 2026
  09:58"), requested_local ("10:10", or day-qualified when the collection day differs from
  the day placed), dining, table, total_pence, lines: [{name, size_label, qty,
  options: [{group, name}]}], reorder: {lines: [{product_id, menu_item_id, qty, option_ids}],
  complete}}], total, page, page_size}`, newest first, the token's member only; no token
  → 401 `bad_token`. `reorder` uses F's `_reorder_lines` (a line whose product, size or
  an option has vanished is dropped and `complete` is false). Rate-limited like `/me`.
- **`PATCH /api/shop/me`** `{first_name?, email?, phone?, birthday_day?, birthday_month?,
  marketing_opt_in?}` → the `member` block. Any subset; a field left out is left alone;
  `email`/`phone` sent as `null` clear that contact; `birthday_day`+`birthday_month` both
  `null` remove the birthday. Consent goes through loyalty's `set_marketing_opt_in`
  (source "online shop"); everything else through loyalty's `update_details`, which now
  takes `set_email/email`, `set_phone/phone` and `source` on `DetailsChange`. The contact
  rules moved into `join.py` beside it (`contact_taken`, `_member_contact`) and the back
  office's `patch_member` delegates its `_taken` there, so there is one uniqueness rule:
  normalised (`+447700900123`), 422 `bad_email` / `bad_phone`, 409 `contact_taken`,
  422 `contact_required` when both would be empty, 422 `nothing_to_change` on `{}`,
  422 `first_name_required` on `first_name: null`, 422 `invalid_request` on an unknown
  field. Verified all of those by curl; the audit row reads "member changed first name,
  birthday, phone from the online shop". **Deviation from `PreferencesIn`'s stance**
  (loyalty §: "email and phone are not editable here … a typo would lock the member
  out"): the owner's decision lists them, so the shop lets a member change them; the
  one-contact-kept rule is the safety net.
- **Review fixes** (coordinator, 2026-09-29), all in J's files:
  1. `GET /orders/{code}` (customer) events are an allowlist — `placed, paid, accepted,
     preparing, ready, collected, cancelled, rejected, refund_needed, refunded,
     reward_unavailable, reward_released, expired`; `refund_needed` reads only "A refund
     is due.", `refunded` "Refunded." (no provider ids); `note`, `notified`, `notify_failed`,
     `pos_*`, `refund_failed`, `payment_error`, `sale_recorded` never leave. Verified: after
     a staff note and an accept, the DB holds `placed, note, accepted, notify_failed` and
     the customer sees `[('placed', …), ('accepted', None)]`.
  2. Order rate limit is 20/min and 60/hour per IP, counted on 201 and honeypot hits only
     (`_check` before, `_count` after); 409/422 refusals do not count. Verified: 25
     consecutive 422s then a 201. 429 wording unchanged.
  3. `GET /config` carries `sms_notify: 'off'|'ready'|'all'` (the setting, "off" when
     Twilio is not configured) and `updates: {email, push, sms}` (setting on AND channel
     configured). Verified on this box: `sms_notify: "off"`, all three false.
  4. `orders._collect`: a held reward that is gone at COLLECTED no longer raises the
     total on a PAID order. PAID online → `discount_pence`/`total_pence` stay as paid,
     the discount still lands on the sale line it was for (gross 0 here), nothing is
     redeemed, event `reward_unavailable` "The free drink had already been used; the
     online price stands."; UNPAID counter → discount withdrawn and the new total, as
     before. Verified with `_collect` on a `.backup` copy (both branches).
- **Cleanup**: test orders `SC-9B65KV`, `SC-UW4UCG`, `SC-4DUZWK` (and their lines/events)
  deleted; no `web:` sale was written on the live DB (collect ran on the copy);
  `guest_orders` restored to `false`; the test member's name, phone, birthday and consent
  restored (its `birthday_set_at` is now today, and four loyalty audit rows from the
  PATCH tests remain).
- **Not verified**: the public app (`site/web`) does not yet read `require_account` or
  handle `sign_in_required` — that is the checkout's job, outside J; the toggle was checked
  through the API and `tsc`/`vite build`, not clicked in a browser.

### K — sign-in wall, account and profile (2026-09-29)

Files: `site/web/src/pages/account.astro` (new), `site/web/src/scripts/shop/{AccountApp.tsx,member.ts}` (new),
`account/{Account,SignInWall}.tsx`, `{api,types,store,router,toast,mock,App}.ts(x)`, `components/TopBar.tsx`,
`views/{Basket,Overview}.tsx`, `checkout/{Checkout,common}.tsx`, `status/Status.tsx`, `site/web/src/styles/shop.css`;
one-liners: `astro.config.mjs` (`/account` out of the sitemap), `pages/rewards.astro` (copy only),
`components/Header.astro` ("My account" link + an inline script that swaps in the first name).

- **The account is the thing; the Rewards card lives inside it** (owner, 2026-09-29). `/account` is a
  site-wide static page mounting `AccountApp` (the same `Account` screen the shop had); `/order/account`
  does `location.replace('/account' + search)`, so `?next=checkout` survives and the checkout wall still
  returns to `/order/checkout`. `paths.account()` is `/account`; `router.go()` does a full load when the
  app is not mounted under `/order` (the island's "Back to checkout" / "Order again"). Nav copy: "Sign in"
  / "Create account" (was "Join Rewards") everywhere in the shop; the join tab's lead line is
  `ACCOUNT_LEAD` in `Account.tsx`.
- **Sign-in wall** (`SignInWall.tsx`, `member.needsSignIn()` = `config.require_account && !signedIn`):
  the basket shows the panel under the total and its bar CTA becomes "Sign in to order"; the checkout shows
  the panel in place of the form; `POST /orders` → 401 `sign_in_required` (or `bad_token`) shows the same
  panel inline with a one-line reason and forgets a dead token. Guest flow unchanged when `require_account`
  is false. Both buttons go to `/account?next=checkout&tab=signin|join`.
- **`member.ts`**: `signedIn` (signal: a token is on this device), `member` ({card_id, first_name}, cached
  under `localStorage['sc.shop.member.v1']` so the top bar and the site header say the name on first paint),
  `me` (the last `/me`), `loadMember()` (401 → `signOut()`), `signIn()`, `signOut()`. The Header's inline
  script reads the rewards token slot and this cache; nothing is fetched there.
- **Profile** (signed in), in order: *Your Rewards card* — `cardHtml()` from `GET /api/loyalty/card/{id}`
  with the token (`card.css` imported by `Account.tsx`), `walletHtml()` under it, "Open the full card" →
  `/c/<id>`; a 404/410 on the card, or a club card whose siblings hold no `stamp` card, shows "Add your
  Rewards card" (`POST /api/loyalty/card/{id}/programs {program: <is_default slug>}`), and `card.joinable`
  lists as "Add to your account" (new card's token saved, the account stays on the card it signed in with).
  *Free drink* when `/me.reward` or a `STAMP_CARD` reward. *Your orders* — `GET /api/shop/me/orders`
  paged 10 with "Show more" (a 404/405 falls back to `/me.recent_orders`); "Order again" reuses
  `store.reorder` and, because the basket is a page load away, stashes the notice / toast in sessionStorage
  (`store.stashNotice`, `toast.stashToast`, read by `basket.notice` / `App` on mount); "View" only when this
  device holds the order token. *Your details* — `PATCH /api/shop/me` with the changed fields only; J's
  answer is the `member` block (the mock answers the whole `/me`; both handled); 422 field codes and 409
  `contact_taken` land on the field with the server's `detail` verbatim, others under the form. *Sign out on
  this device.*
- **Status page**: "Signed in as <name> · stamps added when you collect", or, signed out with guests
  allowed, "Create an account to earn stamps on your next order."
- **Mock** (`?mock=`): `wall` (require_account), `/me.member`, `/me/orders` (13 rows, `reorder` adds
  `SC-GONE01` with a gone and a sold-out line), `PATCH /me` (`taken@example.com` → 422 `email_taken`).
  The rewards mock's card is "Olena" while the shop mock's member is "Sasha": two mocks, one visit.
- **Verified**: `npx tsc --noEmit` and `npm run build` clean; in Chrome, mock: wall on basket and checkout,
  sign-in (code 123456) returning to checkout with the basket intact, an order placed and the status line,
  `/order/account` → `/account` redirect, profile at 1443 and at a real 375-wide same-origin iframe
  (no horizontal overflow), Save (`Saved.`), Order again with the left-out sentence on the basket, header
  and mobile menu showing the name. Live: `/me`, `/me/orders` and `PATCH /me` answered for the `Test Join
  Agent` card (its email was PATCHed to a test value and restored to `test-join-agent@example.com`).
- **QA sweep, folded in**: `document.title` on checkout ("Checkout · Order ahead · Sasha's Corner"), account
  ("My account · Sasha's Corner") and status ("Order SC-XXXXXX · Sasha's Corner") through `views/useTitle`
  (new second argument `site` drops the "Order ahead" middle). A non-2xx `GET /config` (a 422 was seen
  mid-deploy) now reads as unreachable: `store.configError` keeps network/timeout/5xx/429 wording and turns
  any other status into "The ordering system answered with an error…" under the overview's "We can't reach
  the shop right now." block (mock flag `cfg422` reproduces it). The site header's account link follows
  sign-in / sign-out without a reload (`member.syncHeader`).
- **Not verified**: a real one-time code (no SMTP/SMS here); Apple/Google Wallet links (not configured);
  the "Add your Rewards card" / joinable flows against the live API (the test member has the default card
  only); a real 401 mid-checkout (mock cannot expire a token after `/me` passed).


### Integrator — readiness audit fixes (2026-09-29)

From the whole-diff code review and the QA sweep (both agents' reports are in the
session, not in the repo). Fixed by the integrator:

- **Site Docker image did not build**: `npm ci` refused TypeScript 7 against
  `@astrojs/check` (only the dev install with `--legacy-peer-deps` worked). The site is
  on TypeScript ^6 now; `sashasite info-export` no longer needs the `site_setting`
  table (falls back to `config/cafe.toml`), which the image's data stage never has.
  `docker compose build api caddy site-api site-static` succeeds.
- **Lightspeed payment webhook** was unauthenticated: it now requires
  `CAFEOPS_LIGHTSPEED_WEBHOOK_SECRET` presented as `X-Lightspeed-Secret` (403 otherwise).
- **Stripe** Checkout Sessions expire after 30 minutes (matching `expire_pending_payments`);
  a payment that still lands on a cancelled/rejected order is recorded as PAID with a
  `refund_needed` event instead of a 409; the redelivery stamp is written through a
  query, so duplicate webhooks are duplicates.
- **Money never floats** in the Lightspeed adapters (`Decimal`).
- **Customer status page** shows only an allowlist of events (no staff notes, no
  provider ids); orders rate limit is 20/min, 60/hour per IP counting placements only;
  `/api/shop/config` carries `sms_notify` and `updates`; a PAID online order keeps its
  paid total when the held reward is gone at COLLECTED. (Agent J applied these.)
- Push unsubscribe sends the endpoint as `?endpoint=`, so only that device is dropped.
- **Catalogue**: products whose ops item has no category are served under a catch-all
  category `more` ("More from the menu", id 0) instead of being unreachable.
- Admin: option-group `kind`/`layout` and upsell `placement` accept lowercase;
  `PUT /upsells/{id}` takes any subset (`UpsellPatchIn`).
- `next_open_local` includes the lead time; "Orders can only be placed for today."
- `cafeops doctor` has an "online ordering" line; `docs/shop/GO-LIVE.md` written.
- `site/web/node_modules` and `site/web/.astro` untracked and ignored.
