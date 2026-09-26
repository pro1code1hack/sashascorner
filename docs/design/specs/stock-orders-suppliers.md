# Spec: Stock, Orders, Suppliers (Menu Stock App v2)

Source of truth for the look: `docs/design/Menu Stock App v2.dc.html` (markup lines 184–286
and 393–506; logic lines 512–620, 744–902, 1014–1105) embedded by
`docs/design/Cafe Ops v2.dc.html`. Demo data: `sc-data.js` (`SC_DATA`), `sc-comp.js`
(`SC_COMP.stock`, `.seasons`, `.rate`). `docs/design/screenshots/v2.jpg` is an **older**
iteration (search box + chip layout differ); where it disagrees with the markup, the markup
wins.

Everything in the design runs on `localStorage` with a toy stock model (a constant daily
`rate`, counts carrying their own `expected`). **None of that arithmetic is to be ported.**
Every number comes from the API; section 4 says where. Section 5 lists the places where
the design's behaviour contradicts an invariant. Build to the resolution given there, not
to the prototype.

Line refs `L123` are to `Menu Stock App v2.dc.html` unless another file is named.

---

## 0. Shared shell, tokens and helpers

### 0.1 Frame (embedded in Cafe Ops v2)

- **Left nav:** `Cafe Ops v2`, width **232px** desktop / **200px** compact, bg `#f6f7fa`,
  border-right `1px solid #eef0f4`. The *Every day* group is `Stock`, `Orders` (with a
  badge), `Agents`; the *Menu* group holds `Suppliers`.
  - The Orders badge counts orders in status `Waiting`: `font:700 11px`, white on `#4a6fd1`,
    radius 999, padding `2px 7px`.
  - Active nav item: bg `#fff`, fg `#3558b8`, weight 700, shadow
    `0 1px 2px rgba(31,38,51,.06),0 0 0 1px rgba(31,38,51,.04)`.
- **Banners above the page** (shell-level): stale sync and cash. They are not part of this
  spec, but the stale banner's copy says "Stock estimates … are behind until the next sync",
  so Stock must tolerate them pushing the page down.
- **Page header** (L12–18). Row padding `12px 20px`, border-bottom `1px solid #e8ebf0`,
  gap 18.
  - Title: `font:800 21px Nunito; letter-spacing:-.01em`. Subtitle: 14px `#5b6475`.
  - Spacer, then `savedLabel` (14px `#8a93a3`, text `Saved`).
  - Then an optional primary button: bg `#4a6fd1`, white, radius 18, padding `9px 18px`,
    16px 700.
  - Titles and subtitles verbatim (`PAGE`, L545):
    - Stock: `Stock` / `what is on the shelf: worked out from sales, checked by counting`
    - Orders: `Orders` / `one basket per supplier, confirmed in Telegram`
    - Suppliers: `Suppliers` / `who we buy from, and on what terms`
  - Add button: Stock none; Orders none; Suppliers `+ Add supplier`.
- **Rail** (L20–38). `showRail` is true for Stock and Suppliers and false for Orders.
  - Desktop: a left column **210px**, border-right `1px solid #eef0f4`, padding
    `10px 10px 16px`, gap 2.
    - Head row: `font:700 11px; color:#8a93a3; uppercase; letter-spacing:.08em;
      padding:14px 10px 6px`.
    - Item row: min-height 36, padding `0 10px`, radius 10, 14px. Label ellipsised; count
      12px, opacity .6.
  - Compact (`compact=true`): the rail becomes a horizontal chip strip under the header,
    `padding:12px 20px; gap:8px; overflow-x:auto; border-bottom:1px solid #eef0f4`.
    - Chip: height 36, radius 999, padding `0 14px`, 14px, border `1px solid {bd}`.
    - Head rows are dropped from the strip (`railChips = rail.filter(isRow)`).
- **Selection helper `on(active)`** (L520):
  - active: bg `#edf1fc`, fg `#3558b8`, border `#c9d5f5`, weight 700;
  - inactive: bg `transparent`, fg `#1f2633`, border `#e2e6ed`, weight 500.
  - Every chip, segment, rail row and day toggle below uses this helper unless stated.
- **Global type:**
  - Nunito 400–800 (Google Fonts), `font-variant-numeric: tabular-nums`, antialiased.
  - Text `#1f2633`; secondary `#5b6475`; tertiary `#8a93a3`.
  - Danger `#d4554a`, or `#c2453b` on tinted fills.
  - Hairlines `#e8ebf0` / `#eef0f4` / `#f1f3f6`; inputs `#d5dae3` / `#e2e6ed`.
  - Primary `#4a6fd1`. Hover transition `background .15s, border-color .15s`.

### 0.2 Viewports

- **Desktop 1440×900:** content width = 1440 − 232 = **1208px**.
  - Stock and Suppliers subtract the 210px rail + 1px border, leaving **997px**.
  - The Stock detail drawer takes another 400px, leaving a **~596px** table.
- **Compact (iPad) 1180px:** content = 1180 − 200 = **980px**. There is no side rail (chip
  strip instead), so the Stock table is 980px, or **~579px** with the drawer open.
- No other compact-specific rule exists for these three tabs. The widths that change are
  the nav, rail→chips and the Orders basket grid (`minmax(520px,1fr)`: 2 columns desktop,
  1 column compact). The Stock drawer is a fixed **400px** in both modes; unlike Menu's
  `drawerW` it is not compact-aware.

### 0.3 Formatters in the design (to be reproduced with exact decimals)

- **`gbp(p)`:** `'£' + (p/100).toFixed(2)`. Negative is `'−£'` (U+2212).
- **`unitPrice(p)`:** pounds shown to 2 dp when ≥ £1, 3 dp when ≥ £0.10, else 4 dp. For
  example `£0.0734`.
- **`fmtQ(n,u)`:**
  - Returns `—` when there is no value.
  - Shows an integer with en-GB grouping in any of these cases:
    - the unit is EACH (`unit`);
    - the unit is ml/g and |n| ≥ 10;
    - n is an integer and |n| ≥ 10.
  - Otherwise 1 dp when |n| ≥ 10, else 2 dp.
  - Suffix: `' L'` for L, none for EACH, else `' ml'`, `' g'` or `' kg'` (lowercase).
