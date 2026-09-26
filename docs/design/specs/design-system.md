# Design system v2: extracted from the v2 design export

Source files (treated as data): `docs/design/Cafe Ops v2.dc.html` (shell),
`Agents App v2.dc.html`, `Menu Stock App v2.dc.html`, `Finance App v2.dc.html`.
`sc-comp.js` holds **data only** (`window.SC_COMP` = templates, components, flavours).
It has no components and no colours, so it contributes nothing to the visual system.
Every value below was grepped from inline `style=""` attributes and from the
`DCLogic` scripts, where the state colours live. Nothing here is invented. Where this
spec *proposes* a value the design never states, such as hover or focus, the entry
says **(proposed)**.

This file **supersedes `docs/phase4/DESIGN-LAW.md`** in the places listed in section 0.
The data law in that file (Fig, dec, null never 0, low-confidence forecasts shown in
place of the number) still applies, and so does CLAUDE.md §10.10.

---

## 0. What changes from the Phase 4 design law

| Topic | DESIGN-LAW.md (Phase 4) | v2 design (this file) |
|---|---|---|
| Theme | Dark zinc-950 ramp, measured from finsepa.com | **Light.** White surfaces, `#f6f7fa` canvas and sidebar. No dark mode exists in the design |
| Font | Inter Variable + DM Mono | **Nunito** 400/500/600/700/800 only. No monospace; figures use `tabular-nums` |
| Base size | 12px, line-height 20px | **14px** is the workhorse (191 uses), then 13 (75) and 15 (41) |
| Estimate marking | Dotted underline (`.est`) | **Italic.** The design says so on screen: "Estimates in *italic*, counts upright" |
| Uppercase labels | Banned | **Used** in three places: nav group heads (11px/700/.08em), the Stock table header (11px/700/.06em) and field labels in the recipe editor (11px/700/.05em). CLAUDE.md §10 still says "avoid uppercase eyebrow labels". **Owner decision needed**; this spec follows the design by default (see 3.3) |
| Stat cards, sparklines, DeltaPill | Signature patterns | **Absent.** Figures sit in plain rows and grey tiles. Drop `StatCard`, `Sparkline`, `DeltaPill`, `TabsUnderline` |
| Accent | blue-500 `#3b82f6` | `#4a6fd1` (brand) and `#3558b8` (brand ink) |
| Semantic colour | ok/warn/bad at 500 | Red `#d4554a` does most of the work. Green, amber and the deeper red appear only as **wash + ink pairs** inside pills and tiles |
| Tabs | Filled pill vs underline | Pill filters (two variants, see 5.4) and one segmented control |
| Modals | Impact gate in place, no modal | Same. The design has **no modal or overlay anywhere**. Side drawers replace them |

Kept from the Phase 4 law: tabular figures everywhere, colour only where a threshold
is crossed, nothing wide scrolls the page, English only, no emoji as iconography. The
design uses the glyphs ★ ☆ × ✓ – +; these are text characters, and keep them
`aria-hidden` with a label.

---

## 1. Colour: every literal, with its count and role

Counts are across the four v2 files, markup and scripts (`grep -oiE '#[0-9a-f]{3,6}'`).

| Hex | Uses | Token (proposed name) | Role in the design |
|---|---:|---|---|
| `#5b6475` | 127 | `ink-2` | Secondary text: page subtitles, row sub-lines, column headers in list tables, field labels (legacy style), "we think you have" |
| `#e8ebf0` | 103 | `line` | Default hairline: row dividers, card borders, header bottom rule, pill borders (legacy), table header rule |
| `#8a93a3` | 98 | `ink-3` | Muted: meta lines, nav group heads, Stock table header, placeholders-as-text, `×` close glyphs, "decided/declined", counts at opacity .6 |
| `#d5dae3` | 78 | `line-strong` | Control borders: secondary buttons, compact inline inputs and selects, dashed "add" outlines, toggle-off track, meter outlines |
| `#fff` | 66 | `surface` | Page content, cards, drawers, inputs, active nav item, active segment, white action button on a wash |
| `#1f2633` | 62 | `ink` | Primary text and figures. Also P&L total rule (2px) |
| `#d4554a` | 47 | `alert` | The one alarm colour: errors ("That's not it"), crossed thresholds (margin < 60%, drift > 15%, runs out ≤ 3 days, use-by ≤ 3 days, cash > £5 off), danger outline buttons, dashed warning boxes, changed-cell border in the recipe editor, "Waiting" order status, failed agent run, cash banner dot, the preferred-supplier star |
| `#e2e6ed` | 28 | `line-control` | Border of the height-based controls: 38/40px inputs and selects, the search box, drawer footer buttons, inactive filter chip, "Show more" |
| `#4a6fd1` | 26 | `brand` | Primary buttons, logo tile, solid active pill (Finance/Agents), nav count badge, bars in charts and meters, selected-card ring |
| `#eef0f4` | 19 | `line-soft` | Structural dividers: sidebar right edge, drawer edges, section rules inside drawers, recipe component card borders, photo placeholder fill |
| `#f6f7fa` | 13 | `canvas` | Sidebar, login background, menu-grid background, cost side panel, grey stat tiles, size cells, P&L total column |
| `#f1f3f7` | 7 | `wash` | Neutral wash: segmented-control track, drawer close button, tier square, stale-sync banner, "Never counted" and "Checklist" pills |
| `#3558b8` | 7 | `brand-ink` | Brand as text: active nav label, active soft chip, text-style actions ("+ Add ingredient", "Show 40 more", "Open recipe"), checkbox tick |
| `#fdf1ef` | 6 | `alert-wash` | Cash banner, flagged table rows (cash and bank differences, expenses needing a look), changed input cells, flavour missing its ingredient |
| `#fdecea` | 5 | `bad-wash` | Pill and tile background for a crossed threshold: margin < 60%, "Excluded" trust, "Low" checklist, the Delete item button |
| `#edf1fc` | 5 | `brand-wash` | Selected row, active soft chip, info panel ("made from recipe X") |
| `#c2453b` | 5 | `bad-ink` | Text on `bad-wash` |
| `#f1f3f6` | 3 | `line-row` | Row divider inside dense tables (Stock rows, the "All sizes" summary, the cost grid). One step lighter than `line` |
| `#e8f5ee` | 3 | `ok-wash` | "Trusted" pill, margin ≥ 60% chip and tile |
| `#237a57` | 3 | `ok-ink` | Text on `ok-wash` |
| `#3fa57a` | 2 | `ok` | Toggle track when on ("On the menu", flavour active) |
| `#e2e2e2` | 2 | fold into `line` | P&L dashed row rule. An outlier grey; use `line` |
| `#fdf3e1` | 1 | `warn-wash` | "Drifting" trust pill |
| `#9a6a12` | 1 | `warn-ink` | Text on `warn-wash` |
| `#fdf6e7` | 1 | `est-wash` | "*Italic* costs are estimates" note |
| `#fafbfc` | 1 | `canvas-2` | Stock "as of" meta strip under the filters |
| `#c9d5f5` | 1 | `brand-line` | Border of the active soft chip and size tile |
| `#c9cfda` | 1 | fold into `line-strong` | "Swappable" checkbox border |
| `#2d3f75` | 1 | `brand-deep` | Text inside the `brand-wash` info panel |

