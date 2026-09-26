# Recipes, Menu items, Ingredients & prices — implementation spec

Source of truth for this spec: `docs/design/Menu Stock App v2.dc.html` (template lines
288–391 Recipes, 40–127 Menu items, 129–182 Ingredients; logic lines 511–1106),
`docs/design/sc-comp.js` (templates, modifiers, seasons, rate), `docs/design/sc-data.js`
(174 products, 115 ingredients), `docs/design/image-slot.js` (photo slot), and the shell
`docs/design/Cafe Ops v2.dc.html` (line 66 embeds the app with `embedded`, `tab`,
`compact`). Design files were read as data. Line numbers below refer to
`Menu Stock App v2.dc.html` unless another file is named.

The design is a localStorage prototype: every edit calls `this.mut(...)`, which writes
the whole dataset to `localStorage['sc-menu-stock-v1']` and flashes `Saved`. **That
save model is not buildable against this backend** (effective dating, mandatory impact
preview, integer pence, decimal-string quantities). Section 4 lists every conflict;
section 3 says what each control must call instead. Build the pixels from sections 1–2
and the behaviour from sections 3–5.

---

## 0. Shared frame, tokens and primitives

### 0.1 Fonts and global
- Font: `Nunito` 400/500/600/700/800 (Google Fonts). `body { font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased; color: #1f2633 }`.
- Inputs/selects inherit Nunito, colour `#1f2633`, background `#fff`.
- Links `#1f2633`, hover `#4a6fd1`.

### 0.2 Colour tokens (every literal used in these three tabs)

| Token (proposed name) | Value | Used for |
|---|---|---|
| `ink` | `#1f2633` | primary text, values |
| `ink-2` | `#5b6475` | secondary text, meta lines |
| `ink-3` | `#8a93a3` | captions, counts, units, placeholders, `×` glyphs |
| `line` | `#e8ebf0` | header border, list row borders, most card borders |
| `line-soft` | `#eef0f4` | rail border, drawer borders, component cards |
| `line-faint` | `#f1f3f6` | table row rules (summary, cost grid) |
| `field` | `#e2e6ed` | input/select border (40/38px controls) |
| `field-2` | `#d5dae3` | dashed "add" borders, filter selects, chips, toggle off track |
| `field-3` | `#c9cfda` | checkbox border (Swappable) |
| `surface-2` | `#f6f7fa` | canvas behind menu grid, cost column, size cells, stat tiles |
| `surface-3` | `#f1f3f7` | close button, no-margin badge bg |
| `photo-bg` | `#eef0f4` | image slot background |
| `accent` | `#4a6fd1` | primary buttons, selected card ring |
| `accent-ink` | `#3558b8` | selected text, "add" link text, Show more, checkmark |
| `accent-bg` | `#edf1fc` | selected row/chip bg, template-link banner |
| `accent-bd` | `#c9d5f5` | selected chip border |
| `accent-deep` | `#2d3f75` | template-link banner text |
| `bad` | `#d4554a` | margin under threshold, changed-cell border, warnings, star (preferred), Delete outline |
| `bad-ink` | `#c2453b` | low margin badge text, Delete item text |
| `bad-bg` | `#fdecea` | low margin badge/tile bg, Delete item bg |
| `bad-bg-2` | `#fdf1ef` | changed-cell bg, missing-flavour card bg |
| `good-ink` | `#237a57` | healthy margin badge/tile text |
| `good-bg` | `#e8f5ee` | healthy margin badge/tile bg |
| `on` | `#3fa57a` | toggle track when on |
| `warn-bg` | `#fdf6e7` | "Italic costs are estimates" note |
| `white` | `#fff` | cards, drawer, inputs |

The "selected / not selected" helper used everywhere (line 529):
```js
const on = a => ({ bg: a ? '#edf1fc' : 'transparent', fg: a ? '#3558b8' : '#1f2633',
                   bd: a ? '#c9d5f5' : '#e2e6ed', fw: a ? 700 : 500 });
```

### 0.3 Radii used
999px (pills, toggles, chips), 18px (primary header button, Discard/Apply), 16px (menu
cards, standalone tab pills), 14px (component cards, cost grid, panels, secondary pills),
12px (inputs 40px on menu search, size buttons, stat tiles, drawer lines, Show more,
banner, chips 12px in "used in"), 10px (most inputs/selects, rail rows, icon buttons),
6px (estimate checkbox), 5px (Swappable checkbox).

### 0.4 Formatting functions (design, lines 515–518, 537)
```js
const gbp = p => (p < 0 ? '−£' : '£') + (Math.abs(p) / 100).toFixed(2);          // pence → "£3.90", "−£0.60" (U+2212)
const unitPrice = p => { const v = Math.abs(p)/100;                               // pence per unit, variable dp
  return '£' + (v >= 1 ? v.toFixed(2) : v >= 0.1 ? v.toFixed(3) : v.toFixed(4)); };
const norm = u => { u = (u||'').toLowerCase(); return u === 'each' ? 'unit' : u; };
const conv = (lu, iu) => ...  // L<->ml, kg<->g; ANY OTHER MISMATCH RETURNS 1 (see §4 C-9)
const fmtD = iso => new Date(...).toLocaleDateString('en-GB', { weekday:'short', day:'numeric', month:'short' }); // "Sat 26 Sep"
```
Build these on `web/src/lib/dec.ts` + `format.ts` (section 6): `gbp` → `moneyDec`/`money`;
`unitPrice` → a new `unitPriceDec(d: Dec)` in `format.ts` with the same 2/3/4-dp rule;
never `parseFloat`.

