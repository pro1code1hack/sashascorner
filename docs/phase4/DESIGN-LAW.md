# Design law — read before touching any screen

The owner rejected the frontend twice. The second time: *"the whole interface and UI
is too vibecoded and the goal is to match best world designs… the design I currently
observe is shocking and completely wrong, needs to be done from scratch."*

References, in the owner's order of emphasis: **finsepa.com** (named three times),
**swetrix.com**, ClickUp.

## Why the last attempt failed

The palette was **invented** — warm browns, an amber accent, radii and spacing chosen
by feel. Invented systems read as arbitrary because they are. The fix is not more
taste; it is to *borrow a system that someone has already made coherent* and apply it
without deviation.

## The system, measured (not guessed) from finsepa.com

It is the **Tailwind zinc ramp on zinc-950**, one blue accent, vivid green/red.
Already encoded in `web/src/styles.css` — use the tokens, never raw hex:

| role | token | value |
|---|---|---|
| page | `bg-canvas` | `#09090b` zinc-950 |
| card | `bg-surface` | `#18181b` zinc-900 |
| raised / active / chip | `bg-raised` | `#27272a` zinc-800 |
| hairline | `border-line` | `#27272a` |
| divider | `border-line-2` | `#3f3f46` |
| figures, headings | `text-ink` | `#fafafa` |
| body | `text-ink-2` | `#d4d4d8` |
| labels | `text-ink-3` / `text-ink-4` | `#a1a1aa` / `#71717a` |
| accent | `text-brand` / `bg-brand` | blue-500 `#3b82f6` |
| good / bad / needs-a-human | `ok` / `bad` / `warn` | green-500 / red-500 / amber-500 |

### The type scale, and a mistake worth not repeating

| | reference | here |
|---|---|---|
| workhorse size | **12px** (1,459 of 3,913 elements) | 12px |
| steps | 14 / 16 / 18 / 20 | 11 / 14 / 16 / 20 |
| weights | 400 / 500 / 600 / 700 | 400 / 500 / 600 |
| base line-height | — | 1.6 (buys back at 12px what the reference gets from its font) |

The first pass used a **14px** base, reasoned from the audience: this is a back-office
opened twice a week, not a terminal watched all day. That reasoning was not wrong, but
it was **not mine to apply** — the brief named the reference twice and said to follow
it. Substituting taste for an explicit instruction is how the first two attempts got
rejected, and doing it again in a smaller way is still doing it.

Corrected to 12px. It is one token: raise `font-size` on `body` in `styles.css` and the
whole scale steps back up together.

A measurement caution recorded because it nearly produced a wrong answer: 12px looked
like it might be inflated by marketing small print, so the count was re-run excluding
footer chrome. There is no `<footer>` on that page and the ratio was unchanged. Check
the objection before acting on it.

Radii: `rounded-card` (12px), `rounded-card-lg` (16px), `rounded-control` (8px),
`rounded-chip` (6px), `rounded-pill`.

## The five signature patterns. Use them; do not invent alternatives.

> **On sparklines specifically.** `<Sparkline>` exists and is a signature pattern of
> the reference, but only ONE endpoint carries a time series: `/api/channels` now
> returns `daily[]` per channel, and `/api/stock/{id}` has drift history (already a
> dated column chart). `/api/today`, `/api/stock` and `/api/orders/draft` answer for a
> single instant. A shape drawn through one point is decoration, and a series that
> dips to the floor on a day nobody reported says "trade collapsed" — which is a lie.
> Use it where a real series exists; leave the card plain everywhere else.

1. **Stat cards, not a stat strip.** Each figure is its own bordered card
   (`<StatCard>`): small grey label, large figure, a tinted `<DeltaPill>` on the same
   line, and a `<Sparkline>` beneath where a trend exists. A row of four bordered
   cards is the reference's opening move; one flat panel with four columns is what we
   had and it reads as a table someone forgot to finish.
2. **Two tab styles that mean different things.** `<TabsUnderline>` switches *what a
   section is about* (finsepa's Stocks/Crypto/Indices; swetrix's Country/Region/City
   inside a card header). `<Tabs>` (filled pill) *filters a list that stays the same
   thing*. Never use one for both.
3. **Tables**: small grey **sentence-case** headers, generous rows, hairline dividers,
   first column a `<Cell top sub>` (name over a quieter second line), figures
   right-aligned, a trend sparkline in the last column where one exists.
4. **`<Panel tone>`** for any backend-authored paragraph that must not be skimmed
   past — a coloured left rule and a tinted wash.
5. **`<SectionLabel>`** between card groups.

## Banned, explicitly

- **UPPERCASE TRACKED-OUT LABELS.** Both the reference and the original spec reject
  them; they cost legibility at 11px and read as a dashboard cliché. Sentence case.
- Inventing a colour, radius or shadow outside the tokens.
- Colour as decoration. `ok`/`warn`/`bad` mark states the **backend has classified**.
  An estimate is marked by the dotted `EST` rule, never by colour — 280 of 298 costed
  items are estimates, so it is the ambient condition, not an alarm.
- Emoji as UI iconography.

## Data law (unchanged, and it outranks the design)

- Every number through `<Fig>`. Money is integer pence **or an exact decimal string of
  pence** — check `lib/types.ts`, several fields are strings like `"42.291667"`.
  **`money()` throws on a fractional number.** Use `lib/dec.ts`; never float maths.
- A missing value is `null`, never `0` → `<Fig missing="why" />`, excluded from
  aggregates with the exclusion stated.
- A low-confidence forecast has **no number**: render the reason *in place of* it.
- Anything wide scrolls inside `<ScrollX>`. The page must never scroll sideways — a
  figure sliced mid-digit still reads as a number. Verified at 1400/1024/768/375px.
- Placeholder supplier terms (6 of 8) must be visible wherever a date or window
  derived from them is shown.
- English only. Russian belongs to the Telegram bot.

## Rules of engagement

- `npx tsc --noEmit` clean when you finish. Do **not** run `npm run build` (it races
  other agents). **Write NO TESTS** — the owner has forbidden them repeatedly.
- Do not edit `styles.css`, `ui.tsx`, `shell.tsx` or `lib/**`. Need a primitive?
  Say so in your report and work around it locally.
- Keep all existing behaviour and every honest caveat. This is a **re-skin, not a
  rewrite**: the data handling in these screens was reviewed and is correct.
