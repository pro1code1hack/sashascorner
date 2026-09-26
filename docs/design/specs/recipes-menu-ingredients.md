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
preview, integer pence, decimal-string quantities).

**Layout of this document:** §V visual spec (V0 shared, V1 Recipes, V2 Menu items,
V3 Ingredients) · §D data contract · §B backend mapping (EXISTS / DERIVABLE / MISSING)
· §C conflicts with invariants and decisions · §A proposed API · §F frontend
replacement and reuse · §S summary of MISSING. Build the pixels from §V and the
behaviour from §B–§A.

---

## V0. Shared frame, tokens and primitives

### V0.1 Fonts and global
- Font: `Nunito` 400/500/600/700/800 (Google Fonts). `body { font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased; color: #1f2633 }`.
- Inputs/selects inherit Nunito, colour `#1f2633`, background `#fff`.
- Links `#1f2633`, hover `#4a6fd1`.

### V0.2 Colour tokens (every literal used in these three tabs)

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

### V0.3 Radii used
999px (pills, toggles, chips), 18px (primary header button, Discard/Apply), 16px (menu
cards, standalone tab pills), 14px (component cards, cost grid, panels, secondary pills),
12px (inputs 40px on menu search, size buttons, stat tiles, drawer lines, Show more,
banner, chips 12px in "used in"), 10px (most inputs/selects, rail rows, icon buttons),
6px (estimate checkbox), 5px (Swappable checkbox).

### V0.4 Formatting functions (design, lines 515–518, 537)
```js
const gbp = p => (p < 0 ? '−£' : '£') + (Math.abs(p) / 100).toFixed(2);          // pence → "£3.90", "−£0.60" (U+2212)
const unitPrice = p => { const v = Math.abs(p)/100;                               // pence per unit, variable dp
  return '£' + (v >= 1 ? v.toFixed(2) : v >= 0.1 ? v.toFixed(3) : v.toFixed(4)); };
const norm = u => { u = (u||'').toLowerCase(); return u === 'each' ? 'unit' : u; };
const conv = (lu, iu) => ...  // L<->ml, kg<->g; ANY OTHER MISMATCH RETURNS 1 (see §C C-9)
const fmtD = iso => new Date(...).toLocaleDateString('en-GB', { weekday:'short', day:'numeric', month:'short' }); // "Sat 26 Sep"
```
Build these on `web/src/lib/dec.ts` + `format.ts` (§F): `gbp` → `moneyDec`/`money`;
`unitPrice` → a new `unitPriceDec(d: Dec)` in `format.ts` with the same 2/3/4-dp rule;
never `parseFloat`.

### V0.5 Units shown in the UI
`UNITS = ['L', 'ml', 'kg', 'g', 'unit']` (line 514). Backend enum is
`L|KG|ML|G|EACH`. Display mapping: `L→L`, `ML→ml`, `KG→kg`, `G→g`, `EACH→unit`
(and `unit` in per-unit labels: `£0.0450/unit`). `loaf` exists in `sc-data.js` for one
ingredient and has no backend equivalent — drop it.