rgba literals, all shadows:

| Value | Uses | Where |
|---|---:|---|
| `rgba(31,38,51,.06)` | 3 | active nav item, menu card, login card (large layer) |
| `rgba(31,38,51,.04)` | 3 | active nav item and menu card 1px ring, login card small layer |
| `rgba(31,38,51,.08)` | 1 | active segment in the segmented control |
| `rgba(0,0,0,.2)` | 2 | toggle knob |

Also `opacity:.6` on counts inside chips, `.8` on the price line inside the size tile,
`.55` on an inactive menu card and `.5` on an inactive flavour card.

### 1.1 How the design uses colour

The rules are written as the scripts implement them:

- **Red means a threshold was crossed, and nothing else.** Every red in the scripts is
  a condition: `m < 0.6`, `Math.abs(d1) > 0.15`, `runDays <= 3`, `nextExp <= today+3`,
  `Math.abs(diff) > 500`, `status === 'Waiting'`, `status === 'failed'`.
- **Green appears only as `ok-wash` + `ok-ink`** in a labelled pill or tile, and as the
  toggle track. A margin figure that is fine is `ink`, not green (`mColor`). Only the
  pill background encodes "fine".
- **Wash + ink pairs** are the semantic unit: ok (`#e8f5ee`/`#237a57`), warn
  (`#fdf3e1`/`#9a6a12`), bad (`#fdecea`/`#c2453b`), neutral (`#f1f3f7`/`#5b6475`),
  brand (`#edf1fc`/`#3558b8`). Never put semantic ink on white without its wash, with
  one exception: a plain red figure (`alert`) marks a crossed threshold inside a table.
- **Two reds.** `alert #d4554a` is used as a figure, a border or a dot on white.
  `bad-ink #c2453b` is the text colour on `bad-wash`. Do not merge them; the lighter
  one fails contrast on the pink wash.
- The preferred-supplier star is `#d4554a`, which spends the alarm colour on a
  preference. **Recommend `brand` for the star** (owner decision; one-token change).

### 1.2 Contrast check (proposed fixes)

`ink-3 #8a93a3` on white is about 3.2:1, which fails WCAG AA for body text. The design
uses it for 12–14px meta text and for the sidebar sync line. Keep it for **≥ 14px
non-essential** meta only, and use `ink-2 #5b6475` (≈ 6.1:1) wherever the text carries
information: the stale-sync line, the "Decided recently" timestamps and the empty
states. `alert #d4554a` on white is about 4.0:1. It passes at ≥ 14px bold or ≥ 18px;
for 13px red text use `bad-ink #c2453b` (≈ 4.9:1).

---

## 2. Typography

**Font:** Nunito, weights 400 / 500 / 600 / 700 / 800. The design loads it from Google
Fonts; **self-host it instead** with `@fontsource-variable/nunito`, the same approach
the app takes for Inter today (`web/src/styles.css:2`). Global body settings, from every
file's `<helmet>`:
`font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased`.
The design never sets a line-height except `1.25` on menu card titles and `1.15` on the
sidebar brand name. **Proposed: 1.4** (about 20px at 14px) as the body default.

### 2.1 Measured scale

