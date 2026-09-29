# Front-end audit — every screen of the back office (2026-09-29)

Scope: `web/src` (45k lines, 30 routes). Method: every screen file read in full by six
area auditors against the `frontend-design` DON'T list plus WCAG 2.2; a global grep for
colour literals, palette classes, gradients, easing, overlays, polling; and a live pass of
the fixture-mode build (`VITE_FIXTURES=1`, Vite on :5178) at 1920px and inside a 375px
iframe, running an in-page script for overflow, headings, landmarks, names, labels,
tap-target size and measured contrast on 22 routes. Numbers below marked *measured* come
from that pass. Shop, Loyalty and Website screens have no recorded fixtures, so they were
audited statically only.

This is an audit, not a fix. Nothing in `web/` was changed. Paths are relative to `web/src`.

---

## 1. Anti-patterns verdict

**Pass, with three caveats.** Shown to a designer, this would not read as AI-generated.

What is *not* there, and it matters: zero Tailwind palette classes, zero gradients, zero
glassmorphism beyond two faint sticky strips, no glow, no dark-mode-by-default, no icon-
heading-text card grids, no sparklines, no modals (the only overlays are the kit `Drawer`
sheet and the off-canvas nav), no traffic-light encoding, one type family with tabular
figures, elevation by hairline, and copy with a person's voice ("not uploaded: missing, not
zero", "Nobody has booked for today. Walk-ins only.", "A guess stays a guess"). Hex
literals exist in exactly three files (the loyalty pass palette twice, a print stylesheet
once). Estimates are italic, counts upright, missing is a dash with a reason. That is
disciplined work.

The tells a designer *would* catch are structural, not visual:

1. **The KPI grid survives in one place.** Loyalty › Insights opens with six identical
   bordered tiles, big number, small label, green/red delta (`screens/loyalty/InsightsScreen.tsx:116-201`).
   Website › Today puts 30px figures inside an 18px sentence and shows the week as seven
   identical number tiles (`screens/website/TodayScreen.tsx:229,243,403-440`). Menu › item
   sales has a four-up grey stat strip (`screens/menu/ItemSales.tsx:144-150`).
2. **Red and green used as moods, not thresholds.** Any month-on-month dip is painted
   `bad-ink` in three dashboards; "preferred supplier" is a red star; one member in five
   gets a red or amber avatar; "saved" is green text on white in eight places. The design
   law (red = crossed threshold only; green only inside a pill or the toggle track) is
   written in `styles.css` and broken by the screens.
3. **The same thing built five different ways.** Data tables as div grids beside a
   `<Table>` primitive (15+ sites); six status-line components; five copies of the date
   helpers; two `Panel`s; a blue `Switch` cloned from the green `Toggle`; a private
   `ArmChip` beside `ConfirmTwiceButton`; link-buttons hand-rolled six times. This is the
   fingerprint of generation in passes, and it is what makes the fixes below expensive.

---

## 2. Executive summary

| Severity | Count | What they are |
|---|---|---|
| Critical | 4 | a list that loses its names on laptops; a row that hides every figure from screen readers; two dead deep links; a one-tap broadcast to every member |
| High | 17 | contrast lost through opacity; div-tables; chart tab-stop floods; unlabelled inputs; write-on-arrow-key; data loss on save; 22–32px primary tap targets |
| Medium | ~30 | colour-as-mood; KPI tiles; hex and off-scale values; polling; 2.6k dead lines; copy that names retired features |
| Low | ~20 | radii, glyph icons, sticky headers that cannot stick, new-tab cues |

Roughly 70 distinct findings, most of them a handful of patterns repeated. **Overall
quality: 7.5 / 10.** The foundation (tokens, primitives, semantics, copy, money handling)
is better than most hand-built back offices. The debt is consistency and a semantics pass
that never happened.

**The five that matter most**

1. **Stock list shows no ingredient names between 900 and ~1150px** (*measured*: name
   column 0px at 903, 6px at 1022, 82px at 1100, readable from ~1180). iPad landscape and
   most laptops hit this. `screens/stock/StockList.tsx:20-21`.
2. **Stock rows are silent to screen readers.** `aria-label="{name}: open details"`
   replaces the row's name, so Left, Drift, Trust and Runs out are never read, and the
   header row is `aria-hidden`. Invariant 6 does not exist for assistive tech.
   `screens/stock/StockList.tsx:85,122`.
3. **Two deep links land in the wrong place.** Ingredient › supplier link opens the *first*
   supplier (`?id=` written, `/id` read); Loyalty catalogue links to `/rewards?p=` and the
   redirect drops the query, so a second programme's catalogue cannot be opened.
   `screens/ingredients/Detail.tsx:231`, `screens/members/Catalogue.tsx:45,58,259`, `screens/loyalty/LoyaltyArea.tsx:62-68`.
4. **"Send to 143 people" is one click, no confirmation.** `ConfirmTwiceButton` guards it
   only when the monthly limit is hit. `screens/loyalty/messages/Composer.tsx:227-244`.
5. **Text faded by `opacity-55/60/70` fails AA in 13 places** (*measured*: 2.2–4.1:1),
   including the kit's own `FilterChip` counts and `GridCard` inactive state, so every list
   inherits it. `components/ui/chips.tsx:53`, `components/ui/card.tsx:63`, plus 11 screens.

**Recommended order:** run `/adapt` on Stock (item 1), `/harden` on Stock rows, the two
links and the composer (2–4), then `/normalize` on the opacity and colour habits (5 and
§4). Everything else is a sprint of consistency work, not redesign.

