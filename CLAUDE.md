# Café Ops — Stock, Composition & Auto-Ordering

**Single source of truth. Backend and frontend. v2.**

> **Status: v2 Phase 0 complete.** v1's Phase 1 (Lightspeed, composition, stock/drift,
> forecast/ordering) is merged; the v2 extension — batches, shelf life, seasons,
> multi-supplier sourcing, prep time, channels, agent log — has landed on top.
>
> **Read [`ARCHITECTURE.md`](ARCHITECTURE.md) too.** It records every decision that
> differs from this brief and why. Several are load-bearing, in particular §1
> (**no test suite**, owner's instruction, which supersedes §14 below) and §8E (a
> silent `Qty` comparison bug worth understanding before you write repository code).
>
> Agent briefs: [`docs/phase1/`](docs/phase1/).
>
> **This file is a condensed rendering of the brief, and condensing has already lost
> normative content once** — the API shapes in §10.9 were dropped and then cited to an
> agent as if present. When something here reads as a summary, check `ARCHITECTURE.md`
> before assuming the detail never existed.


> **Status markers in this file went stale once and misled an agent** (four features
> marked NOT YET BUILT were fully implemented and wired). Verified 2026-09-23 against
> the running system: backend is ~40k lines across domain/services/integrations/bot/
> jobs/api/agent/seed/db, 35 CLI commands, 13 API routes. Before trusting a "not
> built" here, grep for it.

---

## 1. Context

Single-site independent café in Dundee. Opened November 2025. ~£3–5k monthly revenue,
25–40 transactions a day, one or two staff plus a manager. POS is **Lightspeed
Restaurant K-Series**. Also sells through Deliveroo and Just Eat.

Stock lives in one person's head and gets topped up by walking to a shop most
mornings. Costs live in a hand-maintained spreadsheet.

The system replaces both: POS sales → recipe resolution → theoretical stock depletion
→ par levels constrained by shelf life → draft purchase orders **per supplier** in
Telegram for one-tap confirmation. Plus a back-office web app where composition is
configured once and every cost recalculates from it.

### Goals

1. Nobody has to remember what is running out.
2. Correctly-sized draft orders are ready before each delivery day, split across the
   right suppliers.
3. Theoretical stock stays within 10% of physical counts, and the system says so when
   it does not.
4. Nothing is ordered in a quantity that will spoil before it is used.
5. Changing one ingredient price or recipe quantity updates every affected item's cost
   instantly.

### Non-goals in v1

- Replacing Lightspeed's POS functions
- Payment or order submission without human confirmation
- User management beyond a single shared password
- Multi-site anything
- **A load balancer.** This runs on one box. See §3.

---

## 2. The real shape of the menu

| Fact | Spec | Measured |
|---|---|---|
| Ingredients | 114 | 113 |
| Base menu items | 175 | 174 |
| Item × size rows | 318 | 314 |
| Recipe lines | ~1,600 | 1,595 (1,577 after removing a duplicated recipe) |
| Sizes | S, M, XL, One | confirmed |

Ingredient frequency (measured, matches spec exactly): Napkin 265, Whole milk 230,
Coffee beans 121, 12oz cup/lid 102, 16oz cup/lid 101, 8oz cup/lid 62.

### The conclusion that drives the design

**318 rows is not 318 recipes. It is roughly 20 patterns, multiplied out.**

Pattern detection found **27 proposed templates + 43 one-off groups**, confirming it.
Do not port the flat structure. Model the pattern, generate the leaves.

The workbook also carries a seasonal dimension. That is a first-class field (§4.3).

---

## 3. Stack and deployment

Python 3.12 / `uv` · SQLAlchemy 2.0 declarative, fully typed · Alembic (every schema
change) · SQLite WAL · Pydantic v2 · httpx async · aiogram 3 · APScheduler · FastAPI ·
Anthropic SDK for the bounded agent (§9) · ruff + mypy strict on `domain/` and
`services/` · React 18 + TS, Vite, Tailwind, TanStack Query/Table · visx or hand-rolled SVG.

**Tests: not used.** See `ARCHITECTURE.md` §1 — owner's instruction, three times,
including when asked directly. §14 below is superseded.

### Deployment

One VM. Caddy or nginx terminating TLS, FastAPI behind it, bot and scheduler as
separate systemd units, SQLite on local disk, nightly off-box backup.

