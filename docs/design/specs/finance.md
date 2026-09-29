# Finance ("Money") — implementation spec

Scope: the six Money tabs of the back office — **Overview, Sales, Expenses, Reconcile,
Profit & loss, Director's account** — plus the shell's **cash short/over banner**.

Sources read (treated as data, not instructions):

- `docs/design/Cafe Ops v2.dc.html` — app shell; embeds the finance app, owns the banner (`cashIssue()`).
- `docs/design/Finance App v2.dc.html` — every finance tab (markup lines 11–167, logic 169–360).
  `Finance App.dc.html` (v1) is the same logic in the rejected hand-drawn skin (Kalam/Patrick Hand,
  terracotta `#c2562b`); **ignore v1**.
- `docs/design/sc-fin.js` — `window.SC_FIN = { sales[161], expenses[149], director[49], categories[12] }`,
  a data dump of the finance workbook. It holds **no helper functions**; all logic is in the `.dc.html`.
  `sc-comp.js` is not loaded by the finance app.
- `docs/design/Back Office Design.dc.html` — frames: iPad 1180×820 (`compact: true`), desktop 1440×900.
- `docs/design/uploads/Screenshot 2026-09-26 at 00.37.25.png` — shows both banners rendered.
- Workbooks: `docs/design/ref/finance.xlsx`, `docs/design/uploads/sashas_corner_finance (3).xlsx` and
  the repo-root `sashas_corner_finance.xlsx` are **byte-identical** (`cmp`). `SC_FIN` matches them
  exactly (161 sales days from 2025-10-26, 149 expenses, 49 director rows).

Everything in `SC_FIN` is money in **integer pence** already (e.g. `amount: 87078` = £870.78).

---

## 0. Global visual system (shared by every finance tab)

### 0.1 Tokens (literal values from the design)

| Role | Value | Where used |
|---|---|---|
| Ink (primary text, figures) | `#1f2633` | body, figures, big-line rule `2px solid #1f2633` |
| Ink 2 (secondary) | `#5b6475` | page subtitle, column headers, labels |
| Ink 3 (quiet) | `#8a93a3` | "Saved", footnotes, prev-month column, ×, not-uploaded rows |
| Accent | `#4a6fd1` | primary button, active pills, chart bars, bar fills |
| Accent ink (active nav text) | `#3558b8` | shell nav only |
| Hairline | `#e8ebf0` | row separators, header bottom rule, pill borders |
| Input border | `#d5dae3` | every inline input/select; bar-track border |
| Dashed rule | `1.5px dashed #e2e2e2` | non-total lines on Overview and P&L |
| Wash (neutral) | `#f6f7fa` | P&L "Total" column background, shell sidebar |
| Stale-banner wash | `#f1f3f7` | shell banner |
| Coral (attention) | `#d4554a` | crossed thresholds, flagged rows, cash-banner dot |
| Coral wash | `#fdf1ef` | flagged row background, cash banner background |
| Page background | `#fff` | |

Font: **Nunito** 400/500/600/700/800 (Google Fonts), `font-variant-numeric: tabular-nums`,
`-webkit-font-smoothing: antialiased`. Inputs and selects inherit Nunito, colour `#1f2633`, bg `#fff`.
Links `#1f2633`, hover `#4a6fd1`. Interactive pills/buttons have `transition: background .15s, border-color .15s`.

Headline style `font: 800 <size> 'Nunito'; letter-spacing: -.01em` — call it **H(size)** below.

### 0.2 Number formatting (verbatim helpers)

```js
gbp  = p => (p < 0 ? '−£' : '£') + (Math.abs(p)/100).toLocaleString('en-GB',{minimumFractionDigits:2,maximumFractionDigits:2}) // "£1,234.56", "−£12.00" (U+2212)
gbp0 = p => (p < 0 ? '−£' : '£') + Math.round(Math.abs(p)/100).toLocaleString('en-GB')                                        // "£1,235"
pct  = x => x == null || !isFinite(x) ? '—' : Math.round(x*100) + '%'
mLabel = 'YYYY-MM' -> 'Apr 26'          // MN[month-1] + ' ' + yy
fd   = iso -> toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short'})   // "Fri 20 Mar"
```

Editable money inputs display **plain `12.34`** (no £, no separators) — `(p/100).toFixed(2)` — and keep
the user's raw string while typing (`<field>Str`). **The design parses with `parseFloat`**
(`pen = v => Math.round(parseFloat(v.replace(/[£,]/g,''))*100)`); the build must parse the string as an
exact decimal (invariant 11, see §4). Empty/garbage → the design stores 0; the build should reject
non-numeric input in place (red input border `#d4554a`) and keep the last good value.

### 0.3 Finance frame (all tabs)

Container: `display:flex; flex-direction:column; height:100%; overflow:hidden; background:#fff`.

**Top bar** — `display:flex; align-items:center; gap:18px; padding:12px 20px; border-bottom:1px solid #e8ebf0`.

- Embedded (the only mode the shell uses): **page title** H(21px) + **subtitle** 14px `#5b6475`, side by side.
  Titles/subtitles (`FPAGE`, verbatim):

  | tab | title | subtitle |
  |---|---|---|
  | overview | **Money** | how the month is going |
  | sales | Sales | one row per trading day |
  | exp | Expenses | everything paid out |
  | rec | Reconcile | does the money that should arrive actually arrive? |
  | pl | Profit & loss | worked out from sales and expenses |
  | dir | Director's account | money in from you, money out to you |

  Note the Overview's bar title is "Money", not "Overview" (the nav label is "Overview").
- Standalone mode (not used by the shell; implement only if a standalone route is wanted): title
  "Sasha's Corner · Money" H(19px) + tab pills (`border:1px solid #e8ebf0; radius:16px; padding:6px 14px;
  16px`; active bg `#4a6fd1` fg `#fff`).
- Spacer `flex:1`.
- **Save status**: 14px `#8a93a3`. Empty initially; `"Saved"` after any successful mutation;
  `"Loading workbook…"` while data loads. Build: show "Saving…" while a write is in flight, "Saved" on
  success, and on failure coral text "Not saved — <reason>" (the design has no error state).
- **Primary button** (Sales, Expenses, Director only): bg `#4a6fd1`, `#fff`, radius 18px,
  padding `9px 16px`, 16px/700, nowrap. Labels: `+ Add a day` · `+ Add expense` · `+ Add entry`.

**Month bar** (Overview, Sales, Expenses, Reconcile; **hidden** on P&L and Director):
`display:flex; gap:6px; align-items:center; padding:10px 20px; border-bottom:1px solid #e8ebf0;
overflow-x:auto` (scrolls horizontally when it overflows; pills `flex:none`).
Leading label "Month" 14px `#5b6475`, `margin-right:4px`. Pills: `border:1px solid #e8ebf0;
radius:12px; padding:6px 14px; 14px`; active bg `#4a6fd1` fg `#fff`, else transparent/`#1f2633`.
Order: **All**, then every month with any sale **or** expense, ascending (`Oct 25 … Apr 26`).
Default selection: the **latest month that has sales** (not expenses). Selection is shared across the
four tabs (one state), not persisted.

Body: `flex:1; min-height:0; overflow-y:auto`.

### 0.4 Inline-editing grammar (Sales, Expenses, Director, Reconcile inputs)

Every row is always in edit mode. Inputs: `border:1px solid #d5dae3; radius:10px; padding:2px 6px;
font-size:14px; min-width:0` (dates `padding:2px 4px; 13px`; selects `padding:2px; 13px`; numbers
right-aligned). Each change commits immediately (design: on every keystroke → localStorage → "Saved").
Build: commit on **blur / Enter** (and on change for selects, date pickers and toggles), debounced;
optimistic UI; roll back + coral message on failure. Row delete is a bare `×` (color `#8a93a3`,
centered, 20px column) with **no confirmation** in the design — build must add undo ("Deleted · Undo",
5 s) because these are money records.

Grid rows: `display:grid; gap:10px (8px on Expenses/Director); padding:5px 24px;
border-bottom:1px solid #e8ebf0; align-items:center; font-size:14px`.
Header rows: same grid, `padding:8px 24px; color:#5b6475; font-size:13px`.

### 0.5 Compact (iPad 1180×820) vs desktop (1440×900)

The shell passes `compact` only to Menu/Stock; **the finance app receives no compact prop**. The only
difference is the shell sidebar: **200px** (compact) vs **232px** (desktop). Content width is therefore
≈980px (iPad) and ≈1208px (desktop). Everything in finance is fluid (`minmax(0, fr)` tracks), except:

- Reconcile tables are capped `max-width:980px` (≈ full width on iPad).
- P&L grid has `min-width:900px` inside `overflow-x:auto`; with 7 months + Total it fits both widths
  (≥ 200 + 8×90 = 920 → a few px of horizontal scroll on iPad is possible; acceptable, it is the design).
- Overview left column is ≈393px on iPad: label track = 393 − 2×110 − 20 ≈ 153px, so
  "Delivery apps (customers paid)" and "Net profit (before tax)" wrap to two lines at 17/20px. Allowed;
  keep numbers top-aligned (`align-items:start` would be better than the implicit stretch).
- Expenses at 980px: fr unit ≈ 72px; category select ≈ 80px shows truncated names ("Stock — fo…"). Acceptable
  per design; add `title` tooltips with the full value.

Tap targets: rows are ~30px tall (design choice for dense tables); pills/buttons ≥32px.
CLAUDE.md asks for 375px survival — the design has no phone layout; see §4.9.

---

## 1. Screens

### 1.1 Overview (`m-overview`)

