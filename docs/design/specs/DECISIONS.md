# Back-office redesign — owner decisions (2026-09-26)

These override anything in the four specs (`design-system.md`, `shell-agents.md`,
`stock-orders-suppliers.md`, `recipes-menu-ingredients.md`, `finance.md`) that disagrees.

## Scope
Complete redesign of every screen in the design's nav: Stock, Orders, Agents, Recipes,
Menu items, Ingredients, Suppliers, Overview, Sales, Expenses, Reconcile, Profit & loss,
Director's account, Settings, plus Login and the empty-install / Setup checklist.
Backend and frontend. The design (`docs/design/Cafe Ops v2.dc.html` and the sub-apps) is
the visual spec; CLAUDE.md §13 invariants win over the design wherever they conflict,
using the resolutions recommended in each spec's "Conflicts" section.

## Decisions
1. **Web orders stay read-only.** No "Send to Telegram to confirm" button. The web app
   never creates or confirms a `purchase_order`; `api/routers.py:8-11` stands. Orders
   screen shows drafts, history, and "Waiting in Telegram" for pending ones. Receiving a
   delivery against an already-confirmed order, cancelling, and marking sent are fine.
2. **Uppercase labels: follow the design.** Sidebar group heads and table headers are
   uppercase as designed. CLAUDE.md §10's ban is lifted.
3. **Password change in Settings: full version.** scrypt hash in DB with explicit
   precedence over the env var; session tokens instead of the raw password on every
   request; 5 attempts/min rate limit on login; change requires current password and
   ≥10 chars; a change revokes every session, writes an audit row and sends a Telegram
   notice. Also fix the unlock bug (shell checks `/api/meta`, which is unauthenticated).
4. **Finance data:**
   - Capital injections are shown separately on Director's account and do **not** count
     toward "company owes you".
   - Rename "Square cash" / "Square fees" → "Till cash" / "Card fees" everywhere.
     *(Superseded for cash by 26: it is just "Cash" now.)*
   - Just Eat never paid cash: the £114.92 of Just Eat money in the workbook's cash column
     is imported as Just Eat (card/platform) takings, not cash.
   - VAT: unanswered. Do not claim prices include or exclude VAT; drop the "Prices exclude
     VAT" footnote.
5. **Settings rows:** keep password + Lightspeed status/Sync now; "Show empty-install" →
   "Setup checklist"; drop bot-language select and "Reset demo data" (per shell-agents spec).
6. **Operator identity:** per-device "who's using this" name (not a login), sent with
   counts, receipts, checklist answers, write-offs, decisions.
7. **Agent proposals:** new `agent_proposal` table; the agent may only insert proposals;
   accept/decline is human, recorded, and goes through services.
8. **No tests** (ARCHITECTURE.md §1). Verify by running: ruff, mypy strict on
   domain/ + services/, `tsc`, `vite build`, and exercising endpoints against the real DB.

## Menu item page (owner feedback, 2026-09-26)
9. **No impact preview on a one-off item's ingredient edit.** The "What this change does"
   panel is gone from the item page's recipe editor: edit the ingredients, then
   "Save recipe" (still applied from today, so past sales keep their recipe;
   invariant 3 stands). This relaxes CLAUDE.md §5.6's "mandatory impact preview"
   for that editor only; price changes and shared recipes (Menu › Recipes) keep theirs.
10. The item page fills the screen (no 1080px cap): main column plus a side column
    with the photo and actions. Ingredients are picked from a searchable list with
    category filters, and a size can copy another size's ingredients.

## Money and orders (owner feedback, 2026-09-26)
11. **Reconcile, Profit & loss and Director's account screens are removed.** P&L is
    consolidated into Sales as charts ("Profit by month"). Decision 4's finance rules
    (capital injections, till/card renames, Just Eat, VAT) still hold for the data.
12. **Orders are expenses** (superseded by 19 for placement). Orders leave the "Every day"
    nav; they briefly lived in Money › Expenses as tabs. The waiting badge
    moves to Expenses. Order totals are not merged into the expenses log (the workbook's
    expenses already include supplier spend; merging would double-count).
13. **Orders and draft orders are rows, not cards,** in the Menu list layout. The Orders
    list is filterable: search, supplier, status, date range, "no receipt yet", sort.