---

## 3. Detailed findings

Format: location · category · what and impact · WCAG · fix · command.

### 3.1 Critical

**C1 · `screens/stock/StockList.tsx:20-21` · Responsive**
`compact:grid-cols-[44px_minmax(0,1fr)_104px_112px_68px_122px_minmax(0,150px)_64px]` fixes
~514px of columns plus gaps and gives the name `minmax(0,1fr)`. With the 200px sidebar,
the name column is 0px at 903px, 6px at 1022px and 82px at 1100px (*measured*). The owner
on an iPad or a 13" laptop sees rows of figures with no ingredient. WCAG 1.4.10.
Fix: `minmax(160px,1fr)` and let the card `scroll-x`, or drop Use-by/Runs-out below `wide:`.
→ `/adapt`

**C2 · `screens/stock/StockList.tsx:85,116-124` · A11y**
The row is a `<button aria-label="{name}: open details">`; `aria-label` replaces the
accessible name, so Left (est.), Last count, Drift, Trust, Runs out and Use by are never
announced, and the header row is `aria-hidden="true"`. A screen-reader user gets a list of
names and nothing else. WCAG 4.1.2, 1.3.1. Fix: drop the `aria-label`, keep the figures as
content with sr-only prefixes ("Drift: "), or render the kit `<Table>` (as `BuyList` does).
→ `/harden`

**C3 · Broken deep links · Copy/functional**
- `screens/ingredients/Detail.tsx:231` writes `href('/suppliers', { id })` → `#/suppliers?id=7`;
  `screens/suppliers/SuppliersScreen.tsx:84` reads `Number(loc.segments[1])` and ignores
  `?id`, so "Where to buy" always opens the first supplier. Fix: `href(\`/suppliers/${id}\`)`.
- `screens/members/Catalogue.tsx:45,58,259` still navigate to `/rewards?p=<slug>`;
  `screens/loyalty/LoyaltyArea.tsx:62-68` redirects with `navigate(target, { replace: true })`
  and no `query`, so `?p=` is lost and the Programme segmented control snaps back to the
  default programme. Fix: link to `/loyalty/programme…` directly and pass `loc.query` through.
→ `/harden`

**C4 · `screens/loyalty/messages/Composer.tsx:227-244` · UX (outward, irreversible)**
"Send to N people" is a plain `Button onClick={submit}`; the two-tap guard is used only
when `limitReached`. A mis-tap pushes a lock-screen notification to every member. Fix:
always route "Send now" through `ConfirmTwiceButton` with `armedLabel="Tap again to send to N people"`.
→ `/harden`

### 3.2 High

**H1 · Counts faded with `opacity-60` (kit) · A11y · *measured***
`components/ui/chips.tsx:53` (`FilterChip` count), `components/ui/FilterBar.tsx:1929`
(`FilterToggle` count), `screens/menu/common/Rail.tsx:41`, `screens/menu/IngredientPicker.tsx:237`:
12px ink at 60% on white = 4.14:1; brand-ink at 60% on brand-wash (the active chip) =
2.6:1. Seen on Stock, Ingredients, Menu, Recipes, Bookings. WCAG 1.4.3. Fix: `text-ink-2`
(inactive) / `text-brand-ink` (active) at full opacity. → `/normalize`

**H2 · Whole row or card faded as the "off" state (13 sites) · A11y · *measured***
`components/ui/card.tsx:63` `GridCard inactive → opacity-55` (menu card text 2.17–3.57:1);
`screens/menu/MenuItemsScreen.tsx:562`; `screens/ingredients/IngredientsScreen.tsx:343` (retired);
`screens/orders/History.tsx:178` (cancelled, 2.6:1 sub-line); `screens/shop/OrdersScreen.tsx:271`;
`screens/shop/OptionsScreen.tsx:138`; `screens/shop/PromosScreen.tsx:135,268`;
`screens/shop/CategoriesDrawer.tsx:61`; `screens/shop/OnlineSection.tsx:304`;
`screens/loyalty/messages/CampaignCard.tsx:78,84,94`; `screens/recipes/Swaps.tsx:105`;
`screens/recipes/Editor.tsx:558`. WCAG 1.4.3. Fix: keep text at full contrast; say "off"
with the existing `StatusTag`/`Pill` and fade only the photo. → `/normalize`

**H3 · `screens/stock/StockList.tsx:159-170` · Responsive (hidden critical info)**
Trust and Use-by are `hidden compact:block`; the phone sub-line shows last count · drift ·
runs out only, so "Excluded" (auto-order forced off) and the use-by date never reach a
phone. Fix: fold the trust word and use-by into the phone sub-line. → `/adapt`

