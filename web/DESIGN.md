# Design — Café Ops web

Sasha's Corner, 23 Commercial Street, Dundee. Coffee, pastries, bubble tea, seasonal
drinks. The daily interface is the Telegram bot; **this** is where the owner sits down a
couple of times a week, on a laptop, and thinks. Nothing in it is urgent. It must not
look like an operations console.

This is pass one of the process spec §11 mandates. **No code in this document.** The
self-review against §11's avoid-list, and what I changed because of it, is §7.

---

## 1. The governing decision: this product is small numbers

Every figure in the payloads is two or three significant digits with a decimal tail:
`0.18` L of milk, `85` seconds of prep, `77.709059` pence of cost, `−13.8%` drift,
`£3.60` on the menu, `4` days of shelf life left. There is no figure in this product
that benefits from being set at 48px inside a box. What the figures need is to **line
up**, to **carry their own precision honestly**, and to be **instantly separable from
the prose around them**.

Three rules follow, and they drive everything below.

**R1 — Figures are monospaced; prose is not.** A number set in the mono family *is* a
measured value. A number set in the serif does not exist: there are none. This gives the
app a second channel for free — the reader can tell data from commentary without reading
either.

**R2 — Tabular alignment is structural, not a font feature.** See §3.

**R3 — Rounding is visible, never silent.** Derived costs arrive as exact decimal
strings of pence (`"77.709059"`). Displaying `77.71p` and stopping is a small lie that
compounds: nine rounded item costs will not sum to the rounded total. So in the two
places where exactness is the point — the composition editor's cost column and the
impact preview — the digits past the displayed precision are **printed, in the muted
ink, in the same figure column**: `77.70⁹⁰⁵⁹` reads as `77.71p` at a glance and as
`77.709059p` when you look. Everywhere else the figure is rounded and the units are
declared. The tail is never a tooltip, because a tooltip is a thing you have to suspect
exists.

### Units and precision, decided once

| Quantity | Displayed as | Why |
|---|---|---|
| Money under £1 | pence, 2 dp — `77.71p` | A café thinks in pence below a pound. `£0.7771` is four leading noise characters. |
| Money £1 and over | pounds, 2 dp — `£26.95` | |
| Money, exact-tail contexts | `77.70⁹⁰⁵⁹p` | R3. Cost column + impact preview only. |
| Ingredient quantity | as sent, trailing zeros trimmed, unit always attached — `0.18 L`, `47.162 EACH` | The string is authoritative; we do not re-render it. |
| Percentage | 1 dp, sign always for drift and deltas — `−13.8%`, `+3.1%` | An unsigned drift is ambiguous about direction, and direction is the whole finding. |
| Prep time | whole seconds — `85 s` | |
| Days | whole days — `4 days left` | |

No float arithmetic anywhere. Where the UI must derive a figure (the expiry-loss running
cost is the only aggregate it computes), it is exact fixed-point on integers, and rows
with a missing cost are excluded from the total and the exclusion is printed.

---

## 2. Palette — six roles

Colour is reserved for a **crossed threshold**. Nothing is coloured for being good;
there is deliberately no positive colour in the palette, because a traffic light needs
at least two lights and this one only has one.

| Token | Hex | Role |
|---|---|---|
| `paper` | `#F1F2F4` | The page. A cool blue-grey light — Dundee daylight off the Tay, not a cream tea-room. |
| `slab` | `#FDFDFE` | The only surface you can type into: an editable quantity cell. Nothing else in the app uses it. |
| `ink` | `#15191E` | Figures, headings. Near-black with a blue cast. 15:1 on paper. |
| `muted` | `#5A636D` | Labels, prose, the counted-basis equation, the exact tail. 5.2:1 on paper. |
| `rule` | `#D3D8DD` | Hairlines. Derived: `rule-strong` `#9AA3AC` for the divider that separates theoretical from counted, and for section breaks. |
| `flag` | `#96262B` | Oxblood. **Crossed threshold only.** 7.3:1 on paper. Derived: `flag-wash` `#F6E9E7`, the ground of a drift-attribution block, and `flag-light` `#E8A29C`, the same idea made readable on ink inside the commit gate (8.4:1). |

What earns `flag`: drift past the 10% tuning band or the 15% refusal line; a theoretical
figure with no count anchor; a short-dated batch; a negative on-hand; a margin that goes
negative in a preview; a supplier minimum not met. What does **not** earn it: an
estimated price (42 of 113 are estimates — the common case is not an alarm), an
out-of-season item, a low-confidence forecast, or any number merely being large or small.