Layout: `padding:20px 24px; display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.3fr); gap:28px`.

#### Left column — the month statement

- Title H(22px), `margin-bottom:4px`: `"April 2026"` (`toLocaleDateString('en-GB',{month:'long',year:'numeric'})`)
  or `"Everything so far"` when All.
- Sub 14px `#5b6475`, `margin-bottom:14px`: `"{days} trading days entered · {n} expenses"`
  (days = sales rows in period with total > 0; n = all expenses in period incl. capital/drawings).
- Lines: `display:grid; grid-template-columns:minmax(0,1fr) 110px 110px; gap:10px; padding:6px 0`.
  Col 1 label (optionally `padding-left:14px`), col 2 value right-aligned, col 3 previous month
  right-aligned 14px/400 `#8a93a3`.
  - **Big line**: 20px/700, `border-bottom:2px solid #1f2633`.
  - **Normal line**: 17px/400, `border-bottom:1.5px dashed #e2e2e2`.
  - **Warn**: value colour `#d4554a` (else `#1f2633`). Previous column never turns coral.
  - Previous-month column is blank when All is selected or when the selected month is the first month.

  Lines, in order (verbatim labels; `c` = this period, `p` = previous month):

  | # | Label | Value | Style | Shown when |
  |---|---|---|---|---|
  | 1 | Takings | `c.revenue` | big | always |
  | 2 | Card | `c.card` | indent | always |
  | 3 | Cash | `c.sq + c.own` | indent | always |
  | 4 | Delivery apps (customers paid) | `c.chG` | indent | `c.chG` or `p.chG` non-zero |
  | 5 | Stock bought | `−c.cogs` | normal | always |
  | 6 | Stock as % of takings | `pct(c.cogs / c.revenue)` | indent; **warn if > 0.32** | always |
  | 7 | Stock written off | `−c.wo` | indent; **warn if c.wo > 0** | `c.wo` or `p.wo` non-zero |
  | 8 | Gross profit | `c.gp` | big | always |
  | 9 | Delivery app commission | `−c.chC` | indent | `c.chC` or `p.chC` non-zero |
  | 10 | Delivery app ads | `−c.chA` | indent | same as 9 |
  | 11… | each OPEX category (Rent, Utilities, Wages, Marketing, Square fees, Other) | `−byCat[k]` | indent | that category non-zero in c or p |
  | last | Net profit (before tax) | `c.net` | big; **warn if < 0** | always |

  Values use `gbp` (pence precision), percentages `pct`.
- Footnote 13px `#8a93a3`, `margin-top:6px`: `"Right column: previous month."` + (if capital or
  drawings in period) `" Not counted as running costs: equipment/setup £{gbp0(capital)}, personal £{gbp0(drawings)}."`

#### Right column (flex column, `gap:18px`)

**A. "Takings, day by day"** — header row `justify-content:space-between; align-items:baseline`:
title H(16px); right text 14px `#5b6475` `"about £{gbp0(revenue/days)} a day"` (blank if 0 days).

Chart (single month): a **column chart, one bar per calendar day** of the month.
- Plot: `display:flex; align-items:flex-end; gap:3px; height:170px; border-bottom:1px solid #e8ebf0; margin-top:8px`.
- Bars: `flex:1`, colour `#4a6fd1`, no radius, height `max(3, v/max*100)%` where `v` = day's total
  (card + cash — **excludes** delivery apps), `max` = the month's max day.
- Day with no row / zero: design sets `height:2px; background:transparent` — i.e. **invisible**, although
  the caption says "dashes". **Build: draw a 2px `#d5dae3` dash** so the caption is true.
- Tooltip (`title`): `"{d}: £123.45"` or `"{d}: nothing entered"`.
- Y axis: none, no gridlines, no value labels (deliberate: numbers live in the left column).
- X axis caption row: `justify-content:space-between; 13px #8a93a3; margin-top:4px`:
  left `"1st"`, centre `"dashes = no sales entered that day"`, right `"{n}th"` where n = days in month.
  **Bug:** yields "31th"; build with an ordinal (`31st`, `30th`, `28th`, `29th`).

Chart (All): one bar per month (revenue incl. delivery gross), height `max(2, v/max*100)%`,
tooltip `"Apr 26: £1,594.45"`; right caption = last month label; **left caption still says "1st" (bug)**
→ build shows the first month label; centre caption should be omitted in this mode.

**B. "Where the money went"** — title H(16px) `margin-bottom:6px`. Up to **6** rows, the period's
**operating** expense categories (COGS + OPEX; capital/drawings excluded), sorted desc by amount.
Row: `grid-template-columns:150px minmax(0,1fr) 90px; gap:10px; padding:3px 0; 14px`.
Label · bar track (`height:14px; border:1px solid #d5dae3`) with fill `#4a6fd1` width `v/max*100%`
(max = largest category) · value `gbp0` right-aligned. Empty period → section shows the title only
(build: add 14px `#8a93a3` "No expenses entered for this month.").

**C. "Needs a look · {count}"** — box `border:1.5px dashed #d4554a; radius:12px; padding:12px 14px`.
Title H(16px) `margin-bottom:4px`. Rows (`display:flex; justify-content:space-between; gap:10px;
14px; padding:3px 0; cursor:pointer`), value colour `#5b6475`:
1. Up to **4** flagged expenses (see flag rule §2.3): label = description, value = `gbp(amount)`;
   click → Expenses tab with **"Needs a look"** filter on.
2. Always: `"Expenses without a receipt"` · count; click → Expenses with **"No receipt"** filter on.

`count = flaggedExpenses + (noReceipt > 0 ? 1 : 0)` (the list shows only 4 even when count is higher).
The box is always rendered, even at 0 — CLAUDE.md §10 ("colour only for crossed thresholds") →
**build: coral dashed border only when count > 0; otherwise `#e8ebf0`**.

Reference figures from the workbook data (for visual QA; delivery rows are the design's demo values,
which must not ship — §2.5):

| Month | Takings | Card | Cash | Stock bought | Stock % | Net |
|---|---|---|---|---|---|---|
| Mar 26 | £3,027.31 | £2,405.44 | £31.77 | −£879.55 | 29% | £272.88 |
| Apr 26 (default) | £1,594.45 | £1,293.05 | £0.00 | £0.00 | 0% | £1,484.03 |

April has sales but **zero expenses entered**, so it shows a large profit that is an artefact of
missing data — see the completeness rule in §4.6.

### 1.2 Sales (`m-sales`)

> **Superseded for cash (DECISIONS 26, owner 2026-09-28):** the built Sales tab has ONE
> "Cash" column and one "Cash" field in its "Add cash" drawer. The design's separate
> "Square cash" / "Own cash" columns below (`sq`, `own`) are the design reference only;
> wherever this spec says `sq + own`, the app has a single `cash_pence`.

Grid (header, rows, total): `grid-template-columns:120px 60px repeat(4,minmax(0,1fr)) 80px 90px minmax(0,1.4fr) 20px`.
Header row is `position:sticky; top:0; background:#fff`.

Headers (verbatim): `Date · Day · Card £ · Square cash £ · Own cash £ · Total · Orders · Avg ticket · Note · (blank)`
(money/number headers right-aligned).

Row cells:
1. `<input type=date>` — editing the date moves the row (re-sorts; may leave the month).
2. Day `Mon`…`Sun`, `#5b6475`.
3–5. Card / Square cash / Own cash: money inputs, right-aligned.
6. Total `gbp(card+sq+own)` (read-only, right).
7. Orders: integer input, placeholder `—`, empty when 0.
8. Avg ticket `gbp(total/tx)` or `—` when tx = 0, `#5b6475`.
9. Note: text input, placeholder `—`.
10. `×` delete.

Rows: sales in the selected month (or all), **ascending by date**.
Total row: `padding:10px 24px; 15px/700; border-top:1px solid #e8ebf0`:
`"{n} days"` · blank · Σcard · Σsq · Σown · Σtotal (all `gbp`) · Σtx (blank if 0).

**+ Add a day**: new row dated the day after the last row in view; if the view is empty, the 1st of the
selected month (or today on All). Values 0, note ''. If the new date falls outside the selected month,
the month selector jumps to it.

Empty month: header + `0 days` total row only. Build: add 15px `#8a93a3` centred
"No days entered for this month." (padding 40px) above the total.

Validation (build): one row per date (the workbook has one row per trading day) — adding or re-dating
onto an existing date is refused inline ("There is already a row for Tue 3 Mar"). Negative money refused.

Data note: in the workbook `tx` is 0 on every row and Own cash is 0 on every row; "Just Eat: £x" notes
sit on rows whose Square-cash column holds the Just Eat amount (7 rows, £114.92) — see §2.1.

### 1.3 Expenses (`m-exp`)

**Filter bar**: `display:flex; gap:8px; align-items:center; padding:10px 24px; border-bottom:1px solid #e8ebf0; flex-wrap:wrap`.
1. Search input, width 210px, `border:1px solid #e8ebf0; radius:12px; padding:4px 10px; 14px`,
   placeholder `Search vendor or note` — case-insensitive substring over `desc + ' ' + notes`.
2. Category select: `All categories`, `All stock` (= any COGS category), then each of the 13 categories.
3. Type select: `All types` · `Running costs` (Operating) · `Equipment / setup` (Capital) · `Personal (drawings)` (Drawings).
4. Toggle **Needs a look**: `border:2px solid #d4554a; radius:14px; padding:6px 14px; 14px`;
   off = transparent bg / coral text; on = coral bg / white text.