| px | Uses | Weight / tracking | Used for |
|---:|---:|---|---|
| 11 | 12 | 700, uppercase, .05–.08em; or 800 for the size codes in cells | Nav group heads, Stock table header, field labels in the recipe editor, S/M/XL cell labels, tier square |
| 12 | 36 | 400/700 | Chip counts, pill text (trust, status, badge), meta under fields, sidebar sync line, tool name under agent |
| 13 | 75 | 400 | Row sub-lines, table header (sentence-case style), notes, footnotes, run-log details |
| **14** | **191** | 400; 600 for names in tables; 700 for buttons | **Body, table cells, buttons, pills, banners** |
| 15 | 41 | 400; 700 for card titles; 800 for section heads | Nav items, inputs (40px), card titles, drawer section heads |
| 16 | 15 (+17 as `font:800 16px`) | 800, -.01em | **Section headings** ("Sizes and price", "What it costs", "Where to buy"); login and primary button text; order supplier name uses 18 |
| 17 | 5 (+3) | 800 | Panel heading ("Waiting for you · 2", "Run log") |
| 18–19 | 11 | 800 | Big tile figures (sell price, cost, margin), order basket supplier, Finance section heads |
| 21–22 | 17 | 800, -.01em | **Page title** in the header bar (21); screen-level heading (22); Settings title (22) |
| 24 | 3 | 800, -.01em | Editable entity name (ingredient, supplier, recipe) |
| 26 | 2 | 800, -.01em | Login wordmark; count-mode item name |
| 30 | 2 | 800 / 400 italic | Empty-install headline; "We think you have" estimate figure (italic) |

### 2.2 Type tokens (proposed consolidation)

| Token | Size / line | Weight | Replaces |
|---|---|---|---|
| `text-label` | 11 / 16 | 700, uppercase, `tracking-[.06em]` | 11px headers and labels |
| `text-xs` | 12 / 16 | 400 | 12px |
| `text-sm` | 13 / 18 | 400 | 13px |
| `text-base` | 14 / 20 | 400 | 14px |
| `text-md` | 15 / 22 | 400 | 15px |
| `text-lg` | 16 / 22 | 800, -.01em | section heads (`font:800 16px`) |
| `text-xl` | 18 / 24 | 800, -.01em | 17, 18 and 19px heads and tile figures |
| `text-2xl` | 22 / 28 | 800, -.01em | 21 and 22px page titles |
| `text-3xl` | 26 / 32 | 800, -.01em | 24 and 26px |
| `text-4xl` | 30 / 36 | 800, -.01em | 30px |

Headings are **always weight 800 with `letter-spacing:-.01em`** (45 occurrences of
`-.01em`). There is no 900. Weight 500 appears only on inactive nav and chips; 600 on
table names, drawer secondary buttons and "+ Add" actions; 700 on buttons, pills and
active states.

### 2.3 Figures

- `tabular-nums` globally. Numbers in tables are **right-aligned** (`text-align:right`,
  91 uses). The cells: Left (est.), Drift, Price, Cost, Profit, Margin, Pack £, Per unit,
  Total, £, Difference, In/Out/Balance.
- **Estimates are italic** (`font-style:italic`, plus 6 dynamic `estStyle` or `prepStyle`
  bindings):
  - the estimated on-hand is always italic, because stock is theoretical (invariant 6);
    counts are upright;
  - a cost or unit price is italic when its price source is ESTIMATE;
  - prep seconds are italic until timed;
  - a modifier charge is italic when guessed.
  Each screen repeats the legend in `ink-3` 13px: "Estimates in *italic*, counts upright".
- A missing value is shown in words or as `—`, never as 0: "no price", "not uploaded:
  missing, not zero", "not counted", "not yet", "—".
- Money is formatted `£12.00` and negative is `−£` (U+2212, see `gbp()` in the shell
  script). Percentages have no decimal, except the worst margin in the impact preview,
  which has one.

---

## 3. Shape, space, elevation, motion

### 3.1 Radii (counts)

`10px` ×103 · `12px` ×37 · `14px` ×36 · `50%` ×10 · `16px` ×9 · `999px` ×7 · `18px` ×5 ·
`6px` ×3 · `20px` ×2 · 5/7/8/9/11/24px ×1 each.

| Token | Value | Used for |
|---|---|---|
| `rounded-xs` | 6px | checkboxes (5–6px) |
| `rounded-sm` | 8px | tier selector squares, 7px tier badge |
| `rounded-control` | 10px | inputs, selects, compact buttons, icon buttons, info-panel button |
| `rounded-button` | 12px | height-based buttons, nav items, search, size tiles, tiles and inner cards, secondary pills |
| `rounded-card` | 14px | cards, banners, panels, filter pills (legacy), dashed warning boxes |
| `rounded-card-lg` | 16px | menu grid cards; the legacy primary "pill" buttons use 16–20px, normalise to 16 |
| `rounded-login` | 24px | login card only |
| `rounded-full` | 999px / 50% | filter chips, trust pills, count badges, toggles, round steppers, dots |

Segments inside the segmented control are 9px: the 12px track minus the 3px padding.
Compute it as `calc(var(--radius-button) - 3px)` rather than adding a token.

### 3.2 Spacing rhythm

Gap counts: `8px` ×54, `10px` ×54, `4px` ×33, `6px` ×23, `12px` ×7, `14px` ×6,
`18px` ×6, `2px` ×7, `28px` ×3. That is a **2/4/6/8/10/12/14/18/28** ladder; Tailwind's
default 4px scale covers it with `gap-2.5` (10), `gap-3.5` (14) and `gap-4.5` (18).