**Estimates and absences are typographic, not chromatic.** An estimated figure carries a
dotted underline and its source in brackets — `77.71p ⟨estimate⟩`. A missing cost is an
em-rule and the words `no price` in muted, plus `excluded from totals` on the aggregate
it was left out of. Neither is red, because neither is wrong.

**Measurement drift and expiry drift share the hue and differ in form** — solid rule
versus ticked rule — because they are both thresholds crossed but have opposite fixes,
and a second hue would rebuild the traffic light. Form survives colour-blindness and
greyscale printing; a hue pair does not.

---

## 3. Type — two families, and why the figures are monospaced

**Newsreader** (variable, `opsz` axis) for everything that is words — prose, headings,
the API's own sentences, the masthead. An editorial serif built for screen reading: it
holds up at 14px, and its display optical size gives the masthead and screen titles
character without a second display family. There is **no sans-serif anywhere in this
application.**

**DM Mono** (300 / 400 / 500) for every figure, every column label and every control.

The justification for a monospace is not taste, it is failure modes:

1. **A monospace cannot fail to be tabular.** Proportional tabular figures depend on the
   `tnum` OpenType feature actually reaching the glyphs — through a variable font, a
   CSS `font-feature-settings` that a later rule can clobber, a `font-variant-numeric`
   that a reset can drop, and a fallback family that may not carry the feature at all.
   Every one of those is a silent failure: the column simply stops aligning and nobody
   gets an error. In a monospaced family, equal advance width is the definition of the
   family. There is no feature to lose. Given that spec §11 says data that does not
   align in columns cannot be scanned, I am not prepared to rest the primary reading
   affordance on a feature flag.
2. **DM Mono's figures are narrow and light**, which matters when one cell legitimately
   holds `105.235471` and the next holds `5`. A wider mono pushes a six-decimal cost out
   of a three-pane layout; DM Mono at 300/400 also keeps a dense grid from going grey-dark
   with ink.
3. **Its `1`, `7`, `0`, `6`, `9` are unmistakable at 13px**, and `0.18` versus `0,18`
   versus `018` is a recipe error that costs money.
4. **The serif/mono split is the second encoding channel** (R1), and I need every
   non-colour channel I can get, having spent colour on thresholds. Serif means *somebody
   wrote this*; mono means *something measured this*. It is a wider gap than serif/sans or
   sans/mono, so it reads without being learned.

Fallbacks are declared as `ui-monospace, "SF Mono", Menlo, monospace` — all tabular by
construction, so R2 holds even if the webfont never loads. Both families are bundled
locally (no CDN), so the app works offline and there is no flash of proportional figures.

Sizes: figures 15px/500 for a headline value, 13px/400 for a subordinate one, 12px for
the exact tail and equations. Prose 14px. Headings at body size in Newsreader 600.
**Nothing in this app is set in uppercase** except the API's own vocabulary, which is data.

---

## 4. Composition editor — wireframe

Spec §10.1. Three panes. The boldness goes here, and it goes into the grid and the gate,
not into ornament.