5. Toggle **No receipt**: `border:1px solid #e8ebf0; radius:14px; padding:6px 14px`; on = `#4a6fd1`/white.
6. Spacer, then summary 15px: `"{n} expenses · "` + **bold** `gbp(Σamount of filtered rows)`.

Header grid: `120px minmax(0,1.1fr) minmax(0,1.8fr) 84px minmax(0,1fr) minmax(0,1.05fr) 40px minmax(0,1.6fr) 20px`, gap 8px.
Headers: `Date · Category · Vendor / description · £ · Paid by · Type · Rcpt · Notes · (blank)`.

Row cells: date input · category select (13 options, see §2.3) · description text · amount money input ·
method select (`Card, Bank transfer, Direct debit, Standing order, Cash, Cash withdrawal`) ·
type select (`Running` / `Equipment` / `Personal` → Operating/Capital/Drawings) · receipt checkbox
(18×18, `border:1px solid #e8ebf0; radius:6px; 12px`, shows `✓` when true, click toggles) ·
notes input (placeholder `—`) · `×`.

Flagged row (§2.3 rule): row bg `#fdf1ef`, notes input border `#d4554a`.
Sort: **descending by date**. Filters combine with AND, within the selected month.

Empty: `"No expenses match these filters."` — `padding:40px; text-align:center; #8a93a3; 15px`.

**+ Add expense**: date = today if today is in the selected month (or All), else the 1st of the month;
category `Stock — food`, desc '', amount 0, method Card, receipt false, notes '', kind Operating.
All filters and the search are **cleared** so the new row is visible. Build: focus the new row's
description input; refuse to persist amount 0 / empty description (keep as a local draft until valid).

Navigation in: Overview's "Needs a look" rows open this tab with the flag toggle on; "Expenses without a
receipt" opens it with No receipt on.

### 1.4 Reconcile (`m-rec`)

Container `padding:18px 24px; display:flex; flex-direction:column; gap:28px`. Three sections.

#### A. "Card takings vs the bank"

Heading row (`align-items:baseline; gap:14px; flex-wrap:wrap`): H(19px) title, then 14px `#5b6475`
inline sentence with two inputs: `Paid out after [2] working days, card fees about [1.75] %`
(inputs `width:34px` and `46px`, centred, `border:1px solid #d5dae3; radius:10px; padding:1px 4px`).
Both are **persisted settings** (defaults 2 and 1.75).

Summary 14px `#5b6475` (`margin:2px 0 8px`):
`"£X expected after fees · £Y arrived · N day(s) don’t match"` or `"No card takings this month."`

Table (max-width 980px) `grid-template-columns:130px 110px 130px 120px 110px minmax(0,1fr)`, gap 10px,
rows `padding:4px 0`. Headers: `Sold on · Card on till · Due in bank · Arrived £ · Difference · (note)`.
One row per sales day in the month with card > 0, ascending.

Per row (verbatim logic):
```js
due      = addWorkingDays(date, lag)            // skips Sat/Sun only
expected = Math.round(card * (1 - fee/100))
arrived  = bank[date] ?? (due <= today ? expected : null)   // !!! assumes arrival
diff     = arrived == null ? null : arrived - expected
```
Cells: `fd(date)` · `gbp(card)` · `fd(due)` · money input (placeholder `not yet`) · diff text
(`not yet` | `ok` when |diff| ≤ £1.00 | `gbp(diff)`) · note 13px `#8a93a3`.
Mismatch (|diff| > 100p): diff colour `#d4554a`, row bg `#fdf1ef`, note
`"less than expected after fees: a refund or chargeback?"`. Else if no real bank figure:
note `"assumed from the payout pattern (bank feed not connected)"`.

**Must change in build:** an unrecorded payout must **not** be counted as arrived (invariant 8 — the
design fabricates "arrived = expected" for past-due days, which makes every unrecorded day read "ok").
Build: arrived null → diff `not recorded` in `#8a93a3`, excluded from the "arrived" total, and the
summary adds `· N not recorded`. Also note that the workbook's card figures for Sep–Mar are
themselves **bank deposits** (README: "Card sales for Sep-Mar are from Mettle Square deposits (date =
deposit date)"), so reconciling them against the bank is circular — those rows render
`"imported from bank deposits"` in the note column and are excluded from the summary (§2.1 `basis`).

#### B. "Delivery apps: what they took vs what you kept"

Heading row: H(19px) title · secondary button `Upload a CSV export` (`border:1px solid #d5dae3;
radius:14px; padding:6px 14px; 14px`) · 13px `#8a93a3` note: before click
`"Deliveroo figures are demo until an export is uploaded."`, after click
`"File picker goes here: CSV from the partner portal (no API access)."` (design stub). Build: a real file
picker → upload → import report inline ("12 days read, 0 rejected"); the "demo" copy disappears
because demo figures are not seeded (§2.5).

Table (`margin-top:8px`, max 980px) `grid-template-columns:90px 110px 110px 110px 110px 110px 80px`.
Headers: `Month · App · Customers paid · Commission · Ads · You kept · Kept`.
Rows: for each month in scope (the selected month, or on All every month that has any channel data) ×
`[Deliveroo, Just Eat]`:
- Data present: month `"04/26"` · app · three money inputs (gross, commission, ads) ·
  `You kept = gbp(gross − comm − ads)` · `Kept = round(kept/gross*100)%` (or `—`).
- Missing: row text `#8a93a3`; cells 3–7 merged: `"not uploaded: missing, not zero"`.
Build: if any of the three is null (unreported), `You kept`/`Kept` show `—` with title "commission not
reported" (existing `ChannelMetric.net_pence` rule).

#### C. "Cash: counted vs the till"

Title H(19px); summary 14px `#5b6475` `margin:2px 0 8px`:
`"{n} days with cash · net difference £X · N day(s) over £5 out"` (blank if none).

**Chart** — diverging bars, one per cash day (ascending): plot `height:90px; max-width:980px;
display:flex; align-items:center; gap:3px; border-bottom:1px solid #e8ebf0`. Each bar slot `flex:1;
height:100%`; bar height `max(1, |d|/vmax*50)%` where `d = counted − till` and
`vmax = max(100p, max|d|)`; colour `#d4554a` if |d| > 500p else `#4a6fd1`; tooltip `"Fri 20 Mar: £12.00"`.
Caption 12px `#8a93a3` `margin:2px 0 10px`: `"bars above the line: more cash than the till says; below: less"`.
The design aligns positive bars to the **top** of the box and negative to the **bottom** and draws no
middle line, which contradicts its own caption. **Build: a zero line at 50% height (1px `#d5dae3`),
positive bars grow up from it, negative down.** Days not yet counted: design treats them as d = 0 and
draws a 1% blue sliver (indistinguishable from "spot on") → build draws nothing for uncounted days.

Table (max 980px) `grid-template-columns:130px 110px 120px 110px minmax(0,1fr)`. Headers:
`Day · Till says · Counted £ · Difference · (note)`. Row: `fd(date)` · `gbp(sq+own)` · money input
(placeholder `not counted`) · diff (`—` if not counted; `spot on` if 0; else `gbp(diff)`) · note.
|diff| > £5.00: diff `#d4554a` **700**, row bg `#fdf1ef`, note `"over £5 out: needs an explanation"`.
Empty: `"No cash taken this month."` 14px `#8a93a3` `padding:10px 0`.

**Missing in design, needed for the banner:** a way to explain a discrepancy. Build: the note cell on a
flagged row becomes a text input (placeholder `why? (e.g. float not topped up)`); once filled the row
keeps its coral diff but loses the coral background, and the banner stops pointing at it (§1.7).

### 1.5 Profit & loss (`m-pl`)

No month bar. Container `padding:18px 24px`.
Heading row (`align-items:baseline; gap:12px; margin-bottom:10px`): H(22px) `Profit & loss` + 14px
`#5b6475` `"Worked out from the sales and expenses logs. Equipment and personal spending are kept out of running costs."`

Matrix: `overflow-x:auto` wrapper → `display:grid; grid-template-columns:minmax(200px,1.6fr)
repeat(M+1, minmax(90px,1fr)); min-width:900px; gap:0`. Columns: label, every month (ascending, all
months with data), **Total**. Cells `padding:5px 8px; white-space:nowrap`; label left, figures right;
Total column bg `#f6f7fa`. Figures `gbp0` (whole pounds), percentages `pct`.

Row styles: header row = 700, `#5b6475`, no border, cells `Oct 25 … Apr 26 · Total`;
big = 17px/700 `border-bottom:2px solid #1f2633`; normal = 16px/400 `1.5px dashed #e2e2e2`.
Warn = cell text `#d4554a` (per cell).

Rows (verbatim):

| Label | Per column | Style / warn | Shown |
|---|---|---|---|
| Card sales | card | | always |
| Cash sales | sq + own | | always |
| Delivery apps (gross) | chG | | always |
| **Takings** | revenue | big | always |
| each COGS category | −byCat | | category non-zero in the Total |
| Stock written off | −wo | warn if wo > 0 | always |
| Stock % of takings | cogs/revenue | pct; warn if > 32% | always |
| **Gross profit** | gp | big | always |
| each OPEX category | −byCat | | non-zero in Total |
| Delivery app commission | −chC | | always |
| Delivery app ads | −chA | | always |
| **Net profit** | net | big; warn if < 0 | always |
| Equipment / setup (not in P&L) | −capital | | always |
| Personal spending (not in P&L) | −drawings | | always |

Footnote 13px `#8a93a3` `margin-top:10px`: `"Stock costs above 32% of revenue are marked. Before tax."`