**H4 · Data tables built as div grids with visual-only headers (15+ sites) · A11y**
Stock list; `screens/ingredients/IngredientsScreen.tsx:34,323`; `screens/suppliers/Products.tsx:36,190-209`
(nine editable columns); `screens/stock/StockItemSections.tsx:257-285,305-329` (counts, batches);
`screens/ingredients/Detail.tsx:222` (offers); `screens/menu/MenuItemsScreen.tsx:531-598` (list view);
`screens/menu/ItemPage.tsx:315-384`; `screens/menu/ItemSales.tsx:88-123`; `screens/shop/OrdersScreen.tsx:225-233`;
`screens/shop/OptionsScreen.tsx:91-97`; `screens/shop/PromosScreen.tsx:124-130`;
`screens/shop/LiveOrdersScreen.tsx:379-405` (no header at all); `screens/agents/AgentsScreen.tsx:410-415`
(header `aria-hidden`); `screens/orders/ShopRuns.tsx:68-90`; `screens/members/Staff.tsx:79-145`
(invalid ARIA table, 4 cells vs 5 headers); `screens/recipes/Editor.tsx:768-800`
(`role="table"` with bare spans); `screens/shop/OrderPage.tsx:188-227,308-322` (header
`display:none` on phone). Screen readers hear "£3.20 45% £1.20" with no column names.
WCAG 1.3.1. Fix: the kit `<Table>` (caption, scope, scroll-x already handled) or sr-only
per-cell labels that are *not* `sm:hidden`. → `/harden`

**H5 · Charts: every mark is a tab stop, or none is reachable · A11y**
`screens/money/TillInsights.tsx:225-235` heatmap: ~100 `span[role=cell][tabindex=0]`;
`screens/money/SalesDashboard.tsx:527-546` up to 45 focusable `<rect>`;
`screens/money/ProfitSection.tsx:121-129,172-179` `div tabIndex=0 aria-label` with no role
(name not exposed); `screens/loyalty/insights/charts.tsx:87-94` ~63 stops per page;
`screens/money/OverviewScreen.tsx:303-327` values only in `<title>` on hover. WCAG 2.4.3,
4.1.2, 1.1.1. Fix: one shared pattern, roving tabindex plus an sr-only `<table>` (the
loyalty charts already ship the table at `charts.tsx:123-132`). → `/harden`

**H6 · `screens/menu/IngredientPicker.tsx:71-79,115-131,225-239` · A11y**
The popover's wrapper `onKeyDown` swallows Enter, so pressing Enter on a category chip
picks the highlighted ingredient instead; and it closes on outside `mousedown` only, so a
keyboard user who tabs out leaves a `z-40` listbox open. WCAG 2.1.1, 2.4.7. Fix: handle
keys only when `e.target` is the search input; close on `onBlur` outside root. → `/harden`

**H7 · `screens/website/bookings-drawers.tsx:237-243` · A11y/UX**
"Mark as" is a `Segmented` whose `onChange` PATCHes the booking; the kit moves selection
on every arrow key, so exploring Booked → Arrived → No-show writes three times, and this
path has no Undo. WCAG 3.2.2, 3.3.4. Fix: commit on Enter/Space, or plain buttons with the
same `undo` the row buttons pass. → `/harden`

**H8 · `screens/website/CafeDetailsScreen.tsx:285,288` · Responsive**
Latitude/longitude use `<Input numeric>` → `inputMode="decimal"`; the iOS decimal keypad
has no minus, and Dundee's longitude is negative. Fix: `inputMode="text"` with a pattern.
→ `/harden`

**H9 · Unlabelled inputs on write paths · A11y**
`screens/shop/LiveOrdersScreen.tsx:348` reject reason (placeholder only);
`screens/shop/OrderPage.tsx:441` staff note. WCAG 3.3.2, 4.1.2. Fix: `<Field label>`.
→ `/harden`

**H10 · `screens/shop/SettingsScreen.tsx:32,54` · UX (data loss)**
`<div key={updated_at}>` wraps all nine panels and every Save invalidates `SHOP_KEY`, so
saving *Dining* remounts *Hours* and discards half-typed edits there. Fix: key panels on
their own fields or lift drafts. → `/harden`

**H11 · `screens/shop/PromosScreen.tsx:216-231` · UX**
After "Create banner" the drawer stays keyed to `'new'`: the photo slot never appears and a
second Save creates a duplicate banner. Fix: `after: (b) => setOpen({ kind: 'banner', id: b.id })`.
→ `/harden`

**H12 · Primary phone actions at 22–32px · Responsive**
`components/ui/toggle.tsx:118` `Stepper` sm = 22px round buttons, used for packs on
`screens/orders/OrderPage.tsx:143-149`; `screens/orders/OrderBits.tsx:241-263` private
`ArmChip` "Cancel order" 28px, 12px text, no `aria-live` (the kit's `ConfirmTwiceButton`
has it); `screens/orders/OrderBits.tsx:300-326` receive sheet: 28px `xs` inputs in a
three-column grid at 375px; `screens/shop/LiveOrdersScreen.tsx:332-361` Accept 40px,
Reject/Cancel 32px, "Open" a 13px text link; `screens/suppliers/Products.tsx:42` ★/× 28px;
`screens/recipes/Editor.tsx:571` `size-8` override; `screens/website/TodayScreen.tsx:295`,
`screens/website/MessagesScreen.tsx:214` Undo link 24px; recipes editor "Mark as timed"
129×16, "Charge is a guess" 311×20, hourly-rate input 52×22 (*measured*). WCAG 2.5.8.
Fix: `size="lg"` on the board and order page; `IconButton` for glyphs; `min-h-11` on
phone-only links. → `/adapt`

**H13 · Focus dropped after remove/close (6 sites) · A11y**
`screens/menu/ItemPage.tsx:716`; `screens/recipes/Editor.tsx:460,571`;
`screens/stock/CountFlow.tsx:61-72` (queue ends → body); `screens/loyalty/members/PhysicalCard.tsx:80-84`
(sticker picker); `screens/website/CafeDetailsScreen.tsx:673` (TOC scrolls, focus stays).
WCAG 2.4.3. Fix: move focus to the neighbour control or the add button, as
`screens/website/MessagesScreen.tsx:121-129` already does. → `/harden`