### Desktop (≥1024px)

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ Sasha's Corner  Composition                            23 Commercial Street, Dundee   │
│ ──────────────────────────────────────────────────────────────────────────────────────│  ← hairline, full bleed
│ Composition · Stock                                    as of 23 Sep 2026, 18:01       │
├──────────────────────┬──────────────────────────────────────────┬─────────────────────┤
│ Templates         1  │ Flavoured Latte                          │ Consequences        │
│                      │ Latte / flavoured latte · 3 sizes        │                     │
│ Latte / flavoured    │ 1 axis: Flavour (3 options) · 3 modifiers│  size        S      │
│   Flavoured Latte    │ ─────────────────────────────────────────│  cost    67.94⁶⁷⁰⁶p │
│     9 items          │                    S       M      XL     │  margin      78.8%  │
│     10 components    │ COFFEE                                   │  after lab.  69.3%  │
│                      │  Coffee beans     0.018   0.018  0.027 KG│  per min   201.64p  │
│ ── hairline ─────────│    1900.00p/KG                           │  items          3   │
│                      │                                          │ ─────────────────── │
│ Nothing else is      │ MILK                                     │  size        M      │
│ materialised yet.    │  Whole milk   ▸   0.18    0.18    0.25  L│  cost    77.70⁹⁰⁵⁹p │
│ 27 proposals sit in  │    69.70⁵⁸⁸²p/L · substitutable          │  margin  78.4–81.0% │
│ the importer.        │    3 modifiers substitute here (+40p)    │  after lab. 68.9–72.7%│
│                      │                                          │  per min 199.3–234.6p│
│                      │ FLAVOUR                                  │  items          3   │
│                      │  set by the Flavour axis  15      20   25 │ ─────────────────── │
│                      │    Vanilla · Caramel · Pistachio(season) │  size       XL      │
│                      │                                          │  cost   105.23⁵⁴⁷¹p │
│                      │ PACKAGING                                │  margin  74.3–75.5% │
│                      │  8oz paper cup     1       ·       ·  EA │  after lab. 62.9–64.5%│
│                      │    8.50⁸⁰⁰⁰p · optional                  │  per min 144.3–182.9p│
│                      │  8oz cup lid       1       ·       ·  EA │  items          3   │
│                      │  12oz paper cup    ·       1       ·  EA │ ─────────────────── │
│                      │  12oz cup lid      ·       1       ·  EA │ Lowest margin       │
│                      │    2.57⁴⁰⁰⁰p ⟨estimate⟩                  │  Pistachio Latte XL │
│                      │  16oz paper cup    ·       ·       1  EA │  62.9% after labour │
│                      │  16oz cup lid      ·       ·       1  EA │  135 s prep, from   │
│                      │                                          │  the item not the   │
│                      │ SUNDRY                                   │  template           │
│                      │  Napkin            1       1       1  EA │ ─────────────────── │
│                      │    0.80⁰⁰⁰⁰p ⟨estimate⟩                  │ 9 of 9 items are    │
│                      │ ═════════════════════════════════════════│ costed from         │
│                      │ Prep seconds      75      85     100   s │ ESTIMATE prices.    │
│                      │   ⟨estimate⟩ · labour £14.50/hr loaded   │ Every margin here   │
│                      │ ─────────────────────────────────────────│ inherits that.      │
│                      │ 3 of 9 items are out of season today:    │                     │
│                      │ Pistachio Latte S, M, XL — Spring only   │                     │
│                      │ (01 Mar–31 May). Their recipes still     │                     │
│                      │ resolve; only today's menu is in question│                     │
│                      │                                          │                     │
│                      │ Milk M edited 0.18 → 0.20   [ Preview ]  │                     │
│                      │ Edits take effect today. Yesterday's     │                     │
│                      │ costs stay as they were.                 │                     │
└──────────────────────┴──────────────────────────────────────────┴─────────────────────┘
```

Notes the wireframe is making, deliberately:

- `·` in a size cell means **this component is not used at that size** — 8oz cup exists
  only at S. It is not `0`, and a typed `0` would mean something different (used, zero
  quantity). Two absences, two glyphs.
- The unit sits **outside** the grid, once per row, at the right. Inside the cells there
  are only figures, so the column aligns.
- The per-unit ingredient price sits under the row in muted with its exact tail, so the
  owner can see where the cost comes from without leaving the pane.
- The axis slot has no ingredient and the row says so in words. Its quantity is still
  editable, because the axis substitutes the ingredient, not the amount.
- `Prep seconds` is a row *in the same grid*, below a double rule, because it is a
  per-size quantity like any other and putting it elsewhere is what makes people forget
  that labour has a recipe too.
- The right pane is not a card and has no boxes in it. It is a three-column figure block
  per size, separated by hairlines. Margins are ranges where the flavour price deltas
  make them ranges — a single number there would be a fiction.

### The commit gate — the only inverted surface in the app

Mandatory before every commit. Not a modal: it replaces the component grid and the
consequences pane **in place**, on an ink ground with paper text, so the edit you are
about to make is the only thing on the screen. There is no backdrop to dismiss by
accident and no scroll-lock to get wrong at 375px.

```
      ┌──────────────────────────────────────────────────────────────┐
      │ Before you change the Whole milk slot                        │
      │ ──────────────────────────────────────────────────────────── │
      │  Milk       0.18 L → 0.20 L   size M                         │
      │             0.12 L → 0.14 L   size S                         │
      │             0.25 L → 0.30 L   size XL                        │
      │                                                              │
      │  Affects                     9 menu items                    │
      │  Cost per item        +1.39p to +3.49p   not uniform         │
      │  COGS, last 30 days            +£26.95                       │
      │  Margin per minute      −0.98p to −2.42p                     │
      │                                                              │
      │  Lowest margin after                                         │
      │    Vanilla Latte XL     74.3% → 73.5%                        │
      │                                                              │
      │  ▏The cost delta is not uniform across the 9 items, so there │
      │  ▏is no single per-item figure. The range is above; the       │
      │  ▏per-item figures are below.                                │
      │                                                              │
      │  Per item                  cost        margin                │
      │   Pistachio Latte M   77.71 → 79.10   81.0 → 80.7            │
      │   … 8 more                                                   │
      │                                                              │
      │  This preview wrote nothing.                                 │
      │  ──────────────────────────────────────────────────────────  │
      │  [ Apply from today ]   [ Cancel ]                           │
      │  Apply from today is the only option. Recipe edits are        │
      │  effective-dated: today forward changes, history does not.    │
      │  That is what keeps last month's margins honest.              │
      └──────────────────────────────────────────────────────────────┘