**No load balancer.** 40 transactions a day, two users, one SQLite file with a single
writer. A second app instance would contend on the same file and make things worse.
Reverse proxy yes. Revisit only if this becomes multi-site.

### On async

**Do not make everything async.** SQLite has one writer.

- Async only at I/O edges: Lightspeed client, aiogram handlers, agent calls.
- Sync everywhere else, including all DB access via the sync `Session`.
- Handlers reach DB work through `asyncio.to_thread`.
- Pragmas: `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`,
  `synchronous=NORMAL`. (Alembic runs with FK enforcement OFF — see `ARCHITECTURE.md`
  §8F.7 for why it must.)
- No SQLite-specific SQL in application code.

---

## 4. Domain model

**All money integer pence. Quantities `Decimal`. Timestamps UTC, tz-aware.**
29 tables in `cafeops/db/models/`.

> **Before writing any query against a quantity column, read `ARCHITECTURE.md` §8E.**
> `Qty` is stored as a scaled integer on SQLite for a reason.

### 4.1 Ingredients, batches, shelf life

`ingredient` (unit `L|KG|ML|G|EACH`, tier, waste_factor, **storage
`AMBIENT|CHILLED|FROZEN`**, **shelf_life_days**, **open_life_days**,
**transit_buffer_days**, cost cache + `current_cost_source`), `ingredient_price`
(effective-dated, **supplier_id**, `source INVOICE|ESTIMATE|SUPPLIER_FEED`),
**`stock_batch`** (qty_received/remaining, received_at, expires_at, opened_at,
unit_cost_pence, expired_at), `stock_count`, `stock_movement` (append-only, **batch_id**,
type now includes **`EXPIRED`**), `par_level`, `drift_observation`
(**expired_qty_in_window**).

Shelf life **caps order size** (§5.4, invariant 4). Milk with a 7-day life must never
be ordered on a 9-day cover window however good the forecast.

Depletion is FIFO across batches **by effective expiry**, not receipt date. A batch
reaching expiry with stock left produces an `EXPIRED` movement and an alert — the
honest waste figure nobody currently has.

### 4.2 Composition

`drink_template` (+ **prep_seconds_by_size**), `size_profile`, `template_component`
(role, nullable ingredient, `qty_by_size` JSON of **strings**, is_substitutable,
effective dating), `variant_axis`, `variant_option` (+ **season_id**), `modifier`
(targets a **role**), `menu_item` (+ **prep_seconds**, **season_id**),
`manual_recipe_line`, `menu_item_cost` (+ **labour_cost_pence**, **prep_seconds**,
**loaded_hourly_rate_pence**), `legacy_staged_recipe`.

### 4.3 Seasons

`season` (name, starts_on, ends_on, is_recurring_annually). Two mandatory consequences:

1. **Forecasting excludes out-of-season history.** Pumpkin in October must not inflate
   the July baseline, nor read as "unused for 9 months, drop it".
2. **Ordering respects the window.** Cap at remaining season days; warn when a season
   ends with stock on hand.

Recurring seasons that wrap the new year are handled — see `ARCHITECTURE.md` §8F.6.

### 4.4 Suppliers and multi-sourcing

Eight suppliers: Cakesmiths, Brakes, Booker, Cups Direct, Monolith (portals/email),
Tesco and Amazon (manual), Nataly (custom, **unspecified — spec §15 q5**).

`supplier` (+ **cutoff_time**, **delivery_fee_pence**,
**free_delivery_threshold_pence**, **terms_are_placeholders**), `supplier_product`
(+ **moq_packs**, **last_seen_price_at**).

**Only Tesco and Amazon have real terms.** The other six carry
`terms_are_placeholders = True`. Every order built from them must say so.

One ordering run produces **N draft orders**, one per supplier. Sourcing prefers the
preferred product unless an alternate is materially cheaper per unit **and** the switch
does not push another supplier below its minimum. Surface that trade-off; never resolve
it silently.

Every Tesco routing is logged in `tesco_routing` with its retail premium. The
accumulated log is the argument for fixing the ordering cadence.

### 4.5 Sales and orders

`sale` (+ `applied_modifiers` JSON, voided, is_refund, expanded_at), `purchase_order`
(+ **routing_reason**, **delivery_fee_pence**), `po_line` (+ **received_expires_at**,
**cap_reason**), `checklist_response`.