**H14 · Live regions mounted already containing their text (11 sites) · A11y**
`screens/suppliers/SuppliersScreen.tsx:133-142`; `screens/stock/writes.tsx:50-59`;
`screens/ingredients/Detail.tsx:421`; `screens/ingredients/PhotoAllergens.tsx:142,277`;
`screens/loyalty/messages/Composer.tsx:246`; `screens/loyalty/messages/CampaignCard.tsx:95`;
`screens/loyalty/programme/StickerSet.tsx:170`; `screens/loyalty/MemberCardPage.tsx:469`;
`screens/members/shared.tsx:217`; `screens/menu/MenuItemsScreen.tsx:635`;
`screens/menu/Photo.tsx:124` (replace gives no feedback at all). A `role="status"` that
appears with content is not reliably announced. WCAG 4.1.3. Fix: always-mounted
`<p role="status">`, swap the text (`screens/loyalty/programme/JoiningLink.tsx:110-113`
does it right). → `/harden`

**H15 · Validation not tied to its field (8 sites) · A11y**
`screens/shop/SettingsScreen.tsx:168,240,332`; `screens/shop/OptionsScreen.tsx:370-374`;
`screens/money/ExpensesScreen.tsx:513-516,615-617,659-663`; `screens/stock/StockItemSections.tsx:642-668`;
`screens/loyalty/ProgrammeScreen.tsx:134-143`; `screens/loyalty/members/AddMember.tsx:96-115`;
`screens/loyalty/MemberCardPage.tsx:423-435`. Loose `<p class="text-bad-ink">` with no
`aria-describedby`/`aria-invalid`, sometimes off-screen. WCAG 3.3.1. Fix: `<Field error>`
(the kit wires both). → `/harden`

**H16 · Reasons that live only in `title` (9 sites) · A11y**
`screens/stock/NeedsAttention.tsx:217` ("no forecast yet", invariant 9 reason in tooltip);
`screens/stock/StockItemSections.tsx:107,111,537` (tier-A lock);
`screens/stock/StockList.tsx:144,163`; `screens/recipes/RecipesScreen.tsx:108-116`
(disabled "+ New recipe" explains itself only in `title`); `screens/recipes/Editor.tsx:605-609`
(per-size flavour values only in `title`), `:175-182`; `screens/shop/LiveOrdersScreen.tsx:320-326`;
`screens/orders/OrderBits.tsx:33-39` ("No receipt yet"). Touch and screen readers get
nothing. WCAG 1.4.13, 4.1.2. Fix: print the reason in the cell or as sr-only text. → `/harden`

**H17 · DOM order puts the side column first · A11y/Responsive · *measured***
`screens/menu/ItemPage.tsx:96-107` and `screens/stock/StockItemPage.tsx:56-57` render the
`<aside>` first with `xl:order-last`; below 1280px a phone user scrolls past Delivery /
Write off / Take off the menu before the figures, and the outline runs h1 → h3 ×4 → h2.
`screens/members/Catalogue.tsx:36-40` adds a second `<h1>`/`<header>` under the Loyalty
header. WCAG 1.3.2, 2.4.6. Fix: main column first in JSX, `order-first` on the aside;
side headings `h2`. → `/harden`

**H18 · Fake tab and radio groups · A11y**
`screens/stock/StockScreen.tsx:213-238` `role="tablist"` with no `aria-controls`, roving
`tabIndex` or arrow keys; `screens/loyalty/programme/JoiningLink.tsx:68-96` `role="radio"`
buttons, every one a Tab stop, arrows do nothing. WCAG 4.1.2. Fix: kit `Segmented`, or
plain links with `aria-current` as `screens/loyalty/LoyaltyHeader.tsx:45-71` does. → `/harden`

**H19 · `screens/shop/alert.ts:213-230` + `LiveOrdersScreen.tsx:264` · A11y**
Snooze silences the chime but the title keeps flashing every 1.2s until `newCount` is 0;
the only visual equivalent of the chime is a 60-second blue edge. WCAG 2.2.2. Fix: honour
snooze for the flash; a persistent `role="status"` strip ("2 new orders since 14:03").
→ `/harden`

### 3.3 Medium

**Colour and theming**
- Red for things that are not a crossed threshold: any negative delta
  (`screens/money/TillInsights.tsx:118-127`, `screens/shop/InsightsScreen.tsx:43`,
  `screens/loyalty/InsightsScreen.tsx:125`); preferred-supplier star `text-alert`
  (`screens/ingredients/Detail.tsx:225`, `screens/suppliers/Products.tsx:324`); "estimate"
  toggle in `bad-ink` (`screens/recipes/Editor.tsx:175-182`); avatar tints cycling through
  `bad-wash`/`warn-wash` (`screens/loyalty/members/bits.tsx:41-47`). → `/normalize`
- Green as text on white (8): `screens/money/ExpensesScreen.tsx:390`,
  `screens/website/CafeDetailsScreen.tsx:138-140`, `screens/loyalty/MembersScreen.tsx:203`,
  `screens/loyalty/MemberCardPage.tsx:179,525`, `screens/loyalty/messages/Composer.tsx:247`,
  `screens/loyalty/messages/CampaignCard.tsx:36,96`, `screens/loyalty/InsightsScreen.tsx:125`. → `/normalize`