### 0.5 Units shown in the UI
`UNITS = ['L', 'ml', 'kg', 'g', 'unit']` (line 514). Backend enum is
`L|KG|ML|G|EACH`. Display mapping: `L→L`, `ML→ml`, `KG→kg`, `G→g`, `EACH→unit`
(and `unit` in per-unit labels: `£0.0450/unit`). `loaf` exists in `sc-data.js` for one
ingredient and has no backend equivalent — drop it.

### 0.6 App frame (embedded in the shell)
The three tabs are rendered **inside** the Cafe Ops shell (`Cafe Ops v2.dc.html:66`,
`embedded=true`), so the standalone brand/tab pills (lines 13–14) are not built. The
shell nav on the left is `232px` desktop / `200px` compact (`Cafe Ops v2.dc.html:145`,
`navW`), labels: section head **Menu**, items **Recipes**, **Menu items**,
**Ingredients**, **Suppliers** (line 124 there — note the nav says "Ingredients"; the
page title says "Ingredients & prices").

**Page header** (line 12): `display:flex; align-items:center; gap:18px; padding:12px 20px;
border-bottom:1px solid #e8ebf0`.
- Title: `800 21px Nunito`, `letter-spacing:-.01em`, then subtitle `#5b6475 14px`
  (siblings in the same flex row, 18px gap).
- Titles/subtitles verbatim (line 540 `PAGE`):
  - Recipes — `set a recipe once; every item made from it follows`
  - Menu items — `everything you sell, with price and margin`
  - Ingredients & prices — `what things cost`
- Spacer `flex:1`.
- Save status: `#8a93a3 14px`, text `{savedLabel}` — design shows `Saved` after any
  mutation, `Loading workbook…` before data. (Section 3 replaces this with a real
  write status; keep the slot and styling.)
- Primary add button: `background:#4a6fd1; color:#fff; border-radius:18px;
  padding:9px 18px; font-size:16px; font-weight:700; transition: background .15s,
  border-color .15s`. Label per tab: `+ New recipe`, `+ Add item`, `+ Add ingredient`.

**Body** (line 24): `flex:1; min-height:0; display:flex`.

**Left rail — desktop only** (`wideRail = !compact`, lines 25–38):
`width:210px; flex:none; border-right:1px solid #eef0f4; overflow-y:auto;
padding:10px 10px 16px; display:flex; flex-direction:column; gap:2px`.
- Group head: `padding:14px 10px 6px; font:700 11px Nunito; color:#8a93a3;
  text-transform:uppercase; letter-spacing:.08em`.
- Row: `min-height:36px; padding:0 10px; border-radius:10px; font-size:14px;
  font-weight:{on.fw}; background:{on.bg}; color:{on.fg}; display:flex;
  justify-content:space-between; align-items:center; gap:8px`. Label ellipsised
  (`white-space:nowrap; overflow:hidden; text-overflow:ellipsis`), count
  `font-size:12px; opacity:.6; flex:none`.

**Chip row — compact only** (lines 20–22), placed between header and body:
`display:flex; gap:8px; overflow-x:auto; scrollbar-width:none; padding:12px 20px;
border-bottom:1px solid #eef0f4`. Each chip = one rail **row** (heads dropped,
`railChips = rail.filter(r => r.isRow)`): `height:36px; border:1px solid {on.bd};
border-radius:999px; padding:0 14px; font-size:14px; font-weight:{on.fw};
white-space:nowrap; background:{on.bg}; color:{on.fg}; gap:6px`, label then count
(`12px, opacity .6`). Horizontal scroll, no scrollbar.

### 0.7 Layout widths (from `base`, line 610)

| Value | Desktop (1440) | Compact (iPad 1180) |
|---|---|---|
| Rail | 210px column | replaced by chip row |
| Recipes cost column `costW` | 380px | 320px |
| Menu card min width `cardMin`, drawer closed | 200px | 170px |
| Menu card min width, drawer open | 180px | 150px |
| Menu drawer `drawerW` | 420px | 380px |
| Ingredients list `listW` | 320px | 290px |

Resulting content widths (viewport − shell nav − rail): desktop `1440−232−210 = 998px`,
compact `1180−200 = 980px`. So: menu grid ≈ 4 columns desktop closed / 2 open;
5 compact closed / 3 open. Recipes editor column 618px desktop / 660px compact.
Ingredient detail 678px desktop / 690px compact.

**Below 1180px** (CLAUDE.md §10 floor: must survive 375px) the design says nothing.
Required behaviour: at `< 900px` the Menu drawer, Recipes cost column and Ingredient
detail become full-width stacked panels (drawer over the grid with a back affordance;
cost column below the editor; list → detail navigation), chip row always used, and
the per-size grids keep `minmax(0,1fr)` columns so nothing scrolls horizontally.