### 4.6 Channel marketing metrics

`channel_metric`, `channel_item_metric`. Deliveroo and Just Eat are an ad platform as
well as a channel: impressions, ranking, spend. Worth showing — ROAS, contribution
after commission **and** ad spend, and items that rank well but convert badly.

**Access reality:** partner APIs are gated to certified POS integrators. Owner has
portal logins and can export manually, so build a `ChannelSource` protocol with a CSV
implementation first and a browser-agent one second. Every row records its `source`.

### 4.7 Tiers

A: calculated, auto-order eligible. B: calculated, always reviewed. C: checklist only.

**Tier A membership is earned, not assigned** (§5.2). Roster is 12 items; **oat milk
starts at B** — see `ARCHITECTURE.md` §2.1 and the K-Series modifier finding.

---

## 5. Algorithms

### 5.1 Theoretical on-hand — IMPLEMENTED

```
on_hand(ingredient, at) = latest count before `at` + Σ movements in (that count, at]
```

Sales become `SALE` movements via `resolve_recipe(..., at=sale.sold_at)`, depleting
batches FIFO by expiry, with `waste_factor` applied.

### 5.2 Drift — IMPLEMENTED

```
drift_pct = (theoretical - counted) / max(counted, epsilon) * 100
```

| Drift, last 2 counts | Action |
|---|---|
| < 10% | eligible for `auto_order_enabled = True` |
| 10–15% | propose a `waste_factor` adjustment, stay manual |
| > 15% | force off, alert |

**No ingredient enters auto-ordering without two consecutive counts under 10%.**
Enforced structurally: `set_auto_order(..., True)` raises.

**v2 adds attribution.** If `EXPIRED` movements explain most of the gap, the problem is
over-ordering, not a bad recipe. The two fixes are opposite, so report which it is.

### 5.3 Forecast — IMPLEMENTED

```
base_daily    = EWMA(daily consumption, trailing 28 days, alpha=0.3)
dow_factor[d] = mean(weekday d) / mean(all), trailing 8 weeks, clamp [0.5, 2.0]
forecast(day) = base_daily * dow_factor[weekday(day)]
```

**Correction applied:** taken literally this counts the weekday effect twice, swinging
`base_daily` by 32.6% depending on which weekday the window ends. The implementation
deseasonalises first. See `ARCHITECTURE.md` §8C.

**Seasonal items** use the same calendar window from the previous season scaled by
year-on-year growth. With no prior season, flat-rate from the first two weeks and mark
low-confidence. **IMPLEMENTED** — `domain/forecast.py:seasonal_forecast`, called from
`services/build_order.py:242`.

Under 14 days of history: flat mean, flagged. The API returns the flag; the UI renders
it **in place of** the number.

### 5.4 Order sizing, shelf-life constrained

```
cover_days      = lead_time_days + days_to_next_delivery_after(target) + safety_days
effective_cover = min(cover_days,
                      shelf_life_days - transit_buffer,   if perishable
                      days_remaining_in_season,           if seasonal)
need  = Σ forecast over effective_cover - on_hand - qty on open POs
packs = ceil(need / pack_size), clamped into [min_qty, max_qty]
```