| Context | Padding |
|---|---|
| Page header bar | `12px 20px`, bottom border `line` |
| Toolbar / filter row | `10–14px 20px`, bottom border `line-soft` |
| Scrolling content | `16–20px 20–24px` (Stock rows `6px 20px`; Finance tables `5px 24px`) |
| Card | `12px 14px` (×14 uses) |
| Drawer body | `16px 18px 24px`, `gap:18px` between sections |
| Tile (grey stat) | `10px 12px` |
| Sidebar | `18px 10px 14px`; items `0 10px` |
| Empty-install | `36px 44px` |
| Empty state | `40px` or `60px`, centred |

### 3.3 Elevation

Only three shadows exist, and elevation is otherwise carried by borders:

- `shadow-raised`: `0 1px 2px rgba(31,38,51,.06), 0 0 0 1px rgba(31,38,51,.04)`. Active
  nav item and menu card.
- `shadow-seg`: `0 1px 2px rgba(31,38,51,.08)`. Active segment.
- `shadow-login`: `0 1px 2px rgba(31,38,51,.04), 0 12px 32px rgba(31,38,51,.06)`. Login
  card.
- `shadow-knob`: `0 1px 2px rgba(0,0,0,.2)`. Toggle knob.
- The selected menu card uses a ring, `0 0 0 2px #4a6fd1`.

### 3.4 Motion

`transition: background .15s, border-color .15s` on every interactive pill and button
(48 uses); `left .15s` on the toggle knob; `box-shadow .15s` on menu cards. There are
**no keyframes and no animations.** Keep the existing reduced-motion rule
(`web/src/styles.css`, `@media (prefers-reduced-motion)`).

### 3.5 States the design leaves out (proposed)

The design sets `outline:none` on 27 inputs and defines no hover styles. CLAUDE.md
§10.10 requires visible keyboard focus, so:

- **Focus (all interactive):** `outline: 2px solid var(--color-brand); outline-offset: 2px`
  via `:focus-visible`. Inputs also get `border-color: brand` and
  `box-shadow: 0 0 0 3px var(--color-brand-wash)`.
- **Hover:** primary → `brand-ink #3558b8` background; secondary, outline and ghost →
  `canvas #f6f7fa` background; nav item → `rgba(255,255,255,.6)`; table row → `canvas-2`.
- **Disabled:** `opacity:.5; cursor:not-allowed`. The design never disables anything;
  it hides actions instead ("canSend").
- **Pending (a write in flight):** the button keeps its width, its label becomes a verb
  in progress ("Applying…"), and it is disabled.

---

## 4. Tailwind v4 `@theme` block (drop-in replacement for `web/src/styles.css`)

```css
@import 'tailwindcss';
@import '@fontsource-variable/nunito';

@theme {
  /* surfaces */
  --color-surface: #ffffff;
  --color-canvas: #f6f7fa;      /* sidebar, login bg, side panels, tiles */
  --color-canvas-2: #fafbfc;    /* meta strips, row hover */
  --color-wash: #f1f3f7;        /* neutral wash: segmented track, close btn, stale banner */

  /* ink */
  --color-ink: #1f2633;
  --color-ink-2: #5b6475;
  --color-ink-3: #8a93a3;

  /* lines */
  --color-line: #e8ebf0;        /* default hairline */
  --color-line-soft: #eef0f4;   /* structural: sidebar edge, drawer edge */
  --color-line-row: #f1f3f6;    /* dense-table row divider */
  --color-line-control: #e2e6ed;/* 38/40px field borders */
  --color-line-strong: #d5dae3; /* compact control borders, dashed add */

  /* brand */
  --color-brand: #4a6fd1;
  --color-brand-ink: #3558b8;
  --color-brand-deep: #2d3f75;
  --color-brand-wash: #edf1fc;
  --color-brand-line: #c9d5f5;

  /* semantic: threshold crossed */
  --color-alert: #d4554a;       /* figure, border or dot on white */
  --color-alert-wash: #fdf1ef;  /* flagged row, cash banner, changed cell */
  --color-bad-ink: #c2453b;     /* text on bad-wash */
  --color-bad-wash: #fdecea;
  --color-warn-ink: #9a6a12;
  --color-warn-wash: #fdf3e1;
  --color-ok: #3fa57a;          /* toggle track only */
  --color-ok-ink: #237a57;
  --color-ok-wash: #e8f5ee;
  --color-est-wash: #fdf6e7;    /* "italic = estimate" note */

  --font-sans: 'Nunito Variable', 'Nunito', system-ui, -apple-system, 'Segoe UI', sans-serif;

  --text-label: 0.6875rem;  --text-label--line-height: 1rem;
  --text-xs: 0.75rem;       --text-xs--line-height: 1rem;
  --text-sm: 0.8125rem;     --text-sm--line-height: 1.125rem;
  --text-base: 0.875rem;    --text-base--line-height: 1.25rem;
  --text-md: 0.9375rem;     --text-md--line-height: 1.375rem;
  --text-lg: 1rem;          --text-lg--line-height: 1.375rem;
  --text-xl: 1.125rem;      --text-xl--line-height: 1.5rem;
  --text-2xl: 1.375rem;     --text-2xl--line-height: 1.75rem;
  --text-3xl: 1.625rem;     --text-3xl--line-height: 2rem;
  --text-4xl: 1.875rem;     --text-4xl--line-height: 2.25rem;

  --radius-xs: 6px;
  --radius-sm: 8px;
  --radius-control: 10px;
  --radius-button: 12px;
  --radius-card: 14px;
  --radius-card-lg: 16px;
  --radius-login: 24px;

  --shadow-raised: 0 1px 2px rgb(31 38 51 / 0.06), 0 0 0 1px rgb(31 38 51 / 0.04);
  --shadow-seg: 0 1px 2px rgb(31 38 51 / 0.08);
  --shadow-login: 0 1px 2px rgb(31 38 51 / 0.04), 0 12px 32px rgb(31 38 51 / 0.06);
  --shadow-knob: 0 1px 2px rgb(0 0 0 / 0.2);
  --shadow-selected: 0 0 0 2px #4a6fd1;

  --ease-ui: ease;
  --default-transition-duration: 150ms;

  --breakpoint-compact: 56.25rem;  /* 900px: sidebar becomes a drawer below this */
  --breakpoint-wide: 80rem;        /* 1280px: 232px sidebar and wide drawers from here */
}

@layer base {
  :root { color-scheme: light; }
  html, body { background: var(--color-surface); }
  body {
    color: var(--color-ink);
    font-family: var(--font-sans);
    font-size: var(--text-base);
    line-height: var(--text-base--line-height);
    font-variant-numeric: tabular-nums;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--color-ink); }
  a:hover { color: var(--color-brand); }
  input, select, textarea, button { font: inherit; color: inherit; }

  .fig { font-variant-numeric: tabular-nums lining-nums; }
  .est { font-style: italic; }             /* replaces the dotted underline */
  .scroll-x { overflow-x: auto; scrollbar-width: thin; }   /* keep */
  :focus-visible { outline: 2px solid var(--color-brand); outline-offset: 2px; }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
  }
}
```