### 0.8 Common controls
- **Field 40** (menu drawer): `height:40px; box-sizing:border-box; border:1px solid #e2e6ed;
  border-radius:10px; padding:0 10px; font-size:15px; background:#fff; outline:none`.
- **Field 38** (recipes): same with `height:38px; font-size:14px`.
- **Small label over field** (recipes flavour/swap cards): `display:flex;
  flex-direction:column; gap:4px; font-size:11px; color:#8a93a3; font-weight:700;
  text-transform:uppercase; letter-spacing:.05em; min-width:0` — the inner control
  resets `font-weight:400; text-transform:none; letter-spacing:0; color:#1f2633`.
- **Dashed add button**: `border:1.5px dashed #d5dae3; border-radius:10px` (or 14px in
  pill form), `color:#3558b8` where specified.
- **Icon close/remove `×`**: 36×36 (32×32 in flavour card), `border-radius:10px`,
  `color:#8a93a3; font-size:18px`; the drawer close has `background:#f1f3f7;
  color:#5b6475`.
- **Toggle**: track `40×24`, `border-radius:999px`, on `#3fa57a` / off `#d5dae3`,
  `transition: background .15s`; knob `18×18` white circle, `top:3px`,
  `box-shadow:0 1px 2px rgba(0,0,0,.2)`, `left` 19px on / 3px off (menu drawer) or
  18px / 2px (flavour card), `transition:left .15s`.
- **Section title**: `font:800 16px Nunito; letter-spacing:-.01em`.
- Focus: design has `outline:none` on inputs. CLAUDE.md §10.10 requires visible keyboard
  focus — add `:focus-visible { box-shadow: 0 0 0 2px #4a6fd1 }` (does not change the
  resting design). Respect `prefers-reduced-motion` by dropping the `.15s` transitions.

---

## 1. Recipes tab (`tab === 'rec'`)

### 1.1 Rail (logic lines 905–912)
- For each distinct template category `c` (in first-appearance order): head
  `c.replace(' / flavoured latte', '')` (so `Latte / flavoured latte` → `Latte`), then
  one row per template in that category: label = template name, count =
  `activeFlavours × sizes.length` (number of sellable items it makes). Selected by id.
- Then head `Not in a recipe`, row `One-off items`, count = number of products with no
  `templateId`.
- Selecting a row sets `selT` and **discards any unsaved draft** (`draft: null`) — see
  §1.9 for the guard this needs.
- Default selection on load: template named `Flavoured Latte`, else first (line 593).
- Header add button: `+ New recipe` → creates `{ name:'New recipe', cat:'Latte /
  flavoured latte', sizes:['S','M','XL'], components:[], prep:{S:45,M:50,XL:60},
  prepMeasured:false, basePrice:{S:0,M:0,XL:0}, flavours:[], mods:[], history:[] }`
  and selects it (line 911).

### 1.2 Layout
Two columns inside the body (line 289): editor `flex:1; min-width:0; overflow-y:auto;
padding:20px 22px 32px` (white) + cost column `width:{costW}; border-left:1px solid
#eef0f4; overflow-y:auto; padding:18px 16px; background:#f6f7fa`.

### 1.3 One-off items view (`selT === '__one'`, lines 291–295, logic 913–914)
- Title `One-off recipes` — `800 22px`, `letter-spacing:-.01em`.
- Meta `#5b6475 14px`, `margin:4px 0 14px`, verbatim:
  `{N} items have their own hand-written recipe (cakes, bottled drinks, toasties, meal deals…). That is correct for them. Tap one to edit it in Menu items.`
- Wrap of pills (`gap:6px`): `border:1px solid #d5dae3; border-radius:12px;
  padding:6px 14px; font-size:14px`, one per one-off product sorted A–Z. Click →
  navigate to Menu items tab with that item open (`tab:'menu', selP, cat:'All', q:''`).
- Cost column is empty in this view (renders nothing, keeps the grey background).

### 1.4 Template editor header (lines 297–298)
- Name input: `flex:1; border:none; border-bottom:1px solid #e8ebf0; font:800 24px
  Nunito; letter-spacing:-.01em; padding:2px 0`. Right of it (baseline-aligned, gap
  10px) the raw category `#5b6475 14px nowrap` (e.g. `Latte / flavoured latte`).
- Meta `#5b6475 14px; margin:4px 0 16px`:
  `Makes {A×n} menu items: {A} flavours × {n} sizes ({S, M, XL})` where A = active
  flavours, n = sizes.

### 1.5 "What goes in, per size" (lines 300–318)
Wrapper `flex column; gap:10px; margin-bottom:26px`. Title `What goes in, per size`.

**Component card** (one per component): `flex column; gap:10px; background:#fff;
border:1px solid #eef0f4; border-radius:14px; padding:12px 14px`.
- Top row (`gap:8px; align-items:center`):
  1. Role select, field-38 with overrides `width:130px; flex:none; font-weight:700;
     font-size:12px; letter-spacing:.04em; background:#f6f7fa; border-color:#f6f7fa`.
     Options: `COFFEE, MILK, BASE, FLAVOUR, TOPPING, PACKAGING, SUNDRY` (shown
     uppercase as values).
  2. `flex:1`: if one ingredient for all sizes → ingredient select (field-38, width
     100%) with options `— pick ingredient —` + all ingredients A–Z; if the slot has a
     different ingredient per size (`ingBySize`) → text `Different item for each size`
     (`#5b6475 14px`).
  3. **Swappable** toggle, `title="Customer can swap this"`: `height:38px; padding:0 10px;
     border-radius:10px; font-size:13px; color:#5b6475; gap:6px`; box `18×18; border:1.5px
     solid #c9cfda; border-radius:5px; font-size:12px; color:#3558b8; font-weight:800`
     containing `✓` when on, empty when off. Label `Swappable`.
  4. Remove `×` (36×36).