14. **Each order has its own page** (`#/money/expenses/orders/<id>`) with the receipt
    photo. Receiving there puts stock on the shelf (a batch + DELIVERY movement per
    line), so Stock updates immediately. Decision 1 stands: never a confirm on the web.
15. **Receipt photos** reuse the menu photo store (`media_asset`, content-addressed,
    served at `/media/…` without auth like menu photos). A photo is evidence only.
16. **Stock and Ingredients share one list layout and filter set** (the Menu items list).
    They are the same entities viewed two ways: Stock = what is on the shelf, updated
    as orders are received; Ingredients = what things cost.
17. **Migrations:** nothing is live, so schema additions go into the existing
    unreleased migration (`62aa94a23687`) rather than new revisions, until first deploy.

## Orders without Telegram (owner, 2026-09-26, later the same day)
18. **The Telegram bot is not in use for now; orders are placed on the web.** This reverses
    decision 1. A draft basket becomes a DRAFT order with "Create order" (recomputed by the
    server from today's run, one open order per supplier and delivery date); on the order
    page a named person sets the packs and confirms. Invariant 1 is unchanged: confirmation
    needs a name (repository + CHECK constraint). The app still never sends anything to a
    supplier. Telegram wording is gone from the web ("Waiting to confirm", not "in Telegram").
19. **Orders is its own Money page** (after Expenses) with three sub-pages: Orders, Draft
    orders, Shop runs. The order page follows Money › Overview's statement layout.
20. **Stock:** "Needs attention" is its own tab between On the shelf and What to buy — one
    card per job (Use soon, Running out, Drifting, Count these, Marked low), each with its
    action; the ingredient opens as a page (`#/stock/<id>`), not a drawer.
21. **Sales is a dashboard** above the day-by-day table: figures with "vs previous period",
    takings over time (stacked by card / cash — till/own split retired by 26 — day/week/month, legend keys
    toggle series, hover for the breakdown), where it came from (till methods + delivery
    apps from monthly statements, with commission and ads; a month not uploaded is
    "missing", never £0), by weekday, best days, then Profit by month. Colours validated
    with the dataviz checker: card #4a6fd1, cash #1baf7a (`--color-chart-cash`), delivery
    apps #8a4fc8. Per-order channels need Lightspeed; the `sale` rows today are demo seed.

## Ingredient stock page (owner, 2026-09-26, later the same day)
22. **The ingredient's stock page (`#/stock/<id>`) is laid out like the menu item page**:
    white panels with an `h2` per section and uppercase table labels. The main column
    holds *On the shelf* (worked out · last counted · runs out, side by side), *Counts*
    (drift and cause, the count table, "Count it now") and *Batches*. The side column
    holds *Delivery came in* and *Write off* as labelled fields, then *Tier* and *How long
    it keeps* / reorder level. The old drawer (`StockDrawer.tsx`) is gone; the sections
    now live in `StockItemSections.tsx`.
    **No "why" prompt on a tier move:** one tap changes it. `reason` is optional on
    `POST /api/ingredients/{id}/tier`, and the service records a default. Tier A is still
    earned (invariant 2): the A button is disabled until earned, and the server refuses it anyway.

## Accessibility overrides of design values (UI audit, 2026-09-26)
23. **Five colour tokens no longer match the design export**, because the design values fail
    WCAG AA where the screens use them. The audit named the conflict and the owner ran the
    fix anyway (`/impeccable:normalize`). Don't "restore" these from `design-system.md` §1:

    | Token | Design | Now | Why |
    |---|---|---|---|
    | `ink-3` | `#8a93a3` | `#687080` | 3.1:1 on white, 2.9:1 on canvas; it labels every table column and nav group |
    | `alert` | `#d4554a` | `#c2453b` | 4.0:1 on white; 20 of its 21 uses are text under WCAG's large size |
    | `bad-ink` | `#c2453b` | `#b8443a` | 4.4:1 on `bad-wash` (pills) |
    | `warn-ink` | `#9a6a12` | `#8f6210` | 4.3:1 on `warn-wash` ("Drifting" pill) |
    | `ok` | `#3fa57a` | `#34936b` | switch track at 3.05:1 against white |

    Keyboard focus on fields is now a 2px brand edge (border + `ring-1 ring-brand`) instead
    of the design's pale `brand-wash` halo, which was ~1.1:1 and could not be seen. Chart
    series colours from decision 21 are tokens now (`--color-chart-*` in `styles.css`).

## Sales dashboard, till lines (owner, 2026-09-28)
24. **Sales now leads with "Till sales"**, built from the Lightspeed receipt lines in `sale`
    (`GET /api/finance/sales/insights`, `services/finance/sales_insights.py`): figures with
    "vs the same number of days before", sales over time, by channel, when people buy
    (weekday × hour heatmap), by weekday, best sellers (£ or units), by category, size mix,
    items per receipt. Then Takings (decision 21), Profit by month, Day by day, with a
    sticky section nav.
    **One filter row scopes the page**: period and weekday apply to both ledgers; channel,
    category, product and size apply to till lines only; paid by, orders and day total to
    takings only. Each section says which filters it ignores. Clicking a product,
    category, channel, size or weekday in a chart sets that filter.
    **The two ledgers are never added together.** Takings say how much money came in;
    till lines say what was sold. A day with no till lines is `null`, not £0. When every
    line in the window starts `DEMO-` the section is badged "Demo data".

## Website admin moves into the back office (owner, 2026-09-28)
25. **One back office.** The public website's admin (bookings, messages, events, website
    menu, photos, café details), which lived at `/admin` on the site with its own olive /
    caramel look and its own password, is now the **Website** group in this app's sidebar
    (`#/website…`, `web/src/screens/website/`), in the back office's styles and behind the
    back office's one password. This reverses the same morning's choice to dress the site
    admin in the café's brand.
    **How:** the site API (`sashasite`) stays the owner of that data. The back office
    forwards `/api/website/<path>` to the site's `/api/admin/<path>` after its own sign-in
    (`cafeops/api/areas/website.py`), with a shared secret (`SITE_SERVICE_KEY`, one value in
    `.env` read by both) that the site accepts in place of its cookie. The site's own
    login, logout and password routes are not forwarded. Photo files come through the open
    `/api/website-media/*` (they are public on the site anyway). Site admin passwords no
    longer matter to the owner; the back office's Settings password guards both.