- KPI tile grids: `screens/loyalty/InsightsScreen.tsx:116-201` (six bordered `KpiTile`s,
  orphan at `wide`); `screens/website/TodayScreen.tsx:229,243,403-440`;
  `screens/menu/ItemSales.tsx:144-150` (local `Fig` re-implements `Tile`). Fix: the Sales
  `Figures` `<dl>` strip (`screens/money/filters.tsx:265-286`). → `/normalize`
- Hex and off-scale values: `screens/loyalty/programme/StickerSet.tsx:150` `hover:bg-[#e3e9fb]`;
  pass palette hand-coded in two encodings across `screens/loyalty/programme/CardPreview.tsx:9-10,70,77`
  and `screens/loyalty/members/PhysicalCard.tsx:34,72,91,135` (empty-slot numbers 2.9:1 and
  3.6:1); `text-[10px]/[11px]`, `rounded-[6/8/9/18/20px]`, `px-[18px]`, `py-[18px]`,
  `shadow-[…]` in ~15 places (`screens/stock/CountFlow.tsx:186,192`,
  `screens/ingredients/IngredientsScreen.tsx:195`, `screens/ingredients/Detail.tsx:113,691`,
  `screens/menu/MenuItemsScreen.tsx:260`, `screens/recipes/RecipesScreen.tsx:111`, loyalty ×8).
  Fix: `--color-pass-blush`/`--color-pass-ink` tokens; scale values. → `/normalize`
- Glass: `screens/stock/NeedsAttention.tsx:76` `backdrop-blur`, `screens/money/SalesScreen.tsx:344`
  `backdrop-blur-[2px]`. Nested cards: `screens/stock/StockItemSections.tsx:216,672`
  (bordered boxes inside `Panel`). → `/normalize`
- Layout-property animation and a duplicate primitive: `screens/loyalty/programme/Switch.tsx:52`
  `transition-[left]` (kit `Toggle` uses `translate-x`). → `/optimize`
- Low-contrast chart marks: heatmap bin 1 vs empty cell 1.2:1
  (`screens/money/TillInsights.tsx:186,234`); `bg-seq-2` bars 2.1:1
  (`screens/loyalty/insights/charts.tsx:28,100`); donut slice `brand-line` 1.35:1
  (`screens/menu/CostDonut.tsx:18-25,90,126`); `button role="rowheader"` hides the drill
  affordance (`screens/money/TillInsights.tsx:211-219`); "Sold out" white on 24% scrim over a
  photo (`screens/shop/OnlineSection.tsx:308`). WCAG 1.4.11. → `/normalize`
- `ink-3` on wash/canvas is 4.36–4.56:1 (*measured*: Ingredients group letters
  `text-lg font-extrabold text-ink-3` on wash 4.36:1; `screens/loyalty/programme/StickerSet.tsx:161`
  on brand-wash 4.4:1). Passes on white (4.9:1). Fix: `ink-2` on any wash. → `/normalize`

**Responsive**
- Two stacked banners take ~170px above every page header at 375px (*measured*); the
  Lightspeed one ships live. `components/shell/Shell.tsx:162-227`, `components/ui/banner.tsx`.
  Fix: one-line collapsed banner on phone, or dismiss remembered per install. → `/adapt`
- Recipes editor on phone: the only Apply/Discard is in the cost column that stacks under
  History at the bottom (`screens/recipes/Editor.tsx:126,354,750`); `ItemPage.tsx:767` has
  the sticky-bar pattern. → `/adapt`
- Ingredients list name column 51px at 903px (*measured*, `screens/ingredients/IngredientsScreen.tsx:34`);
  offer rows with three `fr` columns and no breakpoint (`screens/ingredients/Detail.tsx:222`). → `/adapt`
- `screens/loyalty/programme/StickerSet.tsx:152` `touch-none` on enabled tiles blocks page
  scroll over most of a phone screen. → `/adapt`
- Breakpoints differ per screen: `lg:` in `screens/website/TodayScreen.tsx:160` and
  `screens/money/ProfitSection.tsx:82`; loyalty tabs use `compact:`, `md:` and `min-[820px]:`. → `/normalize`

**Performance**
- `lib/shop-api.ts:105-171` all three shop reads set `refetchIntervalInBackground: true`;
  the order page polls every 15s while hidden and nothing there rings. → `/optimize`
- Same query, different options: `['website','settings']` declared three times
  (`screens/website/bookings-parts.tsx:113`, `details-lib.tsx:13`, `TodayScreen.tsx:131`);
  media under two keys (`events-page.tsx:244` vs `photos/model.ts:152`), so an upload never
  reaches the events copy. Loyalty badge fires an extra `/api/members?limit=1` on every tab
  and a stamp triggers three refetches (`screens/loyalty/LoyaltyHeader.tsx:30`,
  `screens/loyalty/MemberCardPage.tsx:55-56`). → `/optimize`
- Dead code: `screens/members/` has seven unreachable files, 2,558 lines, still
  type-checked (`Alerts, Campaigns, Insights, MemberPage, MembersArea, MembersList, Programme`);
  `lib/members-api.ts` dead hooks and methods; `screens/money/shared.tsx:172-473` 260 lines
  of inline-edit cells with no callers; `lib/finance-api.ts:81,162-163,253-262` director
  API for a removed screen; `screens/agents/AgentsScreen.tsx:54,63,238-241` unreachable
  operator branches. Duplicates: `Panel` ×2, `sourceLabel` ×2 (disagree on null),
  `MONTHS` ×3, date helpers ×5 in website with two weekday conventions, `ago()`
  re-implemented (`screens/website/MessagesScreen.tsx:48-67`). → `/simplify`