- Size grid: `display:grid; grid-template-columns:repeat(n, minmax(0,1fr)); gap:8px`.
  Cell: `flex column; gap:6px; background:#f6f7fa; border-radius:10px; padding:8px`:
  - size label `11px 800 #8a93a3` (`S`, `M`, `XL`, `One`);
  - if per-size ingredient: ingredient select (field-38, `font-size:13px`);
  - qty row (`gap:6px`): input field-38 `text-align:right; flex:1` with
    **changed-state** colours: `border-color:#d4554a; background:#fdf1ef` when this cell
    differs from the saved version, else `border-color:#d5dae3; background:#fff`
    (`cellStyle`, line 925); unit label `#8a93a3 13px; width:28px` (`L`, `ml`, `kg`,
    `g`, `unit` — from `norm(c.unit)`, rendered verbatim, so `l` shows as `l`; show `L`).
- Changed-cell rule (line 929): a cell is "changed" if the component is new, or its
  qty for that size differs from saved, or its per-size ingredient differs.

**Flavour placeholder row** (line 313, shown when any flavour has an ingredient):
`display:flex; gap:10px; background:#f6f7fa; border-radius:14px; padding:12px 14px;
font-size:14px; color:#5b6475` — tag `FLAVOUR` (`700 12px; letter-spacing:.04em;
color:#1f2633`) then `Set by the flavour chosen below, per size`.

**Time + base price** (lines 314–316): `grid; columns:minmax(0,1fr) minmax(0,1fr);
gap:10px`, two cards styled like component cards.
- Card 1 heading row: `Time to make` (`800 14px`) + state link (`12px 700`, clickable):
  `timed` in `#5b6475` when measured, `estimate, not timed yet` in `#d4554a` when not.
  Clicking toggles the measured flag. Grid of n cells: label = size (small-label
  style), input field-38 right-aligned, **italic when not measured**, changed-state
  colours, suffix `s` (`#8a93a3 12px`, not uppercased).
- Card 2 heading: `Base price` (`800 14px`) + ` before flavour extra` (`400 12px
  #8a93a3`). Grid of n cells: size label, `£` prefix (`#8a93a3 13px`), input
  right-aligned, changed-state colours. Value shown as `(pence/100).toFixed(2)`
  (keeps raw text while typing).

**Add component** (line 317): `align-self:flex-start; height:40px; border:1.5px dashed
#d5dae3; border-radius:10px; padding:0 14px; font-size:14px; font-weight:600;
color:#3558b8` — `+ Add component`. Adds `{ role:'SUNDRY', ing:'', qty: '1' for every
size, unit:'unit', subst:false }`.

### 1.6 Flavours (lines 320–338)
- Heading row (`gap:10px; baseline; margin-bottom:6px`): `Flavours · {count}` (all
  flavours, active or not) + `each flavour makes one menu item per size` (`#8a93a3 13px`).
- Grid `repeat(auto-fill, minmax(250px,1fr)); gap:10px`. Card = component-card style
  with `background:{f.bg}` (`#fdf1ef` when the flavour is flagged `missing` and has
  no ingredient, else transparent → white page) and `opacity: 1` active / `.5`
  inactive.
  - Row 1: name input (field-38 borderless: `border-color:transparent; padding:0 4px;
    font-weight:700; flex:1`), on-menu toggle (`title="On menu"`, knob 18/2px), remove
    `×` (32×32).
  - `Ingredient` select (small-label style); border `#d4554a` when missing, else
    `#d5dae3`. Options: `— none —` + ingredients whose category is Syrup, Specialty,
    Chocolate or Tea, or named Honey, A–Z (line 805).
  - Grid `70px 80px minmax(0,1fr); gap:8px`: `Qty` (right-aligned), `Extra £`
    (right-aligned, `(priceDelta/100).toFixed(2)`), `Season` select (`all year` +
    seasons by name).
- Below: `+ Add flavour` (`border:1.5px dashed #d5dae3; border-radius:14px;
  padding:6px 14px; font-size:14px`) and note `#8a93a3 13px`:
  `Adding a flavour creates {n} sellable item{s} when you apply.` (singular when n=1).
  New flavour default: `name 'New flavour', ing '', qty '30', unit 'ml', priceDelta 0,
  season null, active true`. Wrapper margin `10px 0 22px`.

### 1.7 Swaps a customer can ask for (lines 340–357)
- Title `Swaps a customer can ask for`. Chips (`gap:6px; margin-bottom:10px`):
  `border:1px solid #e8ebf0; border-radius:14px; padding:6px 14px; font-size:14px;
  background/colour from on(enabled for this template)`. Label
  `{modifier name} +{gbp(price)}` with `£0.00` rendered `£0` (e.g. `Decaf +£0`,
  `Oat milk +£0.50`). **Click saves immediately** (bypasses draft, line 948).