```

- `cost_delta_pence_per_item` is `null` when the delta is non-uniform, and the API sends
  a range instead. The preview shows the range and prints the API's own warning. It never
  invents an average.
- The effective-dating sentence is in the gate, at body size, next to the button. Not a
  tooltip, not a help icon.
- If the slot has been superseded since the page loaded the API answers 409 with the
  current component id. That is rendered as its own state — *"this slot was edited
  elsewhere; id 14 is current; reload the template"* — not as a generic failure, because
  the 409 exists precisely so that a stale edit cannot be waved through.

### Mobile (375px)

```
┌───────────────────────────────┐
│ Sasha's Corner                │
│ Composition · Stock           │
│ ───────────────────────────── │
│ Flavoured Latte           ▾   │  ← templates become a select
│ 9 items · 10 components       │
│ ───────────────────────────── │
│ COFFEE                        │
│  Coffee beans (house blend)   │
│  1900.00p/KG                  │
│    S      0.018 KG            │  ← the grid transposes:
│    M      0.018 KG            │     sizes become rows, and the
│    XL     0.027 KG            │     figure column still aligns
│ ───────────────────────────── │
│ MILK                          │
│  Whole milk    substitutable  │
│  69.70⁵⁸⁸²p/L                 │
│    S      0.12  L             │
│    M      0.20  L   edited    │
│    XL     0.25  L             │
│ ───────────────────────────── │
│ …                             │
│ ───────────────────────────── │
│ Prep seconds ⟨estimate⟩       │
│    S      75 s                │
│    M      85 s                │
│    XL    100 s                │
│ ───────────────────────────── │
│ Consequences                  │
│  S  67.95p  78.8%  201.6p/min │  ← one line per size, mono,
│  M  77.71p  78.4–81.0%  …     │     still a column
│  XL 105.24p 74.3–75.5%  …     │
│ ───────────────────────────── │
│ [ Preview 1 edit ]            │  ← sticky at the bottom
└───────────────────────────────┘
```

---

## 5. Stock — wireframe

Spec §10.2. The screen's job is that **theoretical and counted are never confusable.**
Three mechanisms at once, none of them a tooltip and none of them a legend:

1. **Position.** The theoretical figure is the row's headline, in the on-hand column.
   The counted figure never appears in that column, ever. It appears below, inside an
   equation that names it.
2. **Type and weight.** Theoretical: mono 500, 15px, ink. Counted: mono 400, 12px,
   muted, always inside the phrase `counted 12.037 on 19 Sep + ledger 36.086`.
3. **A rule of the UI, stated once at the top of the screen and enforced everywhere:**
   a counted figure is never printed bare. If a number appears without the word
   *counted* and a date attached to it, it is theoretical.

A row whose `has_count_basis` is false gets `flag`: it is a movement sum with no
physical anchor, which is a threshold crossed, not a detail.

### Desktop (≥1024px)

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ Sasha's Corner  Stock                                   23 Commercial Street, Dundee     │
│ ─────────────────────────────────────────────────────────────────────────────────────────│
│ Composition · Stock                            tier A ▾   as of 23 Sep 2026, 23:59       │
│                                                                                          │
│ Every figure below is theoretical — the ledger's projection from the last physical        │
│ count. A count is the only truth. 11 ingredients · 6 earn auto-ordering · 1 refused ·     │
│ nothing short-dated · expiry loss since last count £1.16 across 7 costed ingredients.     │
│                                                                                          │
│                      on hand ┃ basis                     drift   soonest    runs out     │
│ ──────────────────────────────────────────────────────────────────────────────────────── │
│ Whole milk            48.123 ┃ counted 12.037 on 19 Sep  −13.8%   4 days     28 Sep      │
│  L · CHILLED               L ┃ + ledger 36.086, 105 mov   drifting            in 5.7 d   │
│ ▌ ────────────────────────────────────────────────────────────────────────────────────── │
│ ▌ OVER-ORDERING            expiry loss     1.665 L   ▐╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱▐ 100%      │
│ ▌                          measurement     0     L                             0%        │
│ ▌ 100% of the loss is stock that expired, not consumption the recipe missed. Order        │
│ ▌ less: shorten the cover window or cut the pack count. Tuning waste_factor here          │
│ ▌ would hide a purchasing problem inside the recipe and make every cost wrong too.        │
│ ▌ The count also found 1.665 L MORE than the ledger expected — a delivery nobody          │
│ ▌ entered, or a miscount. Chase separately; not evidence about recipe or order size.      │
│ ▌ waste_factor 0.10 → suggested 0.083 · stays manual until 2 counts under 10%             │
│ ──────────────────────────────────────────────────────────────────────────────────────── │
│ 16oz paper cup       118.340 ┃ counted 159.750 on 19 Sep +23.5%      —        30 Sep      │
│  EACH · AMBIENT         EACH ┃ + ledger −41.410, 42 mov   excluded            in 7.5 d   │
│ ▌ Auto-ordering refused and revoked: drift +23.46% exceeds 15%. Theoretical stock is      │
│ ▌ not trustworthy, so every quantity sized from it is wrong from now on.                  │
│ ▌ RECIPE OR WASTE FACTOR   measurement    37.472 EACH ▐████████████████████▐ 100%        │
│ ▌                          expiry loss     0     EACH                          0%        │
│ ──────────────────────────────────────────────────────────────────────────────────────── │
│ Napkin               295.845 ┃ counted 428.155 on 19 Sep +13.6%      —        28 Sep      │
│  EACH · AMBIENT         EACH ┃ + ledger −132.310, 71 mov  drifting             in 5.6 d   │
│ ▌ tuning band · stays manual · waste_factor 0.01 → suggested …                            │
│ ──────────────────────────────────────────────────────────────────────────────────────── │
│ Coffee beans          2.287  ┃ counted 3.115 on 19 Sep     +3.1%   354 days    24 Sep     │
│  KG · AMBIENT             KG ┃ + ledger −0.828, 61 mov     trusted            in 0.9 d    │
│ ──────────────────────────────────────────────────────────────────────────────────────── │
│ Chocolate powder      5.000  ┃ counted 5.000 on 19 Sep     never    479 days   withheld   │
│  KG · AMBIENT             KG ┃ + ledger 0.000, 0 mov       counted             ↓          │
│   no consumption recorded in the 28 days to 22 Sep: there is no forecast to give, and     │
│   a zero here would mean "nothing known", not "nothing needed"                            │
│ ──────────────────────────────────────────────────────────────────────────────────────── │
│ …                                                                                        │
│                                                                                          │
│ ── Open batches ──────────────────────────────────────────────────────────────────────── │
│ Whole milk      #25   48.123 L    received 21 Sep   expires 28 Sep   4 days   £33.54     │
│ Coffee beans    #7     2.287 KG   received 11 Aug   expires 12 Sep 2027  354 d  £43.44   │
│ …                                                                                        │
│ Short-dated (3 days or fewer): none today.                                               │
│ Milk's usable window is 5 days — 7 days' shelf life less a 2-day transit buffer. All 113  │
│ shelf lives are ESTIMATE defaults; a wrong one either wastes stock or causes a stockout.  │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

Notes:

- The `┃` is `rule-strong`, full row height. It is the theoretical/counted divider and it
  is the most prominent vertical line on the screen. That is on purpose.
- **Both halves of the drift split are always printed, even at zero.** `0 L` of
  measurement error next to `1.665 L` of expiry loss is the finding. Printing only the
  non-zero half destroys the comparison and points the owner at the wrong fix half the
  time, which is exactly the failure spec §10.2 names.
- The split bar: `████` solid = measurement, `╱╱╱╱` ticked = expiry. Same oxblood, two
  textures. Readable in greyscale, readable to a deuteranope.
- A **withheld run-out prints the reason in the run-out column's place** — the word
  `withheld` where the date would be, and the API's own sentence directly under it. The
  number is not in the payload and there is nowhere it could be shown even if someone
  wanted to.
- `trusted` is muted text, not a green pill. `excluded` is `flag` at 600 with the
  oxblood row rule. There is no green anywhere on this screen.
- Expiry loss as a running cost is the one figure the UI derives: `expiry_qty ×
  unit_cost` summed, exactly, in integer arithmetic, with any ingredient whose cost is
  missing excluded and the exclusion count printed next to the total.

### Mobile (375px)

The table cannot survive, so it transposes — but mechanism (1) survives, because *below*
is a position and the equation still names the counted figure.

```
┌───────────────────────────────┐
│ Stock · tier A                │
│ Every figure here is          │
│ theoretical. A count is the   │
│ only truth.                   │
│ 11 ingredients · 6 auto · 1   │
│ refused · expiry loss £1.16   │
│ ───────────────────────────── │
│ Whole milk       L · CHILLED  │
│                               │
│    48.123 L                   │  ← theoretical: 20px mono 500
│    theoretical, 23 Sep 23:59  │
│    ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈   │  ← the divider becomes horizontal
│    counted 12.037 on 19 Sep   │  ← counted: 12px mono muted
│    + ledger 36.086 (105 mov)  │
│                               │
│    drift −13.8%   drifting    │
│    runs out 28 Sep, in 5.7 d  │
│    soonest expiry 4 days      │
│ ▌ OVER-ORDERING               │
│ ▌ expiry loss   1.665 L  100% │
│ ▌ ▐╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱▐   │
│ ▌ measurement   0     L    0% │
│ ▌ Order less: shorten the     │
│ ▌ cover window or cut the     │
│ ▌ pack count. …               │
│ ───────────────────────────── │
│ Chocolate powder  KG ·AMBIENT │
│     5.000 KG                  │
│     theoretical, 23 Sep       │
│    ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈   │
│     counted 5.000 on 19 Sep   │
│     + ledger 0.000 (0 mov)    │
│                               │
│     runs out — withheld       │
│     no consumption recorded   │
│     in the 28 days to 22 Sep: │
│     there is no forecast to   │
│     give, and a zero would    │
│     mean "nothing known"      │
└───────────────────────────────┘
```

---

## 6. Why this is Sasha's Corner and not a dashboard template

- **The menu is three axes and a size ladder, not 314 recipes** — one Flavoured Latte
  template drives nine sellable items, and the left pane leads with that count because
  it is the single fact that explains the whole product. A template app for a
  hypothetical restaurant would lead with the recipe list.
- **Milk's five-day window is the spine of the thing.** Seven days less a two-day
  transit buffer, on the one ingredient that moves 8.4 L a day. Shelf life is printed
  next to the on-hand figure on every perishable row rather than hidden in a batch
  drawer, because on this site the shelf life *is* the constraint on the order.
- **Seasonality is a first-class state, not an error.** Pistachio Latte is Spring-only
  and is out of season on 23 September: three of nine items unavailable today, said in
  prose, uncoloured, with the recipes still resolving. Bubble tea and seasonal drinks
  mean a third of a template can be legitimately unsellable in any given week, and a
  design that treats that as a fault would cry wolf all autumn.
- **Estimates are the ambient condition of a small café's books.** 42 of 113 prices and
  all 113 shelf lives are estimates. The design therefore spends no colour on them and
  keeps the flag for the seven or eight things a week that genuinely need a decision.
- **It is a quiet room.** Cool Dundee daylight, hairlines, no tiles, nothing blinking,
  one inverted surface in the entire app and it only appears when you are about to change
  a recipe. This is read on a laptop over a coffee, twice a week.

---

## 7. Self-review against spec §11's avoid-list — what I changed

| Avoid-list item | What my first plan had | What I changed it to |
|---|---|---|
| Four-rounded-box KPI grid, big number, tiny green arrow | A four-tile summary row on stock: *Ingredients / Auto-order enabled / Forced manual / Expiring value*, each in a rounded box. | **Deleted.** It is now one run-in sentence with the figures inline in mono: `11 ingredients · 6 earn auto-ordering · 1 refused · nothing short-dated · expiry loss £1.16`. No boxes, no arrows, no deltas-since-yesterday, because nothing on this screen changes fast enough for a delta to mean anything. |
| Cream ground, high-contrast serif, terracotta | `#FBF7F0` paper, a display serif masthead, terracotta `#C1622E` accent. | **Replaced wholesale.** Cool grey-blue paper `#F1F2F4`, no serif anywhere, oxblood `#96262B` reserved for crossed thresholds. |
| Every section in an identical rounded card with the same soft shadow | Composition's three panes and stock's summary/table/batches were each a white rounded card with a shadow. | **All card chrome removed.** Sections are separated by hairlines and generous gutters on the paper ground. Nothing in the app is elevated at all (see §8.2); the commit gate is distinguished by inversion instead, so that treatment carries the meaning *a decision is required*. Radius is 2px at most anywhere. |
| Tracked-out uppercase eyebrow labels | `COMPONENTS`, `CONSEQUENCES`, `DRIFT ATTRIBUTION` as 10px letterspaced caps. | **Removed.** Headings are sentence case at body size in Newsreader 600; weight and the serif's optical axis do the work. The one surviving uppercase is the API's own role vocabulary (`COFFEE`, `MILK`, `PACKAGING`) and the attribution `headline`, which are data, not chrome. |
| Green-good / red-bad as the only encoding | `trusted` green, `drifting` amber, `excluded` red; expiry loss red and measurement amber. | **There is now no green and no amber in the palette at all.** One oxblood, one meaning. Trust status is carried by text and weight; the measurement/expiry distinction is carried by bar texture and the two different headline verbs, so it reads in greyscale. |
| Sparklines as decoration | A 40px drift sparkline in every stock row. | **Removed from the row.** The drift history (8 dated observations with the waste factor and expired quantity at each) is genuinely informative, so it moves to the ingredient detail view as a dated column chart **with the ±10% tuning band and ±15% refusal line drawn as labelled rules** — so the shape answers a question (which counts crossed which gate) instead of decorating a cell. |

