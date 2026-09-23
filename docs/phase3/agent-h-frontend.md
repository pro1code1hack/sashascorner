# Agent H — Frontend: design pass, composition editor, stock

**Owns:** `web/` (except `web/fixtures/`, which is generated — read it, never hand-edit it)

You are building the screens spec §10 says to build **first and fully**: the
**composition editor** and **stock**. Nothing else until those two are real.

## Read first

1. `CLAUDE.md` §10 (what the app is, build order, the states worth designing),
   §10.9/§10.10 (API shapes and technical notes), §11 (design direction), §13 (invariants).
2. `ARCHITECTURE.md` §8H — **the API does NOT match §10.9's shapes and deliberately so.**
   §8H has the field-by-field mapping. Build against the fixtures, not against §10.9.
3. `docs/phase1/README.md` — the rules that bind every agent here.

## The process spec §11 mandates — follow it in order

**Pass one, NO CODE.** Produce, as a markdown file in `web/DESIGN.md`:
- Palette: 4–6 named hex values with roles.
- Type: one or two families, **with the numeral choice justified**. This product is
  almost entirely small figures (£142, 27 transactions, 46%, 0.18 L, 50 s) and spec §11
  says the treatment of numerals is the main design decision. Tabular figures are not
  optional; data that does not align in columns cannot be scanned.
- ASCII wireframes for the composition editor and stock, **desktop and mobile**.
- Three or four lines on what makes this specific to Sasha's Corner, 23 Commercial
  Street, Dundee — coffee, pastries, bubble tea, seasonal drinks. Not a dashboard
  template.
- Then review your own plan against spec §11's avoid-list and **say what you changed**.

Spec §11's avoid-list, because these read as generated: the four-rounded-box KPI grid
with a big number and a tiny green arrow; cream background with high-contrast serif and
terracotta; every section in an identical rounded card with the same soft shadow;
tracked-out uppercase eyebrow labels; green-good/red-bad as the only encoding;
sparklines as decoration rather than information.

**Colour discipline:** resist the traffic light. Most numbers here are neither good nor
bad, and colouring them all asserts a judgement the data cannot support. Reserve colour
for a **crossed threshold**: drift beyond tolerance, expiry loss, a cash discrepancy,
COGS above target, a margin that went negative after an edit.

**Pass two, build.** React 18 + TypeScript + Vite + Tailwind + TanStack Query/Table.

## The composition editor (spec §10.1) — where the boldness goes

Three panes. Left: templates by category with the item count each drives
("Flavoured Latte — 62 items" explains the whole model at a glance). Centre: component
slots by role with per-size quantities in an editable grid, **plus prep seconds per
size**. Right: live consequences — resolved cost per size, ingredient margin, margin
after labour, **margin per minute**, items affected.

**The impact preview is mandatory before every commit** and must be read and trusted
before a destructive-feeling action:

```
Milk 0.18 L -> 0.20 L (size M)
Affects 21 menu items · Cost per item +£0.014 · COGS, last 30 days +£11.60
Lowest margin after  Pistachio Latte M, 70.9% -> 70.4%
[ Apply from today ]  [ Cancel ]
```

**"Apply from today" is the only option.** There is no retroactive edit, because
effective dating is what keeps history honest. State that once, plainly, in the UI —
not in a tooltip.

## Stock (spec §10.2)

**Theoretical and counted figures must be distinguishable at a glance** — by type,
weight or position, *not* a tooltip and not a legend to memorise. Confusing them is
this product's main failure mode. The API gives you `on_hand.is_theoretical`,
`on_hand.has_count_basis` and `on_hand.basis_label` for exactly this.

Also: open batches with expiry dates, a short-dated list, expiry write-offs as a running
cost, and the drift panel **split into measurement error versus expiry loss** — those
two have opposite fixes, so showing one undifferentiated number tells the owner to do
the wrong thing half the time. `drift.attribution` carries the cause, the headline and
the action. `drift.trust_status` is `trusted | drifting | excluded`, already mapped for
you — do not re-derive it.

## Non-negotiable technical rules

- **Build against `web/fixtures/*.json`** — 19 real responses, `index.json` lists the
  query behind each. Regenerate with `cafeops api-fixtures --out web/fixtures` if needed.
- **Money arrives as integer pence or an exact decimal string of pence.** Format at the
  edge. **No float arithmetic anywhere.** A float touching money is a bug (invariant 11).
- **Quantities arrive as strings and stay strings** until formatted. `0.1 + 0.2` in a
  recipe editor is unacceptable (spec §10.10).
- **A missing cost is `null`, never `0`** (invariant 8). Items with `is_missing` are
  shown, flagged, and **excluded from aggregates**. 42 of 113 prices are ESTIMATE, so
  `is_estimate` is the common case — design for it, do not treat it as an edge.
- **A low-confidence forecast has no number to render** (invariant 9). The API omits
  `qty` entirely. Render the reason **in place of** the figure, never beside it.
- **A shelf-life-capped order line carries `cap_reason` and `cap_kind`** — if the user
  does not see the cap was deliberate they will override it and create the waste it
  prevented (invariant 4).
- Auth: one shared password, sent as `X-API-Key`. No user management.
- Quality floor: responsive to 375px, visible keyboard focus, `prefers-reduced-motion`
  respected, readable contrast. Design for the laptop; survive on the phone.
- **English only.** The Russian is the bot's alone.

## Also

- **Write NO TESTS.** The owner has instructed this repeatedly. Verify by running the
  dev server and describing what you see; a build that compiles is not evidence.
- `npm run build` must succeed and TypeScript must be clean (`tsc --noEmit`). Say the
  exact commands you ran.
- Do not touch anything outside `web/`.