- `screens/money/SalesDashboard.tsx:412,753,757` float averages then `Math.round` where
  `avgTicket` two screens away uses the integer idiom. → `/optimize`

**Semantics and interaction**
- Whole-panel `aria-live` re-announces buttons on every preview
  (`screens/menu/common/Impact.tsx:103`, `screens/recipes/ProposalView.tsx:105`,
  `screens/recipes/Swaps.tsx:169`); `useArrivals` announces the operator's own moves
  (`screens/shop/LiveOrdersScreen.tsx:47-62`). → `/harden`
- Generic repeated names per row: "Remove line"/"Quantity"/"Unit" ×6
  (`screens/menu/ItemPage.tsx:716-726`), "Ingredient" for every per-size select
  (`screens/recipes/Editor.tsx:436-441,473,517`). → `/harden`
- Switch labels flip with state ("Sold out today, switch, off"):
  `screens/menu/ItemPage.tsx:203`, `screens/shop/OnlineSection.tsx:159-160`. → `/clarify`
- Inherited option chips are `aria-pressed` and inert (`screens/shop/OnlineSection.tsx:206-214`);
  duplicate ids from several `Input`s in one `Field` (`:175-186,260-265`). → `/harden`
- Native 113-option ingredient `<select>` survives in the recipe editor after the owner
  rejected it (`screens/recipes/Editor.tsx:501-530,575-587`, `screens/recipes/Swaps.tsx:129-142`);
  `IngredientPicker` already takes `options`. → `/harden`
- `screens/loyalty/MemberCardPage.tsx:203` renders a null as `'0.0'` (invariant 8);
  `screens/ingredients/Detail.tsx:372-377` uses a disabled checkbox as a status. → `/harden`
- Drafts top-up candidates styled exactly like the `add` button but static
  (`screens/orders/Drafts.tsx:169-178`). → `/clarify`
- Section jump links carry no `aria-current` (`screens/money/SalesScreen.tsx:345-349`). → `/harden`

**Copy**
- Agents still says "waiting in Telegram" and "Order confirmations happen in Telegram"
  (`screens/agents/AgentsScreen.tsx:167,271`), contradicting DECISIONS 18/28.
- `screens/shop/OptionsScreen.tsx:84` points at the retired "Shop menu › product".
- The Sales empty state tells the owner to run `cafeops import-finance --commit`
  (`screens/money/SalesScreen.tsx:374-378`); `screens/website/shared.tsx:18-38` shows env
  var names; `screens/shop/SettingsScreen.tsx:312,410` likewise.
- Backend words on screen: `COFFEE / SUNDRY` role enums (`screens/recipes/Editor.tsx:426-430`),
  raw `CASH`/`WEB` channels (`screens/menu/ItemSales.tsx:19-24`), "withheld"
  (`screens/stock/BuyList.tsx:388`), preset slugs `counter-qr`
  (`screens/loyalty/programme/JoiningLink.tsx:92`), Pydantic 422 text verbatim
  (`screens/website/details-lib.tsx:44-45`), "click to arm" (`screens/shop/SoundToggle.tsx:50`).
- A button says **Delete** and archives (`screens/suppliers/SupplierPage.tsx:133-140`);
  "+ New recipe" opens a proposal (`screens/recipes/RecipesScreen.tsx:108-116`);
  `ErrorBox` doubles the sentence ("Couldn't load Today's bookings didn't load.",
  `screens/website/TodayScreen.tsx:141`, `MessagesScreen.tsx:134`); "No ingredient 4 in
  stock." leaks an id (`screens/stock/StockItemPage.tsx:53`).
- Website: the joining-link panel says "put it on the counter" but offers no QR to print
  (`screens/loyalty/programme/JoiningLink.tsx`). → `/clarify`

### 3.4 Low

- Sticky header that cannot stick: `<Table stickyHeader>` sits inside `ScrollX`
  (`screens/stock/BuyList.tsx:296`).
- `scrollIntoView({behavior:'smooth'})` ignores reduced motion (`screens/stock/NeedsAttention.tsx:85`).
- Glyph icons (`★ ↺ ± × ♥ ✎`) in coloured circles (`screens/loyalty/MemberCardPage.tsx:229-239`);
  `font-mono` URL (`screens/loyalty/programme/JoiningLink.tsx:99`).
- New-tab links with no cue: `screens/orders/OrderBits.tsx:226`, `screens/shop/OnlineSection.tsx:325-333`.
- `<span role="navigation">` inside the header `<p>` (`screens/menu/MenuTabs.tsx:19`,
  `screens/website/PhotosScreen.tsx:43`); `<div>` inside `<button>` (`screens/agents/AgentsScreen.tsx:342-349`).
- Empty states with no way out (`screens/stock/StockScreen.tsx:385`, `screens/stock/BuyList.tsx:294`);
  "Nothing goes out of date" is a button that filters to nothing (`StockScreen.tsx:403-406`);
  permanent "Saved" on a read-only list (`screens/ingredients/IngredientsScreen.tsx:193`).
- Website hand-rolls link-buttons ×6, status lines ×6, empty states ×7, its own toolbar,
  and `h2` at four sizes; Events files use another formatter.