Two further changes from the same review, not prompted by a specific bullet:

- My first cost column showed `77.71p` and stopped. That is a silent rounding of an exact
  decimal, and nine of them will not sum to the printed total. Changed to the **exact
  tail** (§1, R3) in the two places exactness decides something.
- My first drift panel rendered only the dominant cause. Changed to **always print both
  halves, including the zero one**, because the zero is half the evidence.

---

## 8. Second review, against the `impeccable:frontend-design` guidance

Spec §11's process points at a design skill; loading it after pass one surfaced three
places where my plan above was the safe choice rather than the right one. All three are
changed, and the sections above are written as amended.

**8.1 The type pairing. Inter is out; there is no sans in the app at all.**
Inter is named as an overused default, and on reflection it was doing nothing that
justified it. The pairing is now **Newsreader** (variable serif, `opsz` axis — editorial,
made for screen reading, excellent at 14px) for every word, and **DM Mono** (300/400/500)
for every figure, every column label and every control. Three consequences:

- The concept sharpens into something nameable: **a printed ledger page for a coffee
  shop.** Serif prose, mono figures, hairline rules, cool paper. That is a position, not
  a template.
- The mono/sans channel of R1 becomes a mono/**serif** channel, which is a wider gap than
  mono/sans and therefore reads faster.
- The guidance also warns against monospace as shorthand for "technical". I am keeping it
  as a deliberate exception, for the reason in §3: in a product whose primary reading
  affordance is column alignment, a monospace is the only family that cannot silently
  stop being tabular. It is load-bearing here, not atmosphere — and pairing it with a
  serif rather than a sans is what stops it reading as a terminal.

Newsreader on cool grey is not the avoid-list's *cream + high-contrast serif +
terracotta*: the ground is blue-grey, the serif is low-contrast and set at text sizes,
and there is no terracotta in the palette.

**8.2 The impact preview is no longer a modal.** Modals are called lazy and in this case
they are also weaker. The gate is now an **inline takeover**: it replaces the component
grid and the consequences pane in place, so the thing you are about to change is the only
thing on screen, with no backdrop to click away and no scroll-lock to get wrong at 375px.
It is also **the only inverted surface in the application** — ink ground, paper text —
which is unmissable, cannot be mistaken for content, and needs no shadow. Elevation is
therefore deleted from the system entirely; *inversion* carries the meaning "stop and
read this before you commit". This required one palette addition: `flag-light`
`#E8A29C`, the oxblood readable on ink (8.4:1), used only inside the gate.

**8.3 The oxblood left border is gone.** A thick coloured border on one side is called
out as a lazy accent, correctly. Replaced with a **reserved left gutter for marginalia**:
a fixed column down the whole layout in which flags, attribution headlines and
annotations *hang*, the way a marginal note hangs in a printed book. Nothing else may
occupy it. So `OVER-ORDERING` sits out in the margin beside the milk row, aligned with
its first line, and the attribution sits on `flag-wash` with a hairline — no bar, no
badge, no pill. On mobile the gutter collapses and the marginal note becomes the line
above, which is the same reading order.

Also taken from the guidance: a fluid type and space scale on `clamp()` rather than fixed
steps; container queries so the three panes respond to their own width rather than the
viewport's; asymmetric pane widths (the grid is the widest thing on the page by a long
way, because it is where the work happens); and one orchestrated 160ms staggered reveal
on first paint, dropped entirely under `prefers-reduced-motion`.

Not taken: OKLCH colour functions. Spec §11 asks for named hex values with roles, so the
tokens stay hex; the neutrals are all tinted toward the ink's blue so the guidance's point
about untinted greys is still honoured.

---

## 9. What changed during the build, and why

Pass two found seven things the wireframes could not. Recorded here so this document
describes what shipped rather than what was hoped for.

**9.1 The decimal column is structural, not coincidental.** The consequences pane first
right-aligned the whole cost figure, head and exact tail together. That *looked* aligned
only because all three sizes happened to have four-digit tails; the moment one had five
the column would have quietly stopped lining up, which is the exact failure §11 names —
data that does not align in columns cannot be scanned, and an alignment that holds by
luck is worse than none because it hides its own fragility. The cost row is now two grid
cells: the head right-aligns in a fixed column so the decimal points stack, and the tail
hangs left into its own. Verified in the browser: all 124 figures on the wide layout and
137 on the narrow resolve to DM Mono with `lining-nums tabular-nums`, so the alignment
rests on the family, not on a feature flag.

**9.2 The exact tail came off the rates.** Per-minute margin was rendering as
`201.642635p–257.642635p` and wrapping to two lines. The tail exists because a rounded
cost gets summed and the rounding would hide a discrepancy (§1 R3) — a *rate* is neither
summed nor reconciled, so the digits were noise pretending to be rigour. Per-minute now
reads `£2.02–£2.58`. The tail is kept where it earns its place: unit costs, resolved item
costs and the gate's cost deltas.

**9.3 The flagged rows lost their tint.** Row-level `flag-wash` plus the attribution
panel's own ground turned the top third of the stock screen pink with only three of
eleven rows flagged — and the real data has far more. That is the traffic light coming
in through the back door. The row tint is gone; the gutter note, the status word and the
attribution panel carry it, which is three channels already.

**9.4 The split bars were slabs.** They took a full `1fr` of the row, so a 100/0 split
read as a giant red bar rather than as a proportion. Capped to a measure-width column,
which is all two halves need to be compared.

**9.5 A fourth trust state, and the right words for it.** `drift.trust_status` is
`trusted | drifting | excluded` and **null** when no drift observation exists — the
majority state on the seeded data. Null was rendering in the same muted grey as
`trusted`, which reads as a clean bill of health when it is an absence of evidence; the
two want opposite responses. It now renders in ink (muted = settled, ink = unfinished,
flag = threshold crossed), the drift cell says `no reading` rather than an em-rule, the
gutter says `no evidence either way`, and the summary sentence counts them.

The wording needed correcting too. These ingredients **have been counted** — Chocolate
powder carries `counted 5 on 25 Jul` in the basis column of the same row. What they lack
is a drift *observation*. Calling them "never counted" would have contradicted a figure
two cells to the left, which is precisely the confusion this screen exists to prevent.
They are `not yet judged`.

**9.6 Quantities in the gate are printed exactly as sent.** `trimQty` turned the API's
`0.20` into `0.2`, so a before/after pair read `0.18 → 0.2` with its decimals out of
step. In a comparison the API's own string is what should appear. Sizes there also sort
by the size ladder now, not alphabetically — `Object.keys` order put M before S.

**9.7 The narrow layout omits unused sizes rather than dotting them.** On the wide grid a
`·` marks a slot not used at that size, distinct from a typed `0`. Transposed to one row
per size on a phone, a `·` row would be a line of nothing, so those sizes are simply
absent — and the explanatory sentence is rewritten for that layout instead of describing
a glyph that is not on screen.

---

## 10. Deliberate omissions

- **No dark mode.** An internal tool opened on a laptop in daylight a few times a week;
  a second palette is a second set of contrast bugs for no reader. Stated rather than
  forgotten.
- **No animation** beyond one 160ms staggered reveal on first paint and a 120ms opacity
  on the gate, both dropped entirely under `prefers-reduced-motion`.
- **No charts on the two screens built first.** The margin scatter belongs to the margin
  screen, which is later in the build order.
```