## One cash figure per day (owner, 2026-09-28)
26. **One Cash entry per day; "Own cash" retired.** Owner: *"what is the difference for
    the till cash vs cash itself like we need only one entry of the cash please
    properly"*. The Sales tab, its drawer ("Add cash": Date, **Cash** — "Cash taken
    today", Note), the table, the charts, Overview, Profit by month and Transactions all
    show a single **Cash** figure. The words "Till cash" and "Own cash" are gone from
    the UI.
    **Data:** a typed cash figure is a `payment_day` row with `method = CASH`, source
    MANUAL. `PaymentMethod.CASH_OFF_TILL` stays in the enum (old rows and migrations
    reference it) but **nothing writes it** — not the Sales tab, not `import-finance`,
    which now sums the workbook's "Square cash" and "Own cash" (override, else auto)
    into one CASH row. Every read folds any legacy CASH_OFF_TILL row into Cash, so no
    money is dropped; typing a new cash figure for a day removes a typed/workbook
    CASH_OFF_TILL row, so the day keeps one figure.
    **API:** `cash_till_pence` + `cash_off_till_pence` → `cash_pence` (Sales days,
    totals, P&L/overview period figures); `editable.cash_till` + `editable.cash_off_till`
    → `editable.cash`; `POST /api/finance/sales` and `PATCH …/sales/{date}` take
    `cash_pence`. Do not reintroduce a second cash field without the owner.

## One consolidated Sales dashboard (owner, 2026-09-28, later the same day)
27. **Sales is one dashboard, not two stacked ones.** Owner: *"integrate the whole
    dashboard into one consolidated view where we can filter cash, see deliveroo, see
    just eat … the top dashboard should consolidate the bottom stuff"*. The separate
    "Till sales" and "Takings" sections (decisions 21 and 24) are merged into **Money
    in**: one figures strip (till sales, receipts, avg basket, per day open, busiest
    hour · money taken, card, cash · delivery apps), one **Over time** chart (card and
    cash as stacked bars, till sales as a line over them, legend keys toggle), one
    **Where it came from** panel (rung up through · paid by · delivery statements for the
    months in view), the heatmap, **By weekday** and **Best days** with a Till sales /
    Money taken switch. Then **What sold** (best sellers, category, size, baskets), Profit
    by month, Day by day.
    **Filters:** "Paid by" is now Card or cash / Card / Cash and picks that money across
    the takings figures, chart series and the day table (which drops the other column);
    the old "Has card / Card only" day filters are gone. The channel filter always offers
    In store, Deliveroo and Just Eat; picking an app filters till lines to it *and* swaps
    card and cash (till money) for that app's monthly statements. Clicking Card or Cash
    in Where it came from sets Paid by, the same as channels and products.
    **Still true:** the two ledgers are never added (`SalesDashboard.tsx` header); a
    statement not uploaded is "not uploaded", never £0.