- `UndoBar` gives 5s (`screens/money/shared.tsx:434-438`); `ShopRuns` slices to 40 with no
  "and more" (`screens/orders/ShopRuns.tsx:75`); hour axis with no am/pm
  (`screens/loyalty/InsightsScreen.tsx:290-305`); hard-coded `href="#/suppliers"`
  (`screens/orders/Drafts.tsx:58`); `LoyaltyHeader` tab strip clips its focus ring
  (`screens/loyalty/LoyaltyHeader.tsx:45`); Setup "Copied" not announced
  (`screens/setup/SetupScreen.tsx:22-36`); the `AddMember` form has no submit inside it
  (`screens/loyalty/members/AddMember.tsx:76-85`); description autosaves mid-sentence
  (`screens/website/photos/LibraryTab.tsx:204`).
- Print stylesheet uses `#fff/#000/#999` (`screens/website/BookingsScreen.tsx:418-436`):
  acceptable, but the one place the "no hex in a screen" rule is broken on purpose.

**A decision, not a defect:** every order is stamped "Confirmed by Back office" because
`OperatorControl` is a `return null` stub by owner instruction (`components/shell/Operator.tsx:15`,
`lib/operator.ts:27`, `screens/orders/OrderPage.tsx:179-193`). Invariant 1's audit trail
is a constant. Either accept it and drop "by {operator}" from the success copy, or add one
remembered "Your name" field beside Confirm. Raise with the owner.

---

## 4. Patterns and systemic issues

| Pattern | Sites | Where it starts |
|---|---|---|
| Opacity as the "off" state, pushing text under AA | 13 + the kit | `card.tsx:63` `GridCard`, `chips.tsx:53` count |
| Data tables as div grids with visual-only headers | 15+ | every list except `BuyList`, `ProposalView`, `MembersScreen` |
| Hover-first charts with per-mark tab stops or none | 6 charts | `TillInsights`, `SalesDashboard`, `ProfitSection`, `OverviewScreen`, loyalty charts |
| Live region mounted with its text | 11 | one correct model: `JoiningLink.tsx:110` |
| Validation as a loose red paragraph | 8 | one correct model: `shop/SettingsScreen.tsx:129` (Dining) |
| Reason only in `title` | 9 | Stock, Recipes, Live orders, Orders |
| Focus dropped on remove | 6 | one correct model: `MessagesScreen.tsx:121` |
| Red/green as mood, not threshold | ~15 | dashboards, avatars, stars, "saved" |
| Private re-implementation of a kit primitive | 8 | `ArmChip`, `Switch`, `Fig`, `Panel`×2, link-buttons ×6, status lines ×6, `CountBadge` |
| Fixed-pixel column grids only checked at 375 and 1440 | 3 | Stock, Ingredients, Detail offers |
| Backend vocabulary on screen | 9 | enums, slugs, CLI, env vars, 422 text |
| Generation seam: v1 `screens/members` beside v2 `screens/loyalty` | 2.6k dead lines, 3 live files | delete seven files, move three |

---

## 5. Positive findings (keep, and copy elsewhere)

- **Tokens and primitives are real.** Zero palette classes, zero hex outside the pass
  card and a print sheet; every chart series is a `--color-chart-*` token; five tokens were
  darkened for AA on purpose and documented (DECISIONS 23). `styles.css` is the source of truth.
- **The kit gets semantics right by default:** `Button` is always a `<button>`;
  `Field` wires label, hint, error, `aria-describedby`, `aria-invalid`; `Table` has a
  caption and `scope`; `Tr` is keyboard-operable; `Drawer` is a real dialog on phone with
  focus trap, Esc and focus return (*verified live*); `Segmented` roves focus; `IconButton`
  requires a label. *Measured*: no unnamed control, no unlabelled input, no image without
  alt, no `th` without scope on any fixture-backed route at either width.
- **Money and quantities never touch a float** except three display averages
  (`figures.ts`, `model.ts`, `lib/dec`, `BigInt` in `ShopRuns`).
- **The invariants are visible:** estimates italic and counts upright everywhere
  (`StockItemSections.tsx:93-102`); a withheld forecast shows its reason *in place of* the
  number (`BuyList.tsx:373-378`); missing months are "not uploaded, not zero"
  (`SalesDashboard.tsx:224,701`); the two-ledger rule is stated in the module doc and on
  screen (`SalesScreen.tsx:231-238`).
- **Exemplary components to replicate:** `photos/Img.tsx` (alt, lazy, width/height,
  srcset, blur); `photos/SlotCard.tsx` (three keyboard routes to reorder, focus kept);
  `photos/FocalEditor.tsx` (pointer + keyboard + live description); `CostDonut.tsx`
  (role=img with a full text breakdown and unpriced lines listed); `loyalty/insights/charts.tsx:123-132`
  (sr-only table per chart); `IngredientPicker.tsx` (real combobox, `aria-activedescendant`);
  `TillInsights.tsx:150-166` `DrillRow` (every chart row a real `<button aria-pressed>` that
  sets a removable filter chip); `sitemenu-rows.tsx` `MoveButtons` (44px ↑/↓ with focus
  restored); `MessagesScreen.tsx:121-129` (focus never lost on remove);
  `LiveOrdersScreen.tsx:189-191` (per-column live region so the chime has a spoken twin);
  `CountFlow.tsx` (always-mounted live region, Enter-to-save, rationale on the page).
