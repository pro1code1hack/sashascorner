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