Note: "Stock % of takings" uses `cogs` **including** write-offs (`cogs2 = cogs + wo`), while the
Overview line of the same name does too — consistent, but the label says "Stock", so write-offs are
counted as stock cost. Keep.

Differences from the workbook's Monthly P&L sheet (intentional in the design, keep): no gross/net
margin % rows, no "Corporation Tax reserve (19%)" / "after tax" rows, months span all data rather than
a Jan–Dec year selector, delivery-app and write-off rows added.

### 1.6 Director's account (`m-dir`)

No month bar. Summary strip `padding:16px 24px 0; display:flex; gap:28px; align-items:baseline; flex-wrap:wrap`:
three stats (label 14px `#5b6475` over value 22px): **Put in** `gbp(Σin)` · **Taken out** `gbp(Σout)` ·
**Company owes you** `gbp(Σin − Σout)` (value 700) · then a 14px `#8a93a3` note (max-width 380px):
`"Money you put into the business, minus personal spending paid from the business account."`
Workbook reference: in £6,850.00, out £563.54, balance £6,286.46.

Negative balance: the design still says "Company owes you −£x". The workbook says "(Positive = company
owes you. Negative = you owe company.)" → **build: label flips to "You owe the company" and shows the
absolute value**.

Table: header `padding:14px 24px 8px`, grid `120px minmax(0,1fr) minmax(0,2fr) 100px 100px 100px minmax(0,1.4fr) 20px`, gap 8px.
Headers `Date · Type · Description · In £ · Out £ · Balance · Notes · (blank)`.
Row: date input · type select (`Capital injection`, `Loan to company`, `Drawings`, `Repayment`) ·
description · In money input (placeholder `—`) · Out money input (placeholder `—`) ·
running balance `gbp` (read-only, right) · notes (placeholder `—`) · `×`.
Rows **ascending by date**; running balance accumulates in that order (ties keep insertion order).
**+ Add entry**: today, type `Drawings`, zeros, empty text. No empty state in the design (build: 
"No entries yet. Money you put in or take out goes here." 15px `#8a93a3`, padding 40px).

Build validation: exactly one of In/Out > 0; type constrains direction (Capital injection / Loan to
company → In; Drawings / Repayment → Out); mismatch shows the input border coral.

### 1.7 The shell's cash banner