## Transactions from Telegram (owner, 2026-09-28)
28. **The Telegram bot is back in use, for money -- not for orders.** Decision 18 stands
    for ordering (orders are created and confirmed on the web); the bot now takes the
    things the till never sees, so `sale` is the whole picture and not the Lightspeed
    slice. Four commands, all Russian, all owner-chat only:
    - `/sale` -- a sale past the till: pick a channel (**Cash (off till)**, Deliveroo,
      Just Eat, Other), then items by category button or typed name, a quantity, an
      optional app price and date, «Записать». Written as ordinary `sale` rows with
      `source = MANUAL`, `recorded_by = telegram:<user>`, expanded into stock by the
      nightly job like a till line. «Отменить эту продажу» voids (never deletes).
    - `/cash` -- the day's one cash figure (decision 26), today / yesterday / a typed
      day; refused when that day's cash came from an export.
    - `/export` -- today / 7 / 30 days / everything as `transactions_<from>_<to>.csv`.
    - a sent `.csv` -- classified by its header row (transactions file, takings
      export, or Deliveroo / Just Eat report), shown as a **dry run that writes
      nothing**, written on «Записать». The same file twice adds nothing.
    **Data:** `SaleChannel.CASH` is new and means "paid in cash, not rung through the
    till"; a till sale paid in cash is still `EPOS`. `sale.source`
    (`POS_API | MANUAL | CSV_UPLOAD | LOYALTY`), `sale.recorded_by`, `sale.note` are new;
    every existing row is `POS_API`, loyalty redemptions `LOYALTY`. **`EPOS` cannot be
    typed anywhere** (`services/record_sale.py` refuses it): the till is synced from
    Lightspeed and a typed till sale would count twice.
    **Web:** Money › Transactions › Receipts gains a **Source** filter (Till / Added by
    hand / CSV file / Loyalty reward), shows "added by hand by telegram:sasha" under a
    typed receipt, offers **Export CSV**, and names Cash (off till) as a channel here
    and on the Sales dashboard. No "add a sale" form on the web yet; the API has it
    (`POST /api/finance/transactions`, `.../{receipt_id}/void`, `.../export.csv`,
    `.../import`, `GET .../menu`).

## One menu everywhere (owner, 2026-09-29)
29. **The shop menu, the menu and the website menu are one thing.** Owner: *"shop menu
    and menu itself should be consolidated — it is the same thing"*. Earlier the same day
    the Order online catalogue was folded into Menu items (the "Online ordering" section
    on `#/menu/<id>`, the Categories drawer on the list, `#/shop/menu` redirects). This
    folds the last separate copy: the public website's menu page, which had its own
    presentation (descriptions, signature marks, hidden flags, blurbs, order) in the
    site's `site_menu_*_meta` tables and its own screen, Website › Website menu.
    **Now:** the site reads `shop_product` / `shop_category` read-only as the website
    menu's presentation (`description`, `visible`, `featured` = the signature mark,
    `sort_order`; category `name`, `blurb`, `visible`, `sort_order`), so "Shown online and
    on the website menu" and "Featured · Signature on the website" on the item page, and
    the Categories drawer, are the website menu too. Website › Website menu is gone from
    the nav; `#/website/menu` redirects to `#/menu`; the site's three menu write routes
    answer 410 pointing at the back office; Website › Today's menu note links to Menu
    items; the item page's side column gains "View on the website menu".
    **Data:** the site tables are kept, not dropped, and no longer written. Their copy
    (222 descriptions, 8 signature marks, 1 hidden item, 15 blurbs) was carried into the
    shop once with `cafeops shop adopt-website-menu`, which never overwrites a value set
    in the back office and can be re-run. The website keeps its own stable category slugs
    (`/menu#hot-matcha`, photo slots `menu.<slug>`), not the shop's, and item names stay
    `menu_item.name` (the shop's `display_name` override is shop-only, so site item ids
    do not move).