When `effective_cover < cover_days`, **say so on the line** ("capped at 4 days — milk
shelf life"). Otherwise the user overrides it and creates the waste the cap prevented.

Below `min_order_pence` or `free_delivery_threshold_pence`, top up with
**non-perishable** tier B items by shortest remaining cover. **Never top up with
perishables** — that is buying waste to save a delivery fee (invariant 5).

The shelf-life and season caps are **IMPLEMENTED** (`domain/ordering.py`,
`CapKind`). A live draft today produces 2 shelf-life caps, 1 out-of-season skip and
2 below-par-floor skips, each with its reason.

### 5.5 Supplier split — IMPLEMENTED

One order per supplier with its own terms; re-source or defer a group short of its
minimum; a Tesco `MANUAL` order only for what cannot wait. Output is a set of orders
each with a one-line rationale, not one basket. Per-supplier grouping works; sourcing
choice between alternates is **implemented** in `domain/sourcing.py` (677 lines); a
live draft reports 4 `sourcing_choices`, including oat milk switched to Tesco at 24%
cheaper with the forgone saving stated.

### 5.6 Cost, labour and true margin — IMPLEMENTED

```
labour_cost       = prep_seconds / 3600 * loaded_hourly_rate_pence   (£14.50/hr)
true_margin       = price - ingredient_cost - labour_cost
margin_per_minute = (price - ingredient_cost) / (prep_seconds / 60)
```

`margin_per_minute` reorders the menu against plain margin % — verified, a 85%/3min
drink loses to a 70%/40s one. Cost changes cascade into `menu_item_cost`. Every
composition edit produces an **impact preview** before commit.

### 5.7 Recipe resolution — IMPLEMENTED

Pure `resolve_recipe(item, modifiers, at)`. Components valid at `at` for the item's
size; variant options fill role-matched slots; modifiers in `SUBSTITUTE → SCALE → ADD`;
`SUBSTITUTE` into a non-substitutable slot **raises**; `waste_factor` applies to
depletion only; `manual_recipe` bypasses.

**Effective dating is mandatory.** Editing closes old rows and opens new ones.

---

## 6. Seeding from the legacy workbook — IMPLEMENTED

`cafeops import-legacy`, three passes: ingredients → `ingredient` + `ingredient_price`
+ shelf-life defaults; flat recipes → `legacy_staged_recipe` + `manual_recipe_line`;
**pattern detection that proposes, never writes.** A human confirms in the UI before
templates exist. 43 one-off groups stay `manual_recipe = True`, which is correct.

Known dirt is surfaced, not fixed: `'card' (£3.00)`, `Syrup Gift Set`, VERIFY rows,
`Strawberry bliss`, a duplicated `Rose Hot Chocolate`, and 42 of 113 prices ESTIMATE.

---

## 7. Backend layout

```
cafeops/
  config.py
  db/{base,types}.py  models/  repositories/protocols.py
  domain/                  PURE: no SQLAlchemy, no I/O, no config import
    types.py               shared dataclasses + enums   [INTEGRATOR-OWNED]
    units.py composition.py stock.py forecast.py ordering.py drift.py tiers.py
    sourcing.py labour.py   (v2, not yet written)
  integrations/lightspeed/  channels/ (v2)  suppliers/
  agent/                    (v2, not yet written) runner.py tools.py policies.py
  services/  bot/  jobs/  api/  seed/  cli.py
migrations/  docs/phase1/  docs/phase4/  web/
```

`domain/types.py` and `db/repositories/protocols.py` are **integrator-owned**. An agent
needing a change raises it.

---

## 8. Architecture

Everything writes through the **services layer**. The bot, the API and the agent are
all clients of it — none touches the database directly. That is what keeps §13's
invariants enforceable in one place.

---

## 9. Where the AI agent belongs

**Deterministic stays deterministic.** Stock maths, par levels, cover windows, order
sizing and costing must be reproducible and identical on every run. An LLM in that path
makes an unauditable system that orders differently on Tuesday than Monday.

The agent gets three jobs, each behind a whitelisted tool interface:

1. **Browser automation where no API exists** — channel reports, supplier portal
   baskets. Output is data or a staged basket, never a submitted payment. Falls back to
   CSV when it breaks, and it will break.
2. **Narration and anomaly explanation** — turning a drift report into a sentence
   somebody acts on. Reads computed numbers, never computes them.
3. **Import assistance** — proposing template groupings and `waste_factor`
   adjustments, always as a proposal a human confirms.

Hard rules: never writes to `stock_movement`, `purchase_order` or composition directly.
Every action logged to `agent_action_log` with inputs, output and tool. Anything that
would spend money stops at a human.

---

## 10. Frontend

Daily interface is the **Telegram bot** (Russian). The web app is where you think about
the business and configure composition; opened a few times a week on a laptop, must
survive 375px. **English only.** Nothing in it is urgent — it should not look like an
operations console with live tiles.

Build order: **composition editor** (three panes, per-size grid incl. prep seconds,
live consequences, mandatory impact preview, apply-from-today only) → **stock**
(theoretical vs counted distinguishable at a glance by type/weight/position, not a
tooltip; open batches, short-dated list, expiry write-offs; drift split into
measurement vs expiry) → orders and suppliers (grouped, with cap reasons and the
panic-buy report) → menu margin (velocity × margin scatter, y-axis toggle to
margin-per-minute; the disagreement is the finding) → channels → today/money/P&L →
import review.

Design: numbers carry it (tabular figures, deliberately set). Resist the traffic
light — colour only for crossed thresholds. Avoid the KPI card grid, cream/serif/
terracotta, identical rounded cards, uppercase eyebrow labels, green-good/red-bad as
the only encoding, decorative sparklines. Estimated or missing costs: shown, flagged,
excluded from aggregates, never zero.

### 10.9 API shapes (from the spec — restored)

These were in the brief and were lost when this file was condensed — and then cited to
an agent as if present. They are the spec's own sketch. **`cafeops/api/` does NOT match
them and deliberately so**: it nests `Cost`, `OnHand` and `Forecast` into types that
carry their own rules, which is what makes invariants 6, 8 and 9 impossible for a view
to drop. `ARCHITECTURE.md` §8H has the full field-by-field mapping and the reasoning.
Build the frontend against `cafeops api-fixtures`, not against this block.

```ts
GET /api/templates/:id
{ id, name, category,
  components: [{ id, role, ingredient, qty_by_size, prep_seconds_by_size,
                 is_substitutable, is_required }],
  axes: [{ id, name, role, options: [{id,name,ingredient_id,price_delta_pence,season_id}] }],
  resolved: [{ size, ingredient_cost_pence, labour_cost_pence,
               margin_pct, margin_per_minute_pence,
               items: [{menu_item_id,name,price_pence}] }] }

POST /api/templates/:id/preview          // never writes
{ changes: [...] } ->
{ affected_item_count, cost_delta_pence_per_item, monthly_cogs_delta_pence,
  worst_margin_after: {item, before, after}, warnings: string[] }

GET /api/stock
{ items: [{ id, name, unit, tier, theoretical_qty, last_count_qty, last_counted_at,
            drift_pct, drift_attribution: {measurement_pct, expiry_pct},
            status: 'trusted'|'drifting'|'excluded',
            projected_runout_date,
            batches: [{id, qty_remaining, expires_at, days_left}] }] }

GET /api/orders/draft
{ orders: [{ supplier, target_delivery_date, total_pence, meets_minimum,
             lines: [{ ingredient, suggested_packs, unit_price_pence,
                       cap_reason: 'shelf_life'|'season'|null,
                       note }],
             rationale: string }],
  tesco_emergency: [{ ingredient, qty, reason, retail_premium_pence }] }

GET /api/channels?days=30
{ channels: [{ channel, orders, gross_pence, commission_pence,
               ad_spend_pence, net_pence, roas,
               items: [{ menu_item_id, name, views, orders, rank_in_category }] }] }
```

### 10.9b The two write endpoints (v2, not in the original spec)

```ts
POST /api/suppliers/:id/confirm      // terms confirmed WITH the supplier
{ lead_time_days, delivery_weekdays[], min_order_pence, delivery_fee_pence,
  cutoff_time, free_delivery_threshold_pence } ->
{ supplier_id, name, was_placeholder, changed: string[], supplier }

POST /api/ingredients/:id/shelf-life  // a shelf life somebody checked
{ shelf_life_days, open_life_days, source: 'supplier'|'packaging' } ->
{ ..., usable_days_after, usable_days_changed_by }
```

All of a supplier's terms go together: the cover window is computed from several
at once, so a partial confirmation would clear the invented-terms warning on an
order that is still partly fiction. `source: 'estimate'` is refused — confirming a
guess as a guess quiets the warning without adding knowledge. `usable_days_changed_by`
is the field worth rendering: shelf life caps order size, so the honest answer to
"what did I just do" is *a single order may now cover three more days of trade*.

### 10.10 Frontend technical notes (from the spec — restored)

- Build against a fixture layer; every screen renders from static JSON.
  (`cafeops api-fixtures --out web/fixtures` writes real responses, not samples.)
- Money as integer pence, formatted at the edge. No float arithmetic.
- Quantities as strings, handled as decimals. `0.1 + 0.2` in a recipe editor is
  unacceptable.
- Auth: single shared password.
- Quality floor: responsive to 375px, visible keyboard focus, reduced motion
  respected, readable contrast.

---

## 11. Commands

```bash
uv run alembic upgrade head
uv run cafeops import-legacy --dry-run | --commit
uv run cafeops seed --demo
uv run cafeops stock --as-of today [--tier A] [--batches]
uv run cafeops drift [--backfill] [--tier A]     # REQUIRED after any reseed
uv run cafeops supplier list | confirm <name> --lead-days N --days 2,5 ...
uv run cafeops shelf-life list | set <name> --days N [--open-days N] --source supplier
#   ^ the operator path for the doctor's two standing warnings. Before these existed
#     the only remedy on offer was editing a seed file (ARCHITECTURE.md 8O).
uv run cafeops count / expand / ingredients / info
uv run cafeops simulate [--weeks 8] [--supplier X] [--cadence-days 7]
uv run cafeops sync --fixtures --from D --to D
uv run cafeops proposals / materialise-template / templates / components
#   ^ proposals prints a stable `id`. Confirm by id: two pairs of proposals share
#     a name and they are different recipes (ARCHITECTURE.md 8Q).
uv run cafeops edit-recipe / cost-rollup / menu-costs / set-price
uv run ruff check . && uv run ruff format --check .
uv run mypy cafeops/domain/ cafeops/services/   # strict, zero type: ignore
```

---

## 12. Build plan

- **v2 Phase 0 — done.** 29-table model, squashed migration, `domain/types.py`,
  protocols, shelf-life defaults, FIFO + expiry, batch rebuild, eight suppliers,
  seasons, labour types, §16 deliverables.
- **v2 Phase 1 — six agents.** A Lightspeed · B Composition + labour · C Stock &
  batches · D Forecast, sourcing, ordering · E Bot & jobs · F Channels & agent.
  E is held until A–D settle, as it consumes their output.
- **Phase 2 —** wire together, resolve contract drift, real legacy import, shelf-life
  confirmation, deploy.
- **Phase 3 — done.** Frontend: seven screens on one design system.
- **Phase 4 — done.** The design was rejected twice and rebuilt from a *measured*
  reference rather than an invented palette (ARCHITECTURE.md 8N, docs/phase4/DESIGN-LAW.md).
  The deployed bundle reads the live API rather than fixtures; verified across
  7 screens x 4 widths with zero horizontal page overflow and no console errors.

---

## 13. Invariants

1. Nothing is ordered without human confirmation. Ever, in v1.
2. Auto-order eligibility is earned through drift history, never set manually.
3. Recipe edits are effective-dated. History is never rewritten.
4. Order quantity never exceeds what will be consumed within shelf life or season.
5. Minimum-order top-ups never use perishables.
6. Every stock figure is labelled theoretical or counted. Never blurred.
7. Waste factor affects stock depletion, never menu cost.
8. Estimated costs stay flagged through every rollup, aggregate and export. A missing
   cost is `None`, never zero.
9. Low-confidence forecasts say so in place of the number, not beside it.
10. The agent never writes to stock, orders or composition directly. It calls a service
    or emits a proposal, and every action is logged.
11. All money in integer pence. A float touching money is a bug — `Qty` raises.
12. The stock ledger is append-only. Corrections are `ADJUSTMENT` movements.
    **One documented exception:** `services/rebuild_batches.py` with `purge=True`
    (its default) deletes `EXPIRED` movements and all batches before replaying the
    ledger. Those rows are *derived* — the expiry sweep computes them from batch
    state — so recomputing them loses nothing recorded. It must never delete a
    `SALE`, `RECEIPT`, `COUNT` or `ADJUSTMENT`, and today only `cafeops seed --demo`
    calls it. A new caller of `rebuild_batches` is a decision, not a refactor.

---

## 14. Testing

**Superseded by owner instruction: no tests are written.** See `ARCHITECTURE.md` §1 for
what that costs and which invariants are consequently unguarded. Invariants 1 and 3
were moved into places that need no test — a `CHECK` constraint and a repository query
respectively — and invariant 2 into a repository that raises.

---

## 15. Questions

Answered: §15.1 K-Series · §15.2 modifiers only on real-time endpoints, so oat milk
stays tier B · §15.3 shelf lives seeded as ESTIMATE · §15.4 supplier terms are
placeholders · §15.6 portal login + manual export · §15.7 £14.50/hr · §15.8 tier A 12
items · §15.9 English dashboard.

**Still open: §15.5 — what does "Nataly (custom)" supply, and through what channel?**