Keep the existing `.scroll-x` scrollbar styling, restyled to `line-strong`. Swap the
dependency `@fontsource-variable/inter` and `@fontsource/dm-mono` for
`@fontsource-variable/nunito` in `web/package.json`. `@fontsource-variable/newsreader`
is unused and can go.

---

## 5. Components (the primitives `web/src/components/` needs)

Each entry gives exact classes built on the tokens above. `h-10` is 40px, and so on.
Where the design has two generations of the same control, the **height-based
generation** (Stock, menu drawer, recipe editor) is the canonical one. The
**padding-based generation** (Finance, Agents, supplier and ingredient forms) folds
into it. The older family's differences are recorded so nothing is lost.

### 5.1 Button

| Variant | Classes | Source in the design |
|---|---|---|
| `primary` | `h-10 px-4.5 rounded-button bg-brand text-white text-md font-bold hover:bg-brand-ink` | "Start a count" (40px, r12, 15px). Legacy: "Add item" `9px 18px r18 16px`; "Accept" `9px 16px r16 14px`; "Apply from today" r18 |
| `primary` `size="sm"` | `h-9 px-4 rounded-card-lg text-base font-bold` | Agents Accept, "Send to Telegram to confirm" |
| `primary` `block` `size="lg"` | `w-full h-12 rounded-card text-lg font-bold` | Login "Open" (`padding:14px`, r14, 16px); count "Save · next" |
| `secondary` | `h-10 px-3.5 rounded-control border border-line-control bg-surface text-base font-semibold hover:bg-canvas` | Drawer footer "Duplicate" and "Remove size" |
| `outline` `size="sm"` | `h-8 px-3.5 rounded-button border border-line-strong text-base hover:bg-canvas` | Settings "Change", "Sync now"; "Record"; "Upload a CSV export"; history row actions (12px) |
| `danger` | `h-8 px-3.5 rounded-button border border-alert text-alert hover:bg-alert-wash` | "Delete" (ingredient, supplier), "Write off", "Reset demo data" |
| `danger-soft` | `h-10 px-3.5 rounded-control bg-bad-wash text-bad-ink font-bold` | "Delete item" |
| `on-wash` | `h-8 px-3.5 rounded-control bg-surface text-ink font-bold` | Banner action ("Sync now", "Look at it"); info-panel "Open recipe" (`text-brand-ink`) |
| `ghost` | `h-10 px-3 rounded-control text-ink-2 hover:bg-canvas` | "Copy to other sizes" |
| `link` | `text-sm text-ink-3 underline` (no box) | "Stop for now", "Clear filters", "pretend it's stale" |
| `add` (dashed) | `h-10 px-3.5 rounded-control border-[1.5px] border-dashed border-line-strong text-base font-semibold text-brand-ink` | "+ Add ingredient", "+ Add component", "+ Link an ingredient", top-up suggestions (13px, r12) |
| `IconButton` | `size-9 rounded-control grid place-items-center text-lg text-ink-3` plus the `bg-wash text-ink-2` variant for drawer close | the × buttons (36px; the banner × is 32px, raise it to 36) |
| `confirm-twice` | behaviour: the first press relabels to "Tap again to …" and arms for 4s; the second press fires | "Reset demo data". Use for any destructive action with no preview |

Props: `variant`, `size ('sm'|'md'|'lg')`, `block`, `pending`, `pendingLabel`, `disabled`.
Always render a `<button>`: the design's `<div onClick>` is not keyboard accessible.

### 5.2 Input / Select / Field