- Modifier cards grid `auto-fill minmax(250px,1fr); gap:10px` — **global** modifiers
  (all, not only enabled): name input (borderless, bold); two-col `Does`
  (`replaces`=SUBSTITUTE / `adds`=ADD) and `To` (roles); `Ingredient` (all
  ingredients); two-col `Qty` (placeholder `same`) and `Charge £` (italic when the
  charge is a guess, `src === 'guess'`).
- Footnote `#8a93a3 13px; margin:6px 0 22px`, verbatim:
  `Italic charges are guesses; the workbook only prices oat milk, coconut milk, syrup pumps and marshmallow. Swaps save straight away.`

### 1.8 Recipe history (lines 359–361)
Title `Recipe history`. Rows newest first: `display:flex; gap:12px; font-size:14px;
padding:4px 0; border-bottom:1px solid #e8ebf0` — `from {fmtD(date)}` (`#5b6475;
width:110px`) + text (first three diff lines joined ` · ` plus ` · +N more`). Final
line `#8a93a3 14px`: `Imported from the workbook. Every change after this keeps its own date.`

### 1.9 Cost column (lines 364–390)
- Header row (`gap:8px; margin-bottom:8px`): `What it costs` (title) + flavour picker
  select (`max-width:170px; border:1px solid #d5dae3; border-radius:10px; padding:2px
  4px; font-size:13px`) listing all flavours. Default = first active flavour with an
  ingredient, else first active, else none.
- Cost grid: `grid-template-columns:96px repeat(n,minmax(0,1fr)); gap:0 8px;
  font-size:14px; background:#fff; border-radius:14px; padding:6px 14px`. Every cell
  `padding:8px 0; border-bottom:1px solid #f1f3f6; white-space:nowrap`; first column
  left-aligned `#5b6475`, values right-aligned `#1f2633`. Header row: blank + sizes
  (`#5b6475`). Rows (lines 961–966), for the selected flavour:

  | Row label | Value | Weight | Red (`#d4554a`) when |
  |---|---|---|---|
  | `Price` | `gbp(basePrice[z] + flavour.priceDelta)` | 400 | — |
  | `Ingredients` | `gbp(Σ line costs)` | 400 | — |
  | `Staff time` | `gbp(prep[z]/3600 × rate)` | 400 | — |
  | `Margin` | `round((p−c)/p × 100)%` or `—` | 700 | < 60% |
  | `After staff` | `round((p−c−labour)/p × 100)%` or `—` | 400 | < 40% |
  | `Per minute` | `gbp((p−c)/(prep/60))` or `—` | 700 | — |

- Rate line (`margin:10px 0 18px; 14px #5b6475`): `Staff time at £` + input (`width:52px;
  border:1px solid #d5dae3; border-radius:10px; padding:1px 5px; text-align:right`)
  showing `(rate/100).toFixed(2)` (default `14.50`) + `an hour`. **Saves immediately,
  globally** in the design.
- **Clean state** (no draft): box `border:1px solid #e8ebf0; border-radius:14px;
  padding:12px; font-size:14px; color:#5b6475`:
  `No unsaved changes. Edit any quantity, price or flavour and the effect shows up here before anything is saved.`
- **Dirty state — impact preview** (lines 372–386): white box `border:1px solid #e8ebf0;
  border-radius:14px; padding:14px`:
  - Title `What this change does`.
  - Diff lines (`14px; padding:2px 0`), max 8 then `…and {k} more`. Line templates
    (logic 974–984), verbatim:
    `Renamed to {name}` · `Added {ingredient or role lowercased}` ·
    `{old ing} → {new ing}` · `{name} {size}: {old} → {new} {unit}` ·
    `{name} can now be swapped` / `{name} can no longer be swapped` ·
    `Removed {ing}` · `Prep {size}: {old}s → {new}s` · `Price {size}: £a → £b` ·
    `New flavour: {name}` · `{old} renamed {new}` · `{flavour}: {old ing} → {new ing}` ·
    `{flavour} syrup {old} → {new}` · `{flavour} extra £a → £b` ·
    `{flavour} back on the menu` / `{flavour} off the menu` ·
    `{flavour} season → {season name or 'all year'}` · `Removed flavour: {name}` ·
    `Prep times marked as timed` / `Prep times marked as estimates`.
  - Figures grid (`1fr auto; gap:4px 10px; margin:10px 0; 14px; border-top:1px solid
    #e8ebf0; padding-top:8px`), labels `#5b6475`, values bold:
    - `Menu items affected` — count of active flavour×size whose cost or price changed,
      or which are new / re-activated.
    - `Cost per item` — `no change`, a single signed value, or a range
      `+£0.012 to +£0.036` (3 dp, U+2212 minus).
    - `Thinnest margin after` — `{flavour} {size}, {before}% → {after}%` (1 dp), red if
      after < 60%.
    - `Over a month` — value in `#8a93a3`: `needs item sales from Lightspeed`.
  - Warnings (`#d4554a 13px`), verbatim templates:
    `{k} flavour(s) has/have no flavour ingredient, so their cost is too low: {names…}` ·
    `A size has no price.` · `Prep times are estimates, so staff-time figures are too.`
  - Note `13px #5b6475; margin:8px 0 12px`:
    `Changes apply from today. Past sales keep the recipe they were sold with; history is never rewritten.`
  - Buttons (`gap:8px`): `Discard` (`flex:1; border:1px solid #e8ebf0; border-radius:18px;
    padding:5px; 14px`) and `Apply from today` (`flex:2; background:#4a6fd1; color:#fff;
    border-radius:18px; padding:7px; 14px 700`).