Rendered by the shell above every page (below the optional stale-sync banner), in a
`<sc-for list=banners>`:
`display:flex; gap:12px; align-items:center; margin:10px 16px 0; padding:10px 12px 10px 16px;
border-radius:14px; background:#fdf1ef; font-size:14px`
- 8×8 dot `#d4554a` (round)
- Text (flex:1): `"Cash was £12.00 over on Fri, 20 Mar 2026 and nobody has explained it yet."`
  — `'Cash was ' + gbp(|diff|) + (diff<0 ? ' short' : ' over') + ' on ' + date.toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short',year:'numeric'}) + ' and nobody has explained it yet.'`
  (the shell's `gbp` is the simple `£12.00` form without thousands separators; use the shared one.)
- Action pill `Look at it` (bg `#fff`, radius 10px, padding `7px 14px`, 700, `#1f2633`) → navigates to
  **Reconcile** (`m-rec`). Build: also select the discrepancy's month and scroll to/highlight its row.
- Close `×` 32×32, `#8a93a3`, 18px — dismisses for the session only (design: component state).

Trigger (verbatim `cashIssue()`): among all sales days with a declared cash count, those where
`|declared − (sqCash + ownCash)| > 500` (strictly more than £5.00); pick the **most recent date**; show
its signed diff. Nothing when none. The design's demo seeds fake declarations
(`i%7===3 → −£4.20`, `i%11===5 → +£12.00`) — **do not seed these**.

Build rules: exclude discrepancies that have an explanation (§1.4 C); banner comes from the API
(§5, `GET /api/finance/alerts`), not client-side scanning; dismissal persists per discrepancy for the
session (key by date), and a new discrepancy re-raises the banner.

The stale banner (shell-owned, `#f1f3f7`, grey dot `#8a93a3`) says "…Stock estimates and takings are
behind until the next sync." — takings freshness is a finance concern: its `syncAt` must be the latest
takings import, not only the Lightspeed sale sync (§3).

---

## 2. Data model implied by the design

### 2.1 Trading day (Sales tab)

```ts
sale = { date:'YYYY-MM-DD', card:int, sqCash:int, ownCash:int, tx:int, note:string }   // pence
```
- `total = card + sqCash + ownCash`; `avgTicket = total / tx` (tx>0).
- Workbook `Daily Sales` (B date, D "Square — card", E "Square — cash", F "Own cash (auto from Cash
  Transactions)", G "Own cash (override)", H `=D+E+IF(G>0,G,F)`, I transactions, J avg, K notes).
  Own cash = override if > 0 else Σ `Cash Transactions` for the date (that sheet is empty in the
  workbook; own cash is 0 on every row).
- Semantics the design leaves implicit and the build must make explicit:
  - `card` for 2025-09…2026-03 is a **bank deposit amount on the deposit date**, not till takings
    (README row 36). April 2026 is Square dashboard data. → carry `basis` per figure.
  - `sqCash` on 7 rows is **Just Eat revenue** ("Just Eat: £29.62" notes, £114.92 total, README row 37),
    not cash in the drawer. → import those as channel revenue (Just Eat), not till cash, so the cash
    reconciliation does not expect them in the drawer (open question to owner, §4.4).
  - "Square cash" = cash rung on the till; "Own cash" = cash sales **not** rung on the till.
    **Superseded (DECISIONS 26, owner 2026-09-28):** the app keeps ONE cash figure per day,
    "Cash" (`payment_day` CASH). The workbook's two columns are summed into it on import.

### 2.2 Cash declaration (Reconcile C, banner)

`cashDecl: { [date]: pence | null }` — a counted cash figure per trading day.
`diff = counted − (sqCash + ownCash)`; flagged when `|diff| > 500`. No explanation field exists in the
design (needed, §1.4). Not in the workbook.

### 2.3 Expense

```ts
expense = { id, date, cat, desc, amount:int, method, receipt:boolean, notes, kind:'Operating'|'Capital'|'Drawings' }
```
- Categories (13, `CATS`): COGS = `Stock — coffee, Stock — dairy, Stock — syrups, Stock — cakes,
  Stock — food, Stock — packaging, Stock — other`; OPEX = `Rent, Utilities, Wages, Marketing,
  Square fees, Other`. (Workbook validation list `Expenses!L5:L17` — same 13; `SC_FIN.categories`
  omits "Stock — coffee" but the data uses it.) Workbook uses an em dash `—` in category names.
- `kind` is derived **once** at first load from notes, then stored:
  `/capital expenditure/i → Capital`, `/director drawings|personal/i → Drawings`, else `Operating`.
  Workbook: 6 Capital (all category Other, £4,602.03), 44 Drawings (all Other, £563.54), 99 Operating.
- **Flag ("Needs a look")**: `/ask user|verify|\?\?/i` on notes — 14 rows in the workbook (Mol Ndcc ×4,
  Amazon ×7, Monolith, signage, Partners& insurance). Build: a stored `needs_review` boolean set by the
  importer from this regex, cleared by the user (design has no clear action; build: clearing the note
  text or a "Looks right" action on the row).
- `receipt`: boolean only (0 of 149 have one). No receipt file/photo, **no VAT** anywhere in the design.
- Workbook `Expenses` sheet: B date, C category, D vendor/description, E amount (£ float), F method,
  G receipt?, H notes.

### 2.4 Director's account entry

```ts
director = { id, date, type:'Capital injection'|'Loan to company'|'Drawings'|'Repayment', desc, inP:int, outP:int, notes }
```
`balance = Σ inP − Σ outP` (running, by date). Workbook `Director Account`: B date, C type, D description,
E in (£), F out (£), G notes; rows 6–54; totals at row 56, balance at row 58.
**The 44 Drawings rows duplicate the 44 Drawings expenses exactly** (same dates, descriptions,
Σ £563.54): the same bank outflow is recorded twice — once as an expense (kind Drawings, excluded from
the P&L) and once as a director's-account "out". The build must store it once (§3.3 `director_entry.expense_id`).

### 2.5 Delivery-app month (Reconcile B, P&L)

```ts
channel = { month:'YYYY-MM', app:'Deliveroo'|'Just Eat', gross:int, comm:int, ads:int, demo:boolean }
```
`kept = gross − comm − ads`. The design **invents** Deliveroo Jan–Apr 2026 (gross £412.50/£468.20/£590.10/
£301.40, commission = 30% of gross, ads £75/£30/£45.24/£20) flagged `demo:true`. These are fabricated
and must **never be seeded**; with no data every month reads "not uploaded: missing, not zero".

### 2.6 Payout settings and bank arrivals (Reconcile A)

`bankLag` (working days, default 2), `cardFee` (% float, default 1.75), `bank: { [soldOnDate]: pence|null }`.
The design seeds one fake short payout (`cards[len−6]`, −£18.50) — do not seed.

### 2.7 Stock written off (Overview, P&L)

From the Menu/Stock data: `Σ writeoff.qty × (packCost/pack)` over write-offs dated in the period
(`woBy(k)`). Added to COGS (`cogs2 = cogs + wo`).

### 2.8 Derived period figures (`monthCalc(k)`, verbatim semantics)

```js
sales = sales in period; ex = expenses in period
byCat = Σ amount by category over ex where kind === 'Operating'
card, sq, own = Σ over sales
cogs  = Σ byCat[COGS]      opex = Σ byCat[OPEX]
capital  = Σ amount where kind === 'Capital'   drawings = Σ amount where kind === 'Drawings'
chG, chC, chA = Σ gross, comm, ads over channel months in period
wo = stock written off in period
revenue = card + sq + own + chG
cogs'   = cogs + wo
opex'   = opex + chC + chA
gp  = revenue − cogs'          net = revenue − cogs' − opex'
days = count of sales rows with total > 0
```
Period = one `YYYY-MM` or `all`. Previous month = the preceding month **in the list of months with
data** (not the calendar month — if a month had no data at all, "previous" skips it; build: use the
calendar month and show blanks).

---

## 3. Backend mapping

Legend: **EXISTS** — built and usable as is · **DERIVABLE** — computable from existing tables with a new
service/view · **MISSING** — needs new schema.

### 3.1 What exists today

| Thing | Where | Notes |
|---|---|---|
| `payment_day` table: one row per (business_date, method, source), `gross/refunds/fees/discounts_pence` nullable, `transactions`, `source`, `source_ref`, `notes`, `imported_at` | `cafeops/db/models/payment.py:46-96`; migration `migrations/versions/dff1f404b11c_payment_day_daily_takings_from_a_.py:26-43` (current **head**, down_revision `3c41d7a9e2b0`) | `net_pence` is None unless every deduction reported (`payment.py:85-96`). |
| Enums `PaymentMethod {CASH, CARD, VOUCHER, ACCOUNT, OTHER}`, `PaymentSourceKind {CSV_UPLOAD, POS_API, MANUAL}` | `cafeops/db/models/enums.py:444-465` | stored `native_enum=False` → CHECK constraint; adding members needs a batch_alter migration. |
| Payment import (idempotent on date+method+source) and window read | `cafeops/services/ingest_payments.py:44-86` (`ingest_payments`), `:110-171` (`read_takings`) | **Bug for this feature:** `read_takings` sums `gross_pence` across *all sources* (`:125-128`), so a MANUAL row and a CSV row for the same day/method double the takings. Needs a source-precedence rule (§3.4). |
| Payment sources: CSV reader with refusal discipline, browser-agent source | `cafeops/integrations/payments/base.py:30-92`, `csv_source.py` (aliases `:47-60`), `browser_source.py`, CLI `cafeops/cli/payments.py` mounted in `cafeops/cli/__init__.py` (`cafeops payments import|report|browser-plan`) | Reusable for "Upload" flows. |
| `GET /api/takings?days=` | route `cafeops/api/routers.py:223-234`; view `cafeops/api/views/payments.py:27-46`; schema `TakingsResponse` `cafeops/api/schemas.py:766-791` | Rolling N-day window, not month; totals only, no per-day rows. |
| Today summary (alerts list, severity rule "colour only for a crossed threshold") | `cafeops/api/views/today.py:36-263`; `TodayAlert` `schemas.py:1287-1293` | No money alerts yet — natural home for the cash banner alert. |
| Meta/enum export | `cafeops/api/views/meta.py:62-104` | Add finance enums (categories, methods, kinds, director types). |
| Channel metrics per **day**: `gross_pence`, `commission_pence`, `ad_spend_pence` (nullable), `source` | `cafeops/db/models/channel.py:40-96`; `SalesChannelName {DELIVEROO, JUST_EAT}` `enums.py:142-146`; `ChannelSourceKind {CSV_UPLOAD, BROWSER_AGENT, PARTNER_API, MANUAL}` `enums.py:149-160`; `net_pence` refuses partial subtraction (`channel.py:73-93`) | Month totals DERIVABLE by summing. CSV import exists (`integrations/channels/`). |
| Sales lines from Lightspeed (`sale`: `gross_pence`, `sold_at`, `channel`, `voided`, `is_refund`, `lightspeed_receipt_id`) | `cafeops/db/models/sale.py:19-48` | No tender/payment method on sales → card/cash split is **not** derivable from `sale`; order count is. |
| Expiry write-offs: `EXPIRED`/`WASTE` movements with `batch_id`; `stock_batch.unit_cost_pence` (Qty, exact) | `cafeops/db/models/enums.py:62-72`; `db/models/stock.py:46-60`; `db/models/batch.py:30-69`; `db/repositories/stock.py:88-100` (EXPIRED qty in a window); `db/repositories/batch.py:161` (`expiry_losses_due`, costed, due-not-yet-swept) | A **valued** write-off total per month is DERIVABLE. |
| Purchase orders (`purchase_order.total_pence`, `status`, `confirmed_at`) | `cafeops/db/models/purchase_order.py:29-70` | Could later auto-create "Stock — …" expenses on delivery; not required by the design. |
| Current Money screen | `web/src/screens/Money.tsx:97-339` (draft spend, supplier terms, waste, `NOT_COVERED` list at `:60`); `web/src/screens/money/Takings.tsx:432` (`/api/takings` card); `web/src/lib/api.ts:244-246` | Its `NOT_COVERED` list ("Rent, utilities… Never entered. There is no table for them") is exactly what this spec adds. The new finance tabs replace this screen; ConfirmTerms/SupplierTerms move to Suppliers (another area). |
| Legacy workbook import | `cafeops/seed/legacy.py:207-224` (`import_legacy`), reads **only** `Ingredients` (`:243`) and `Recipes` (`:432`) | **Does not read** Daily Sales, Cash Transactions, Expenses, Director Account, Monthly P&L. |

### 3.2 Classification of every datum and action

| Design datum / action | Status | Source / proposal |
|---|---|---|
| Card takings per day | **EXISTS** (storage) | `payment_day` method CARD. Needs `basis` column (§3.3) and MANUAL/LEGACY source writes. |
| Till ("Square") cash per day | **EXISTS** (storage) | `payment_day` method CASH. |
| Own cash (off-till) per day | **RETIRED** (DECISIONS 26) | Folded into the day's one Cash figure (`payment_day` CASH). `CASH_OFF_TILL` stays in the enum for old rows; nothing writes it. |
| Orders (tx) per day | **DERIVABLE** + manual override | `COUNT(DISTINCT lightspeed_receipt_id)` over non-voided `sale` per local day; else `payment_day.transactions`; manual override in `trading_day.transactions_override`. |
| Day note | **MISSING** | `trading_day.note`. |
| Add / edit / delete a day | **MISSING** | services + endpoints (§5). |
| Month list, totals, avg/day, avg ticket | **DERIVABLE** | view over the above. |
| Expense rows (all fields) | **MISSING** | `expense` + `expense_category` tables. |
| Needs-a-look flag, no-receipt filter | **MISSING** | `expense.needs_review`, `expense.has_receipt`. |
| Operating / Capital / Drawings kind | **MISSING** | `expense.kind`. |
| Cash counted per day | **MISSING** | `cash_count` table. |
| Cash discrepancy explanation | **MISSING** (and missing in design) | `cash_count.explanation`, `explained_at`. |
| Cash banner | **DERIVABLE** once `cash_count` exists | `GET /api/finance/alerts` (+ `TodayAlert kind="cash"`). |
| Payout lag, card fee % | **MISSING** | `finance_setting` (single row). Fee in **basis points** (175), not float. |
| Bank arrival per card day | **MISSING** | `card_payout` table. |
| Due date (working days) | **DERIVABLE** | domain function; should skip England & Wales/Scotland bank holidays (Scotland: Dundee) — design skips weekends only. |
| Delivery apps month gross/commission/ads | **DERIVABLE** from `channel_metric` (sum per month) | Manual month edits: **MISSING** → `channel_statement` table (§3.3). |
| Delivery CSV upload | **EXISTS** (CLI/integration) / **MISSING** (HTTP) | `integrations/channels/csv_source.py`; add a multipart endpoint. |
| Stock written off per month (£) | **DERIVABLE** | Σ over `stock_movement` type EXPIRED/WASTE × `stock_batch.unit_cost_pence` (movement without batch → ingredient current cost, flagged estimate). Null if any unpriced. |
| COGS by category (purchases) | **DERIVABLE** after `expense` | Σ expense where category.group = COGS and kind = OPERATING. |
| P&L matrix | **DERIVABLE** after the above | `services/finance_pl.py`. |
| Director entries, running balance | **MISSING** | `director_entry` table. |
| "Saved" state | n/a | client state from mutation results. |

### 3.3 Proposed schema (SQLAlchemy 2.0, typed; money `int` pence; `*_at` = `UTCDateTime` tz-aware UTC; business dates = `Date` in Europe/London)

All new tables use `enum_col(...)` (`cafeops/db/models/_common.py`), `TimestampedMixin`-style
`created_at`, and an `updated_at`. Money columns get `CHECK (x >= 0)` unless stated. New file
`cafeops/db/models/finance.py`; register in `db/models/__init__.py`.

**Enums** (`db/models/enums.py`):
```python
class PaymentMethod(enum.Enum):  # extend
    CASH = "CASH"                     # the day's one "Cash" figure (DECISIONS 26)
    CASH_OFF_TILL = "CASH_OFF_TILL"   # RETIRED: was "Own cash"; never written, read as Cash
    CARD = "CARD"; VOUCHER = "VOUCHER"; ACCOUNT = "ACCOUNT"; OTHER = "OTHER"

class PaymentSourceKind(enum.Enum):  # extend
    CSV_UPLOAD = "CSV_UPLOAD"; POS_API = "POS_API"; MANUAL = "MANUAL"
    LEGACY_WORKBOOK = "LEGACY_WORKBOOK"   # NEW

class PaymentBasis(enum.Enum):        # NEW
    TILL = "TILL"                     # what the till took that day
    BANK_DEPOSIT = "BANK_DEPOSIT"     # a settlement figure on its deposit date (workbook Sep–Mar card)

class ExpenseGroup(enum.Enum):  COGS = "COGS"; OPEX = "OPEX"
class ExpenseKind(enum.Enum):   OPERATING = "OPERATING"; CAPITAL = "CAPITAL"; DRAWINGS = "DRAWINGS"
class ExpenseMethod(enum.Enum): CARD="CARD"; BANK_TRANSFER="BANK_TRANSFER"; DIRECT_DEBIT="DIRECT_DEBIT"; STANDING_ORDER="STANDING_ORDER"; CASH="CASH"; CASH_WITHDRAWAL="CASH_WITHDRAWAL"
class FinanceSource(enum.Enum): MANUAL="MANUAL"; LEGACY_WORKBOOK="LEGACY_WORKBOOK"; BANK_CSV="BANK_CSV"; CSV_UPLOAD="CSV_UPLOAD"
class DirectorEntryType(enum.Enum): CAPITAL_INJECTION="CAPITAL_INJECTION"; LOAN_TO_COMPANY="LOAN_TO_COMPANY"; DRAWINGS="DRAWINGS"; REPAYMENT="REPAYMENT"
```

**`payment_day` — add column**
- `basis: Mapped[PaymentBasis]` not null, server_default `'TILL'`.

**`trading_day`** (the Sales row's non-money parts)
- `id` PK · `business_date: Date` **unique** · `note: Text | None` · `transactions_override: int | None` (CHECK ≥ 0) ·
  `source: FinanceSource` · `created_at`, `updated_at`.
- A "day" in the Sales tab = union of `trading_day` and `payment_day` dates.

**`expense_category`**
- `id` PK · `name: String(60)` unique (seed the 13 names verbatim, em dash) · `group: ExpenseGroup` ·
  `sort: int` · `is_active: bool` default true.

**`expense`**
- `id` PK · `paid_on: Date` (index) · `category_id` FK → `expense_category.id` not null ·
  `description: String(300)` not null · `amount_pence: int` not null, CHECK > 0 ·
  `method: ExpenseMethod` · `kind: ExpenseKind` default OPERATING · `has_receipt: bool` default false ·
  `notes: Text | None` · `needs_review: bool` default false · `supplier_id` FK → `supplier.id` nullable
  (lets the Orders "shop runs" view stop regex-matching descriptions) · `purchase_order_id` FK nullable ·
  `source: FinanceSource` · `source_ref: String(400) | None` (e.g. `Expenses!R42`) ·
  `deleted_at: UTCDateTime | None` (soft delete; reads filter it) · `created_at`, `updated_at`.
- Indexes: `(paid_on)`, `(kind, paid_on)`, `(needs_review)`.
- VAT: not in the design — do **not** add a column now (§4.8).

**`cash_count`**
- `id` PK · `business_date: Date` unique · `counted_pence: int` CHECK ≥ 0 · `counted_by: String(120) | None` ·
  `counted_at: UTCDateTime` · `explanation: Text | None` · `explained_at: UTCDateTime | None` ·
  `source: FinanceSource` · `updated_at`.
- Expected cash = `payment_day` CASH (+ any legacy CASH_OFF_TILL) gross for that date (TILL basis only).

**`card_payout`**
- `id` PK · `sold_on: Date` unique (the trading day the card money belongs to) ·
  `arrived_pence: int` CHECK ≥ 0 · `arrived_on: Date | None` · `source: FinanceSource` ·
  `source_ref` · `notes` · `updated_at`.
- (If a bank CSV is imported later, payouts that batch several days need a split table; out of scope.)

**`finance_setting`** (single row, `id = 1` CHECK)
- `payout_lag_working_days: int` default 2 (CHECK 0–10) · `card_fee_bp: int` default 175 (CHECK 0–1000) ·
  `cash_tolerance_pence: int` default 500 · `payout_tolerance_pence: int` default 100 ·
  `stock_pct_threshold_bp: int` default 3200 · `updated_at`.
  (Thresholds are literals in the design; storing them keeps view and banner in one place.)

**`channel_statement`** (monthly figures typed or uploaded per app)
- `id` PK · `channel: SalesChannelName` · `month: Date` (first of month, CHECK day = 1) ·
  `gross_pence`, `commission_pence`, `ad_spend_pence: int | None` (null = not reported) ·
  `source: ChannelSourceKind` · `source_ref` · `updated_at` · UNIQUE(`channel`, `month`).
- Read rule: statement row if present, else Σ `channel_metric` days in the month **only if every day of
  the month with orders has all three figures**; else the month is "incomplete" (value null + caveat).

**`director_entry`**
- `id` PK · `entry_date: Date` · `type: DirectorEntryType` · `description: String(300)` ·
  `in_pence: int` default 0 · `out_pence: int` default 0 ·
  CHECK `(in_pence > 0 AND out_pence = 0) OR (out_pence > 0 AND in_pence = 0)` ·
  CHECK type/direction (`CAPITAL_INJECTION, LOAN_TO_COMPANY → in`; `DRAWINGS, REPAYMENT → out`) ·
  `notes` · `expense_id` FK → `expense.id` **unique, nullable** · `source` · `source_ref` ·
  `deleted_at` · `created_at`, `updated_at`.
- Rule: an expense with `kind = DRAWINGS` **is** a director's-account out-movement. The service creates/
  updates/deletes the linked `director_entry` when an expense's kind becomes/stops being DRAWINGS
  (amount/date/description mirrored, read-only in the Director table with a "from expenses" hint).
  This removes the workbook's double entry.

**Effective dating:** none of these are effective-dated (invariant 3 covers recipes). Edits overwrite
in place; history is kept via `updated_at` + soft delete. If audit becomes a requirement, add an
append-only `finance_edit` log (table, row id, field, old, new, at) — not required by the design.

### 3.4 Alembic migration outline (one revision, `down_revision = "dff1f404b11c"`)

1. `batch_alter_table('payment_day')`: recreate the `paymentmethod` and `paymentsourcekind` CHECK
   constraints with the new members; `add_column('basis', sa.Enum('TILL','BANK_DEPOSIT', name='paymentbasis',
   native_enum=False), server_default='TILL', nullable=False)`. (ARCHITECTURE §8F.7: Alembic runs with FK
   enforcement OFF — batch mode rebuilds the table.)
2. `create_table` in FK order: `expense_category`, `finance_setting`, `trading_day`, `cash_count`,
   `card_payout`, `channel_statement`, `expense`, `director_entry` (+ indexes, uniques, CHECKs above).
3. Data: `op.bulk_insert(expense_category, [...13 rows...])` and `finance_setting` row id=1 with defaults.
   Categories are reference data, so seeding them in the migration is correct; **no transactional data
   in the migration**.
4. Downgrade drops in reverse and restores the old CHECKs (refuse if rows use the new enum values).

### 3.5 Services (all writes here — CLAUDE.md §8)

`cafeops/services/finance/` (sync, `Session` in; routes call via `asyncio.to_thread` like the rest):
- `trading_days.py` — `create_day` / `update_day(date, card, cash, orders_override, note)` (one cash figure, DECISIONS 26)
  writes `payment_day` rows with `source=MANUAL, basis=TILL` (one per non-null method) and `trading_day`;
  `move_day(old, new)`; `delete_day(date)` (MANUAL/LEGACY rows only — CSV/POS rows are not user-deletable,
  the UI shows those inputs read-only with "from export").
- `takings_read.py` — per-day resolved takings with **source precedence per (date, method)**:
  `POS_API > CSV_UPLOAD > MANUAL > LEGACY_WORKBOOK`; lower-precedence rows are ignored, and a
  disagreement > £1 between sources is surfaced as a caveat. Fix `read_takings`
  (`services/ingest_payments.py:117-128`) to use this.
- `expenses.py` — create/update/soft-delete; keeps `director_entry` in sync for DRAWINGS.
- `cash.py` — `record_count(date, pence, by)`, `explain(date, text)`, `open_discrepancies()`.
- `payouts.py` — `record_payout(sold_on, pence | None)`, `due_date(sold_on, lag)` (domain, pure),
  `reconcile_month(month)`.
- `channels_month.py` — `upsert_statement(...)`, `month_figures(month)` (statement > complete daily sum > null).
- `write_offs.py` — `valued_write_offs(since, until) -> (pence|None, is_estimate, caveats)`.
- `pl.py` — `period_figures(period)` implementing §2.8 with the completeness rules of §4.6; pure maths in
  `cafeops/domain/finance.py` (no I/O, mypy strict).
- `director.py` — CRUD + running balance.
- `settings.py` — read/update `finance_setting`.

### 3.6 Importing the workbook

New command `uv run cafeops import-finance --workbook sashas_corner_finance.xlsx --dry-run | --commit`
(`cafeops/seed/finance.py`, same header-scanning approach as `seed/legacy.py`; idempotent on
`source_ref`):
- **Daily Sales** (header row 5): rows with any of D/E/G/I/K. D → `payment_day` CARD with
  `basis = BANK_DEPOSIT` for dates ≤ 2026-03-31 and `TILL` for April (README row 36); E → CASH (TILL),
  **except** rows whose note matches `/^Just Eat: £/` → `channel_statement`/`channel_metric` JUST_EAT
  gross (commission/ads null) and no cash row; G (or F if G blank) is **added to** E's CASH row
  (one cash figure per day, DECISIONS 26 — never a separate CASH_OFF_TILL row); I →
  `payment_day.transactions` when > 0; K → `trading_day.note`. `source = LEGACY_WORKBOOK`,
  `source_ref = "Daily Sales!R{n}"`. 161 days.
- **Cash Transactions**: empty in this workbook; if rows exist, sum per date into the day's CASH (items are
  not imported as `sale`, they have no receipt ids).
- **Expenses** (header row 5, rows 6–500): B/C/D/E/F/G/H; amount via `Decimal(str(v)) * 100`, refuse
  non-2dp values; category name matched exactly (em dash); `kind` from the §2.3 regexes;
  `needs_review` from `/ask user|verify|\?\?/i`; `has_receipt` from G truthy. 149 rows.
- **Director Account** (header row 5, stop at the `TOTAL:` row): Drawings rows are **matched** to the
  imported DRAWINGS expense with the same date + description + amount and linked (`expense_id`), not
  inserted twice; unmatched rows inserted. Capital injections (5 rows, £6,850) inserted. Report the
  match count (expect 44/44).
- **Monthly P&L / Yearly Summary / Dashboard**: formulas only — not imported; used as a **check**:
  after import, recompute months with `pl.period_figures` and compare to the workbook's cached values
  (`data_only=True`) for Card/Cash/COGS/OPEX rows; print differences.
- Nothing from the design's demo (`finMigrate`: channels, cashDecl, bank) is imported.

Which workbook: the repo-root `sashas_corner_finance.xlsx` (v4, "Data status last updated 28 Apr 2026",
161 sales days) is what the design was built from. `sashas_corner_finance__LEGACY_.xlsx` has the same
149 expenses and 49 director rows but 159 sales days (lacks 2025-10-26 £0.98 and 2025-11-05 £3.92) and
is the recipe source. ARCHITECTURE.md §0 "The workbook" calls `sashas_corner_finance.xlsx` "a 21 Apr stub
that must not be used" — true of the old ~/Downloads file for **recipes**, not of this 231 KB v4 file
(its Recipes sheet is shorter). Recommendation: finance sheets from the root file; recipes stay on
LEGACY; amend ARCHITECTURE §0 to say so.

---

## 4. Conflicts with CLAUDE.md / ARCHITECTURE.md, and resolutions

1. **Visual system.** The design (white, Nunito, one blue `#4a6fd1`, coral `#d4554a`) contradicts
   `docs/phase4/DESIGN-LAW.md` (dark zinc-950, blue-500, green/red, 12px workhorse) encoded in
   `web/src/styles.css`. The export is dated 2026-09-26 and is the owner's latest direction, so it wins;
   replace the tokens in `styles.css` with §0.1 and mark DESIGN-LAW.md superseded (record in ARCHITECTURE).
2. **"Square" wording.** The POS is Lightspeed K-Series; Square was the previous POS (ARCHITECTURE §0
   table row "§13.1"). Headers "Square cash £" and category "Square fees" are workbook legacy.
   Recommend "Till cash £" and "Card fees" (keep the import mapping "Square fees" → "Card fees");
   confirm with owner — copy change only. *(Owner later went further: just "Cash", one figure
   per day — DECISIONS 26.)*
3. **Invariant 8 (missing ≠ zero, estimates flagged).** Several design figures treat missing as zero:
   (a) Reconcile assumes unrecorded payouts arrived (§1.4 A) → treat as not recorded;
   (b) P&L/Overview sum channel revenue as 0 for months nobody uploaded → return the month's channel
   figures as `null` with `incomplete: true` and render the takings/net cells with a marker
   ("incomplete — Deliveroo Feb not uploaded") instead of a silently low number;
   (c) Stock written off valued at estimated unit costs must carry `is_estimate` and render italic
   (the Stock screen's convention "Estimates in italic"); unpriced → null, not 0;
   (d) uncounted cash days drawn as zero bars → draw nothing.
4. **Just Eat money in the cash column** (workbook quirk) would make the cash reconciliation expect
   delivery revenue in the drawer. Import it as Just Eat channel revenue; open question to owner whether
   Just Eat ever paid in cash.
5. **Circular reconciliation**: Sep–Mar card figures are bank deposits. `payment_day.basis` separates them.
6. **Completeness of a month** (not in the design, but its default view is misleading): April 2026 shows
   £1,484 net profit because no April expenses exist yet. The P&L service returns per-period caveats:
   "no expenses entered", "N of M trading days have takings", "channel figures missing"; Overview shows
   them in the footnote line (13px `#8a93a3`; stays grey, because colour is only for crossed thresholds).
7. **Capital injection in the director's balance.** For a limited company, share capital is equity and is
   not "owed" back; only loans are. The design/workbook add capital injections to "Company owes you".
   Keep the workbook semantics (owner's instruction) but split the summary in the API
   (`loan_balance_pence` vs `capital_in_pence`) so the UI can be corrected after the owner's accountant
   answers. Add to CLAUDE.md §15 as an open question.
8. **VAT and receipts** are absent from the design; the old Money screen lists "VAT" as not covered.
   Do not invent a VAT model; receipts stay a boolean. Record as a known gap.
9. **375px.** CLAUDE.md §10 requires the web app to survive 375px; the design specifies only 1180/1440.
   Tables of 8–10 columns cannot fit. Minimum: each finance table sits in an `overflow-x:auto` wrapper
   with a sticky first column; the Overview grid collapses to one column below 900px; the month bar
   already scrolls. No page-level horizontal scroll.
10. **Uppercase eyebrow labels** (shell nav group headers "MONEY") conflict with CLAUDE.md §10's
    "avoid uppercase eyebrow labels". Shell-owned; flag to the shell spec.
11. **Traffic-light rule.** The "Needs a look" box is coral even at 0 (§1.1 C) → neutral when 0.
12. **Money parsing with floats** (`parseFloat` in `pen`) violates invariant 11. Frontend: parse with
    `web/src/lib/dec.ts`; API accepts money as **decimal strings of pounds** (`"12.34"`) or integer
    pence — pick **integer pence** in request bodies (see §5) and convert at the edge.
13. **Write routes.** `api/routers.py` docstring says the composition POSTs are the only writes; this
    adds many. Update the docstring; invariant 1 is unaffected (no purchase-order route).
    Deletions of money rows are soft deletes; the stock ledger invariant (12) is untouched.
14. **Takings freshness.** The shell's stale banner claims takings are behind Lightspeed sync; takings
    come from payment imports/manual entry, so its timestamp should be `max(payment_day.imported_at,
    last sale sync)`.

---

## 5. Proposed API (TypeScript)

Conventions: all under `/api/finance`, password-protected like the rest (`api/security.py`). Money =
**integer pence** (`number`, always an integer) — matching `meta.money_encoding` for stored figures;
nullable = not reported / unknown, never 0. Dates `YYYY-MM-DD` (Europe/London business dates);
timestamps ISO-8601 UTC. `Period = 'YYYY-MM' | 'all'`. Every response carries `caveats: string[]`.

```ts
type Pence = number            // integer
type ISODate = string          // 'YYYY-MM-DD'
type Month = string            // 'YYYY-MM'
type Period = Month | 'all'
type Source = 'MANUAL' | 'LEGACY_WORKBOOK' | 'CSV_UPLOAD' | 'POS_API' | 'BANK_CSV' | 'BROWSER_AGENT' | 'PARTNER_API'

// ---------- shared ----------
GET /api/finance/months
interface FinanceMonths {
  months: Month[]                 // ascending, any month with takings or expenses
  default_month: Month | null     // latest month with takings
}

GET /api/finance/meta              // or fold into /api/meta
interface FinanceMeta {
  categories: { id: number; name: string; group: 'COGS' | 'OPEX'; sort: number; active: boolean }[]
  methods: ('CARD'|'BANK_TRANSFER'|'DIRECT_DEBIT'|'STANDING_ORDER'|'CASH'|'CASH_WITHDRAWAL')[]
  kinds: ('OPERATING'|'CAPITAL'|'DRAWINGS')[]
  director_types: ('CAPITAL_INJECTION'|'LOAN_TO_COMPANY'|'DRAWINGS'|'REPAYMENT')[]
  settings: FinanceSettings
}
interface FinanceSettings {
  payout_lag_working_days: number
  card_fee_bp: number              // 175 = 1.75%
  cash_tolerance_pence: Pence      // 500
  payout_tolerance_pence: Pence    // 100
  stock_pct_threshold_bp: number   // 3200
}
PATCH /api/finance/settings  body: Partial<Pick<FinanceSettings,'payout_lag_working_days'|'card_fee_bp'>> -> FinanceSettings

// ---------- period figures (Overview + P&L share this) ----------
interface PeriodFigures {
  period: Period
  label: string                    // 'April 2026' | 'Everything so far' | 'Apr 26' (P&L header)
  trading_days: number
  expense_count: number
  card_pence: Pence
  cash_pence: Pence                         // one cash figure (DECISIONS 26)
  delivery_gross_pence: Pence | null        // null = some app/month not reported
  revenue_pence: Pence                      // till takings + known delivery gross; incomplete=true if any delivery month is missing
  cogs_by_category: { category: string; pence: Pence }[]   // OPERATING only, non-zero
  stock_bought_pence: Pence                 // Σ COGS categories
  written_off_pence: Pence | null
  written_off_is_estimate: boolean
  cogs_pence: Pence | null                  // stock_bought + written_off
  stock_pct_bp: number | null               // cogs / revenue
  stock_pct_warn: boolean
  gross_profit_pence: Pence | null
  opex_by_category: { category: string; pence: Pence }[]
  delivery_commission_pence: Pence | null
  delivery_ads_pence: Pence | null
  net_profit_pence: Pence | null
  net_warn: boolean                         // net < 0
  capital_pence: Pence                      // excluded from P&L
  drawings_pence: Pence                     // excluded from P&L
  incomplete: boolean
  caveats: string[]                         // 'No expenses entered for April 2026', 'Deliveroo: Feb 26 not uploaded', ...
}

GET /api/finance/overview?period=2026-04
interface FinanceOverview {
  current: PeriodFigures
  previous: PeriodFigures | null            // calendar previous month; null for 'all' or first month
  avg_per_day_pence: Pence | null
  chart:
    | { mode: 'days'; days_in_month: number;
        bars: { day: number; date: ISODate; total_pence: Pence | null }[] }   // null = nothing entered (draw dash)
    | { mode: 'months'; bars: { month: Month; label: string; revenue_pence: Pence | null }[] }
  where_it_went: { category: string; pence: Pence }[]   // top 6 OPERATING categories, desc
  needs_a_look: {
    count: number                                        // flagged + (no_receipt>0 ? 1 : 0)
    flagged: { expense_id: number; description: string; amount_pence: Pence }[]   // first 4
    flagged_total: number
    without_receipt: number
  }
  caveats: string[]
}

// ---------- Sales ----------
GET /api/finance/sales?period=2026-04
interface SalesDay {
  date: ISODate
  weekday: 'Mon'|'Tue'|'Wed'|'Thu'|'Fri'|'Sat'|'Sun'
  card_pence: Pence | null
  cash_pence: Pence | null                 // the day's one Cash figure (DECISIONS 26)
  total_pence: Pence
  orders: number | null                    // override ?? derived from sales ?? payment_day.transactions
  orders_source: 'override' | 'pos' | 'payment_export' | null
  avg_ticket_pence: Pence | null
  note: string | null
  basis: 'TILL' | 'BANK_DEPOSIT' | 'MIXED'
  editable: { card: boolean; cash: boolean }   // false when a CSV/POS row wins
  sources: { method: 'CARD'|'CASH'|'CASH_OFF_TILL'; source: Source; source_ref: string | null }[]   // CASH_OFF_TILL: legacy row, labelled "Cash"
}
interface SalesResponse {
  period: Period
  days: SalesDay[]                         // ascending
  totals: { days: number; card_pence: Pence; cash_pence: Pence; total_pence: Pence; orders: number | null }
  caveats: string[]
}
POST   /api/finance/sales            body: SalesDayIn                         -> SalesDay     // 409 if the date exists
PATCH  /api/finance/sales/:date      body: Partial<SalesDayIn> & { date?: ISODate }  -> SalesDay   // re-date = move
DELETE /api/finance/sales/:date                                               -> { deleted: ISODate }
interface SalesDayIn {
  date: ISODate
  card_pence?: Pence | null
  cash_pence?: Pence | null               // stored as CASH, MANUAL
  orders_override?: number | null
  note?: string | null
}

// ---------- Expenses ----------
GET /api/finance/expenses?period=2026-03&q=amazon&category=all|cogs|<id>&kind=all|OPERATING|CAPITAL|DRAWINGS&needs_review=true&no_receipt=true
interface Expense {
  id: number
  date: ISODate
  category_id: number
  category: string
  group: 'COGS' | 'OPEX'
  description: string
  amount_pence: Pence
  method: FinanceMeta['methods'][number]
  kind: 'OPERATING' | 'CAPITAL' | 'DRAWINGS'
  has_receipt: boolean
  notes: string | null
  needs_review: boolean
  supplier_id: number | null
  director_entry_id: number | null        // set when kind = DRAWINGS
  source: Source
  source_ref: string | null
  updated_at: string
}
interface ExpensesResponse {
  period: Period
  expenses: Expense[]                     // descending by date
  count: number
  total_pence: Pence                      // Σ filtered
  caveats: string[]
}
POST   /api/finance/expenses       body: ExpenseIn           -> Expense
PATCH  /api/finance/expenses/:id   body: Partial<ExpenseIn>  -> Expense
DELETE /api/finance/expenses/:id                             -> { deleted: number; undo_token: string }
POST   /api/finance/expenses/:id/restore  body: { undo_token: string } -> Expense
interface ExpenseIn {
  date: ISODate; category_id: number; description: string; amount_pence: Pence
  method: Expense['method']; kind: Expense['kind']; has_receipt: boolean
  notes: string | null; needs_review?: boolean
}

// ---------- Reconcile ----------
GET /api/finance/reconcile?period=2026-03
interface ReconcileResponse {
  period: Period
  settings: Pick<FinanceSettings, 'payout_lag_working_days' | 'card_fee_bp'>
  card: {
    rows: {
      sold_on: ISODate
      card_pence: Pence
      due_on: ISODate
      expected_pence: Pence               // round(card * (1 - fee))
      arrived_pence: Pence | null         // null = not recorded (NEVER assumed)
      diff_pence: Pence | null
      status: 'not_yet_due' | 'not_recorded' | 'ok' | 'mismatch' | 'bank_basis'
      note: string | null                 // 'less than expected after fees: a refund or chargeback?' | 'imported from bank deposits'
    }[]
    expected_total_pence: Pence
    arrived_total_pence: Pence
    mismatched_days: number
    not_recorded_days: number
  }
  delivery: {
    rows: {
      month: Month; label: string          // '04/26'
      channel: 'DELIVEROO' | 'JUST_EAT'
      present: boolean
      gross_pence: Pence | null; commission_pence: Pence | null; ads_pence: Pence | null
      kept_pence: Pence | null; kept_bp: number | null
      source: Source | null
    }[]
  }
  cash: {
    rows: {
      date: ISODate
      till_pence: Pence                    // the day's Cash (CASH + any legacy CASH_OFF_TILL)
      counted_pence: Pence | null
      diff_pence: Pence | null
      status: 'not_counted' | 'spot_on' | 'within' | 'out'   // out = |diff| > tolerance
      explanation: string | null
    }[]
    days_with_cash: number
    net_diff_pence: Pence                  // Σ over counted days
    days_out: number
  }
  caveats: string[]
}
PUT  /api/finance/payouts/:sold_on               body: { arrived_pence: Pence | null; arrived_on?: ISODate | null } -> ReconcileResponse['card']['rows'][number]
PUT  /api/finance/channel-statements/:month/:channel  body: { gross_pence: Pence|null; commission_pence: Pence|null; ads_pence: Pence|null } -> ReconcileResponse['delivery']['rows'][number]
POST /api/finance/channel-statements/upload      multipart: file, channel -> { inserted: number; updated: number; rejected: string[]; unmapped: string[]; notes: string[] }
PUT  /api/finance/cash-counts/:date              body: { counted_pence: Pence | null; counted_by?: string } -> ReconcileResponse['cash']['rows'][number]
POST /api/finance/cash-counts/:date/explain      body: { explanation: string } -> ReconcileResponse['cash']['rows'][number]

// ---------- P&L ----------
GET /api/finance/pl?from=2025-10&to=2026-04     // default: all months with data
interface PLResponse {
  months: Month[]
  columns: PeriodFigures[]                 // one per month, same order
  total: PeriodFigures                      // period 'all' over the range
  cogs_categories: string[]                 // rows to show (non-zero in total)
  opex_categories: string[]
  threshold_bp: number                      // 3200
  caveats: string[]
}

// ---------- Director's account ----------
GET /api/finance/director
interface DirectorEntry {
  id: number
  date: ISODate
  type: 'CAPITAL_INJECTION' | 'LOAN_TO_COMPANY' | 'DRAWINGS' | 'REPAYMENT'
  description: string
  in_pence: Pence
  out_pence: Pence
  balance_pence: Pence                      // running, ascending date then id
  notes: string | null
  expense_id: number | null                 // mirrored from an expense; amount/date read-only here
  source: Source
}
interface DirectorResponse {
  entries: DirectorEntry[]
  put_in_pence: Pence
  taken_out_pence: Pence
  balance_pence: Pence                      // + company owes director, − director owes company
  capital_in_pence: Pence                   // for the §4.7 split
  loan_balance_pence: Pence
  caveats: string[]
}
POST   /api/finance/director      body: DirectorEntryIn          -> DirectorEntry
PATCH  /api/finance/director/:id  body: Partial<DirectorEntryIn> -> DirectorEntry   // 409 on mirrored fields
DELETE /api/finance/director/:id                                 -> { deleted: number }
interface DirectorEntryIn { date: ISODate; type: DirectorEntry['type']; description: string; in_pence: Pence; out_pence: Pence; notes: string | null }

// ---------- banner ----------
GET /api/finance/alerts
interface FinanceAlerts {
  cash: null | {
    date: ISODate
    diff_pence: Pence                       // signed: counted - till
    direction: 'short' | 'over'
    message: string                         // 'Cash was £12.00 over on Fri, 20 Mar 2026 and nobody has explained it yet.'
    open_count: number                      // unexplained discrepancies in total
  }
  takings_last_imported_at: string | null   // for the shell's stale banner
}
```

Also add `TodayAlert{kind:'cash', severity:'act'}` to `api/views/today.py` from the same service so the
Telegram bot can nag in Russian.

Error shape for writes: `422 { detail: [{ field, message }] }` for validation (non-integer pence,
negative amounts, both in/out set, direction/type mismatch); `409` for a duplicate sales date or an edit
to a mirrored director field; `404` unknown id/date. Fixtures: extend `cafeops api-fixtures` with
`finance-*.json` generated from the imported workbook (never from the design's demo values).