### V0.6 App frame (embedded in the shell)
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
  mutation, `Loading workbook…` before data. (§B replaces this with a real
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

### V0.7 Layout widths (from `base`, line 610)

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

### V0.8 Common controls
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

## V1. Recipes tab (`tab === 'rec'`)

### V1.1 Rail (logic lines 905–912)
- For each distinct template category `c` (in first-appearance order): head
  `c.replace(' / flavoured latte', '')` (so `Latte / flavoured latte` → `Latte`), then
  one row per template in that category: label = template name, count =
  `activeFlavours × sizes.length` (number of sellable items it makes). Selected by id.
- Then head `Not in a recipe`, row `One-off items`, count = number of products with no
  `templateId`.
- Selecting a row sets `selT` and **discards any unsaved draft** (`draft: null`) — see
  §V1.10 for the guard this needs.
- Default selection on load: template named `Flavoured Latte`, else first (line 593).
- Header add button: `+ New recipe` → creates `{ name:'New recipe', cat:'Latte /
  flavoured latte', sizes:['S','M','XL'], components:[], prep:{S:45,M:50,XL:60},
  prepMeasured:false, basePrice:{S:0,M:0,XL:0}, flavours:[], mods:[], history:[] }`
  and selects it (line 911).

### V1.2 Layout
Two columns inside the body (line 289): editor `flex:1; min-width:0; overflow-y:auto;
padding:20px 22px 32px` (white) + cost column `width:{costW}; border-left:1px solid
#eef0f4; overflow-y:auto; padding:18px 16px; background:#f6f7fa`.

### V1.3 One-off items view (`selT === '__one'`, lines 291–295, logic 913–914)
- Title `One-off recipes` — `800 22px`, `letter-spacing:-.01em`.
- Meta `#5b6475 14px`, `margin:4px 0 14px`, verbatim:
  `{N} items have their own hand-written recipe (cakes, bottled drinks, toasties, meal deals…). That is correct for them. Tap one to edit it in Menu items.`
- Wrap of pills (`gap:6px`): `border:1px solid #d5dae3; border-radius:12px;
  padding:6px 14px; font-size:14px`, one per one-off product sorted A–Z. Click →
  navigate to Menu items tab with that item open (`tab:'menu', selP, cat:'All', q:''`).
- Cost column is empty in this view (renders nothing, keeps the grey background).

### V1.4 Template editor header (lines 297–298)
- Name input: `flex:1; border:none; border-bottom:1px solid #e8ebf0; font:800 24px
  Nunito; letter-spacing:-.01em; padding:2px 0`. Right of it (baseline-aligned, gap
  10px) the raw category `#5b6475 14px nowrap` (e.g. `Latte / flavoured latte`).
- Meta `#5b6475 14px; margin:4px 0 16px`:
  `Makes {A×n} menu items: {A} flavours × {n} sizes ({S, M, XL})` where A = active
  flavours, n = sizes.

### V1.5 "What goes in, per size" (lines 300–318)
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

### V1.6 Flavours (lines 320–338)
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

### V1.7 Swaps a customer can ask for (lines 340–357)
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

### V1.8 Recipe history (lines 359–361)
Title `Recipe history`. Rows newest first: `display:flex; gap:12px; font-size:14px;
padding:4px 0; border-bottom:1px solid #e8ebf0` — `from {fmtD(date)}` (`#5b6475;
width:110px`) + text (first three diff lines joined ` · ` plus ` · +N more`). Final
line `#8a93a3 14px`: `Imported from the workbook. Every change after this keeps its own date.`

### V1.9 Cost column (lines 364–390)
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

### V1.10 State machine
- `clean` → any edit to name, component, cell, prep, base price, flavour, measured
  flag → `dirty` (local draft; nothing persisted). Changed cells go red.
- `dirty` → `Discard` → `clean` (draft dropped).
- `dirty` → `Apply from today` → **must go through a server preview first** (§B1, §A2) →
  commit → `clean`, new history row, generated menu items updated.
- `dirty` → select another rail row → design silently drops the draft. **Required
  change:** prompt `You have unsaved recipe changes. Discard them?` (Discard / Keep
  editing) — silent loss of an edit is worse than the prompt.
- Swaps chips, modifier card fields and staff rate **write immediately** in the design;
  §C (C-6, C-7) moves them behind preview/confirmation.

### V1.11 Compact
Rail becomes chips (template rows + `One-off items`, heads dropped). Cost column 320px.
Per-size grids unchanged (fr columns). Flavour/modifier grids reflow at 250px minimum.

---

## V2. Menu items tab (`tab === 'menu'`)

### V2.1 Rail (logic 627–633)
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

### V2.2 Grid area (lines 40–61)
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

### V2.3 Item drawer (lines 64–125)
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

### V2.4 Compact
Chip row replaces rail; **no new-category footer**. Card min 170px (150px with drawer).
Drawer 380px.

---

## V3. Ingredients & prices tab (`tab === 'ing'`)

### V3.1 Rail (logic 691–694)
Rows `All` (count all), `Estimated prices` (count `est`), head `Categories`, then each
ingredient category in first-appearance order with its count (Dairy, Dairy alt, Coffee,
Syrup, Tea, Chocolate, Specialty, Packaging, Sundries, Cake (CakeSmiths), Cake, Food,
Bottled in the design data).

### V3.2 List column (lines 130–147)
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

### V3.3 Detail column (lines 148–181)
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

### V3.4 Compact
Chip row replaces rail; list 290px. Detail's field grid collapses via `auto-fit
minmax(150px)`. Offer grid is fixed-template — at 690px it fits.


---

## D. Data contract per screen

Notation: **design derivation** is the prototype's JS, quoted concisely. `IM` is
ingredients by id; `uc(i) = i.packCost / i.pack` (float pence per unit);
`lineCost(l) = parseFloat(l.qty) × uc(IM[l.ing]) × conv(l.unit, i.unit)`;
`sizeCost(z) = Σ lineCost(z.lines)`; `marg(price, cost) = price > 0 ? (price−cost)/price : null`.
The backend column that replaces each datum is given in §B.

### D1. Recipes

| # | Datum | Shown/edited | Design derivation |
|---|---|---|---|
| R1 | Template list grouped by category | rail | `tcats = unique(templates.cat)`; head `c.replace(' / flavoured latte','')` |
| R2 | Items a template makes | rail count, meta | `flavours.filter(f=>f.active).length * sizes.length` |
| R3 | One-off items | rail count, pill list | `products.filter(p => !p.templateId)`, A–Z |
| R4 | Template name | edit | `T.name` |
| R5 | Template category | shown | `T.cat` |
| R6 | Sizes | column heads | `T.sizes` (`S M XL` or `One` or `S M`) |
| R7 | Component role | edit | `c.role` ∈ ROLES |
| R8 | Component ingredient (one) | edit | `c.ing`; changing it sets `c.unit = norm(IM[v].unit)` |
| R9 | Component ingredient per size | edit | `c.ingBySize[sz]` (packaging: 8/12/16oz cup, lid) |
| R10 | Component qty per size | edit | `c.qty[sz]` **string** (`"0.036"`) |
| R11 | Component unit | shown | `norm(c.unit)` |
| R12 | Swappable | edit | `c.subst` |
| R13 | Changed-cell flag | style | `!o || o.qty[sz] !== c.qty[sz] || ingBySize differs` vs saved `T0` |
| R14 | Prep seconds per size | edit | `T.prep[sz]` (int; `parseInt(v) || 0`) |
| R15 | Prep timed vs estimate | edit | `T.prepMeasured` |
| R16 | Base price per size | edit | `T.basePrice[sz]` pence, `Math.round(parseFloat(v)*100)` |
| R17 | Flavour list | edit | `T.flavours[]`: `name, ing, ing2, qty, qty2, priceDelta, season, active, product, missing` |
| R18 | Flavour ingredient options | select | ingredients with cat ∈ {Syrup, Specialty, Chocolate, Tea} or name `Honey` |
| R19 | Flavour missing | style + warning | `fl.missing && !fl.ing` (import could not find the syrup) |
| R20 | Seasons | select | `seasons[]: {id, name, from:'MM-DD', to:'MM-DD'}` |
| R21 | Modifiers (swaps) | chips + cards | `modifiers[]: {name, action SUBSTITUTE|ADD, role, ing, qty, price, src workbook|guess|set}` |
| R22 | Modifier enabled for template | chip state | `T0.mods.includes(m.id)` |
| R23 | Recipe history | list | `T0.history[]: {date, text}` newest first |
| R24 | Cost grid, per size, selected flavour | cost column | `gen(t,fl,sz)` = components with qty>0 + flavour line(s); `price = basePrice[sz] + fl.priceDelta`; `c = Σ lineCost`; `lab = prep[sz]/3600 × rate`; `m = (p−c)/p`; `ml = (p−c−lab)/p`; `pm = (p−c)/(prep/60)` |
| R25 | Staff rate | edit | `D.rate` pence/hour, default 1450 |
| R26 | Impact diff lines | preview | §V1.9 templates, max 8 |
| R27 | Menu items affected | preview | count active flavour×size where `|cA−cB|>0.001 || pA!==pB || new || re-activated` |
| R28 | Cost per item delta | preview | min/max of `cA−cB` over affected, shown to 3 dp |
| R29 | Thinnest margin after | preview | min `mA` over all active flavour×size, with its before |
| R30 | Over a month | preview | not computed ("needs item sales from Lightspeed") |
| R31 | Warnings | preview | missing flavour ingredient; a size with no base price; prep not timed |

Apply (design, lines 994–1001) **regenerates products**: for every flavour, finds or
creates product `"{flavour} {template name minus 'Flavoured '}"`, sets `inactive = !active`,
`cat = template cat`, and **overwrites `sizes[]` wholesale** with `{price: priceOf,
lines: gen(...)}`; flavours removed from the template set their product inactive;
appends a history row dated today.

### D2. Menu items

| # | Datum | Shown/edited | Design derivation |
|---|---|---|---|
| M1 | Product (= one sellable name, several sizes) | card, drawer | `products[]: {id, name, cat, note, inactive, templateId, sizes:[{size, price, lines:[{ing, qty, unit}]}]}` |
| M2 | Category list and type | rail, select | `TYPE[cat] || cats.find(c=>c.name===cat).type || 'Other'` |
| M3 | Category counts | rail | `products.filter(p => p.cat === c).length` |
| M4 | Search | toolbar | `p.name.toLowerCase().includes(q)` |
| M5 | List meta | toolbar | `N + ' items' + (cat==='All' ? '' : ' in ' + cat)` |
| M6 | Paging | grid | `menuLim` default 24, +48 per click |
| M7 | Lowest margin | badge | `min(marg(z.price, sizeCost(z)))` over sizes with price>0; null → `no price` |
| M8 | Price line | card | `sizes.map(z => (n>1 ? z.size+' ' : '') + gbp(z.price)).join(' · ') || 'no price'` |
| M9 | Off the menu | card, toggle | `p.inactive` |
| M10 | Photo | card, drawer | `<image-slot id="menu-{p.id}">` (local sidecar in the prototype) |
| M11 | Note | edit | `p.note` (free text; workbook description e.g. `Wholesale cake (CakeSmiths or similar)`) |
| M12 | From recipe | banner | `p.templateId` + template name |
| M13 | Size price | edit | `z.price` pence from `parseFloat` × 100 |
| M14 | Cost of size | tile | `sizeCost(Z)` |
| M15 | Margin of size | tile | `marg(price, cost)` |
| M16 | Profit | heading | `price − cost` |
| M17 | Recipe lines | edit | `Z.lines[]: {ing, qty (string), unit}` |
| M18 | Unit cost per line | line | `unitPrice(uc(I)) + '/' + unit`, italic if `I.est` |
| M19 | Line cost | line | `gbp(lineCost(l))` |
| M20 | Estimate note | note | lines whose ingredient `est`, with names |
| M21 | All sizes table | table | per size: price, cost, profit, margin, red < 60% |
| M22 | Duplicate / Remove size / Delete | actions | clone with ` (copy)`; splice size; remove product |

### D3. Ingredients & prices

| # | Datum | Shown/edited | Design derivation |
|---|---|---|---|
| I1 | Ingredient | list, detail | `{id, name, cat, pack, packUnit, packCost (pence), unit, supplier (notes), est}` |
| I2 | Estimated count | rail | `ingredients.filter(i => i.est).length` |
| I3 | Category counts | rail | per `i.cat` |
| I4 | Unit cost | list, panel | `unitPrice(i.packCost / i.pack) + '/' + norm(i.unit)` |
| I5 | Supplier links (offers) | list sub, Where to buy | `links[]: {sup, ing, sku, pack, unit, packCost, preferred, guess}` |
| I6 | Use count | list sub, sort | distinct products with any size line using the ingredient |
| I7 | Used in | pills | `usage(id)` products, first 40 |
| I8 | Filters | list | supplier (`all|none|<id>`), price (`all|invoice|est`), use (`all|used|unused`), sort (`az|cost|most|sups`) |
| I9 | Offer unit cost and comparison | Where to buy | `perU(l) = l.packCost / l.pack × conv(I.unit, l.unit)`; `diff = (p−best)/best`; `< 0.005` → `cheapest` |
| I10 | Preferred | star | `l.preferred`; `syncPreferred` copies preferred link's pack/cost/unit onto the ingredient on every save |
| I11 | Impact text | panel | `used.length` menu items |
| I12 | Estimate flag | checkbox | `i.est` toggled freely |
| I13 | Pack size, unit, pack cost, category, name, notes | edit | direct field writes (`writePref` also updates the preferred link) |

---

## B. Backend mapping

Paths are relative to `cafeops/`. **EXISTS** = a read or write path the screen can call
today. **DERIVABLE** = the data is in the database and a view can compute it, but no
endpoint returns it in this shape. **MISSING** = no column or service; must be built.

Baseline facts that shape everything below:

- **The API has four writing POSTs** (`api/routers.py:181` apply, `:209` materialise,
  `:240` supplier confirm, `:254` shelf life). The docstrings at `routers.py:3-6`,
  `app.py:47-50`, `views/__init__.py:11-12` and ARCHITECTURE §8H.3 still say "the only
  write" — stale.
- **The only composition write is a per-size quantity change of an existing template
  component**: `services/edit_composition.py:166 apply_component_qty_change` →
  `db/repositories/composition.py:273 close_and_open_component` (effective-dated
  close/open with `superseded_by_id`) → `jobs/cost_rollup.py:278 rollup_for_template`
  → commit. Preview: `edit_composition.py:115
  preview_component_qty_change_with_labour` (writes nothing). Retroactive dates
  refused (`edit_composition.py:236 require_not_retroactive`, 409).
- **Menu items, ingredients, modifiers and flavour options have no write service at all.**
- **Live data is mostly un-templated**: locally 320 `menu_item` rows / 176 distinct names,
  **1** `drink_template` (Flavoured Latte), 9 rows with `template_id`, 311
  `manual_recipe`. The design's 12 recipes correspond to the 27 **proposals**
  (`GET /api/proposals`), of which one is materialised (ARCHITECTURE §8S).
- **A design "product" is N backend rows.** `menu_item` is one row per (name, size)
  (`db/models/menu.py:29`, unique on `(name, size_code)` at `:76`). The Menu tab must
  group rows by `name`.
- Errors: `{"detail": str, "error": kind}` (`api/app.py:64-73`); 404 `not_found`,
  409 `retroactive_edit` / `component_superseded` / `substitution_refused`, 422
  `invalid_request` (`app.py:101-118`). Proposals/confirm views instead return
  `detail: {"message": ...}`.

### B1. Recipes tab

| Datum / action | Status | Where |
|---|---|---|
| R1 template list, category, sizes, item count, prep by size, prep estimate flag | **EXISTS** | `GET /api/templates` `routers.py:140` → `views/templates.py:117 templates_view` → `TemplateSummary` `schemas.py:233` (`item_count`, `sizes`, `prep_seconds_by_size`, `prep_seconds_is_estimate`) |
| R1 unconfirmed recipes (the other 26) | **EXISTS** (read) | `GET /api/proposals` `routers.py:199` → `views/proposals.py:122` → `ProposalOut` `schemas.py:683` (`proposal_id`, `name`, `category`, `menu_item_count`, `components`, `axes`, `conflicts`, `is_hollow`, `name_is_ambiguous`, `already_materialised`, `blocked_reason`) |
| Confirm a proposal into a recipe | **EXISTS** | `POST /api/proposals/{proposal_id}/materialise` `routers.py:209` → `views/proposals.py:145` → `services/materialise_template.py:211 materialise_proposal` (effective from now; refuses hollow/ambiguous/conflicting/already-done). Client: `web/src/lib/api.ts:330 materialiseProposal` (posts the id — keep) |
| Preview of a proposal before confirming | **MISSING** (API) | CLI `materialise-template --dry-run` (`cli.py:1233`) only. Add `POST /api/proposals/{id}/preview` |
| R2 items a template makes | **EXISTS** | `TemplateSummary.item_count`; per-item list in `TemplateDetail.items` (`schemas.py:296`) |
| R3 one-off items | **DERIVABLE** | `menu_item.manual_recipe = true` (`models/menu.py:54`), group by name; no endpoint lists them. `GET /api/margin` (`routers.py:397`) returns items with `template_id` null — usable but costs-only |
| R4 rename template | **MISSING** | `drink_template.name` unique (`models/composition.py:51`); no service. Rename is not a recipe change — plain update, no effective dating, but regenerated item names (D1 apply) are a POS concern (§C C-4) |
| R5 category | **EXISTS** (read) | `drink_template.category` `composition.py:52`, `TemplateSummary.category` |
| R6 sizes | **EXISTS** | `size_profile` `composition.py:85`; `TemplateSummary.sizes`; codes `S M XL ONE` (`enums.py:53`) — display `ONE` as `One` |
| R7 component role — read | **EXISTS** | `ComponentOut.role` `schemas.py:245` |
| R7 change role / R8 change ingredient / add component / remove component | **MISSING** | `close_and_open_component` only takes `qty_by_size`. Needs a generalised effective-dated close/open (role, ingredient_id, is_substitutable, is_required) + preview. Removing = close with no successor |
| R8 ingredient (one) — read | **EXISTS** | `ComponentOut.ingredient_id / ingredient_name / unit / ingredient_cost: Cost` |
| R9 ingredient per size | **DERIVABLE** | Backend has no `ingBySize`: a per-size item is N components of the same role each with a qty at one size only (ARCHITECTURE §7.5 — "the 8oz cup slot has one at S and nothing at M or XL"). The UI groups same-role components whose `qty_by_size` keys are disjoint into one row; writes go to the individual `component_id`s |
| R10 qty per size — read | **EXISTS** | `ComponentOut.qty_by_size: dict[str,str]` (strings, 6 dp, `api/encoding.py:44`) |
| R10 qty per size — preview | **EXISTS** | `POST /api/templates/{id}/preview` `routers.py:169`, body `ComponentQtyChange {component_id, qty_by_size, window_days}` (`schemas.py:327`) → `PreviewResponse` (`schemas.py:424`, `writes_nothing: true`). Superseded row → 409 |
| R10 qty per size — apply | **EXISTS** | `POST /api/templates/{id}/apply` `routers.py:181`, body `ComponentQtyApply {component_id, qty_by_size, actor, window_days}` (`schemas.py:343`) → `ApplyResponse` (`schemas.py:440`, includes `new_component_id`, `rollup_items_recosted`). **The current client calls the wrong URL** — `api.ts:413 applyFromToday` posts to `/api/templates/{id}/components/{cid}` with `{qty_by_size, effective_from}` → 404. Fix when rebuilding |
| R11 unit | **EXISTS** | `ComponentOut.unit` = ingredient's unit. `template_component` has **no unit column** (`composition.py:110-144`): quantities are always in the ingredient's unit |
| R12 swappable — read | **EXISTS** | `template_component.is_substitutable` `composition.py:134`, `ComponentOut.is_substitutable` |
| R12 swappable — write | **MISSING** | needs the generalised close/open (it changes resolution: `SUBSTITUTE` into a non-substitutable slot raises, CLAUDE.md §5.7) |
| R13 changed-cell flag | client | compare draft to loaded `TemplateDetail` using `dec.cmp`, not string equality (`"0.30"` = `"0.3"`) |
| R14 prep seconds — read | **EXISTS** | `drink_template.prep_seconds_by_size` `composition.py:59`; `TemplateSummary.prep_seconds_by_size`; per item `TemplateItemOut.prep_seconds / prep_source / prep_is_estimate` |
| R14 prep seconds — write | **MISSING** | only `cafeops prep-times --apply` (`cli.py:1797`), which writes *estimates*. Not effective-dated in the schema (a JSON column); labour history lives in `menu_item_cost.prep_seconds` snapshot per rollup |
| R15 prep timed flag — read / write | **EXISTS** / **MISSING** | `drink_template.prep_seconds_is_estimate` `composition.py:67`; no writer |
| R16 base price per size | **DERIVABLE** (read) / **MISSING** (write) | There is no template base price. Each generated item has `menu_item.price_pence` (`menu.py:52`) and each flavour `variant_option.price_delta_pence` (`composition.py:203`). Derive `base[sz] = price_pence − price_delta_pence` per item; if items disagree, show the mode and a warning. Writing = writing every generated item's `price_pence` (see M13) |
| R17 flavours — read | **EXISTS** | `TemplateDetail.axes[].options[]` → `VariantOptionOut` (`schemas.py:264`: `option_id, name, role, ingredient_id, ingredient_name, qty_override, price_delta_pence, season_id, season_name`) |
| R17 flavour second ingredient (`ing2`, e.g. Honey Vanilla) | **MISSING** | `variant_option` has one `ingredient_id` (`composition.py:200`). Either a second FLAVOUR axis, or leave such items manual. Recommend: show read-only "two ingredients — edit in Menu items" |
| R17 flavour qty | **EXISTS** | `variant_option.qty_by_size` (`composition.py:202`) — **per size**, the design shows one `Qty`. Show one input when all sizes equal, else per-size cells |
| R17 flavour on/off | **DERIVABLE** (read) / **MISSING** (write) | no `is_active` on `variant_option`; state = the generated `menu_item.active` rows (`menu.py:53`) sharing `selected_options[axis]=option_id` (`menu.py:50`) |
| R17 add / rename / remove flavour, change its ingredient, qty, extra, season | **MISSING** | `variant_option` is effective-dated (`effective_from/to` `composition.py:208-209`) but has no writer. Adding a flavour must also create menu items (§C C-4) |
| R18 flavour ingredient options | **DERIVABLE** | `ingredient.category` (`models/ingredient.py:27`) ∈ {Syrup, Specialty, Chocolate, Tea} or name Honey. Needs an ingredient list endpoint (I1) |
| R19 flavour missing | **DERIVABLE** | option with `ingredient_id` null on a FLAVOUR axis; proposals report the same in `conflicts`/`warnings`. `TemplateDetail.warnings` (`schemas.py:315`) carries uncosted/estimate warnings |
| R20 seasons list | **DERIVABLE** | `season` table (`models/batch.py:84`: `name, starts_on, ends_on, is_recurring_annually`); only `season_id/season_name` on options are exposed. Add `GET /api/seasons` |
| R21 modifiers — read | **EXISTS** | `TemplateDetail.modifiers` → `ModifierOut` (`schemas.py:284`: `modifier_id, name, action, target_role, ingredient_id, ingredient_name, qty_delta, qty_multiplier, price_pence`) — filtered to modifiers whose `target_role` is a role in this template (`views/templates.py:234`) |
| R21 modifier charge is a guess (`src`) | **MISSING** | `modifier` (`composition.py:223-245`) has no price source column. Needed for invariant 8's spirit on the italic |
| R21 modifier edits | **MISSING** | no writer; `modifier` is **not effective-dated** (§C C-6) |
| R22 modifier enabled per template | **MISSING** (and contradicts the model) | modifiers target a **role**, not a template (`composition.py:226-229`, "lets one Oat milk modifier work across every template that has a MILK slot"). A per-template toggle needs a `template_modifier` join table and resolver change. Recommend: show chips as read-only "applies here because this recipe has a MILK slot" (§C C-7) |
| R23 recipe history | **DERIVABLE** | closed rows: `template_component.effective_from/effective_to/superseded_by_id` (`composition.py:137-141`), `variant_option.effective_from/to`, `drink_template.created_at`. `apply` writes a rollup trigger `"composition edit by {actor}"` (`edit_composition.py:213`) but there is no history table or endpoint. Add `GET /api/templates/{id}/history` |
| R24 cost grid (saved state) | **EXISTS** | `GET /api/templates/{id}` → `TemplateDetail.items[]` → `TemplateItemOut` (`schemas.py:296`): `price_pence`, `cost: Cost`, `margin_pct`, `true_margin_pct`, `margin_per_minute_pence`, `prep_seconds`, `prep_is_estimate`, `availability`. Staff time = `prep_seconds/3600 × rate`, or `menu_item_cost.labour_cost_pence` (`menu.py:146`) via `GET /api/margin` `MarginItemOut` (`schemas.py:1070`). Pick the item by `selected_options` ↔ flavour option |
| R24 cost grid (draft state) | **EXISTS** for qty edits, **MISSING** otherwise | `PreviewResponse.preview.items[]` (`ImpactedItemOut`: `cost_before/after`, `margin_pct_before/after`) and `.labour.items[]` (`LabourItemOut`: true margin, margin/min before/after). Every other edit kind needs the changeset preview (§A2) |
| R25 staff rate | **EXISTS** (config + per-request override) / **MISSING** (persisted edit) | `config.py:70 loaded_hourly_rate_pence = 1450`; `GET /api/meta` → `Meta.loaded_hourly_rate_pence` (`schemas.py:206`); `GET /api/margin?loaded_hourly_rate_pence=` (`routers.py:397`) overrides per request; each `menu_item_cost` stores the rate it used (`menu.py:150`) |
| R26–R29, R31 impact preview | **EXISTS** for qty edits | `ImpactPreviewOut` (`schemas.py:401`): `affected_item_count`, `cost_delta_pence_per_item`, `cost_delta_pence_range`, `worst_margin_after`, `warnings`; `LabourImpactOut` (`schemas.py:377`) adds `untimed_count`, `rank_moves`, labour warnings. The design's diff lines (R26) are client-built from draft vs saved |
| R30 over a month | **EXISTS** | `ImpactPreviewOut.monthly_cogs_delta_pence` — computed from `window_days` of sales (default 30). The design's "needs item sales from Lightspeed" is **wrong for this backend**: show the figure; show the design copy only when the value is null |
| Apply from today | **EXISTS** for one qty edit / **MISSING** for a changeset | as R10. A multi-edit draft needs an atomic changeset apply (§A2) — applying N single edits one by one gives N separate rollups and a half-applied recipe if one is refused |
| + New recipe | **MISSING** | templates are only born from proposals (`materialise_template.py:281 _build`). Recommend: for now the button opens the proposals list ("Confirm a detected recipe"); a blank-template creator is a later decision |
| Waste factor excluded from cost | **EXISTS** | `domain/composition.py:163` costs from `lines`, not `depletion_lines` (`:141-155`) — invariant 7 holds for every figure on this tab |
| Estimated / missing cost flags | **EXISTS** | `Cost {pence: str|null, source, is_estimate, is_missing, excluded_from_aggregates, note}` (`schemas.py:100`); `ResolvedRecipe.cost_source` = weakest (`domain/types.py:323`) |

### B2. Menu items tab

| Datum / action | Status | Where |
|---|---|---|
| M1 list of items with sizes, prices, active, category, template | **DERIVABLE** | `menu_item` (`menu.py:29-68`): `id, lightspeed_id, name, category, template_id, size_code, selected_options, price_pence, active, manual_recipe, prep_seconds, season_id, data_quality_flag`. No list endpoint; `GET /api/margin` returns costed items (`MarginItemOut`) plus `uncosted` (`UncostedItemOut` `schemas.py:1126`, incl. `active`) — together cover every row but not grouped. Add `GET /api/menu-items` (§A3) |
| M2 category + type | **EXISTS** (string) / **MISSING** (type, category list) | `menu_item.category` `menu.py:44` (nullable; locally many are empty or misspelled, e.g. `Spring saesonal drinks`). Drinks/Food/Other has no column — add `menu_category(name, kind)` or keep the design's hard-coded map client-side (recommend table; the design lets users add categories) |
| Add category (`+ drinks` / `+ food`) | **MISSING** | as above |
| M3–M6 counts, search, meta, paging | client | over the grouped list |
| M7 lowest margin badge | **DERIVABLE** | `menu_item_cost` (`menu.py:105-160`) → `CachedCost.margin_pct` (`repositories/menu_cost.py:56`). Must carry `is_estimate` / `is_missing` (invariant 8) — the design badge has neither (§C C-10) |
| M8 price line | **EXISTS** | `menu_item.price_pence` per size row |
| M9 on/off the menu — read | **EXISTS** | `menu_item.active` `menu.py:53`; "available today" (season) is separate: `services/menu_margin.py:201 menu_availability` |
| M9 on/off — write | **MISSING** | per group: set `active` on every size row. Not effective-dated; the sales ledger is unaffected |
| M10 photo | **MISSING** | no column, no upload handling, no `python-multipart` dependency (`pyproject.toml:11`), no media route in `Caddyfile` (§A5) |
| M11 note | **MISSING** (column) / **DERIVABLE** (read-only for imported items) | no `note` on `menu_item`; the workbook note survives in `legacy_staged_recipe.notes` (`composition.py:280`) keyed by `item_name`/`size_code` |
| M12 made from recipe | **EXISTS** | `menu_item.template_id` + `drink_template.name` |
| M13 sell price — write | **MISSING** | nothing writes `price_pence` outside seeds (`seed/legacy.py:481`, `seed/demo.py:318`). It is single-valued with **no history** — `integrations/lightspeed/modifier_probe.py:290` already notes this. Needs an effective-dated price (§C C-3) and a cost rollup is not needed (price does not change cost) but margin caches recompute on read |
| M14 cost of size | **EXISTS** | `menu_item_cost.cost_pence` (nullable) + `cost_source` + `has_missing_cost` (`menu.py:132-136`); API `Cost` via `views/common.py:36 cost_from_cached` |
| M15 margin, M16 profit | **DERIVABLE** | `price_pence − cost` with `dec.ts`; margin null when price 0 or cost missing |
| M17 recipe lines — read | **DERIVABLE** | manual items: `manual_recipe_line` (`menu.py:85-97`: `ingredient_id, qty, effective_from, effective_to`); templated items: resolve via `domain/composition.py:92 resolve_recipe` → `ResolvedRecipe.lines` + `cost_breakdown` (`:163`, `:371`). No endpoint returns lines for a menu item. Add `GET /api/menu-items/{id}` |
| M17 line unit | **MISSING** (by design) | `manual_recipe_line` has **no unit** — qty is in the ingredient's unit. The unit select must be limited to same-dimension units and converted before sending (§C C-9) |
| M17 edit manual recipe lines | **MISSING** | `manual_recipe_line` is effective-dated in the schema but has no writer or preview. Needs a service mirroring `apply_component_qty_change`: close current lines, open new ones from today, `rollup_menu_items` (`jobs/cost_rollup.py:170`), commit |
| M17 edit lines of a templated item | **refuse** | lines come from the template; the design's "edits here are overwritten next time" is a promise this backend cannot keep honestly (§C C-2) |
| M18 unit cost per line | **EXISTS** | `ingredient.current_cost_pence_per_unit` + `current_cost_source` (`models/ingredient.py:38-41`) |
| M19 line cost | **DERIVABLE** | `ResolvedRecipe.cost_breakdown` (`domain/composition.py:371`) — `line_cost_pence` None when unpriced |
| M20 estimate note | **DERIVABLE** | lines whose ingredient `current_cost_source = ESTIMATE` |
| M21 all sizes table | **DERIVABLE** | group rows |
| Add size | **MISSING** | = new `menu_item` row (name, size_code) + lines. POS mapping (`lightspeed_id`) will be null until matched |
| Remove size | **MISSING** | must be `active = false` on that row, never delete (sales reference it) |
| Duplicate | **MISSING** | new rows `{name} (copy)`; lines copied effective today |
| Delete item | **MISSING** — and must not delete | set `active = false`; hard delete breaks `sale` history (§C C-5) |
| + Add item | **MISSING** | new `menu_item` rows, `manual_recipe = true` |
| Copy to other sizes | **MISSING** | writes lines for each size row; must warn when copying PACKAGING (cup sizes differ by size) |

### B3. Ingredients & prices tab

| Datum / action | Status | Where |
|---|---|---|
| I1 list: name, category, unit, unit cost, source | **DERIVABLE** | `ingredient` (`models/ingredient.py:21-80`). Closest endpoint: `GET /api/stock?include_untracked=true` (`routers.py:273`) → `StockRow` (`schemas.py:568`: `ingredient_id, name, tier, unit, unit_cost: Cost, shelf_life, ...`) — lacks category, pack, suppliers, usage. Add `GET /api/ingredients` (§A4) |
| I2 estimated count | **DERIVABLE** | `current_cost_source = 'ESTIMATE'` (42 of 113 open price rows locally) |
| I3 categories | **EXISTS** | `ingredient.category` `:27` (the 13 design categories match the DB exactly) |
| I4 unit cost | **EXISTS** | `current_cost_pence_per_unit` (Qty, 6 dp) + `current_cost_source` |
| Pack size, pack unit, pack cost | **EXISTS** (read, DB) | current open `ingredient_price` row (`ingredient.py:88-115`: `pack_size, pack_unit, pack_cost_pence, cost_per_unit_pence, effective_from, effective_to, source, supplier_id, note`) |
| Change pack cost / pack size (new price) | **EXISTS** as repository + CLI, **MISSING** as API/service | `db/repositories/ingredient.py:89 add_price(ingredient_id, *, pack_size, pack_unit, pack_cost_pence, effective_from, source, note)` closes the open row and opens a new one (effective-dated) and refreshes the cache; `cafeops set-price` (`cli.py:1864`) calls it then `jobs/cost_rollup.py:292 rollup_for_ingredient`. **No `supplier_id` parameter** although the column exists. **No menu impact preview** (CLI dry run prints only old/new unit cost) |
| I5 supplier links | **EXISTS** (DB) / **MISSING** (API) | `supplier_product` (`models/supplier.py:69-88`: `supplier_id, ingredient_id, sku, pack_size, pack_unit, price_pence, is_preferred, product_url, moq_packs, last_seen_price_at`). `GET /api/suppliers` (`routers.py:330`) returns suppliers only |
| Link a supplier / make preferred | **MISSING** | no writer for `supplier_product`. Preferred choice feeds sourcing (`domain/sourcing.py`) — changing it changes draft orders, so it is a write that deserves an order-impact line |
| I9 offer comparison | **DERIVABLE** | `supplier_product.price_pence / pack_size`, converted with `domain/units.py` (raises across dimensions — ARCHITECTURE §3) |
| I10 preferred price drives recipe cost | **conflict** | recipe cost reads `ingredient_price` (effective-dated, source-tagged), sourcing reads `supplier_product`. The design merges them (`syncPreferred`). §C C-8 |
| I6/I7 use count, used in | **DERIVABLE** | manual: `manual_recipe_line` open rows; templated: live `template_component` + `variant_option` ingredient ids → `menu_item.template_id`. `StockDetail.templates_using` (`schemas.py:624`) gives template names only |
| I11 impact text | **DERIVABLE** | count of I7. Upgrade to a real preview (§A4 `price/preview`) |
| I12 estimate checkbox | **EXISTS** (read) / **refuse** (free toggle) | `current_cost_source` / `ingredient_price.source` (`INVOICE|ESTIMATE|SUPPLIER_FEED`). Un-ticking must mean "record a price with a real source", never a flag flip (§C C-1) |
| I13 name | **EXISTS** (unique col) / **MISSING** (write) | rename safe (ids everywhere) |
| I13 category | **MISSING** (write) | plain update |
| I13 unit | **MISSING** — and dangerous | ledger quantities, counts, batches and recipe qtys are all in this unit. Refuse once any `stock_movement`, `manual_recipe_line`, `template_component` or `ingredient_price` references it (§C C-9) |
| I13 notes | **EXISTS** (read) / **MISSING** (write) | `ingredient.source_note` (`ingredient.py:72`, e.g. `Real invoice — 3.4L = £2.37`); also `ingredient_price.note` |
| Shelf life (not in the design's ingredient tab, but lives on the same entity) | **EXISTS** | `POST /api/ingredients/{id}/shelf-life` `routers.py:254` → `views/confirm.py:152` → `services/confirm_terms.py:156 confirm_shelf_life` (refuses ESTIMATE, open > shelf, shelf ≤ transit buffer). Client `api.ts:338 confirmShelfLife`; screen `web/src/screens/ConfirmShelfLife.tsx`. Recommend a "Shelf life" row in the ingredient detail linking to it |
| + Add ingredient | **MISSING** | nothing outside the seed (`seed/legacy.py:281`). Needs unit (backend enum), category, first price with source, storage, shelf life (ESTIMATE default per ARCHITECTURE §8F.1), tier C, `tracking_enabled=false` |
| Delete | **MISSING** — and must not delete | movements, prices, batches reference it. Refuse when referenced; otherwise "retire" (needs a `retired_at` column) |
| Waste factor | **EXISTS** (not in design) | `ingredient.waste_factor` — correctly absent from this cost screen (invariant 7) |

---

## C. Conflicts with CLAUDE.md invariants and ARCHITECTURE.md decisions

| # | Design behaviour | Rule it breaks | Recommended resolution |
|---|---|---|---|
| C-1 | `Price is an estimate` is a free checkbox (`toggleEst: upd(x => x.est = !x.est)`); new ingredients default `est: true` but untick in one click | Invariant 8; CLAUDE.md §10.9b ("confirming a guess as a guess quiets the warning without adding knowledge") | Make the checkbox a **read-only indicator**. Any pack-cost/pack-size edit opens a small "Where is this price from?" choice — `Invoice` / `Supplier price list` / `Still an estimate` → `INVOICE` / `SUPPLIER_FEED` / `ESTIMATE` — and writes a new `ingredient_price` row. The italic clears only when a non-estimate row is in force |
| C-2 | Menu drawer lets you edit the recipe lines of a templated item; banner says "edits here are overwritten next time that recipe is applied" | Invariant 3 (history never rewritten); the template/manual split (`menu_item.manual_recipe`) | For templated items render lines **read-only** (resolved from the template, with per-line cost) and keep the banner as `Made from the {X} recipe. Change it there.` + `Open recipe`. Only `manual_recipe` items get editable lines |
| C-3 | Menu price is overwritten in place (`z.price = …`), and the recipe base price rewrites every item's `sizes[]` | Invariant 3 by analogy; `modifier_probe.py:290` already relies on price being historic ("this schema keeps no sell-price history"); margin over a past window uses today's price | Add `menu_item_price(menu_item_id, price_pence, effective_from, effective_to, source)`; `menu_item.price_pence` becomes the cache of the open row. Price edits apply from today, with a preview (items affected, margin before/after, monthly revenue delta from the sales window) |
| C-4 | Apply creates products for new flavours (`name: fl.name + ' ' + suffix`) and renames/recategorises them | CLAUDE.md §1 non-goal: "Replacing Lightspeed's POS functions"; sales match by `lightspeed_id` (`menu.py:42`) | New menu items are created with `lightspeed_id = null` and shown with a `Not on the till yet` tag until a sync matches them. The impact preview must say: `Adds {n} items. Add them in Lightspeed too, or their sales will not be counted.` Renames must not rename the Lightspeed product |
| C-5 | `Delete item`, `Remove size`, ingredient `Delete` remove rows | Invariant 12 (ledger append-only) — `sale`, `stock_movement`, `ingredient_price` reference these rows | Never hard-delete. Items/sizes → `active = false` (label the button `Take off the menu`; keep `Delete item` only for an item with zero sales and zero history, server-checked). Ingredients → refuse with the reference count, or retire |
| C-6 | Swap chips, modifier fields and staff rate **save straight away**, globally (`this.mut` outside the draft) | CLAUDE.md §5.6 "Every composition edit produces an impact preview before commit"; `modifier` is not effective-dated (`composition.py:223-245`) so an edit silently re-prices history on any re-expansion | Modifier edits go through the same preview → apply flow and a new effective-dated `modifier_version` (or `effective_from/to` on `modifier`). Staff rate on this screen is a **what-if** (query param `loaded_hourly_rate_pence`, not persisted); the persisted rate lives in Settings. Remove the copy `Swaps save straight away.` |
| C-7 | Per-template swap toggles (`T.mods`) | ARCHITECTURE/model: modifiers target a **role** across all templates (`composition.py:226-229`) | Render chips read-only with state derived from roles (a chip is "on" iff the template has a slot of `target_role`; for SUBSTITUTE also iff that slot `is_substitutable`). The effective way to "turn off oat milk for this drink" is the `Swappable` flag on the MILK slot — say so in a caption |
| C-8 | Preferred supplier link's pack/cost overwrites the ingredient's price on every save (`syncPreferred`) | Two sources of truth: recipe cost uses `ingredient_price` (effective-dated, sourced); ordering uses `supplier_product` | A pack-cost edit writes **both** in one service: new `ingredient_price` row with `supplier_id = preferred` and the chosen source, and `supplier_product.price_pence/pack_size/last_seen_price_at` on the preferred link. Changing the star does not re-price recipes until a price is recorded for that supplier; say so in `buyNote` |
| C-9 | `conv()` returns **1** across dimensions (ml ↔ kg); line unit select offers all five units; ingredient unit freely editable | ARCHITECTURE §3 (`convert` raises `IncompatibleUnitsError`), §8E (Qty scale 6 dp) | Unit selects offer only same-dimension units (`L/ml`, `kg/g`, `unit`). Convert with `dec.ts` to the ingredient's unit before sending; server validates via `domain/units.py`. Ingredient unit is read-only once referenced |
| C-10 | Margin badge/tile/table show `NN%` with no estimate or missing marker; `gbp(0)` shown for an ingredient with no price (`uc()` returns 0 when `pack` is 0) | Invariant 8 ("A missing cost is None, never zero"; estimates flagged through every rollup) | Use `costView` (`web/src/lib/format.ts:186`). Missing → badge `cost unknown` (neutral), tile `—` + `Cost unknown: {note}`; estimate → italic value and a `~` prefix on the badge (`~68%`). Lowest-margin badge ignores sizes with missing cost and says `cost unknown` if all are missing. Exclude estimated/missing from any aggregate the tab shows |
| C-11 | Floats everywhere (`parseFloat(v)`, `Math.round(parseFloat(v)*100)`, `(p−c)/p` on floats, `toFixed`) | Invariant 11; CLAUDE.md §10.10 ("`0.1 + 0.2` in a recipe editor is unacceptable") | All money via `dec.ts` (`fromMoney`, `parseDec`), quantities kept as strings end-to-end, percentages computed from `Dec` and only then turned into a display number. Price inputs accept `^\d*(\.\d{0,2})?$`; qty inputs use the existing `QTY_INPUT` regex (`web/src/screens/composition/model.ts`) |
| C-12 | Cost grid & badges colour healthy margins green (`#e8f5ee` / `#237a57`) | CLAUDE.md §10 "Resist the traffic light — colour only for crossed thresholds… avoid green-good/red-bad as the only encoding" | Keep red for `< 60%` (a crossed threshold, and the number is also shown). Render healthy margins neutral (`#f1f3f7` / `#1f2633`) — owner's call; the design's green is recorded here so the choice is explicit |
| C-13 | Footnote `Prices exclude VAT.` | No VAT handling anywhere in the backend (grep: none); `menu_item.price_pence` is the till price, which for a café is VAT-inclusive | Open question for the owner. Until answered, change the copy to `Prices as rung on the till, including VAT.` rather than claim an ex-VAT margin the backend does not compute |
| C-14 | `Over a month — needs item sales from Lightspeed` | The backend already returns `monthly_cogs_delta_pence` from the sales window | Show the figure; fall back to the design copy only when it is null |
| C-15 | Selecting another recipe silently drops the draft | CLAUDE.md §5.6 (preview before commit) is honoured, but edits vanish without a word | Confirm-to-discard prompt (§V1.10) |
| C-16 | Design shows 12 recipes as live and editable | Only 1 of 27 detected templates is materialised (ARCHITECTURE §8S); §6 "pattern detection proposes, never writes"; §8Q confirm **by id** | Rail lists live templates first, then unconfirmed proposals under a head `Detected, not confirmed` (count = `menu_item_count`). A proposal opens read-only with a `Confirm this recipe` action posting `proposal_id` (never name), refused states shown verbatim (`blocked_reason`, `is_hollow`, `name_is_ambiguous`). Hollow proposals (Cake/Juice/Panini/Water) cannot be confirmed |
| C-17 | Recipes `+ New recipe` creates an empty template that immediately regenerates products | §6 import flow; C-4 | Button opens the proposal list; blank template creation deferred (MISSING, needs owner decision) |
| C-18 | Flavour `ing2` (two-ingredient flavour) and single `qty` | `variant_option` has one ingredient and per-size `qty_by_size` | Per-size qty cells when sizes differ; two-ingredient flavours stay manual items (read-only note in the card) |
| C-19 | `Time to make` state toggle flips timed/estimate with no new data | Invariant 8/9 spirit (estimate stays flagged until knowledge is added) | Clicking `estimate, not timed yet` opens the prep cells for entry; `timed` is set only when a value is saved with source `timed`; the reverse (mark as estimate) is always allowed |
| C-20 | Photo persistence via `image-slot` sidecar (`.image-slots.state.json`) | Single-VM deploy, no media route (§A5) | Server-stored photo per item group (§A5). Keep the slot's UX: drop/click to browse, reframe crop, 1200px max, WebP 0.85 |

---

## A. Proposed API

All new routes go on the authenticated `router` (`routers.py:62`), follow the existing
error shapes, and — for writes — the **preview → apply** pair used by composition.
Types below are TypeScript mirrors of the Pydantic models to add to `api/schemas.py`
(`In` with `extra="forbid"`, `Out` frozen). Money: integer pence where stored, exact
decimal-string pence where derived (ARCHITECTURE §8H.5). Quantities: decimal strings.
`Cost` is the existing type (`web/src/lib/types.ts:36`).

### A1. Fix existing contract bugs first
- `web/src/lib/api.ts:413 applyFromToday` → `POST /api/templates/{id}/apply` with
  `{ component_id, qty_by_size, actor }` and type `ApplyResponse` (missing from
  `types.ts`).
- `api.ts:384-397` reads `detail.current_component_id` / `detail.message`, but
  `app.py:64-73` sends `{detail: string, error: 'component_superseded'}`. Either make
  the backend send `detail: {message, current_component_ids: number[]}` for this error
  (recommended: the ids are currently only inside the prose at
  `views/templates.py:505-511`) or parse the string.

### A2. Template changeset (Recipes tab)

```ts
type SizeCode = 'S' | 'M' | 'XL' | 'ONE'
type Role = 'COFFEE' | 'MILK' | 'BASE' | 'FLAVOUR' | 'TOPPING' | 'PACKAGING' | 'SUNDRY'
type QtyBySize = Partial<Record<SizeCode, string>>        // decimal strings, ingredient's unit
type PenceBySize = Partial<Record<SizeCode, number>>      // integer pence

type TemplateOp =
  | { op: 'component.qty'; component_id: number; qty_by_size: QtyBySize }
  | { op: 'component.set'; component_id: number;
      role?: Role; ingredient_id?: number | null; is_substitutable?: boolean; is_required?: boolean }
  | { op: 'component.add'; role: Role; ingredient_id: number | null; qty_by_size: QtyBySize;
      is_substitutable: boolean; is_required: boolean }
  | { op: 'component.remove'; component_id: number }
  | { op: 'prep.set'; prep_seconds_by_size: Partial<Record<SizeCode, number>>; is_estimate: boolean }
  | { op: 'price.base'; base_price_pence_by_size: PenceBySize }   // rewrites generated items' prices from today
  | { op: 'option.set'; option_id: number; name?: string; ingredient_id?: number | null;
      qty_by_size?: QtyBySize; price_delta_pence?: number; season_id?: number | null }
  | { op: 'option.add'; axis_id: number; name: string; ingredient_id: number | null;
      qty_by_size: QtyBySize; price_delta_pence: number; season_id: number | null }
  | { op: 'option.active'; option_id: number; active: boolean }  // sets menu_item.active on its items
  | { op: 'option.remove'; option_id: number }
  | { op: 'template.rename'; name: string }

interface TemplateChangesetIn {
  base_version: string          // opaque token from TemplateDetail (hash of live row ids); 409 if stale
  ops: TemplateOp[]
  window_days?: number          // default 30, sales window for monthly deltas
  loaded_hourly_rate_pence?: number   // what-if only, never persisted
}

// POST /api/templates/:id/changeset/preview   — writes nothing
interface TemplateChangesetPreview {
  template_id: number
  base_version: string
  writes_nothing: true
  at: string                                  // effective-from that apply would use (start of today, local)
  diff: string[]                              // server-built lines, same wording as §V1.9
  preview: ImpactPreviewOut                   // existing shape: affected_item_count, items[], cost_delta_pence_per_item,
                                              //   cost_delta_pence_range, monthly_cogs_delta_pence, worst_margin_after, warnings
  labour: LabourImpactOut                     // existing shape
  revenue_delta_pence_monthly: string | null // from price ops × units sold in window
  items_created: { name: string; size_code: SizeCode; price_pence: number }[]   // option.add
  items_deactivated: { menu_item_id: number; name: string; size_code: SizeCode }[]
  pos_actions: string[]                       // e.g. "Add 3 items in Lightspeed: …" (C-4)
  refusals: { op_index: number; message: string }[]   // e.g. SUBSTITUTE into non-substitutable slot
}

// POST /api/templates/:id/changeset/apply
interface TemplateChangesetApplyIn extends TemplateChangesetIn { actor: string }
interface TemplateChangesetApplied {
  template_id: number
  effective_from: string
  new_version: string
  component_ids_opened: number[]
  component_ids_closed: number[]
  option_ids_opened: number[]
  option_ids_closed: number[]
  menu_items_created: number[]
  menu_items_repriced: number[]
  rollup_items_recosted: number
  summary: string                             // becomes the history row text
}

// GET /api/templates/:id/history
interface TemplateHistoryOut {
  template_id: number
  created_at: string                          // "Confirmed from the workbook import" baseline
  entries: { effective_from: string; actor: string | null; lines: string[] }[]
}

// GET /api/seasons
interface SeasonOut { season_id: number; name: string; starts_on: string; ends_on: string;
                      is_recurring_annually: boolean; is_open_today: boolean }
```

Rules: apply is one transaction (all ops or none), effective from the start of today
(reuse `require_not_retroactive`), each touched component/option closed and reopened
(never updated in place), one `rollup_for_template` at the end. `TemplateDetail`
gains `version: string` and `description` (column exists, `composition.py:53`).
Apply without a preview token is allowed by the API but the UI must never offer the
button until the latest preview for the current draft has returned (mandatory preview).

Proposal preview (C-16):
```ts
// POST /api/proposals/:proposal_id/preview   — writes nothing (wraps the CLI dry run)
interface ProposalPreview { proposal_id: string; would_repoint: number; would_close_manual_lines: number;
  cost_changes: ImpactedItemOut[]; warnings: string[]; blocked_reason: string | null }
```

### A3. Menu items

```ts
// GET /api/menu-items?as_of=
interface MenuGroupOut {
  key: string                       // stable group key = name (unique per size_code); used for photo + selection
  name: string
  category: string | null
  kind: 'DRINKS' | 'FOOD' | 'OTHER'
  note: string | null
  template_id: number | null
  template_name: string | null
  photo_url: string | null          // "/media/menu/<sha256>.webp"
  is_active: boolean                // any size active
  on_till: boolean                  // every size has lightspeed_id
  sizes: MenuSizeOut[]              // ordered S, M, XL, ONE
  lowest_margin: { pct: number | null; is_estimate: boolean; is_missing: boolean }
}
interface MenuSizeOut {
  menu_item_id: number
  size_code: SizeCode | null
  price_pence: number
  active: boolean
  lightspeed_id: string | null
  cost: Cost
  labour_cost_pence: string | null
  margin_pct: number | null         // null if price 0 or cost missing
  manual_recipe: boolean
  data_quality_flag: string | null
}
interface MenuItemsResponse { as_of: string; groups: MenuGroupOut[]; categories: { name: string; kind: string; count: number }[] }

// GET /api/menu-items/:menu_item_id   (one size, with lines)
interface MenuItemDetail extends MenuSizeOut {
  group_key: string
  lines: { ingredient_id: number; ingredient_name: string; qty: string; unit: Unit;
           unit_cost: Cost; line_cost: Cost; source: 'manual' | 'template' }[]
  editable_lines: boolean           // = manual_recipe (C-2)
}

// POST /api/menu-items/:id/lines/preview  and  /lines/apply {actor}
interface ManualLinesIn { lines: { ingredient_id: number; qty: string }[]; also_menu_item_ids?: number[] } // "Copy to other sizes"
// → { preview: ImpactPreviewOut; writes_nothing: true }  /  { effective_from; lines_closed; lines_opened; rollup_items_recosted }

// POST /api/menu-items/prices/preview  and  /prices/apply {actor}
interface MenuPricesIn { prices: { menu_item_id: number; price_pence: number }[] }
// → { items: { menu_item_id; name; size_code; price_before; price_after; margin_pct_before; margin_pct_after; cost: Cost }[];
//     revenue_delta_pence_monthly: string | null; pos_actions: string[]; writes_nothing: true }

// POST /api/menu-items            create  { name, category, sizes: [{ size_code, price_pence, lines: [...] }], actor }
// POST /api/menu-items/:key/meta  { name?, category?, note?, active?, actor }   (applies to every size row in the group)
// POST /api/menu-items/:id/size   { size_code, copy_from_menu_item_id, price_pence, actor }
// POST /api/menu-categories       { name, kind: 'DRINKS' | 'FOOD' | 'OTHER' }
```
Schema additions: `menu_item.note String(400)`, `menu_item.photo_asset_id FK`,
`menu_item_price` (C-3), `menu_category(name unique, kind, sort_order)`.
"Delete" is `meta {active:false}`; a true delete endpoint is only for rows with no
`sale`, `manual_recipe_line` history or price history, and returns 409 otherwise.

### A4. Ingredients

```ts
// GET /api/ingredients
interface IngredientListRow {
  ingredient_id: number
  name: string
  category: string | null
  unit: Unit                          // 'L' | 'KG' | 'ML' | 'G' | 'EACH'
  unit_cost: Cost                     // from current_cost_pence_per_unit + current_cost_source
  pack: { pack_size: string; pack_unit: Unit; pack_cost_pence: number; source: PriceSource;
          supplier_id: number | null; effective_from: string } | null
  note: string | null                 // source_note
  suppliers: { supplier_id: number; name: string; is_preferred: boolean }[]
  used_in_count: number               // distinct menu item groups
  shelf_life: ShelfLifeOut            // existing type
}
interface IngredientsResponse { rows: IngredientListRow[]; categories: { name: string; count: number }[]; estimated_count: number }

// GET /api/ingredients/:id
interface IngredientDetail extends IngredientListRow {
  offers: { supplier_product_id: number; supplier_id: number; supplier_name: string; sku: string | null;
            pack_size: string; pack_unit: Unit; price_pence: number; unit_cost_pence: string;
            vs_cheapest_pct: number | null; is_preferred: boolean; last_seen_price_at: string | null;
            terms_are_placeholders: boolean }[]
  used_in: { group_key: string; name: string; via: 'manual' | 'template'; template_name: string | null }[]
  price_history: { effective_from: string; effective_to: string | null; pack_cost_pence: number;
                   pack_size: string; pack_unit: Unit; source: PriceSource; supplier_id: number | null }[]
}

// POST /api/ingredients/:id/price/preview   and   /price/apply {actor}
interface IngredientPriceIn {
  pack_size: string; pack_unit: Unit; pack_cost_pence: number
  source: 'INVOICE' | 'SUPPLIER_FEED' | 'ESTIMATE'     // explicit; the UI asks (C-1)
  supplier_id: number | null                            // defaults to preferred link
  note?: string
}
// preview → { unit_cost_before: Cost; unit_cost_after: Cost; preview: ImpactPreviewOut; writes_nothing: true }
// apply   → { price_id: number; effective_from: string; rollup_items_recosted: number; supplier_product_updated: boolean }

// POST /api/ingredients                  { name, category, unit, pack: IngredientPriceIn, storage, actor }
// POST /api/ingredients/:id/meta         { name?, category?, note?, actor }       (unit refused once referenced)
// POST /api/ingredients/:id/offers       { supplier_id, sku?, pack_size, pack_unit, price_pence }
// POST /api/ingredients/:id/offers/:spid/preferred   → returns the draft-order sourcing change it causes
// POST /api/ingredients/:id/retire       409 with reference counts when still used
```
Service: `services/set_ingredient_price.py` wrapping `IngredientRepository.add_price`
(extend it with `supplier_id`) + `supplier_product` update + `rollup_for_ingredient`,
with a preview that runs `rollup_menu_items` in a rolled-back savepoint (beware
§8R: pysqlite ignores rollback unless the session is configured for it — preview must
compute without writing, as `preview_component_qty_change` does).

### A5. Menu photos — storage proposal

**Recommendation: content-addressed files on disk, served by Caddy; metadata in SQLite.**

- Table `media_asset(id, sha256 unique, content_type, bytes, width, height,
  created_at)`; `menu_item.photo_asset_id` nullable FK, set on every size row of a group
  (a photo belongs to the product, the card and drawer share `menu-{id}` in the design).
- Upload: `PUT /api/menu-items/{group_key}/photo` with the raw body and
  `Content-Type: image/webp|image/jpeg|image/png` — raw body avoids adding
  `python-multipart`. The client does what `image-slot.js` does: downscale to max
  1200px, encode WebP 0.85, keep the crop as `{x, y, scale}` in
  `menu_item.photo_crop JSON` (or bake the crop in — simpler; recommended). Server:
  ≤ 2 MB, magic-byte check, sha256, write to `/media/menu/{sha256}.webp` atomically
  (temp + rename), then set the FK. `DELETE` clears the FK (the file stays until a
  sweep removes unreferenced assets).
  Response: `{ photo_url: "/media/menu/<sha256>.webp", width, height }`.
- Volumes: new named volume `cafeops_media` mounted **rw at `/media` in `api`** and
  **ro at `/srv/media` in `caddy`**. Do not mount `cafeops_data` into Caddy — it would
  put the SQLite file inside the web server's reach.
- Caddyfile: before the SPA `handle`, add
  `handle /media/* { root * /srv; file_server }` and
  `@media path /media/*` → `Cache-Control "public, max-age=31536000, immutable"`
  (content-addressed names never change). These are unauthenticated URLs; they are
  menu photos with unguessable names, which is acceptable. Serving through the API
  instead would need the `X-API-Key` header, which `<img>` cannot send.
- Backup: `deploy/backup.sh` backs up only the SQLite file. Add an `rsync -a
  /media/ $REMOTE/media/` step and mount `cafeops_media:/media:ro` in `backup`.
- Rejected alternative: BLOBs in SQLite. ~176 photos × ~150 KB ≈ 26 MB would grow the
  4.5 MB database six-fold on the single writer and in every nightly `.backup`, and
  still needs an authenticated fetch-to-blob path to display.
- Fixture / offline mode: `photo_url` null → the slot shows its placeholder (`Photo` /
  `Drop a photo of this item`); upload returns `kind: 'offline'` like other writes.

---

## F. Frontend: what this replaces, what to keep

**Replaced** (route names in `web/src/App.tsx` / `components/shell.tsx`):
- `web/src/screens/Composition.tsx` + `screens/composition/` (`TemplateList.tsx`,
  `SlotGrid.tsx`, `Consequences.tsx`, `model.ts`, `useContainerWide.ts`) → Recipes tab.
- `web/src/screens/Margin.tsx` + `screens/margin/` (`data.ts`, `Disagreement.tsx`,
  `figures.tsx`, `ItemsTable.tsx`, `useWide.ts`) → Menu items tab (per-item margin) and
  the Recipes cost column. The margin % vs margin-per-minute disagreement scatter
  (CLAUDE.md §10 "the disagreement is the finding") has **no home in this design** —
  keep it reachable (e.g. from Finance) rather than delete it.
- `web/src/screens/Proposals.tsx` + `screens/proposals/` (`Confirm.tsx`,
  `Conflicts.tsx`, `Detection.tsx`, `ProposalCard.tsx`, `shape.ts`) → the
  `Detected, not confirmed` rail group in Recipes (C-16).
- `web/src/screens/ConfirmShelfLife.tsx` is **not** replaced; link to it from the
  ingredient detail.

**Keep and reuse:**
- `web/src/lib/dec.ts` — all of it (`parseDec`, `fromMoney`, `add/sub/mul`, `cmp`,
  `toFixed`, `splitExact`, `trimQty`, `sharePct`). Every design `parseFloat` becomes one
  of these.
- `web/src/lib/format.ts` — `money`/`moneyDec` (note: shows pence under £1, e.g.
  `77.71p`; the design's `gbp` always shows `£0.78`, and `unitPrice` shows `£0.0450`.
  Add `gbpDec` and `unitPriceDec` matching the design exactly rather than changing the
  existing helpers used elsewhere), `pct`, `pctRange`, `plural`, `dayShort`,
  **`costView`** (the invariant-8 gate — mandatory for every cost cell here).
- `web/src/lib/api.ts` — `request`, `write`/`WriteResult` (refusal messages verbatim),
  `materialiseProposal` (posts by id), `confirmShelfLife`, `preview` (fix the 409
  parsing, A1). Replace `applyFromToday` (A1).
- `screens/composition/model.ts` — `ROLE_ORDER`, `QTY_INPUT`, `cellKey`, `DirtyCell`.
- `screens/proposals/shape.ts` — `orderSizes`, `isHollow`, `confirmState`,
  `duplicateNames`, `groupQuantities` (conflict display for unconfirmed recipes).
- `screens/margin/data.ts` — `dec`, `isNegative`, `sortItems` (null-safe sort on `Dec`).

---

## S. Summary of MISSING backend pieces

1. Multi-op, effective-dated **template changeset preview/apply** (role, ingredient,
   swappable, add/remove component, prep, base price, flavour options) — only a single
   component qty edit exists.
2. **Menu item writes**: create, rename/category/note, on/off, add/remove size,
   duplicate, manual recipe lines (effective-dated, with preview); plus a grouped
   `GET /api/menu-items` and item detail with lines.
3. **Effective-dated sell price** (`menu_item_price`) with preview; nothing writes
   `price_pence` today.
4. **Ingredient API**: list/detail, create, meta edit, retire; price change as an
   API/service with menu impact preview (only CLI `set-price` exists) and `supplier_id`.
5. **Supplier links** (`supplier_product`) read/write and preferred switch.
6. **Modifier** writes + effective dating + price-source flag; per-template enablement
   contradicts the role model (recommend read-only).
7. **Seasons** list endpoint; template **history** endpoint; proposal **preview**.
8. **Menu photos**: `media_asset` table, upload route, `cafeops_media` volume, Caddy
   `/media/*`, backup step.
9. **Columns**: `menu_item.note`, `menu_item.photo_asset_id`, `menu_category`,
   `ingredient.retired_at`, `modifier` price source.
10. **Client contract bugs**: `applyFromToday` URL/body (404) and 409 superseded parsing.