| Variant | Classes | Source |
|---|---|---|
| `Input` (md, canonical) | `h-10 px-2.5 rounded-control border border-line-control bg-surface text-md outline-none focus:border-brand focus:ring-3 focus:ring-brand-wash` | Drawer fields (40px, 15px) |
| `Input` `size="sm"` | `h-[38px] text-base` | Recipe grid cells, flavour and swap cards |
| `Input` `size="xs"` (in-table) | `h-7 px-1.5 rounded-control border border-line-strong text-base` (13px for dates) | Finance rows, supplier product rows, "Staff time at £__", count history inputs (`padding:2-3px 6px`) |
| `Input` `numeric` | adds `text-right` | every money or qty input |
| `Input` `changed` | `border-alert bg-alert-wash` | Recipe cells whose value differs from the saved recipe (`cellStyle(changed)`) |
| `Input` `missing` | `border-alert` | Shelf life "not set" on a perishable; flavour with no ingredient |
| `Input` `est` | `italic` | prep seconds not timed; guessed charge |
| `MoneyInput` | `£` prefix span (`text-ink-3 text-sm`) + numeric Input. Big variant: inside a grey tile, `text-xl font-extrabold`, borderless | "Sell price" tile, base price cells |
| `SearchInput` | wrapper `h-10 rounded-button border border-line-control px-3 bg-surface flex items-center`; icon = 12px ring `border-2 border-ink-3 rounded-full`; inner input borderless `text-md` | Stock and menu search |
| `TitleInput` | borderless, `border-b border-line`, `text-3xl font-extrabold tracking-[-.01em]` (the drawer variant has no underline, `text-2xl`) | Ingredient, supplier and recipe names |
| `Select` | same boxes as Input (md, sm, xs), native `<select>` | all |
| `Select` `role` | `h-[38px] w-[130px] bg-canvas border-canvas text-xs font-bold tracking-[.04em]` | component role picker |
| `Field` (label wrapper) | `flex flex-col gap-1` + label `text-label text-ink-3` (uppercase 11/700/.05em). Drawer variant: `text-xs font-bold text-ink-3` sentence case. Legacy: `text-base text-ink-2` | all forms |
| `Password` | `h-12 px-3.5 rounded-card border border-line-strong text-lg` | Login (13px 14px padding, r14, 17px) |

**Recommendation:** use one `Field` label style app-wide: `text-xs font-bold text-ink-3`,
sentence case. It is the drawer style, it is legible, and it does not collide with the
uppercase ban in CLAUDE.md §10. Use the uppercase `text-label` only if the owner
confirms the design's uppercase is intended.

### 5.3 Toggle, Checkbox, Stepper, SizeTile

- **Toggle**: track `w-10 h-6 rounded-full` with `bg-ok` on and `bg-line-strong` off,
  `transition-colors`. Knob `size-[18px] rounded-full bg-white shadow-knob absolute
  top-[3px]`, `left-[3px]` → `left-[19px]`, `transition-[left]`. Label to the right
  `text-base font-semibold`. Render as `role="switch" aria-checked`.
- **Checkbox**: `size-[18px] rounded-xs border-[1.5px] border-line-strong` (20px/1px in
  legacy); tick `✓ text-xs font-extrabold text-brand-ink`. Terms-confirmed variant: the
  border and label use `alert` while unconfirmed.
- **Stepper**: round buttons with the value between them. Small: `size-[22px]
  rounded-full border border-line-strong`, value `min-w-11 text-center` (order lines).
  Large: `size-[50px] border-line text-2xl`, value input `text-2xl text-center
  rounded-button` (count mode).
- **SizeTile**: `min-w-16 h-[52px] rounded-button border px-3 flex-col center`; label
  `text-md font-extrabold`, sub `text-xs opacity-80`. Inactive: `border-line-control`;
  active: `bg-brand-wash text-brand-ink border-brand-line`. Add-size uses the dashed
  style.

### 5.4 Chips, pills and the segmented control

The design has **two filter-pill styles**:

1. **`FilterChip` (soft, canonical)**: `h-[34px] px-3.5 rounded-full border text-base
   whitespace-nowrap flex items-center gap-1.5`. Inactive: `border-line-control
   text-ink font-medium`. Active: `bg-brand-wash border-brand-line text-brand-ink
   font-bold`. A trailing count is `text-xs opacity-60`. The 36px height variant is the
   compact category rail. Used for Stock filters and menu categories.
2. **Solid pill (legacy)**: `px-3.5 py-1.5 rounded-card border border-line text-base`;
   active is `bg-brand text-white`. Used for Agents run-log filters, Finance months and
   views, the Orders view switch, supplier delivery days and customer swaps.

**Recommendation:** replace every solid pill with `FilterChip`. The Menu/Stock file is
the most recent and most refined, and two active-state languages for the same job is
the "invented system" problem §8N warns about. Special case: Finance's "Needs a look"
filter uses a red border (`border-2 border-alert`) and `bg-alert text-white` when
active. Keep it as `FilterChip tone="alert"`.

- **Segmented**: track `h-10 p-[3px] rounded-button bg-wash flex items-center gap-0.5`,
  with an optional leading label `text-xs text-ink-3 px-2`. Segment `h-[34px] min-w-[34px]
  px-2.5 rounded-[9px] text-base`: inactive transparent `font-medium`, active
  `bg-surface shadow-seg text-brand-ink font-bold`. Stock uses it for the Tier
  All/A/B/C filter. Use `role="radiogroup"`.
