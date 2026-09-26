# Shared brief — every screen agent reads this first

The owner rejected the first frontend. Her words: *"only 2 tabs, not active and
everything sucks"*. Her references are **finsepa.com** (a dark, card-based financial
dashboard), **ClickUp**, and **swetrix.com**. That overrides the original spec §11
design direction, which banned cards, colour and positive semantics — it produced
something precise that nobody wanted to look at.

## The foundation is already built. Use it; do not invent your own.

- `web/src/styles.css` — dark tokens. Canvas `#12110f`, surface `#1a1816`, raised
  `#221f1c`, brand amber `#e8a33d`, semantic ok/warn/bad/info with `-wash` variants.
- `web/src/components/ui.tsx` — `Card`, `PageHeader`, `Stat`, `StatRow`, `Badge`,
  `Note`, `Fig`, `Table`/`Th`/`Td`, `Button`, `Empty`, `Loading`, `ErrorBox`, `ScrollX`.
- `web/src/components/shell.tsx` — sidebar, already routes all seven screens.
- `web/src/lib/` — `api.ts` (fetch + key), `dec.ts` (decimal maths), `format.ts`,
  `types.ts`.

**Do not edit `styles.css`, `ui.tsx`, `shell.tsx` or `lib/`.** If you need a primitive
that does not exist, say so in your summary and work around it locally. Those files are
shared by five agents working at once.

## Non-negotiables

- **Every number goes through `<Fig>`.** Tabular figures are what make a column
  scannable.
- **Money arrives as integer pence or an exact decimal string of pence.** Format at the
  edge. **No float arithmetic** — use `lib/dec.ts`. `0.1 + 0.2` in this app is a bug.
- **A missing value is `null`, never `0`.** Render `<Fig missing="no price" />`, and
  exclude it from any aggregate with the exclusion stated. 42 of 113 prices are
  estimates, so `is_estimate` is the *ambient* condition, not an edge case.
- **A low-confidence forecast has no number.** The API omits `qty` entirely. Render the
  reason **in place of** the figure, never beside it.
- **Anything wide scrolls inside itself** (`<ScrollX>`). The previous build let a panel
  run off the viewport and truncate its own numbers mid-digit — `67.94670` with the rest
  gone. A cut-off figure is worse than an absent one because it still reads as a number.
- Colour means a **state**, not a mood. `ok/warn/bad` for states the system has
  actually classified; never to decorate.
- Responsive to 375px. Visible focus. `prefers-reduced-motion` respected.
- **English only.** Russian belongs to the Telegram bot.

## Data

Build against `web/fixtures/*.json` — real recorded API responses, `index.json` names
the query behind each. A live API also runs at `/api/*` behind Caddy.

**Do not hand-edit `web/fixtures/`.** Regenerate with
`CAFEOPS_API_PASSWORD=sashascorner-local-dev uv run cafeops api-fixtures --out web/fixtures`
only if you must, and say so.

## Rules

- **Write NO TESTS.** The owner has said so repeatedly. Verify by running
  `npm run dev` and looking at the screen; describe what you actually saw.
- `npx tsc --noEmit` and `npm run build` must both be clean when you finish.
- Stay inside your one screen file plus any `web/src/screens/<name>/` subfolder you
  create. Do not touch other screens.

## The data layer is already wired — do not touch `lib/`

`api.ts` now resolves all seven screens' data, from fixtures by default and from the
live API when `VITE_API_BASE` is set. Call these and nothing else:

```ts
api.today()        // TodaySummary
api.ordersDraft()  // OrdersDraftResponse
api.margin()       // MarginResponse
api.channels()     // ChannelsResponse
api.suppliers()    // Supplier[]
api.health()       // HealthResponse
api.stock(tier) / api.stockDetail(id) / api.templates() / api.templateDetail(id) / api.meta()
```

Every type above is exported from `lib/types.ts` (except `TodaySummary`, from `lib/api.ts`)
and is fully annotated. **Read the type before you read the JSON** — the doc comments
record which fields are load-bearing.

Three that will catch you out:

- `today.draft_order_total_pence`, `draft_order_supplier_count`, `capped_line_count`,
  `emergency_line_count` are **`null` until a draft has been computed today**. `null`
  means *not computed*, not *zero*. Render the difference — "not computed yet" is a
  different sentence from "£0.00".
- `supplier.terms_are_placeholders` is true for **6 of 8 suppliers**. Their lead time,
  delivery days, cutoff, minimum and free-delivery threshold were invented, and every
  cover window computed from them inherits that. It must be visible wherever a date or
  window derived from it is shown — not confined to a footnote.
- `SupplierOrder.skipped[]` is **not an error list**. A shelf-life cap that skipped
  6.084 L of milk is the system working correctly, and the reason string says so at
  length. Present skips as decisions with causes, not as failures.

## Use `prim.tsx` for dense figure work

`components/prim.tsx` adds what `ui.tsx` does not carry: `Exact` (a figure plus its
exact remainder, so no rounding hides), `CostFig` (invariant 8 — a missing cost reads
"no price", never 0, and an estimate is marked with a dotted rule, not colour), `Qty`
(the API's own quantity string, trimmed, unit attached — never parsed to a float), and
the `Sheet`/`Marg`/`Body` gutter layout. Prefer these over re-deriving.