- **`fmtD(iso)`:** en-GB `{weekday:'short', day:'numeric', month:'short'}`, which gives
  `Fri 26 Sept` (Chrome's en-GB writes "Sept"). Many cells strip the weekday with
  `.replace(/^\w+ /,'')`, giving `26 Sept`.
- **Drift % display:** one decimal, `Math.round(x*1000)/10`.

---

## 1. STOCK tab

### 1.1 Layout (L393–506)

The column runs top to bottom: toolbar, info strip, then either the count flow or the listing
(which has an optional drawer on its right).

**Toolbar** (L395–404): `padding:14px 20px; gap:12px; border-bottom:1px solid #eef0f4`,
two rows.

- **Row 1** (flex, gap 10, align center):
  1. **Search.** A 40px-high box, flex 1, border `1px solid #e2e6ed`, radius 12, padding
     `0 12px`, bg `#fff`.
     - Icon: a 12×12 ring, `border:2px solid #8a93a3`, radius 50%, margin-right 8.
     - Input: borderless, 15px, placeholder `Search ingredients`. It filters by name
       substring, case-insensitive (`iq`).
  2. **Tier segmented control.** Height 40, padding 3, bg `#f1f3f7`, radius 12, gap 2.
     - Leading label `Tier`: 12px `#8a93a3`, padding `0 8px 0 6px`.
     - Segments `All`, `A`, `B`, `C`: height 34, min-width 34, padding `0 10px`, radius 9,
       14px.
     - Active segment: bg `#fff`, shadow `0 1px 2px rgba(31,38,51,.08)`, fg `#3558b8`,
       weight 700. Inactive: transparent, `#1f2633`, weight 500.
     - State `tierF` ∈ `all|A|B|C`.
  3. **Count button.** Height 40, bg `#4a6fd1`, white, radius 12, padding `0 18px`, 15px
     700, nowrap.
     - Label `Start a count`, or `Counting…` while a count is running.
     - Click starts a count over the full queue (1.3); clicking again restarts it.
- **Row 2:** filter chips (flex-wrap, gap 8).
  - Chip: height 34, radius 999, padding `0 14px`, 14px, border `1px solid {bd}`, `on()`
    colours.
  - Content: `{name}` then `{n}` (12px, opacity .6).
  - Chips `All`, `Count these`, `Running out`, `Use soon`, `Checklist`. State `stF`.
  - Counts are computed over the rail-filtered set, before the tier and search filters.

**Info strip** (L405–410): `padding:10px 20px; gap:8px 20px; 13px #5b6475; bg #fafbfc;
border-bottom:1px solid #eef0f4`, wrapping.

- `st.asOf`: `Worked out to {fmtD(today)}`, e.g. `Worked out to Fri 26 Sept`.
- `st.soonLine`: clickable, weight 600, sets `stF='soon'`.
  - Colour `#d4554a` when non-empty, else `#5b6475`.
  - Non-empty: `{n} batch|batches go out of date in 3 days · {gbp(value)}`.
  - Empty: `Nothing goes out of date in the next 3 days`.
- `st.wasteLine`: `Written off this month: {gbp(value)} ({count})`.
- Right-aligned (`margin-left:auto`, `#8a93a3`): `Estimates in <i>italic</i>, counts
  upright`.

**Listing** (L430–504): flex row; the table fills the space and the drawer is optional.

- **Header row:** grid `minmax(0,2.2fr) 84px minmax(0,1.3fr) 64px 118px minmax(0,1.2fr)
  70px`, gap 12, padding `10px 20px`, border-bottom `1px solid #eef0f4`,
  `font:700 11px Nunito; uppercase; letter-spacing:.06em; color:#8a93a3`.
  - Headers: `Ingredient` | `Left (est.)` (right-aligned) | `Last count` | `Drift`
    (right-aligned) | `Trust` | `Runs out` | `Use by`.
- **Body:** scrolls (`overflow-y:auto`). Each row uses the same grid, `min-height:52px;
  padding:6px 20px; border-bottom:1px solid #f1f3f6; 14px`.
  - Row background `#edf1fc` when it is the selected ingredient, else transparent.
  - Clicking a row selects it (`selSt`), opens the drawer and leaves counting mode.
  - Cells:
    1. **Tier badge + name.**
       - Badge: 22×22, radius 7, bg `#f1f3f7`, `#5b6475`, `font:700 11px`, text
         `A|B|C`.
       - Name: weight 600, ellipsised.
    2. **Left (est.).** Right-aligned, **always italic**, colour `#1f2633`.
       - `~{fmtQ}`, e.g. `~7.60 L`.
       - `—` when there is no estimate; empty for tier C.
    3. **Last count.** 13px `#5b6475`, ellipsised.
       - Counted: `{fmtQ(qty)} · {date without weekday}`, e.g. `7.00 L · 19 Sept`.
       - Tier C: `marked low` / `marked OK` / `checklist`.
       - Otherwise `never`.
    4. **Drift.** Right-aligned; empty when there is no reading.
       - Sign convention: shown as (counted − expected)/counted. Positive gets a `+`,
         negative keeps `-`, e.g. `-22%`, `+4.1%`.
       - Colour `#d4554a` when |d| > 15%, else `#1f2633`.
       - Weight 700 when |d| ≥ 10%, else 400.
    5. **Trust pill.** Height 24, radius 999, padding `0 10px`, 12px 700.

       | label | fg | bg |
       |---|---|---|
       | Trusted | `#237a57` | `#e8f5ee` |
       | Drifting | `#9a6a12` | `#fdf3e1` |
       | Excluded | `#c2453b` | `#fdecea` |
       | Never counted | `#5b6475` | `#f1f3f7` |
       | (tier C) Low | `#c2453b` | `#fdecea` |
       | (tier C) Checklist | `#5b6475` | `#f1f3f7` |

    6. **Runs out.** 13px, ellipsised.
       - `{fmtD(date)} · {n}d`, e.g. `Sat 3 Oct · 8d`.
       - `today` if under 1 day.
       - `not enough sales` when the rate is 0; empty for tier C.
       - Colour `#d4554a` when ≤ 3 days.
    7. **Use by.** 13px, nowrap: the soonest expiry of a batch still holding stock, as a
       date without weekday. Red when ≤ today + 3.
- **Empty state:** `Nothing in this view.` (padding 40, centred, 15px `#8a93a3`).

**Sort order** (L1030–1032):

1. Tier C rows last.
2. Then by tier A→B.
3. Then trust, in the order Excluded, Never counted, Drifting, Trusted.
4. Then days of cover, ascending, with null last.

**Filters** (L1022–1026):

| chip | predicate (design) |
|---|---|
| All | true |
| Count these | tier ≠ C && (has sales rate or has a count) && trust ≠ Trusted |
| Running out | days of cover ≤ 7 |
| Use soon | soonest live-batch expiry ≤ today+3 |
| Checklist | tier = C |

**Rail** (Stock, L1018–1020): `All` with the total count, a head `Categories`, then one row
per ingredient category with its count. State `stCat`.

### 1.2 Detail drawer (L450–502)

- Container: width **400px**, flex none, border-left `1px solid #e8ebf0`, padding
  `16px 18px`, **bg `#f6f7fa`**, scrolls.
- Title row: name (`font:800 21px`) + close `×` (17px `#8a93a3`), which clears `selSt`.
- **Tier row:** 14px `#5b6475`. Contains `Tier`, three toggles `A` `B` `C`, then
  `{tierNote}` in `#8a93a3`.
  - Toggles: border `1px solid #d5dae3`, radius 8, padding `0 7px`, `font:700 12px`,
    letter-spacing .04em, `on()` bg/fg.
  - tierNote:
    - A: `worked out from sales, can earn auto-ordering`
    - B: `worked out, always reviewed`
    - C: `yes/no checklist`
  - Click → changes tier (see Conflict C1).

**Tier C only** (L455–457): one card (`border:1px solid #e8ebf0; radius 14; padding 12`,
14px).

- Text: `Checklist item: not worked out from sales. Staff mark it OK or low.`
- Two option buttons, `Plenty` (OK) and `Running low` (LOW): flex 1, border
  `1px solid #e8ebf0`, radius 16, padding 3, `on()` colours. Click records the answer.

**Tiers A/B** (L459–486):

1. **Estimate card.** Border `1px solid #e8ebf0`, radius 14, padding `12px 14px`, mb 12. It
   has no white fill and sits on `#f6f7fa`.
   - `We think you have` (13px `#5b6475`).
   - `{est}` (**30px italic**), or `not counted yet`.
   - `worked out, not counted` (13px `#8a93a3`, mb 8).
   - `{how}` (14px `#5b6475`):
     - With a count: `Counted {q} on {fmtD}, + {delivered} delivered, − about {used} used
       over {n} days[, − {wo} written off].`
     - Without: `Count it once and the app keeps track from there.`
2. **Last count card.** Same card style.
   - Header: `Last count` (`font:800 15px`) + an outline pill `{trust}`. The pill has
     border and text in the TRUST_C colour (Trusted `#1f2633`, Drifting `#5b6475`,
     Excluded `#d4554a`, Never counted `#8a93a3`), radius 12, padding `0 8px`, 12px.
   - `{last}` (19px): `{q} on {fmtD}`, or `never`.
   - `{driftLine}` (14px `#5b6475`, margin `4px 0`):
     - First count: `First count, nothing to compare yet.`
     - Otherwise: `The app expected {expected}: out by {pct}% (less than expected).` if the
       count came in low, else `… (more than expected).`
   - **Split** (shown when |drift| ≥ 5%): grid `130px minmax(0,1fr) 44px`, gap `6px 8px`,
     13px, margin `8px 0`. Two bars:
     - `Went out of date`, with a bar whose fill width is the expiry share;
     - `Measuring / recipe`, with a bar whose fill width is 1 − share.
     - Each bar is 12px high, border `1px solid #d5dae3`, fill `#4a6fd1`. The right column
       holds the `%`.
   - Then, with border-top and padding-top 6, 14px: `<b>{fixTitle}</b> {fixBody}`.
     - expiry share ≥ 0.5: `Order less; don’t change the recipe.` / `Most of the gap is stock
       that went out of date. Changing the recipe would hide a buying problem.`
     - otherwise: `Check the recipe or how it’s measured.` / `Most of the gap isn’t explained
       by waste: quantities per drink, spills or a missed delivery.`
   - `{autoLine}` (13px `#5b6475`, mt 6):
     - Not tier A: `Not tier A, so never auto-ordered.`
     - Trusted: `Two counts in a row under 10%: can be auto-ordered (still confirmed in
       Telegram).`
     - Otherwise: `Needs two counts in a row under 10% before it can be auto-ordered.`
3. **Batches.**
   - Header: `Batches` (`800 15px`) + `used oldest-first by use-by date` (12px `#8a93a3`).
   - Table grid `1fr 60px 1fr 64px`, gap `4px 8px`. Head row 13px `#5b6475` with
     border-bottom: `Came in` | `Qty` (right) | `Use by` | `Left` (right).
   - Rows are 14px, padding `4px 0`, border-bottom `#e8ebf0`:
     - `Came in`: received date without weekday;
     - `Qty`: qty received;
     - `Use by`: `fmtD(exp) · {n}d`, or `keeps` when there is no expiry; red when it still
       holds stock and exp ≤ today+3;
     - `Left`: **italic**.
   - Empty: `No deliveries recorded since the last count.` (13px `#8a93a3`).
4. **Delivery row** (13px, gap 6, wrap):
   - `Delivery`, then a qty input (56px wide, placeholder `qty`, border `#d5dae3`, radius
     10), then `use by`, then a date input (12px).
     - The date defaults to today + shelf-life days when a shelf life is set.
   - `Record` button: border `#d5dae3`, radius 12, padding `6px 14px`.
   - Requires qty > 0. Clears both inputs after recording.
5. **Write-off row:**
   - `Write off`, then a qty input, then a select:
     - `Went out of date` (value `Expired`)
     - `Spilled / dropped` (`Spilled`)
     - `Staff drinks` (`Staff`)
     - `Other` (`Other`)
   - `Write off` button: border and text `#d4554a`, radius 12. Requires qty > 0.
6. **How long it keeps.** Shown for all tiers (`800 15px`). Grid of 3 columns, gap 8. The
   labels are 12px `#5b6475`; inputs border `#d5dae3` radius 10 14px.
   - `Unopened (days)` (placeholder `not set`). The border is `#d4554a` when there is no
     shelf life and the category is one of Dairy, Dairy alt, Food, Cake or
     Cake (CakeSmiths).
   - `Opened (days)` (placeholder `—`).
   - `Reorder at` (placeholder `—`).
   - `{shelfNote}` (13px `#8a93a3`, mb 14):
     - `Caps orders: never more than can be used in {n} days (less delivery time).`
     - or `Not in the workbook. Without it, orders for this can’t be capped by shelf life.`
7. **Count history** (A/B only):
   - Header `Count history` (`800 15px`).
   - Rows (flex space-between, 14px, padding `3px 0`, border-bottom) show `{fmtD(date)}`,
     `{qty}`, and `{x}% out` or `first`. The drift text is `#d4554a` when > 15%, else
     `#5b6475`.
   - Button `Count this now`: full width, bg `#4a6fd1`, white, radius 18, padding 6, 14px
     700. It starts a count of this single ingredient.

When the drawer is closed the table takes the full width. The drawer does not open on its
own when embedded: `selSt` starts null.

### 1.3 Count flow (L412–428, L1049–1075)

Counting replaces the listing. The toolbar and info strip stay visible.

- **Scroll area:** padding 24. Card: `max-width:560px; margin:0 auto; border:1px solid
  #e8ebf0; radius 12; padding:22px 24px`.
- Top line (14px `#5b6475`, space-between): `Counting · {i} of {n}` on the left;
  `Stop for now` (underlined) on the right, which exits counting.
- Name: `font:800 26px`, mt 4.
- Hint (14px `#5b6475`, mb 16):
  - EACH: `Count every one, opened packs too.`
  - Otherwise: `Everything you can see, opened ones included. In {u}.`
- **Stepper row** (gap 10):
  - `–` and `+` are 50×50 circles, border `#e8ebf0`, 22px.
  - Between them an input (flex 1, border `#e8ebf0`, radius 12, padding 10, 22px, centred,
    placeholder `What you can see`), then the unit (16px `#5b6475`).
  - Step size: EACH 1; KG/L 0.1; ML/G 10. The `–` never goes below 0.
- **Message** (min-height 52, margin `14px 0`, 15px). It appears only once a value is typed
  (the "expected" figure is hidden until then, on purpose):
  - < 10%: `Expected about {est}. {pct}% out: close enough.` (`#1f2633`)
  - 10–15%: `Expected about {est}. {pct}% out: worth a second look, stays on manual
    ordering.` (`#5b6475`)
  - > 15%: `Expected about {est}. {pct}% out: over 15%, so this comes off auto-ordering until
    it settles.` (`#d4554a`)
  - No prior count: `First count for this one; it becomes the starting point.`
  - Here `pct` = |est − v| / max(v, 0.001) × 100, to 1 dp.
- **Buttons** (gap 10):
  - `Skip`: flex 1, border `#e8ebf0`, radius 20, padding 8, 16px.
  - `Save · next`: flex 2, bg `#4a6fd1`, white, radius 20, padding 10, 16px 700. Ignored
    when the input is empty or not a number.
- Footnote (13px `#8a93a3`, mt 12): `Least-trusted first, so stopping halfway still does
  the useful part. The expected figure shows only after you type.`
- Below the card (max-width 560, centred, mt 14): `Up next:` followed by up to 5 name pills
  (border `#d5dae3`, radius 14, padding `0 8px`, 13px).
- **Finished:** `Count finished: {n} ingredients counted.` + `Back to stock` (underlined),
  centred, padding 60, 17px.
- **Queue:** every non-C ingredient that has either a sales rate or a count. Ordered by
  tier, then trust (Excluded, Never counted, Drifting, Trusted), then days of cover. Skip
  advances without writing.

### 1.4 Data contract: Stock (design derivation → what to use instead)

- **Design's `calc(i)`** (L785–798): last count `lc`, then
  `est = max(0, lc.qty + Σ deliveries after lc − rate × days since lc − Σ writeoffs after
  lc)`.
  - `d1 = (lc.expected − lc.qty)/max(|lc.qty|, .001)`.
  - `trust = |d1|>.15 ? Excluded : (|d0|<.10 && |d1|<.10) ? Trusted : Drifting`, and
    `Never counted` when there is no count.
  - `runDays = est/rate`.
  - Batches are allocated newest-expiry-first to compute `left`.
  - `nextExp` = earliest expiry with left > 0.
- **Soon line** (L1043–1046): batches with left > 0 and exp ≤ today+3. The value is Σ left ×
  unit cost.
- **Waste line** (L1047–1048): Σ this-month writeoffs × unit cost, and their count.
- **Count save** (L1070–1072): pushes `{date, qty, expected, expiryShare}`.

All of these are replaced by server figures (section 4.1). The only client-side
arithmetic that stays is formatting, sorting, the filter predicates and the live
count-preview message. That last one must be computed with `lib/dec.ts`, not floats.

---

## 2. ORDERS tab

### 2.1 Frame (L223–229)

- There is no rail and no add button.
- **Sub-tab bar:** padding `10px 20px`, border-bottom `#e8ebf0`, gap 8, wrap.
  - Chips `Draft orders` | `History` | `Shop runs`: border `1px solid #e8ebf0`
    (constant), radius 14, padding `6px 14px`, 14px, with `on()` bg/fg. State `odView`,
    default `draft`.
  - Right side: `{open} open · {waiting} waiting in Telegram` (14px `#5b6475`). Here `open`
    is Waiting + Confirmed + Sent.
- Body padding `16px 20px`, scrolls.

### 2.2 Draft orders (L231–261, logic L814–883)

1. **Guess banner** (shown when any supplier's terms are unconfirmed): border
   `1.5px dashed #d4554a`, radius 12, padding `8px 12px`, 14px, mb 14.
   - Copy: `{n} of {total} suppliers’ terms are guesses. Delivery days, lead times and
     minimums below are built on them; confirm each on its supplier page.`
2. **Basket grid:** `repeat(auto-fill, minmax(520px,1fr))`, gap 16, align-items start.
   - Tesco is never a basket.
   - A supplier appears if it has lines **or** an order already pending (see below).
   - **Card:** border `#e8ebf0`, radius 14, padding `12px 14px`.
   - **Head** (baseline, gap 10):
     - Supplier name (`800 18px`, flex 1).
     - Status pill: border and text in the status colour, radius 12, padding `0 8px`,
       12px. `Draft` is `#8a93a3`; `Waiting in Telegram` is `#d4554a`; `Confirmed` and
       `Sent` are `#1f2633`.
     - Total (18px).
   - **Terms line** (14px `#5b6475`, margin `2px 0 8px`): `Order by [{cutoff}, ]{fmtD(arrive −
     lead)} · arrives {fmtD(arrive)} · next after that {fmtD(after)} · min {£min|none}[ ·
     fee {£fee}[ (free over {£free})], included|waived]`.
   - **Lines:** grid `minmax(0,1fr) 110px 70px`, gap 10, border-top, padding `7px 0`.
     - Name (15px). Under it (13px `#5b6475`) the `why` line, followed by the cap in
       `#d4554a`:
       - why: `About {rate} a day for {days} days; you have ~{est}[ + {oo} on order].`
       - cap: `Cut to {usable} days after delivery: it keeps {shelf} days.`
     - Stepper: `–` and `+` are 22px circles with border `#d5dae3`, around the qty text
       (min-width 44, centred, 14px) `{packs} × {fmtQ(pack)}`, e.g. `3 × 4 L`. Clicking
       adjusts `odAdj[supplier:ingredient]` by ±1; packs never go below 0.
     - Price: right-aligned, 14px, `gbp(packs × packCost)`.
   - **Under minimum** (only when not pending): border-top, pt 8, 14px `#d4554a`.
     - Copy: `{£short} under their {£min} minimum. Top up with something that keeps (never
       with fresh stock):`
     - Then up to 4 dashed chips (`1.5px dashed #d5dae3`, radius 12, padding `6px 14px`,
       13px) reading `+ {name} · {£packCost}`. Click adds 1 pack of that item to the basket.
       Its why-line is `Added to reach the minimum. Keeps well, so nothing is wasted.`
   - **Footer** (flex, gap 10, mt 10): note (13px `#8a93a3`, flex 1) and the send button.
     - Note copy, in order of precedence:
       - pending Waiting: `Sent to Telegram. Nothing is ordered until someone taps
         Confirm.`
       - pending Confirmed: `Confirmed by {who}. See History.`
       - otherwise `Terms confirmed.` or `Terms are a guess.`
     - Button `Send to Telegram to confirm`: bg `#4a6fd1`, white, radius 16, padding
       `9px 14px`, 14px 700. Shown only when nothing is pending and some line has
       packs > 0.
     - Send → creates an order in status `Waiting` holding the lines with packs > 0. It also
       pushes an Agents-feed proposal `Confirm {Supplier} order · £X` and resets that
       basket's adjustments.
   - A **pending** basket (an open order created today for this supplier) shows the stored
     lines read-only: the steppers are no-ops and there is no why line.
3. **Shop-run box** (when any line cannot wait): border `1.5px dashed #d5dae3`, radius 14,
   padding `12px 14px`, mt 16, max-width 760.
   - Title `Shop run · can't wait for a delivery` (`800 17px`) + total (16px).
   - Rows: grid `minmax(0,1fr) 90px 80px`, gap 10, border-top, padding `6px 0`, 14px.
     - Name, with the why-line under it: `Runs out around {fmtD}; {Supplier} arrives
       {fmtD}.`
     - Qty.
     - Price.
   - Footer: `Retail premium is a guess (25% over the supplier price). Every shop run is
     logged in Shop runs.` (13px `#8a93a3`) + button `Log as bought` (border `#e8ebf0`,
     radius 16, padding `6px 14px`).
     - The button logs a `Bought` order for Tesco and adds a batch for each line.
4. **Uncounted line** (14px `#5b6475`, mt 16): `Count these first; they can’t be ordered
   until there’s a real number: {names, up to 12}[ and N more].`
5. **No-supplier line:** `No supplier set, so not ordered: {names, up to 10}[ and N more].`
6. **Empty state:** `Nothing needs ordering right now.` (padding 40, 16px `#8a93a3`).

**Design sizing logic** (L827–847). Keep it only as a description; do not port it.

- Deliveries:
  - `arrive` = the next delivery weekday on or after today + lead;
  - `after` = the next delivery weekday after `arrive`;
  - `untilArrive` = days from today to `arrive`;
  - `gap` = days from `arrive` to `after`.
- Cover window: `eff = gap + 1`, capped at `max(1, shelf − lead)`.
- `need = rate × (untilArrive + eff) − est − onOrder`; `packs = ceil(need / pack)`.
- Emergency when `est/rate < untilArrive`. The bridge quantity is
  `rate × (untilArrive − est/rate)`, priced at `uc × 1.25`.
- Top-up candidates: preferred links of this supplier that are not in the basket, not tier
  C, and have no shelf life or one over 30 days. Sorted by cover ascending; top 4.

### 2.3 History (L263–274, L884–891)

- Supplier filter: a select (border `#d5dae3`, radius 10, padding `3px 6px`, 14px) with
  `All suppliers` plus each supplier.
- **Table:** grid `110px minmax(0,1fr) minmax(0,2.4fr) 90px 110px 110px 150px`, gap 10.
  Head is 13px `#5b6475`, padding `6px 0`. Rows are 14px, padding `7px 0`, border-bottom.
  - `Created`: date without weekday.
  - `Supplier`.
  - `What` (`#5b6475`): e.g. `2× Whole milk, 6× Oat milk (barista)`.
  - `Total`: right-aligned.
  - `Status`: a pill in the status colour.
    - `Waiting` is `#d4554a`.
    - `Confirmed` and `Sent` are `#1f2633`.
    - `Received`, `Bought` and `Cancelled` are `#8a93a3`.
  - `Arrives`: date without weekday.
  - Actions: chips with border `#d5dae3`, radius 12, padding `6px 14px`, 12px.
- Sorted by created date, descending.
- Actions per status:
  - Waiting → `Cancel`.
  - Confirmed → `Mark sent`, `Cancel`.
  - Sent → `Received → stock`. This sets Received and adds one batch per line at
    `packs × pack`, with expiry today + shelf-life days.
- Empty state: `No orders yet.` (padding 30, `#8a93a3`).

### 2.4 Shop runs (L276–283, L892–901)

- Heading (`800 21px`): `{n} shop runs, {£total} in total`.
- Sub-heading (14px `#5b6475`, max-width 760) verbatim: `Every time stock is bought at a
  supermarket or corner shop because a delivery would come too late. Found in the expenses
  log plus shop runs logged here. The count is the case for fixing delivery days: retail
  almost always costs more than the supplier price.`
- **Bar chart** (max-width 760):
  - Height 120, flex, gap 10, border-bottom `#e8ebf0`.
  - One bar per month, for the last 8 months that have runs. Bar fill `#4a6fd1`, height =
    that month's total ÷ the maximum month total, minimum 2%.
  - Value label above each bar: 12px `#5b6475`, `£123` with no pence.
  - Month labels (`Sep`) sit in a separate row below.
- **Table** (max-width 900): grid `110px minmax(0,1.6fr) minmax(0,2fr) 90px`.
  - Head: `Date` | `Where` | `Why / note` | `£`.
  - Up to 40 rows, newest first.
- **Sources:**
  - Finance expenses whose description or notes match
    `/tesco|convenience|co-?op|aldi|lidl|sainsbury|asda|spar\b|morrisons|farmfoods|iceland/i`.
  - Orders logged as `Bought` via "Log as bought".

---

## 3. SUPPLIERS tab

### 3.1 Layout (L184–221, logic L744–781)

- **Rail:** head `Suppliers`, then one row per supplier. The count on each row is the
  number of linked products. Selection is `selS`, default `s1`. Compact mode shows chips
  instead.
- **Page:** padding `18px 22px`, scrolls.
  - Nothing selected: `Pick a supplier, or add one.` (60px padding, 16px `#8a93a3`).
- **Name row:**
  - The name is an inline input: borderless apart from a bottom border `1px solid #e8ebf0`,
    `font:800 24px`, padding `2px 0`.
  - `Delete` button: border and text `#d4554a`, radius 14, padding `6px 14px`, 14px. It
    removes the supplier and all its links immediately, with no confirmation.
- **Terms grid:** `repeat(auto-fit, minmax(170px,1fr))`, gap 14, margin `16px 0`.
  - Each field is a `<label>` column (gap 4, 14px `#5b6475`) containing the control.
  - Controls: border `#e8ebf0`, radius 10, padding `5px 8px` (selects `5px`), 15px.
  - Fields:
    1. `Type`: select, `Foodservice, Wholesale, Packaging, Cakes & bakery, Supermarket,
       Online, Specialist, Custom`.
    2. `How we order`: select, `Portal, Email, Phone, In store, Message, EDI`.
    3. `Contact / website`: placeholder `—`.
    4. `Lead time (days)`: `—`.
    5. `Minimum order £`: `—`.
    6. `Order cut-off`: placeholder `e.g. 12:00 day before`.
    7. `Delivery fee £`: `—`.
    8. `Free delivery over £`: `—`.
- **Terms checkbox row** (mb 12, 14px): an 18×18 box with radius 6 and border in the current
  colour, holding `✓` or nothing.
  - Colour and label: `#1f2633` with `Terms confirmed with the supplier`, or `#d4554a` with
    `Terms are a guess: tick once confirmed with the supplier`.
  - Click toggles.
- **Delivery-days row** (gap 8, wrap, mb 18):
  - `Delivers on` (14px `#5b6475`).
  - Seven toggles `Mon`…`Sun`: border `#e8ebf0`, radius 12, padding `6px 14px`, 14px,
    `on()` bg/fg.
  - `not set` (13px `#8a93a3`) when none is selected.
  - The design indexes days 0–6 with Mon = 0.
- **Products header** (baseline, gap 12, mb 6):
  - `What we buy here · {n}` (`800 16px`).
  - `★ = the price used in recipe costs` (13px `#8a93a3`).
  - Spacer, then a `Filter` input (150px, border `#d5dae3`, radius 10, padding `3px 8px`)
    that filters by ingredient name.
- **Products table:** grid `28px minmax(0,2.2fr) minmax(0,1.3fr) 70px 64px 80px 100px
  minmax(0,1.1fr) 20px`, gap 8.
  - Head: 13px `#5b6475`, padding `4px 0`, border-bottom. Columns: (star) | `Ingredient` |
    `Their product / SKU` | `Pack` (right) | `Unit` | `Pack £` (right) | `Per unit` (right) |
    `vs other suppliers` | (×).
  - Rows: padding `6px 0`, border-bottom, 14px, align center.
    - **Star.** 17px; `★` in `#d4554a` when preferred, `☆` in `#8a93a3` otherwise. Click
      makes this link the preferred one for its ingredient.
    - **Ingredient.** A select, `— pick ingredient —` then every ingredient A–Z. Changing it
      takes the ingredient's unit and becomes preferred if that ingredient has no preferred
      link.
    - **SKU.** Input.
    - **Pack.** Input, right-aligned.
    - **Unit.** Select, `L, ml, kg, g, unit`.
    - **Pack £.** Input, right-aligned.
    - **Per unit.** `unitPrice(packCost/pack × conv)/unit`, or `—`.
    - **Compare.** 13px:
      - `only supplier` (`#8a93a3`);
      - `same as {X}` (`#8a93a3`);
      - `{n}% cheaper than {X}` (`#1f2633`);
      - `{n}% dearer than {X}` (`#d4554a`).
      - Compared against the cheapest other link for the same ingredient; within ±0.5% counts
        as "same".
    - **Remove.** `×` in `#8a93a3`. Removing the preferred link promotes the next link.
  - Controls in this table: border `#d5dae3`, radius 10, padding `3px 6px`.
- `+ Link an ingredient`: dashed `1.5px #d5dae3`, radius 14, padding `6px 14px`, mt 10. It
  adds a blank row (pack 1, unit `unit`, £0).
- `Notes`: a label with an input (placeholder `Account number, rep, anything`), mt 18.
- **Every change autosaves** (the `mut` path) and flashes `Saved` in the header. Editing the
  pack or pack price of the ★ link rewrites the ingredient's recipe cost (`syncPreferred`,
  L576).

### 3.2 Seeds visible in the demo (L525, L546)

The eight suppliers: Brakes, Booker, Cakesmiths, Cups Direct, Monolith, Tesco, Amazon and
Nataly. Their demo terms are in `SUP_TERMS`. All start with `termsConfirmed=false`.

---

## 4. Backend mapping

Legend:

- **EXISTS**: the endpoint and field are there.
- **DERIVABLE**: the data is in the DB or a service but not exposed.
- **MISSING**: needs a new model column, migration or service.

Paths are relative to `cafeops/`.

### 4.1 Stock

| Datum / action | Status | Where / what |
|---|---|---|
| Ingredient list, tier, unit | EXISTS | `GET /api/stock` (`api/routers.py:273`) → `StockRow.name/tier/unit` (`api/schemas.py:568`). Pass **`include_untracked=true`**, or tier C and untracked rows are missing (`api/views/stock.py:380`). |
| Category (rail + counts) | DERIVABLE | `Ingredient.category` (`db/models/ingredient.py:27`) is not on `IngredientSnapshot` or `StockRow`. Add `category: str \| null`. |
| Left (est.) | EXISTS | `StockRow.on_hand.qty` (`OnHand`, `api/schemas.py:152`), always `is_theoretical=true`. When `has_count_basis=false`, `basis_label` reads `ledger only - NO COUNT`: show `—`/"no count" rather than `~qty` (see C9). |
| Last count qty/date | EXISTS | `on_hand.basis_count_qty`, `on_hand.basis_counted_at`. `counted_by` is DERIVABLE (`db/models/stock.py:29`). |
| Drift % | EXISTS | `StockRow.drift.drift_pct` (`api/schemas.py:501`), = (theoretical − counted)/counted × 100. **Display −drift_pct** to match the design's sign. |
| Trust label | EXISTS, partly | `drift.trust_status` ∈ trusted/drifting/excluded/null (`api/views/stock.py:164`). The design's four words need `clean_streak`/`required_streak` (also on `DriftOut`) and `has_count_basis` (C2). |
| Runs out | EXISTS | `StockRow.run_out.on`, `.days`, `.is_out_of_stock`, `.note` (`api/schemas.py:526`, built at `api/views/stock.py:248`). When withheld, `forecast.is_low_confidence=true` and `on/days=null`: render `forecast.reasons[0]` in the cell (invariant 9). Rate 0 → `note` "nothing is moving" → `not enough sales`. |
| Use by | EXISTS | `StockRow.soonest_expiry_days` + `batches[].effective_expiry`. The server threshold is `SHORT_DATED_DAYS = 3` (`api/views/stock.py:91`), and `is_short_dated` drives `Use soon`. |
| Soon line (count, value) | EXISTS, partly | Value: `StockResponse.summary.expiring_value_pence` (null when any expiring batch is unpriced; `api/views/stock.py:412-458`). Batch count: DERIVABLE client-side (`rows[].batches[].days_left ≤ 3`). Add `summary.short_dated_batches` for one source of truth. |
| Written off this month (£, n) | DERIVABLE | `stock_movement` of type `WASTE`/`STAFF`/`EXPIRED` in the local month (`db/models/stock.py:40`, `db/models/enums.py:62`), valued at `stock_batch.unit_cost_pence` via `batch_id`. Not exposed. Add `summary.written_off_month {value: Cost, count}`. |
| Filters Count these / Running out / Use soon / Checklist | DERIVABLE (client) | Predicates in 1.1, over the row fields above. |
| Tier C "marked low/OK" | DERIVABLE | `SqlChecklistRepository.latest_by_ingredient` (`db/repositories/checklist.py:81`). Add `StockRow.checklist {status, responded_at, responded_by}`. |
| Drawer: estimate breakdown ("+ delivered, − used, − written off") | DERIVABLE | `on_hand.movement_sum`/`movement_count` exist, but not split by type. Add `StockRow.since_count {delivered, sold, wasted, expired, adjusted}` (sums per `MovementType` after the basis count). Drop the design's "about … used over N days" rate language: it is the ledger's SALE sum, not rate × days. |
| Drawer: last count + drift line ("expected X") | EXISTS | `drift.theoretical_qty` / `drift.counted_qty` / `drift.observed_at`. |
| Drawer: split bars + fix copy | EXISTS | `drift.attribution` (`api/schemas.py:480`): `expiry_share`, `cause`, `headline`, `action`, `measurement_qty`, `expiry_qty`. Use `expiry_share` for bar widths. Prefer server `headline`/`action` over the design's two canned sentences, or map `cause` (EXPIRY → "Order less…", MEASUREMENT → "Check the recipe…", MIXED/NEGLIGIBLE → show `headline`). |
| Drawer: autoLine | EXISTS | `drift.auto_order_enabled`, `clean_streak`, `required_streak`, `auto_order_reason`. |
| Drawer: batches table | EXISTS, partly | `StockRow.batches[]` (`BatchOut`, `api/schemas.py:465`): `received_at`, `effective_expiry`, `days_left`, `qty_remaining`, `value_pence`. **Qty received is missing**: add `qty_received` (column `db/models/batch.py:47`). Only open batches are returned (`services/read_stock.py:122`), so "since the last count" is not the filter; see C17. `EXPIRY ASSUMED` lives in `stock_batch.note` (`services/receive_delivery.py:93`); expose it as `expiry_assumed: bool`. |
| Drawer: count history | DERIVABLE | `GET /api/stock/{id}` (`api/routers.py:306`) gives `drift_history[]` (observations only, `api/schemas.py:615`). The first count has no observation, so the design's `first` row needs `stock_count` rows. Add `StockDetail.counts[] {counted_at, counted_qty, counted_by, drift_pct\|null}`. |
| Shelf life / open life (read) | EXISTS | `StockRow.shelf_life` (`ShelfLifeOut`, `api/schemas.py:552`): `shelf_life_days` (null = does NOT expire), `open_life_days`, `transit_buffer_days`, `usable_days`, `is_perishable`, `source` (ESTIMATE/SUPPLIER_FEED…). |
| Reorder at (read) | DERIVABLE | `par_level.min_qty` (`db/models/par.py:26`), the par floor used by `below_par_floor` in `domain/ordering.py:768`. Not on `StockRow`. Add `par {min_qty, max_qty, safety_days}`. |
| **Write: start a count** | n/a | Client-only: the queue is built from `/api/stock`. The bot's equivalent is `bot/views.py:482 build_count_session` (tier, then name order). The design's least-trusted-first order is a client sort. |
| **Write: record a count** | Service EXISTS, endpoint MISSING | `services/record_count.py:133 record_count(session, ingredient_id, counted_qty: Decimal, counted_at, counted_by, note)` returns `CountOutcome` (L77): `on_hand_before`, `drift`, `explanation`, `decision`, `reconciliation`, `notes`. Needs `POST /api/stock/{id}/counts`. `counted_by` is required (see C10). The fractional-EACH refusal lives only in the bot handler (`bot/handlers/count.py:118`); move it into the service, or the endpoint must replicate it. |
| **Write: record a delivery (drawer)** | Service EXISTS, endpoint MISSING | `services/receive_delivery.py:283 receive_adhoc(ingredient_id, qty, expires_at, received_by, unit_cost_pence, note)`. It creates a batch + `DELIVERY` movement. With no expiry given it assumes one and stamps `EXPIRY ASSUMED`. |
| **Write: write off** | MISSING | No service appends `WASTE`/`STAFF` movements with FIFO batch allocation. Needs `services/record_write_off.py`, using `domain.stock.allocate_fifo` + `SqlBatchRepository.apply_allocations`/`append_linked_movements` (`db/repositories/batch.py:268,355`) as `services/expand_recipes.py:300` does. It also needs a reason code (C11). |
| **Write: tier C checklist answer** | Service EXISTS, endpoint MISSING | `services/record_checklist.py:111 record_checklist_answer(ingredient_id, status: OK\|LOW, responded_by)`. |
| **Write: change tier** | MISSING | Nothing writes `ingredient.tier` outside the seed. `domain/tiers.py:339 would_clear_gate` exists to inform the B→A decision. Needs a service (C1). |
| **Write: shelf life** | EXISTS (constrained) | `POST /api/ingredients/{id}/shelf-life` (`api/routers.py:254`, `api/views/confirm.py:152`, `services/confirm_terms.py:156`). Body `ShelfLifeIn` (`api/schemas.py:839`): `shelf_life_days ≥1`, `open_life_days?`, `source: 'supplier'\|'packaging'` (`estimate` refused). Response: `usable_days_changed_by`. It cannot set "does not expire" (null) or clear a value (C4). |
| **Write: reorder at** | MISSING | No service writes `par_level.min_qty`. `SqlParLevelRepository` (`db/repositories/par.py`) only writes the auto-order flags. Needs `set_par_floor` (it must not touch `auto_order_enabled`, invariant 2). |
| Count this now | n/a | Client: a single-item queue. |

### 4.2 Orders

| Datum / action | Status | Where / what |
|---|---|---|
| Baskets per supplier | EXISTS | `GET /api/orders/draft` (`api/routers.py:340`, `api/views/orders.py:236`) → `suppliers[]` (`SupplierOrderOut`, `api/schemas.py:953`). WRITES NOTHING. |
| Basket total, subtotal, fee | EXISTS | `total_pence`, `subtotal_pence`, `delivery_fee_pence` (the applied fee; 0 when waived). "included/waived" = `delivery_fee_pence > 0` vs `supplier.delivery_fee_pence > 0`. |
| Terms line | EXISTS / DERIVABLE | `target_delivery_date` (= arrive). `lead_time_days`, `days_until_next_delivery` (→ "next after that" = target + n). `supplier.cutoff_time`, `supplier.min_order_pence`, `supplier.delivery_fee_pence`, `supplier.free_delivery_threshold_pence`. Order-by date = target − lead: DERIVABLE; add `order_by_date` server-side so it matches `jobs/pre_delivery_orders.py:141 derive_schedule`. |
| Line name, packs, pack, price | EXISTS | `OrderLineOut` (`api/schemas.py:870`): `ingredient_name`, `packs`, `pack_size`, `pack_unit`, `pack_price_pence`, `line_total_pence`, `sku`. |
| Line "why" | EXISTS / DERIVABLE | `forecast` (over `cover_days`), `on_hand_qty`, `on_open_pos_qty`, `cover_days`. "About X a day" = `forecast.qty / cover_days`, and is **absent** when `forecast.is_low_confidence`: render `forecast.reasons` instead (invariant 9). Add `daily_rate_qty` server-side (null when withheld) rather than dividing in the browser. |
| Cap text | EXISTS | `cap_reason`, `cap_detail`, `capped_out_qty`, `is_capped`, `full_cover_days`. The structured `cap_kind` (`db/models/enums.py:172`, persisted on `po_line.cap_kind`) is not on `OrderLineOut`; add it so copy can be chosen without parsing prose. |
| Top-up lines | EXISTS | `is_top_up`, `SupplierOrderOut.min_order_topped_up`. The server tops up automatically, non-perishables only (invariant 5). |
| Under-minimum + top-up chips | EXISTS, partly | `meets_minimum`, `supplier.min_order_pence`. The chip candidates are not exposed. Add `top_up_candidates[] {ingredient_id, name, supplier_product_id, pack_price_pence, cover_days\|null}`, filtered by `is_perishable=false` (C7). |
| Status pill / pending basket | DERIVABLE | `purchase_order` (`db/models/purchase_order.py:28`) with status DRAFT/PENDING_CONFIRM/CONFIRMED/SENT, created by `jobs/pre_delivery_orders.py` via `services/build_order.py:489 create_draft_po`. The draft endpoint ignores persisted orders. Add `SupplierOrderOut.persisted` (C14). |
| "Confirmed by X" | DERIVABLE | `purchase_order.confirmed_by`/`confirmed_at` (`db/models/purchase_order.py:38-39`). |
| Guess banner | EXISTS | `DraftOrdersResponse.placeholder_supplier_names` + `GET /api/suppliers` (`terms_are_placeholders`, `api/schemas.py:636`) for the total. |
| Shop-run box (Tesco) | EXISTS | `DraftOrdersResponse.emergency[]` (`EmergencyLineOut`, `api/schemas.py:1003`): `qty`, `unit`, `reason`, `retail_unit_price_pence`, `preferred_unit_price_pence`, `premium_pence`, `raw_premium_pence`, `retail_is_cheaper`; `emergency_total_premium_pence`; `emergency_notes`. Retail cost per line = qty × retail_unit_price, DERIVABLE; null if unpriced. |
| **Log as bought** | Service EXISTS (parts), endpoint MISSING | Per line: `receive_adhoc` (batch + DELIVERY). Routing log: `SqlSourcingRepository.record_emergency_routing` (`db/repositories/sourcing.py:136`). `services/build_order.py:913 record_emergency_lines` takes a whole `SplitResult`. Needs a `log_shop_run` service taking the lines actually bought, with the real paid price. |
| Uncounted list | MISSING (behaviour) | The backend orders against ledger-only stock; nothing skips `has_count_basis=false`. Add `SkippedOut` reason `NO_COUNT` + `DraftOrdersResponse.uncounted[]` (C8). |
| No-supplier list | DERIVABLE | `domain/sourcing.py:238` produces "no supplier sells this" notes; not structured. Add `DraftOrdersResponse.unsourced[] {ingredient_id, name}`. |
| Header meta "N open · M waiting" | DERIVABLE | `purchase_order` status counts. `OPEN_PO_STATUSES` is at `db/repositories/purchase_order.py:27`. |
| Nav badge (Waiting count) | DERIVABLE | Same source, statuses DRAFT/PENDING_CONFIRM. |
| **Stepper ±1 on a draft basket** | n/a (client) | Held client-side until Send. It becomes `po_line.final_packs` while `suggested_packs` keeps the system's number (`db/models/purchase_order.py:103,105`). |
| **Send to Telegram to confirm** | MISSING (endpoint + push) | Persistence exists (`create_draft_po`, `SqlPurchaseOrderRepository.create_draft`, `db/repositories/purchase_order.py:76`), but it cannot take human `final_packs` overrides, and nothing pushes a card to Telegram: the bot only lists drafts on `/orders` (`bot/handlers/orders.py:46`). Needs a `services/send_for_confirmation.py`: persist PENDING_CONFIRM + notify via `bot/notify.py:109 notifier_for_settings`. `api/routers.py:8` currently says no route creates a PO (C5). |
| Confirm | EXISTS (Telegram only) | `bot/views.py:403 confirm_order` → `SqlPurchaseOrderRepository.confirm` (`db/repositories/purchase_order.py:272`), guarded by `ck_po_confirmed_requires_human` (`db/models/purchase_order.py:75`). **No web confirm**, and the design has none: correct. |
| History list | MISSING (endpoint) | `purchase_order` + `po_line` rows. `list_for_supplier` exists (`db/repositories/purchase_order.py:321`). Needs `GET /api/orders`. |
| **Cancel** | MISSING | `POStatus.CANCELLED` exists (`db/models/enums.py:117`), but nothing sets it. The bot's "cancel" deliberately leaves DRAFT (`bot/handlers/orders.py:80`). Needs `cancel_order(po_id, cancelled_by, reason)`, allowed from DRAFT/PENDING_CONFIRM/CONFIRMED and refused from SENT/RECEIVED. There is no `cancelled_by`/`cancelled_at` column: add both (migration) or append to `notes`. |
| **Mark sent** | Repo EXISTS, endpoint MISSING | `SqlPurchaseOrderRepository.mark_sent(po_id, at)` (`db/repositories/purchase_order.py:308`); CONFIRMED only. The normal path is `bot/views.py:422 dispatch_order`. |
| **Received → stock** | Service EXISTS, endpoint MISSING | `services/receive_delivery.py:178 receive_po_line(po_line_id, received_packs\|received_qty, expires_at, received_by)`, per line. It requires CONFIRMED/SENT/RECEIVED (invariant 1) and closes the order when complete (L568). The design's one-click "receive everything at full qty with assumed expiry" must become a receive sheet (C16). |
| Shop runs (history) | DERIVABLE | `tesco_routing` (`db/models/channel.py:166`); `SqlSourcingRepository.emergency_log`/`emergency_summary` (`db/repositories/sourcing.py:191,200`); CLI `cafeops emergency-report` (`cli.py:2806`). No API. |
| Shop runs from the expenses log | MISSING | There is no expense model anywhere in `cafeops/` (only `payment_day`, `db/models/payment.py:46`). This depends on the Finance area. Until it exists, show `tesco_routing` + logged shop runs only, and change the sub-copy (drop "Found in the expenses log"). |

### 4.3 Suppliers

| Datum / action | Status | Where / what |
|---|---|---|
| List + name | EXISTS | `GET /api/suppliers` (`api/routers.py:330`, `api/views/orders.py:68`) → `SupplierOut`. |
| Linked product count (rail) | DERIVABLE | `supplier_product` rows (`db/models/supplier.py:69`). |
| Type (kind) | MISSING | No column. Add `supplier.kind` (enum or String(40)). |
| How we order | EXISTS (different vocab) | `supplier.order_channel` (`OrderChannel`: PORTAL/EMAIL/EDI/MANUAL/BROWSER_AGENT, `db/models/enums.py:95`) (C13). |
| Contact / website | DERIVABLE | `supplier.contact` (`db/models/supplier.py:31`) and `order_url` (L57). Not on `SupplierOut`. |
| Lead, min, fee, free-over, cut-off, days | EXISTS | `SupplierOut.lead_time_days`, `min_order_pence`, `delivery_fee_pence`, `free_delivery_threshold_pence`, `cutoff_time` (HH:MM string), `delivery_weekdays` (ISO 1–7; **empty = any day**). |
| Terms confirmed | EXISTS | `terms_are_placeholders` (inverse). |
| Notes | MISSING | No column. Add `supplier.notes` (Text). |
| **Write: confirm terms** | EXISTS | `POST /api/suppliers/{id}/confirm` (`api/routers.py:240`, `api/views/confirm.py:86`, `services/confirm_terms.py:88`). All six terms together (`SupplierTermsIn`, `api/schemas.py:804`); `delivery_weekdays` min 1. Response: `was_placeholder`, `changed[]` (C3). |
| **Write: edit name/kind/channel/contact/notes** | MISSING | Needs `update_supplier_profile` service + `PATCH /api/suppliers/{id}`. It **must not** accept terms fields: those go only through confirm. |
| **Write: add supplier** | MISSING | New supplier ⇒ `terms_are_placeholders=True` until confirmed. |
| **Write: delete supplier** | MISSING | FK from `purchase_order.supplier_id`, `supplier_product`, `ingredient_price.supplier_id`, `tesco_routing.would_be_supplier_id`. Hard delete would orphan history, so archive instead (C12). Needs `supplier.archived_at`. |
| Products table (read) | DERIVABLE | `supplier_product`: `ingredient_id`, `sku`, `pack_size`, `pack_unit`, `price_pence`, `is_preferred`, `moq_packs`, `last_seen_price_at`, `product_url` (`db/models/supplier.py:69-91`). Per-unit and cross-supplier comparison are computable via `domain/units.py` convert; `SqlSourcingRepository.options_for` (`db/repositories/sourcing.py:88`) already builds per-ingredient options. No endpoint. |
| **Write: link/edit/unlink product, set ★** | MISSING | No service creates or edits `SupplierProduct` (only `seed/demo.py:434,470`). Needs `services/supplier_products.py`. Changing the ★ link's price must also write an effective-dated `ingredient_price` row (`db/models/ingredient.py:88`, `supplier_id`, source SUPPLIER_FEED or INVOICE) and trigger the cost rollup (`jobs/cost_rollup.py`). The design's `syncPreferred` mutating the ingredient in place is not allowed (effective-dating, invariant 3's spirit for prices). |

---

## 5. Conflicts with invariants / ARCHITECTURE.md, and resolutions

- **C1: Tier is freely clickable A/B/C** (drawer).
  - Conflicts with: CLAUDE.md §4.7 "Tier A membership is earned, not assigned"; ARCH §2.1
    (oat milk held at B); `domain/tiers.py` header (tier moves are a deliberate human
    decision; nothing automates them).
  - Resolution:
    - Allow A→B, A→C, B→C and C→B freely.
    - B→A only through an explicit `Promote to A` action. Show the gate evidence
      (`would_clear_gate`, clean streak), require a name, and record a reason.
    - Never flip `auto_order_enabled` as part of it: invariant 2 still requires the drift
      history.
    - Render the toggles, but make `A` open a confirm sheet rather than switch instantly.
- **C2: Trust labels.**
  - What the design does:
    - It calls a once-counted ingredient `Drifting` (d1 null → Drifting).
    - It uses `Never counted` for no count.
    - It requires two consecutive <10% readings for `Trusted`.
  - What the backend does:
    - `trust_status` is null when there is no observation.
    - ARCH §8N.1 forbids "never counted" for rows that *have* a count.
    - `trusted` = latest verdict ELIGIBLE, even with a clean streak of 1.
  - Resolution: add a server-computed `trust_label` so both screens and the bot agree:
    - `Trusted` iff verdict ELIGIBLE **and** `clean_streak ≥ required_streak`;
    - `Excluded` iff FORCE_MANUAL;
    - `Drifting` for TUNE_WASTE_FACTOR, or ELIGIBLE with a short streak;
    - `Not yet judged` when there is a count basis but no observation;
    - `Never counted` only when `has_count_basis=false`.
  - Pill colours: `Not yet judged` uses the `Never counted` grey. The sort order puts
    `Not yet judged` beside `Never counted`.
- **C3: "Terms confirmed" is a free checkbox,** and terms fields autosave one at a time.
  - Conflicts with: §10.9b/§8O (all six terms confirmed together, never partially; there is
    no "unconfirm" path).
  - Resolution:
    - Replace the checkbox with a state line using the same red/ink colours and copy.
    - Beside it, a `Confirm these terms` button that POSTs all six fields from the form at
      once to `/api/suppliers/{id}/confirm`. Show `changed[]` afterwards.
    - Editing a terms field on a confirmed supplier re-arms the button; nothing saves
      terms until it is pressed.
    - Unticking is not offered. If it is ever needed, add a service that sets the flag
      back to true, with a reason.
- **C4: Shelf life fields autosave and can be blank.**
  - Conflicts with: `ShelfLifeIn` requires `source` ∈ supplier/packaging and ≥1 day
    (`estimate` is refused, §8O). `shelf_life_days=null` means "does not expire" (§8F.1),
    not "not set". Every perishable is seeded ESTIMATE, so the design's red border on a
    blank field would never fire.
  - Resolution:
    - Red border (`#d4554a`) when `shelf_life.source == 'ESTIMATE' && is_perishable`.
    - Show the estimate value in the input, italic (estimates in italic).
    - Editing reveals a two-option source picker (`Supplier told us` / `Read off the pack`)
      and a `Save` button. On success, show `usable_days_changed_by` in the shelfNote slot,
      e.g. "A single order may now cover 3 more days".
    - Replace the note copy `Not in the workbook…` with: "Estimated — confirm it to make
      the order cap real."
    - "Does not expire" is read-only for now.
- **C5: "Send to Telegram to confirm" creates an order from the web.**
  - Conflicts with: `api/routers.py:8` ("deliberately no route that creates, confirms or
    sends a purchase order").
  - Resolution:
    - Allowed, because it does not breach invariant 1. It persists `PENDING_CONFIRM` (never
      CONFIRMED); confirmation still requires a named human in Telegram, enforced by
      `ck_po_confirmed_requires_human`.
    - Keep the web free of any confirm or dispatch button.
    - Update the router docstring and CLAUDE.md §8.
    - Make it idempotent on (supplier, target_delivery_date): the pre-delivery job may
      already have written a DRAFT. In that case update its `final_packs` and flip it to
      PENDING_CONFIRM + push, rather than creating a second order.
- **C6: `+` on a capped line** can exceed the shelf-life or season cap (invariant 4).
  - Resolution: allow it (the owner decides, and the bot allows it too via `adjust_packs`,
    `bot/views.py:375`), but make the override explicit:
    - when `packs > suggested` on an `is_capped` line, the cap text turns into `Over the
      {n}-day shelf-life cap by {k} packs`;
    - the value is stored as `final_packs ≠ suggested_packs`.
  - Never silently raise past the cap.
- **C7: Top-up candidates use "shelf > 30 days or none".**
  - Conflicts with: invariant 5 ("never perishables"). `is_perishable` = has a shelf life
    at all, which is 100 of 113 ingredients (§8F.1).
  - Resolution: candidates come from the server with `is_perishable=false` only. If that
    set is too thin in practice, define a named `LONG_LIFE_DAYS` threshold in
    `domain/ordering.py` and document the change in ARCHITECTURE (it is a policy change,
    not a UI tweak).
- **C8: The design refuses to order uncounted items;** the backend orders against
  ledger-only stock.
  - Resolution: adopt the design. Invariant 6's own note ("not good enough to order
    against", `api/views/stock.py:430`) supports it. Add a `NO_COUNT` skip reason in
    `domain/ordering.py` plus `uncounted[]` on the response.
- **C9: The design's estimate model** is `count + deliveries − rate×days − write-offs`, and
  its run-out date is `est/rate` with no confidence check.
  - Conflicts with: §5.1 (ledger) and invariant 9.
  - Resolution:
    - Always use `on_hand.qty` and `run_out`.
    - A withheld run-out shows `forecast.reasons[0]` in the Runs-out cell (13px
      `#5b6475`, ellipsised with a title attribute). The date is **absent**, not greyed.
    - Rows with `has_count_basis=false` show `—` in Left (est.). They must never show a
      `~qty` that looks like a real estimate.
- **C10: Who counted / received / answered.**
  - Conflicts with: `record_count`, `receive_*` and `record_checklist_answer` all require a
    non-blank name, but the app has one shared password (§1 non-goals) and no identity.
  - Resolution:
    - A one-time "Who's using this?" name field, stored per device in `localStorage` and
      editable in the header.
    - Sent as `counted_by` / `received_by` / `responded_by` / `requested_by`.
    - Any write action is disabled until it is set.
- **C11: Write-off reason "Went out of date".**
  - Conflicts with: the `EXPIRED` movement type is *derived*. The expiry sweep writes it,
    and `rebuild_batches(purge=True)` deletes all of them (invariant 12's exception).
  - Resolution:
    - A manual "went out of date" is a `WASTE` movement with `reason_code=WENT_OFF`.
    - `Spilled` → WASTE/SPILLED, `Staff` → `STAFF`, `Other` → WASTE/OTHER (note required).
    - Needs a `reason_code` column on `stock_movement` (migration), or a structured
      prefix in `note`. Prefer the column, since a code is what the attribution and the
      bot branch on (ARCH §8M).
    - Drift attribution should count WENT_OFF as expiry-type loss.
- **C12: Delete supplier** is an instant hard delete that cascades links.
  - Resolution: archive (`archived_at`), with a confirm step, refused while an open PO
    exists. Linked products stay for history but drop out of sourcing.
- **C13: Channel vocabulary.** The design has `Portal, Email, Phone, In store, Message, EDI`;
  the backend has `PORTAL, EMAIL, EDI, MANUAL, BROWSER_AGENT`.
  - Resolution:
    - Show backend values with labels: PORTAL "Portal", EMAIL "Email", EDI "EDI", MANUAL
      "Phone / in store / message", BROWSER_AGENT "Portal (browser agent)".
    - Phone and Message are distinctions the adapters do not act on. If the owner wants
      them, add them as a display-only `contact_method` rather than new `OrderChannel`
      values, which `adapter_for` dispatches on.
- **C14: Pending-basket detection is by "created today".**
  - Resolution: match a persisted open PO on (supplier, `target_delivery_date`), which is
    the pre-delivery job's own idempotency key (`jobs/pre_delivery_orders.py:231`).
  - Status mapping:
    - DRAFT (job-built, visible in `/orders`) → `Draft (in Telegram /orders)`;
    - PENDING_CONFIRM → `Waiting in Telegram`;
    - CONFIRMED → `Confirmed`;
    - SENT → `Sent`;
    - RECEIVED → `Received`;
    - CANCELLED → `Cancelled`.
  - Retail runs → `Bought`. That is not a PO status; it comes from `tesco_routing` +
    ad-hoc receipts.
- **C15: Tesco premium "is a guess (25%)".**
  - Conflicts with: the backend prices retail from Tesco's real supplier products (Tesco
    has real terms, §4.4) and reports `premium_pence` (floored) and `raw_premium_pence`.
  - Resolution:
    - Box total = Σ retail line cost.
    - Footer copy: `Retail premium over the usual supplier: {£premium}. Every shop run is
      logged in Shop runs.` When the total is null (unpriced), say so; never show 0.
    - Surface `retail_is_cheaper` as a sourcing finding, not a saving.
- **C16: "Received → stock" in one click, with assumed expiries.**
  - Conflicts with: receiving needs a name and should take the carton date (the service
    flags assumed dates).
  - Resolution: clicking opens an inline receive sheet under the row. For each line:
    received packs (defaults to `final_packs`), a use-by date (defaults to today +
    shelf-life days, labelled "assumed" until edited), and `Receive`. Assumed-expiry
    batches show `use by … (assumed)` in the drawer's batch table.
- **C17: The batch table caption.** "No deliveries recorded since the last count" assumes
  a since-count filter. The API returns open (non-empty) batches.
  - Resolution: show open batches. Empty-state copy: `No batches with stock left.` If
    `batch_coverage_gap ≠ 0`, add one 13px line: `{gap} on hand that no delivery accounts
    for; it can't expire or be written off until counted.`
- **C18: Money and quantity inputs use `parseFloat`**, and the stepper does
  `(+v + step).toFixed(3)`.
  - Conflicts with: invariant 11 and CLAUDE §10.10.
  - Resolution:
    - Pounds inputs go through `components/confirm/numbers.ts poundsToPence` (it refuses
      more than 2 dp).
    - Quantities stay strings, handled with `lib/dec.ts` (`add`, `sub`, `cmp`); the
      stepper adds a `Dec` step.
    - EACH quantities refuse fractions, matching the bot (`bot/formatters.py:1725`).
- **C19: Estimated costs in the aggregates.**
  - The soon-line value, the waste-line value, basket totals built from ESTIMATE prices,
    and the supplier table's per-unit price all must:
    - show italic / "est." when the underlying `Cost.is_estimate`;
    - show `—` + a reason, never £0.00, when a figure is unknown (invariant 8).
  - `summary.expiring_value_pence = null` must render as `value unknown (unpriced batch)`.
- **C20: Autosave on every keystroke + "Saved".**
  - Resolution: supplier profile fields save on blur (PATCH). Terms and shelf life save only
    via their explicit confirm actions (C3, C4). `savedLabel` shows `Saved` after a
    successful write and an error string on refusal (reuse `lib/api.ts`
    `write`/`refusalMessage`).
- **C21: Weekdays.** The design uses 0–6 with Mon = 0, and treats an empty set as "not
  set" while its `nextDel` treats empty as any day. The backend uses ISO 1–7, empty = any
  day (walk-in).
  - Resolution:
    - Convert at the edge.
    - Show `any day` instead of `not set` when empty on Tesco/Amazon-style suppliers.
    - Confirming requires at least one day (the server enforces it).
- **C22: Cut-off as free text** ("12:00 day before").
  - Resolution: a `HH:MM` time input. "day before" is derived from `lead_time_days` and
    shown read-only (`Order by 14:00, the day before delivery`).
- **C23: Send pushes a proposal into the Agents feed** as agent "Order builder".
  - The order builder is deterministic (§9), and `agent_action_log` is for the bounded
    agent.
  - Resolution: do not write `agent_action_log`. The Agents screen may *read* pending
    confirmations from `GET /api/orders?status=PENDING_CONFIRM` instead.

---

## 6. Proposed API (TypeScript)

The existing shapes are kept; additions are marked `// NEW`. Conventions:

- money: integer pence, or an exact decimal string of pence (`Pence`);
- quantities: decimal strings;
- dates: ISO.

```ts
type Qty = string;            // decimal string, never a number
type Pence = string;          // exact decimal pence where derived; integer pence use `number`
type ISODate = string; type ISODateTime = string;
type Unit = 'L'|'KG'|'ML'|'G'|'EACH';
type TrustLabel = 'trusted'|'drifting'|'excluded'|'not_yet_judged'|'never_counted';

// ---------- Stock ----------
// GET /api/stock?as_of=today&include_untracked=true      (changed)
interface StockRow {            // existing fields unchanged (api/schemas.py:568)
  ingredient_id: number; name: string; tier: 'A'|'B'|'C'; unit: Unit;
  tracking_enabled: boolean; waste_factor: Qty; unit_cost: Cost;
  on_hand: OnHand; shelf_life: ShelfLife; batches: Batch[];
  batch_qty: Qty; batch_coverage_gap: Qty;
  soonest_expiry_days: number|null; is_short_dated: boolean;
  drift: Drift; run_out: RunOut;
  category: string|null;                                   // NEW
  trust_label: TrustLabel;                                 // NEW (C2)
  checklist: { status: 'OK'|'LOW'; responded_at: ISODateTime; responded_by: string }|null; // NEW
  since_count: { delivered: Qty; sold: Qty; wasted: Qty; expired: Qty; adjusted: Qty }|null; // NEW
  par: { min_qty: Qty; max_qty: Qty; safety_days: Qty }|null;                               // NEW
}
interface Batch {               // existing + NEW
  batch_id: number; qty_remaining: Qty; unit: Unit; received_at: ISODateTime;
  expires_at: ISODateTime|null; opened_at: ISODateTime|null; effective_expiry: ISODateTime|null;
  days_left: number|null; unit_cost_pence: Pence|null; value_pence: Pence|null;
  qty_received: Qty;            // NEW
  expiry_assumed: boolean;      // NEW
}
interface StockSummary {        // existing + NEW
  ingredients: number; unanchored: number; negative: number; short_dated: number;
  unbatched: number; auto_order_enabled: number; forced_manual: number;
  expiring_value_pence: Pence|null; notes: string[];
  short_dated_batches: number;                                          // NEW
  written_off_month: { value: Cost; count: number; month: string };     // NEW ('2026-09')
}

// GET /api/stock/{id}   (changed)
interface StockDetail {
  row: StockRow; drift_history: DriftObservation[];
  suggested_waste_factor: Qty|null; templates_using: string[];
  counts: { stock_count_id: number; counted_at: ISODateTime; counted_qty: Qty;
            counted_by: string; drift_pct: number|null /* null = first */ }[];      // NEW
}

// POST /api/stock/{id}/counts                                       NEW -> services/record_count.record_count
interface CountIn { counted_qty: Qty; counted_by: string; counted_at?: ISODateTime; note?: string }
interface CountOut {
  ingredient_id: number; stock_count_id: number; counted_qty: Qty; counted_at: ISODateTime;
  theoretical_before: OnHand;             // the "expected" figure, authoritative
  drift_pct: number|null;                 // null on first count
  verdict: 'ELIGIBLE'|'TUNE_WASTE_FACTOR'|'FORCE_MANUAL'|null;
  trust_label: TrustLabel; auto_order_enabled: boolean; gate_reason: string;
  alert_level: 'NONE'|'NOTICE'|'ALARM';
  attribution: DriftAttribution|null;
  reconciliation_note: string|null; notes: string[];
}

// POST /api/stock/{id}/deliveries                                  NEW -> receive_adhoc
interface AdhocDeliveryIn { qty: Qty; received_by: string; expires_on?: ISODate;
                            unit_cost_pence?: Pence; note?: string }
interface DeliveryOut { batch_id: number; qty: Qty; expires_at: ISODateTime|null;
                        expiry_assumed: boolean; warnings: string[] }

// POST /api/stock/{id}/write-offs                                  NEW -> services/record_write_off (MISSING)
interface WriteOffIn { qty: Qty; reason: 'WENT_OFF'|'SPILLED'|'STAFF'|'OTHER';
                       recorded_by: string; note?: string /* required for OTHER */ }
interface WriteOffOut { movement_ids: number[]; batches_drawn: { batch_id: number; qty: Qty }[];
                        value: Cost; shortfall_qty: Qty /* not covered by any batch */ }

// POST /api/stock/{id}/checklist                                   NEW -> record_checklist_answer
interface ChecklistIn { status: 'OK'|'LOW'; responded_by: string }

// PUT /api/stock/{id}/par                                          NEW (MISSING service; never touches auto_order_enabled)
interface ParIn { min_qty: Qty; changed_by: string }

// POST /api/ingredients/{id}/tier                                  NEW (MISSING service, C1)
interface TierIn { tier: 'A'|'B'|'C'; changed_by: string; reason: string }
interface TierOut { ingredient_id: number; tier_before: string; tier_after: string;
                    would_clear_gate: boolean; auto_order_enabled: boolean; note: string }

// POST /api/ingredients/{id}/shelf-life    EXISTS, unchanged (ShelfLifeIn / ShelfLifeResponse)

// ---------- Orders ----------
// GET /api/orders/draft   (changed)
interface OrderLine {           // existing (api/schemas.py:870) + NEW
  /* ...existing fields... */
  cap_kind: 'SHELF_LIFE'|'SEASON_END'|'OUT_OF_SEASON'|null;   // NEW
  daily_rate_qty: Qty|null;                                   // NEW, null when forecast withheld
  supplier_product_id: number;                                // NEW (needed to send)
}
interface SupplierOrder {       // existing (api/schemas.py:953) + NEW
  /* ...existing fields... */
  order_by_date: ISODate;                                     // NEW
  next_delivery_date: ISODate;                                // NEW
  fee_applies: boolean;                                       // NEW
  top_up_candidates: { ingredient_id: number; name: string; supplier_product_id: number;
                       pack_size: Qty; pack_unit: Unit; pack_price_pence: number;
                       cover_days: Qty|null }[];              // NEW, non-perishable only
  persisted: PersistedOrderRef|null;                          // NEW (C14)
}
interface PersistedOrderRef { po_id: number;
  status: 'DRAFT'|'PENDING_CONFIRM'|'CONFIRMED'|'SENT';
  confirmed_by: string|null; confirmed_at: ISODateTime|null; total_pence: number;
  lines: { po_line_id: number; ingredient_id: number; ingredient_name: string;
           suggested_packs: number; final_packs: number; unit_price_pence: number;
           pack_size: Qty; pack_unit: Unit }[] }
interface DraftOrdersResponse { /* existing */
  uncounted: { ingredient_id: number; name: string }[];      // NEW (C8)
  unsourced: { ingredient_id: number; name: string }[];      // NEW
}

// POST /api/orders/send                                            NEW (C5) -> PENDING_CONFIRM + Telegram push
interface SendOrderIn {
  supplier_id: number; order_date: ISODate; target_delivery_date: ISODate; requested_by: string;
  lines: { ingredient_id: number; supplier_product_id: number; packs: number;
           suggested_packs: number /* 0 for a hand-added top-up */ }[];
}
interface SendOrderOut { po_id: number; status: 'PENDING_CONFIRM'; total_pence: number;
                         reused_existing_draft: boolean; telegram_sent: boolean; notes: string[] }

// GET /api/orders?status=&supplier_id=&limit=                      NEW
interface PurchaseOrderOut {
  po_id: number; supplier_id: number; supplier_name: string;
  status: 'DRAFT'|'PENDING_CONFIRM'|'CONFIRMED'|'SENT'|'RECEIVED'|'CANCELLED';
  created_at: ISODateTime; target_delivery_date: ISODate;
  confirmed_by: string|null; confirmed_at: ISODateTime|null; sent_at: ISODateTime|null;
  total_pence: number; delivery_fee_pence: number; terms_were_placeholders: boolean;
  lines: { po_line_id: number; ingredient_id: number; ingredient_name: string;
           suggested_packs: number; final_packs: number; unit_price_pence: number;
           pack_size: Qty; pack_unit: Unit; received_qty: Qty|null;
           received_expires_at: ISODateTime|null; cap_reason: string|null; is_top_up: boolean }[];
  actions: ('cancel'|'mark_sent'|'receive')[];      // server decides, from status
}
interface OrdersListResponse { orders: PurchaseOrderOut[];
  counts: { open: number; waiting: number } }         // header meta + nav badge

// POST /api/orders/{po_id}/cancel      NEW   { cancelled_by: string; reason?: string }
// POST /api/orders/{po_id}/mark-sent   NEW   { sent_by: string }            -> repo.mark_sent
// POST /api/orders/{po_id}/receive     NEW   -> receive_po_line per line
interface ReceiveIn { received_by: string;
  lines: { po_line_id: number; received_packs?: number; received_qty?: Qty; expires_on?: ISODate }[] }
interface ReceiveOut { po_id: number; status: string; receipts: DeliveryOut[] }

// POST /api/orders/shop-run            NEW (Log as bought)  -> receive_adhoc + record_emergency_routing
interface ShopRunIn { bought_by: string; where: string /* 'Tesco' */;
  lines: { ingredient_id: number; qty: Qty; paid_pence?: number; expires_on?: ISODate }[] }

// GET /api/orders/shop-runs?months=8   NEW (tesco_routing now; + expenses when Finance lands)
interface ShopRunsResponse {
  runs: { occurred_at: ISODateTime; where: string; note: string; ingredient_name: string|null;
          amount_pence: number|null; premium_pence: number|null; source: 'tesco_routing'|'expense' }[];
  by_month: { month: string; amount_pence: number|null }[];   // null when any run unpriced
  total_pence: number|null; priced_runs: number; runs_count: number;
}

// ---------- Suppliers ----------
// GET /api/suppliers   (changed)
interface SupplierOut { /* existing (api/schemas.py:636) */
  kind: string|null; contact: string|null; order_url: string|null; notes: string|null; // NEW
  product_count: number; archived: boolean;                                            // NEW
}
// POST /api/suppliers                   NEW { name, kind?, order_channel, contact?, notes? } -> placeholders=true
// PATCH /api/suppliers/{id}             NEW { name?, kind?, order_channel?, contact?, order_url?, notes? } (no terms)
// POST /api/suppliers/{id}/archive      NEW { archived_by: string }
// POST /api/suppliers/{id}/confirm      EXISTS, unchanged (all six terms)

// GET /api/suppliers/{id}/products      NEW
interface SupplierProductOut {
  supplier_product_id: number; ingredient_id: number; ingredient_name: string;
  ingredient_unit: Unit; sku: string; pack_size: Qty; pack_unit: Unit; price_pence: number;
  unit_price_pence: Pence|null;           // per ingredient unit, via domain/units.convert
  price_source: 'INVOICE'|'ESTIMATE'|'SUPPLIER_FEED'|null;
  is_preferred: boolean; moq_packs: number; last_seen_price_at: ISODateTime|null;
  vs_best_other: { supplier_name: string; unit_price_pence: Pence; diff_pct: number }|null; // null = only supplier
}
// POST /api/suppliers/{id}/products     NEW { ingredient_id, sku, pack_size: Qty, pack_unit, price_pence, changed_by }
// PATCH /api/supplier-products/{id}     NEW { sku?, pack_size?, pack_unit?, price_pence?, changed_by }
//   -> if preferred and price/pack changed: new effective-dated ingredient_price + cost rollup; response
//      carries { recosted_items: number } (the design's "updates the cost of N menu items").
// POST /api/supplier-products/{id}/prefer   NEW { changed_by }
// DELETE /api/supplier-products/{id}        NEW (soft: refused if referenced by an open PO)
```

**Migrations implied:**

- `supplier.kind`, `supplier.notes`, `supplier.archived_at`;
- `purchase_order.cancelled_by`, `purchase_order.cancelled_at`;
- `stock_movement.reason_code`;
- optionally `stock_batch.expiry_source`, replacing the `EXPIRY ASSUMED` note prefix;
- `supplier_product.archived_at`.

Every new write lives in `services/` (CLAUDE §8), is reached from the API through
`asyncio.to_thread` (`api/runtime.in_session`), and uses a `In` model with
`extra="forbid"`.

---

## 7. The existing frontend this replaces

Replaced screens:

- `web/src/screens/Stock.tsx` (tier-A list)
- `DriftHistory.tsx` (SVG drift chart panel)
- `ConfirmShelfLife.tsx`
- `Orders.tsx` + `screens/orders/*` (read-only run viewer with three recorded runs)
- `screens/money/SupplierTerms.tsx` + `ConfirmTerms.tsx` (supplier terms currently live
  under Money)

Routing is `useState` in `App.tsx:28` (no URLs). The `ScreenName` union is in
`components/shell.tsx:16`. There is no `web/src/screens/confirm/` directory; the confirm
helpers are in `web/src/components/confirm/`.

Keep:

- `lib/dec.ts`, all of it. BigInt fixed point: `parseDec`, `add/sub/mul`, `cmp`, `toFixed`,
  `trimQty`, `sharePct`. Use it for the count preview, the stepper and every sum.
- `lib/format.ts`: `money`/`moneyDec`, `pence`, `pct`, `plural`, `costView(Cost)` (the
  missing-vs-figure and estimate split, invariant 8). The date helpers print `19 Sep`,
  whereas the design shows en-GB `Fri 26 Sept`. Add `dayWeekday()` (en-GB weekday + day +
  month) rather than changing the existing helpers.
- `lib/expiry.ts` `expiryLossSinceCount`: exact expired × unit-cost total that names the
  uncosted rows.
- `components/confirm/numbers.ts` (`poundsToPence`, `penceToPounds`, `parseDays`,
  `parseCutoff`), `fields.tsx`, `outcome.tsx`, and `lib/api.ts`
  `write`/`WriteResult`/`refusalMessage` plus the query-invalidation lists after each write:
  - after shelf life: `stock`, `stock-detail`, `today`, `orders-draft`;
  - after terms: `suppliers`, `orders-draft`, `today`.
- `components/attribution.tsx` `trustLabel` (the four-state rule, null ≠ trusted) and
  `components/onhand.tsx`'s rule that a counted figure is only printed inside a dated
  phrase. Restyle both; keep the logic.
- The withheld-forecast rule (`Stock.tsx:320`, `orders/Derivation.tsx:70`: `qty==null ||
  is_low_confidence` → reason in place of the number). Also the exact-decimal sort
  comparators (`Stock.tsx:54-81`, null run-out last).
- From `screens/orders/data.ts`:
  - `sumPence`;
  - `weekdayNames` (ISO, empty = "any day");
  - `prose` (`--` → —);
  - `channelLabel`;
  - the "totals disagree" check (`Orders.tsx:256`).
- `orders/notes.ts` note bucketing, and `orders/Urgent.tsx`'s premium vs raw-premium
  handling (`retail_is_cheaper`).
- Merge the duplicate order types in `screens/orders/data.ts:22-160` into `lib/types.ts`.
  The data.ts set is the one that matches the API: premiums are `string|null`, and
  `cover_days` is nullable.

Disposable: all layout and grid CSS, StatCards, the SVG chart styling, the recorded-run
tabs and the long explanatory copy.

Fixtures: regenerate with `uv run cafeops api-fixtures --out web/fixtures` after the API
changes. The screens are built against those, per CLAUDE §10.10.