- **Chip (static, clickable link-chip)**: `px-3.5 py-1.5 rounded-button border
  border-line-strong text-base`. Used for "Used in" and one-off recipe links. The "Up
  next" read-only chip is `px-2 rounded-card border-line-strong text-sm`.

### 5.5 Badge / Pill / StatusTag

| Component | Classes | Tones |
|---|---|---|
| `CountBadge` (nav) | `text-[11px] font-bold text-white rounded-full px-[7px] py-0.5` | `bg-brand` (default). Show only when the count is > 0 |
| `Pill` (filled, semantic) | `inline-flex items-center h-6 px-2.5 rounded-full text-xs font-bold whitespace-nowrap` | ok (`bg-ok-wash text-ok-ink`), warn (`warn-wash/warn-ink`), bad (`bad-wash/bad-ink`), neutral (`wash/ink-2`), muted (`wash/ink-3`). Trust: Trusted=ok, Drifting=warn, Excluded=bad, Never counted=muted, Checklist=neutral, Low=bad |
| `MarginChip` (on a photo) | `px-[9px] py-[3px] rounded-full text-xs font-bold` | margin < 60% bad, ≥ 60% ok, no price neutral |
| `StatusTag` (outline) | `px-2 rounded-button border text-xs` with the border and text in the tone colour | Order status: Draft `ink-3`; Waiting `alert`; Confirmed/Sent `ink`; Received/Bought/Cancelled `ink-3`. Trust in the drawer uses the same style |
| `TierBadge` | `size-[22px] rounded-[7px] bg-wash text-ink-2 text-label font-bold grid place-items-center` | A / B / C |
| `Dot` | `size-2 rounded-full` | banner severity |

### 5.6 Table

The design builds tables from CSS grid rows (`grid-template-columns` per table), not
`<table>`. Build `Table` on a real `<table>` for semantics, or on grid with
`role="table"`. Either way, the column template is a prop.

- **Header, canonical (sentence case)**: `text-sm text-ink-2 py-1.5 border-b
  border-line`, with numeric columns `text-right`. Used by Agents, order history,
  supplier products, every Finance table and batches.
- **Header, uppercase (Stock list, "All sizes")**: `text-label text-ink-3 uppercase
  tracking-[.06em] py-2.5 border-b border-line-soft`. **Owner decision.** The
  recommendation is the canonical header everywhere, for the same reason as `Field`.
- **Row, dense (default)**: `py-[5px]–py-[7px]`, `text-base`, `border-b border-line`,
  `items-center`, which gives about 32–34px rows. Finance and history.
- **Row, comfortable (Stock)**: `min-h-[52px] py-1.5 px-5 border-b border-line-row
  items-center`. The first cell is `TierBadge` plus the name `font-semibold truncate`.
- **Row, selectable**: `cursor-pointer`; selected is `bg-brand-wash`; flagged is
  `bg-alert-wash`.
- **Cells**: numeric `text-right tabular-nums`; secondary `text-sm text-ink-2 truncate`;
  estimate `italic`; threshold-crossed figure `text-alert` (drift also `font-bold` once
  |d| ≥ 10%); remove `×` column 20px `text-ink-3 text-center`.
- **Total row**: `py-2.5 text-md font-bold border-t border-line`. In P&L the total row
  has a `2px solid ink` top rule and the dashed rows are `1.5px dashed line`.
- **Expandable row** (Agents run log): clicking toggles a detail block
  `ml-[106px] border-l border-line pl-2.5 text-sm text-ink-2`. Needs
  `aria-expanded` on a button.
- **Key-value grid** (cost grid, impact summary): `grid gap-x-2`, label cells
  `text-ink-2`, value cells right-aligned, row rule `border-line-row`.
- **Sticky header**: Finance sales uses `position:sticky; top:0; background:#fff`.
  Offer `stickyHeader`.
- Wide tables sit in `<ScrollX>`. **The page never scrolls sideways** (DESIGN-LAW data
  law, kept).

### 5.7 Card, Panel, Tile, Notes

| Component | Classes | Source |
|---|---|---|
| `Card` | `rounded-card border border-line p-3 px-3.5` (12px 14px) | proposals, order baskets, "What agents may do" |
| `Card` `soft` | `rounded-card border border-line-soft bg-surface` | recipe component cards, flavour and swap cards |
| `GridCard` (menu) | `rounded-card-lg bg-surface shadow-raised overflow-hidden`, selected `shadow-selected`, inactive `opacity-55` | menu grid |
| `Tile` | `rounded-button bg-canvas px-3 py-2.5` + `Field` label + value `text-xl font-extrabold` | Sell price / Costs us / Margin (the margin tile takes its wash from the threshold) |
| `InfoPanel` | `rounded-button bg-brand-wash text-brand-deep px-3.5 py-3 text-base flex flex-col gap-2` + `on-wash` button | "Made from the recipe X" |
| `EstNote` | `rounded-control bg-est-wash px-3 py-2 text-sm text-ink-2` | "*Italic* costs are estimates" |
| `WarnBox` (dashed) | `rounded-button border-[1.5px] border-dashed border-alert px-3 py-2 text-base` | "Terms are guesses" at the top of Orders, Finance "Needs a look" |
| `DashedPanel` (neutral) | `rounded-card border-[1.5px] border-dashed border-line-strong p-3 px-3.5` | "Shop run · can't wait for a delivery" |
| `Meter` | track `h-3 border border-line-strong` (no radius), fill `bg-brand` | drift attribution split, "Where the money went" |
| `BarChart` | bars `bg-brand`, baseline `border-b border-line`, value labels `text-xs text-ink-2`, missing day `1.5px dashed` outline with no fill; bars over the threshold `bg-alert` | Shop runs, takings by day, cash over/under |
| `Empty` | `p-10 text-center text-ink-3 text-md` (use `ink-2` for contrast, see 1.2) | "Nothing in this view." |