- **Heading structure** is exemplary on Sales, Agents, Overview, Setup (*measured*: one
  `h1`, ordered `h2`/`h3`); landmarks `nav#Main`, `main`, `header` on every page; the
  document title follows the route.
- **Zero horizontal overflow** on all 22 routes at 373px and 1920px (*measured*); every
  wide table scrolls inside itself; the sticky section nav on Sales works on phone.
- **Copy has a voice**, and destructive actions are two-tap with no modal anywhere.
- **Phone layouts that lose nothing:** the order page collapses columns into an inline
  sentence (`OrderPage.tsx:135-139`); Members becomes a real `<ul>` under 900px.

---

## 6. Recommendations by priority

**Immediate (this week, blockers)**
1. Stock list columns between 900 and 1180px (C1) and the phone-hidden Trust/Use-by (H3).
2. Stock row accessible name and header (C2).
3. The two dead deep links (C3); the composer's two-tap send (C4).
4. Shop settings data loss on save (H10); banner create loop (H11).

**Short-term (this sprint, WCAG AA)**
5. Fix `opacity-*` as an "off" state at the kit level, then the 11 screens (H1, H2).
6. Label the two shop inputs (H9); tie the eight validation messages to fields (H15).
7. Bookings status writes on arrow keys (H7); lat/long keypad (H8); picker keyboard (H6).
8. Lift primary phone actions to 44–48px on the board and order page (H12).
9. Live-region mounting (H14), focus return (H13), title-only reasons (H16).

**Medium-term (next sprint, quality)**
10. One table pattern: migrate the div grids to `<Table>` (H4).
11. One chart pattern: roving tabindex + sr-only table (H5); fix the three low-contrast marks.
12. Colour law: remove red-for-dips, green-as-text, semantic avatar tints; convert the
    three KPI tiles to the `Figures` strip; tokenise the pass palette.
13. Copy pass: Telegram wording, enums, CLI/env text, Delete→Archive, raw slugs.
14. DOM order on the two detail pages (H17); fake tablists (H18); chime visual twin (H19).

**Long-term (housekeeping)**
15. Delete the seven dead `screens/members` files and the dead money/finance cells;
    de-duplicate `Panel`, `sourceLabel`, `MONTHS`, date helpers, `Switch`.
16. Add `LinkButton`, `Outcome`/status-line and `ChartTable` primitives so the website and
    loyalty groups stop re-inventing them.
17. Polling: background refetch only where something rings; one `useSiteSettings`, one `useMedia`.
18. Decide the "Confirmed by Back office" question with the owner.

---

## 7. Suggested commands

- `/impeccable:adapt` — C1, H3, H12, banners on phone, recipes-editor apply bar,
  Ingredients/Detail column grids, `touch-none` (≈12 findings).
- `/impeccable:harden` — C2, C3, C4, H4–H11, H13–H19, validation, live regions, focus,
  title-only reasons, fake tablists, duplicate ids, null-as-zero (≈35 findings).
- `/impeccable:normalize` — H1, H2, every colour-law breach, KPI tiles, hex and off-scale
  values, glass strips, nested cards, breakpoint drift, website rhythm (≈25 findings).
- `/impeccable:clarify` — Telegram wording, backend vocabulary, Delete/Archive, flipping
  switch labels, static chips styled as buttons, doubled error sentences (≈14 findings).
- `/impeccable:optimize` — background polling, duplicate queries, `transition-[left]`,
  float averages, loyalty triple refetch (≈6 findings).
- `/simplify` — dead `screens/members` files, dead money cells and director API,
  duplicated helpers and primitives (≈8 findings).

Not audited live: Shop, Loyalty and Website (no fixtures). Record fixtures for them with
`cafeops api-fixtures` or run the pass against the live instance before trusting the phone
layouts there.

---

## 8. Fix status (2026-09-29, same day)

Fixed by seven area agents plus kit changes. `tsc --noEmit` and `vite build` pass for the
whole app. Re-measured live in fixture mode (Stock, Menu, Recipes, Ingredients,
Suppliers, Money, Orders, Agents, Settings, Setup at 375 and 1440px): no horizontal
overflow, no unlabelled inputs, no unnamed controls, no text contrast failures, no
console errors. Stock name column: 186px at 903px, 305px at 1022px (was 0 and 6).
Shop, Loyalty and Website have no fixtures: type-checked and built, not measured live.

**Kit additions:** `LinkButton`, `StatusLine`/`Outcome` (always-mounted live region),
`ChartTable`, `Figures` (moved from money/filters, re-exported there), `Toggle tone`,
`--color-pass-blush`/`--color-pass-ink`. Kit changes: chip counts at full opacity,
`GridCard inactive` fades only the image, `Stepper sm` 28px, compact phone banners,
`Tr` uses `aria-current`, `PageHeader` subtitle/saved are `<div>`s, plain `IconButton` ink-2.

**Deliberately not done:**
- Shop lists (Orders, Options, Promos, Live "Later") keep clickable rows with sr-only cell
  labels rather than `<Table>`, so the whole row stays a link on phone.
- Order cap reasons ("capped at 4 days: shelf life", "under minimum") stay red: a cap is a
  crossed threshold (invariant 4).
- Loyalty member-history glyphs stay (aria-hidden); no icon art exists to replace them.
- Setup still shows CLI commands where no web path exists, by design.
- Loyalty Insights keeps a local figures strip (it carries deltas the kit one does not).

**Still open (owner decision):** order confirmations record the constant operator
"Back office". The success copy no longer claims a person; the audit trail still does.