### 1.10 State machine
- `clean` → any edit to name, component, cell, prep, base price, flavour, measured
  flag → `dirty` (local draft; nothing persisted). Changed cells go red.
- `dirty` → `Discard` → `clean` (draft dropped).
- `dirty` → `Apply from today` → **must go through a server preview first** (§3.1) →
  commit → `clean`, new history row, generated menu items updated.
- `dirty` → select another rail row → design silently drops the draft. **Required
  change:** prompt `You have unsaved recipe changes. Discard them?` (Discard / Keep
  editing) — silent loss of an edit is worse than the prompt.
- Swaps chips, modifier card fields and staff rate **write immediately** in the design;
  section 4 C-6/C-7 moves them behind preview/confirmation.

### 1.11 Compact
Rail becomes chips (template rows + `One-off items`, heads dropped). Cost column 320px.
Per-size grids unchanged (fr columns). Flavour/modifier grids reflow at 250px minimum.

---

## 2. Menu items tab (`tab === 'menu'`)

### 2.1 Rail (logic 627–633)
- Row `All items` (count = all products; selecting resets paging to 24).
- Heads `Drinks`, `Food`, `Other` (skipped if empty); under each, categories whose type
  is that head, A–Z; label strips ` (May 2026)` (so `DISCONTINUED (May 2026)` →
  `DISCONTINUED`); count = products in category.
- Category→type map is hard-coded (`TYPE`, line 512) plus user-added categories.
- **Desktop-only** footer under the rail (lines 31–36): `margin:14px 12px 0;
  border-top:1px solid #e8ebf0; padding-top:10px` — input placeholder `New category`
  (`border:1px solid #e8ebf0; border-radius:10px; padding:4px 8px; font-size:14px`),
  then two buttons `+ drinks` and `+ food` (`flex:1; text-align:center; border:1px
  solid #d5dae3; border-radius:12px; font-size:13px; padding:1px`). Adds the category
  (ignored if blank or duplicate) and selects it. Not present in compact.

### 2.2 Grid area (lines 40–61)
Column `flex:1; background:#f6f7fa`.
- Toolbar (`gap:10px; padding:14px 20px 4px`): search box `height:40px; border:1px solid
  #e2e6ed; border-radius:12px; padding:0 12px; background:#fff` with a CSS magnifier
  (12×12 ring, `border:2px solid #8a93a3; border-radius:50%; margin-right:8px`) and a
  borderless input, placeholder `Search the menu` (15px). Right: meta `#5b6475 14px
  nowrap`: `{N} items` or `{N} items in {category}`.
- Scroll region `padding:12px 20px 24px`; grid `repeat(auto-fill,
  minmax({cardMin},1fr)); gap:14px`.
- Filter: category match AND name contains query (case-insensitive); sorted A–Z.
- **Paging**: first 24 cards; `Show {moreLeft} more` button (`height:42px; background:#fff;
  border:1px solid #e2e6ed; border-radius:12px; padding:0 20px; 14px 700 #3558b8`,
  centred, `padding-top:18px`) adds 48. Selecting `All items` resets to 24.
  (Selecting a category does **not** reset the limit in the design — reset it on any
  filter change.)
- Empty: `Nothing here yet. Use “Add item”.` (`padding:60px; centred; #8a93a3 15px`).

**Card** (lines 45–56): `background:#fff; border-radius:16px; overflow:hidden;
transition:box-shadow .15s`; shadow `0 1px 2px rgba(31,38,51,.06),0 0 0 1px
rgba(31,38,51,.04)` normally, `0 0 0 2px #4a6fd1` when selected; `opacity:.55` when off
the menu.
- Photo: `position:relative; aspect-ratio:4/3; background:#eef0f4`, `<image-slot
  id="menu-{id}" shape="rect" placeholder="Photo">` filling it. Margin badge absolutely
  at `top:8px; left:8px; pointer-events:none; border-radius:999px; padding:3px 9px;
  12px 700`:
  - value = lowest margin across the item's sizes, `{n}%`, or `no price` when no size
    has a price;
  - colours: none → bg `#f1f3f7`, text `#5b6475`; < 60% → `#fdecea` / `#c2453b`;
    otherwise `#e8f5ee` / `#237a57`.
- Body `padding:10px 12px 12px; gap:3px; min-height:64px`: name `15px 700;
  line-height:1.25`, clamped to 2 lines; price line `#5b6475 13px`:
  sizes joined ` · `, each `{size} £x.xx` (size label omitted when only one size), or
  `no price`; if off menu, `Off the menu` (`#8a93a3 12px 700`).
- Click → opens drawer for that item, size index 0.