### 5.8 Drawer (the only overlay-like surface)

- Right-hand panel **inside the layout**, not over it: `flex-none border-l
  border-line-soft flex flex-col min-h-0`. Widths: 420px wide / 380px compact (menu
  item); 400px (stock detail, `bg-canvas`); 380/320px (recipe cost panel, `bg-canvas`).
- Header: `px-4 py-3 border-b border-line-soft flex items-center gap-2`. Context line
  `text-sm text-ink-2`; close `IconButton` with a wash background. The stock-detail
  variant puts the title (`text-2xl`) and a bare `×` in the header.
- Body: `overflow-y-auto px-4.5 pt-4 pb-6 flex flex-col gap-4.5`. Section head
  `text-lg`.
- Footer actions: `border-t border-line-soft pt-3.5 flex gap-2`, buttons `flex-1`.
- **Below 900px it becomes a full-screen sheet**: `fixed inset-0 z-40 bg-surface`, with
  the header sticky, focus trapped, Esc closing it and focus returned to the row that
  opened it. The design has no mobile frame, so this is proposed.

### 5.9 Banner

`mx-4 mt-2.5 px-4 pr-3 py-2.5 rounded-card flex items-center gap-3 text-base flex-none`
containing `Dot`, the text (`flex-1`), an `on-wash` action button and a 36px close.

| Tone | Background | Dot |
|---|---|---|
| `stale` (neutral) | `wash #f1f3f7` | `ink-3 #8a93a3` |
| `alert` | `alert-wash #fdf1ef` | `alert #d4554a` |

Banners stack in the order of the `banners[]` array, above the page content and
below nothing. There is no page header above them; they sit at the top of the main
column. Use `role="status"` for stale and `role="alert"` for alert, and give the close
button an `aria-label`.

### 5.10 Page header

`flex items-center gap-4.5 px-5 py-3 border-b border-line flex-none`: title
`text-2xl` (21px), subtitle `text-base text-ink-2`, then a spacer, then a
`savedLabel` (`text-base text-ink-3`), then the primary action. The subtitle is a
sentence about what the page *is* ("they read, work things out and propose; a person
confirms"). Keep that voice.

### 5.11 Primitive inventory for `web/src/components/`

| File | Exports | Status vs today |
|---|---|---|
| `ui/button.tsx` | `Button`, `IconButton`, `ConfirmTwiceButton` | Replace `ui.tsx:447` and `ui.tsx:698` visuals |
| `ui/field.tsx` | `Input`, `MoneyInput`, `SearchInput`, `TitleInput`, `Select`, `Field`, `PasswordInput` | New (today's inputs are ad hoc per screen) |
| `ui/toggle.tsx` | `Toggle`, `Checkbox`, `Stepper`, `SizeTile` | New |
| `ui/chips.tsx` | `FilterChip`, `FilterChipRow`, `Segmented`, `LinkChip` | Replaces `Tabs` (`ui.tsx:412`) and `TabsUnderline` (`ui.tsx:656`) |
| `ui/badges.tsx` | `CountBadge`, `Pill`, `MarginChip`, `StatusTag`, `TierBadge`, `Dot` | Replaces `Badge` (`ui.tsx:287`) and `Chip` (`ui.tsx:306`) |
| `ui/table.tsx` | `Table`, `Th`, `Td`, `Tr` (`selected`, `flagged`, `expandable`), `TotalRow`, `KeyValueGrid`, `ScrollX` | Restyle `ui.tsx:523-655`; keep `ScrollX` (`ui.tsx:96`) and `Cell` (top/sub) |
| `ui/card.tsx` | `Card`, `GridCard`, `Tile`, `InfoPanel`, `EstNote`, `WarnBox`, `DashedPanel`, `Empty`, `Loading`, `ErrorBox` | Replace `Card`/`Panel`/`Note` (`ui.tsx:25,341,325`); drop `StatCard`, `StatRow`, `Stat`, `DeltaPill`, `Sparkline` |
| `ui/drawer.tsx` | `Drawer` (inline ≥ 900px, sheet < 900px) | New |
| `ui/banner.tsx` | `Banner`, `BannerStack` | New |
| `ui/charts.tsx` | `Meter`, `Bars`, `DivergingBars` | New (hand-rolled SVG or divs, per CLAUDE.md §3) |
| `ui/page.tsx` | `PageHeader`, `SectionHead`, `Toolbar` | Replace `PageHeader` (`ui.tsx:60`) and `SectionLabel` (`ui.tsx:83`) |
| `prim.tsx` | `Fig`, `CostFig`, `Qty`, `PenceExact`, `Exact` | **Keep the logic**; change `EST` (`prim.tsx:66`) from dotted underline to `italic` and the weights to the Nunito set |
| `gate.tsx` | `Gate` | **Keep the logic** (impact preview in place, no modal); restyle to `Card` plus `brand` border |
| `shell.tsx` | `Shell`, `Sidebar`, `Unlock` | Rewrite visuals; see `shell-agents.md` §1 for what to keep |