### 2.3 Item drawer (lines 64–125)
`width:{drawerW}; border-left:1px solid #eef0f4; background:#fff; flex column`.
- Top bar (`gap:8px; padding:12px 16px; border-bottom:1px solid #eef0f4`):
  `{type} · {category}` (`13px #5b6475`, e.g. `Drinks · Latte / flavoured latte`) + close
  `×` (36×36, `#f1f3f7` bg).
- Body `overflow-y:auto; padding:16px 18px 24px; gap:18px`:
  1. **Photo** `height:170px; border-radius:14px; overflow:hidden; background:#eef0f4`,
     `<image-slot id="menu-{id}" placeholder="Drop a photo of this item">` (same id as
     the card, so both show the same photo).
  2. **Name** input `font:800 22px; letter-spacing:-.01em; border:none; padding:0`.
     **Availability toggle** + label `On the menu` / `Off the menu` (`14px 600`; track
     on `#3fa57a`, knob 19px/3px).
     Two-col grid (`gap:8px`): `Category` select (label `12px 700 #8a93a3`; field-40)
     listing all categories A–Z; `Note` input placeholder `e.g. CakeSmiths`.
  3. **Template banner** (only when made from a recipe): `background:#edf1fc;
     border-radius:12px; padding:12px 14px; 14px #2d3f75; gap:8px`, text verbatim
     `Made from the {template} recipe. Change it there; edits here are overwritten next time that recipe is applied.`
     and a button `Open recipe` (`background:#fff; border-radius:10px; padding:7px
     12px; 700 #3558b8`) → Recipes tab with that template selected.
  4. **Sizes and price**: title; size buttons (`min-width:64px; height:52px; border:1px
     solid {on.bd}; border-radius:12px; padding:0 12px; on.bg/on.fg`) showing size
     (`15px 800`) over price (`12px opacity .8`); for each missing size of
     `S, M, XL, One` a dashed add button `+ {size}` (`min-width:52px; height:52px;
     border:1.5px dashed #d5dae3; 13px #5b6475`) — adding copies the current size's
     price and lines, sizes kept in `S, M, XL, One` order.
     Three stat tiles (`repeat(3,minmax(0,1fr)); gap:8px`, `border-radius:12px;
     padding:10px 12px; label 12px 700 #8a93a3; value 800 18px`):
     - `Sell price` — `£` + inline input (tile bg `#f6f7fa`).
     - `Costs us` — `gbp(cost)` (bg `#f6f7fa`).
     - `Margin` — `{n}%` or `no price`; tile bg `#f6f7fa` (no price) / `#fdecea` (<60%) /
       `#e8f5ee`; value colour `#5b6475` / `#c2453b` / `#237a57`.
  5. **Recipe · {size}** (title; or `Add a size to start` when no sizes) with
     `profit {gbp(price−cost)}` (`13px #5b6475`) on the right. One card per line
     (`border:1px solid #eef0f4; border-radius:12px; padding:10px; gap:8px`):
     - row 1: ingredient select (field-40, `— pick ingredient —` + all A–Z) + remove `×`;
     - row 2: qty input (`width:84px`, right) + unit select (`width:76px`, `L ml kg g
       unit`) + unit cost (`12px #8a93a3`, italic if the ingredient's price is an
       estimate, ellipsised; `£0.734/L`) + line cost (`15px 700`, `gbp`).
     Choosing an ingredient sets the line unit to the ingredient's unit.
     Buttons: `+ Add ingredient` (dashed, `600 #3558b8`; adds `{ing:'', qty:'1',
     unit:'unit'}`) and `Copy to other sizes` (plain `#5b6475`; overwrites every other
     size's lines with this size's).
     Estimate note (if any line's ingredient is estimated): `background:#fdf6e7;
     border-radius:10px; padding:8px 12px; 13px #5b6475`:
     `<i>Italic</i> costs are estimates. {k} of {n} ingredients here: {names}.`
  6. **All sizes** table: header grid `48px repeat(4,minmax(0,1fr))`, `11px 700 #8a93a3
     uppercase letter-spacing:.06em`, border-bottom `#eef0f4`: `Size | Price | Cost |
     Profit | Margin` (all but Size right-aligned). Rows `14px; padding:8px 0;
     border-bottom:1px solid #f1f3f6`; margin bold, red `#d4554a` when < 60%.
     Footnote `#8a93a3 12px`: `Margins under 60% are marked. Prices exclude VAT.`
  7. **Actions** (`border-top:1px solid #eef0f4; padding-top:14px; gap:8px`), three
     equal buttons `height:40px; border-radius:10px; 14px`: `Duplicate` (border
     `#e2e6ed`, 600), `Remove size` (same), `Delete item` (`background:#fdecea;
     color:#c2453b; 700`). Duplicate names the copy `{name} (copy)` and opens it.

- Header add `+ Add item` → new item `New item` in the selected category (or `Latte /
  flavoured latte` when `All`), one size `M` at £0 with no lines; opens it and clears
  search.

### 2.4 Compact
Chip row replaces rail; **no new-category footer**. Card min 170px (150px with drawer).
Drawer 380px.

---

## 3-pre. (see §3 for behaviour; this section is visual only)

## 2b. Ingredients & prices tab (`tab === 'ing'`)

### 2b.1 Rail (logic 691–694)
Rows `All` (count all), `Estimated prices` (count `est`), head `Categories`, then each
ingredient category in first-appearance order with its count (Dairy, Dairy alt, Coffee,
Syrup, Tea, Chocolate, Specialty, Packaging, Sundries, Cake (CakeSmiths), Cake, Food,
Bottled in the design data).

### 2b.2 List column (lines 130–147)
`width:{listW}; border-right:1px solid #e8ebf0; flex column`.
- Filter block (`padding:10px 12px; border-bottom:1px solid #e8ebf0; gap:6px`):
  - search input, placeholder `Search ingredients` (`border:1px solid #e8ebf0;
    border-radius:12px; padding:6px 10px; 15px`);
  - 2×2 grid of selects (`border:1px solid #d5dae3; border-radius:10px; padding:3px 4px;
    14px`):
    - supplier: `All suppliers`, `No supplier yet`, then each supplier name;
    - price: `All prices` / `From invoice` / `Estimates`;
    - use: `Used or not` / `Used in recipes` / `Not used`;
    - sort: `Sort A–Z` / `Cost per unit` (descending) / `Most used` (descending by
      number of menu items) / `Fewest suppliers` (ascending, ties A–Z).
  - Meta row (`#8a93a3 13px`, space-between): `{shown} of {total} · italic = estimated price`
    and, when any filter/search/category is active, underlined `Clear filters` (resets
    supplier, price, use, search, category; not sort).
- Rows (`padding:9px 14px; border-bottom:1px solid #e8ebf0; bg #edf1fc when selected`):
  line 1 name (`16px`) and unit cost right (`14px nowrap`, italic when estimated,
  `£0.734/l` — note the list uses `norm(unit)` so litres render as lowercase `l`;
  render `L`); line 2 `#5b6475 13px`:
  `{category} · {supplier names joined ', ' or 'no supplier'} · in {n} items`.
- Default selection: first ingredient.

### 2b.3 Detail column (lines 148–181)
`flex:1; overflow-y:auto; padding:18px 22px`.
- Name input (`800 24px; border-bottom:1px solid #e8ebf0`) + `Delete` pill
  (`border:1px solid #d4554a; color:#d4554a; border-radius:14px; padding:6px 14px; 14px`).
- Field grid `repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin:16px 0`, labels
  `14px #5b6475`, controls `border:1px solid #e8ebf0; border-radius:10px; padding:5px
  (8px for inputs); 15px`: `Category` (select, existing categories), `Pack size`
  (input), `Unit` (select `L ml kg g unit`), `Pack cost £` (input, `(pence/100).toFixed(2)`).
- Cost panel (`flex; gap:18px; wrap; border:1px solid #e8ebf0; border-radius:14px;
  padding:12px 16px; margin-bottom:16px`):
  `Cost per unit` (`14px #5b6475`) over `{unitPrice} / {unit}` (`22px`, italic if est);
  checkbox (20×20, `border:1px solid #e8ebf0; border-radius:6px`, `✓`) + `Price is an estimate`
  (15px); spacer; impact text (`14px #5b6475; max-width:320px`):
  `Changing this price updates the cost of {n} menu items straight away.` or
  `Not used in any recipe yet.`
- **Where to buy**: title + note `#8a93a3 13px`:
  `Recipe cost uses the ★ price from {preferred supplier}. Editing pack or cost above updates it.`
  (blank when no preferred link). Box `border:1px solid #e8ebf0; border-radius:14px;
  padding:4px 14px 10px; margin-bottom:16px`. Offer rows grid
  `28px minmax(0,1.4fr) minmax(0,1.4fr) 110px minmax(0,1fr); gap:10px; padding:8px 0;
  border-bottom:1px solid #e8ebf0; 14px`:
  - star `★` (preferred, `#d4554a`) / `☆` (`#8a93a3`), 17px, click = make preferred;
  - supplier name underlined, click → Suppliers tab;
  - pack `{pack} {unit} for £x.xx[ · sku]` (`#5b6475`);
  - unit cost right-aligned `£x/unit`;
  - comparison: blank when one offer; `cheapest` (`#1f2633`) or `+{n}% vs cheapest`
    (`#5b6475`).
  Empty: `No supplier linked yet. The price above is used as-is.` (`#8a93a3 14px`).
  Footer row: `Link to` + select (`+ add a supplier…` then unlinked suppliers); picking
  one adds a link with the current pack/cost (preferred if it is the first).
- `Notes` input (label style as the field grid) — bound to the workbook's free-text
  supplier column (`I.supplier`, e.g. `Real invoice — 3.4L = £2.37`).
- `Used in {n} item(s)` title, then pills (`border:1px solid #d5dae3; border-radius:12px;
  padding:6px 14px; 14px`) for up to 40 menu items; click → Menu items tab with it open.
- Header add `+ Add ingredient` → `New ingredient`, category = selected rail category
  (or `Sundries`), pack 1 unit £0, **est: true**; selects it and clears search.

### 2b.4 Compact
Chip row replaces rail; list 290px. Detail's field grid collapses via `auto-fit
minmax(150px)`. Offer grid is fixed-template — at 690px it fits.

