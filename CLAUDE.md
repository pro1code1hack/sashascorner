# Café Ops — Stock, Composition & Auto-Ordering

**Single source of truth for this project. Backend and frontend.**

> **Phase 0 is complete and committed.** Read [`ARCHITECTURE.md`](ARCHITECTURE.md)
> too — it records every decision that differs from this brief and why. Several are
> load-bearing, including **no test suite** (owner's instruction, §11 below is
> superseded) and the tier-A hold on oat milk.
>
> Phase 1 agent briefs: [`docs/phase1/`](docs/phase1/).

---

## 1. Context

Single-site independent café in Dundee, Scotland. Opened November 2025. ~£3–5k
monthly revenue, 25–40 transactions a day, one or two staff on shift plus a manager.
POS is **Lightspeed Restaurant K-Series** (Square previously). Also sells through
Deliveroo and Just Eat.

Today, stock lives in one person's head and gets topped up by walking to a shop most
mornings. Costs live in a spreadsheet maintained by hand.

The system replaces both: POS sales → recipe resolution → theoretical stock
depletion → par-level calculation → a draft purchase order in Telegram for one-tap
confirmation. Plus a back-office web app where the menu's *composition* is
configured once and every cost recalculates from it.

### Goals

1. Nobody has to remember what is running out.
2. A correctly-sized draft order is ready before each delivery day.
3. Theoretical stock stays within 10% of physical counts, and the system says so
   when it does not.
4. Changing one ingredient price or one recipe quantity updates every affected
   item's cost instantly.

### Non-goals in v1

- Replacing Lightspeed's POS functions
- Payment or order submission without human confirmation
- User management beyond a single shared password
- Multi-site anything

---

## 2. The real shape of the menu

From the legacy workbook (`sashas_corner_finance__LEGACY_.xlsx`):

| Fact | Spec | Actual (measured) |
|---|---|---|
| Ingredients | 114 | 113 |
| Base menu items | 175 | 174 |
| Item × size rows | 318 | 314 |
| Recipe lines | ~1,600 | 1,595 |
| Sizes in use | S, M, XL, One | confirmed |

Ingredient frequency (measured, matches spec exactly):

```
Napkin       265    12oz cup/lid  102 each
Whole milk   230    16oz cup/lid  101 each
Coffee beans 121     8oz cup/lid   62 each
```

### The conclusion that drives the whole design

**318 rows is not 318 recipes. It is roughly 20 patterns, multiplied out.**

62 flavoured lattes are one recipe with the syrup swapped and quantities scaled by
size. 42 hot chocolates are one recipe with a flavour axis. The legacy spreadsheet
flattened all of it, which is why it has ~1,600 hand-maintained lines and why
changing milk quantity in a latte was a day's work. **Do not port that structure.**
Model the pattern, generate the leaves.

Phase 0's pattern detection found **27 proposed templates + 43 one-off groups**,
confirming the thesis. See `ARCHITECTURE.md` §8.

---

## 3. Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12, `uv` |
| ORM | SQLAlchemy 2.0, declarative, fully typed (`Mapped[...]`) |
| Migrations | Alembic — every schema change, no exceptions |
| DB | SQLite, WAL mode |
| Validation | Pydantic v2 |
| HTTP client | httpx (async) |
| Bot | aiogram 3.x |
| Scheduling | APScheduler |
| API | FastAPI, read-only except the composition editor |
| Tests | ~~pytest, hypothesis, freezegun~~ — **not used, see ARCHITECTURE.md §1** |
| Lint / types | ruff, mypy strict on `domain/` and `services/` |
| Frontend | React 18 + TypeScript, Vite, Tailwind, TanStack Query + Table |
| Charts | visx or hand-rolled SVG |

### On async

**Do not make everything async.** SQLite has one writer. Async repositories buy
nothing at 40 transactions a day and make reasoning worse.

- Async only at I/O edges: the Lightspeed client, aiogram handlers.
- Sync everywhere else, including all DB access via SQLAlchemy's sync `Session`.
- Handlers reach DB work through `asyncio.to_thread`.
- Connect pragmas: `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`,
  `synchronous=NORMAL`.
- No SQLite-specific SQL in application code.

---

## 4. Domain model

**All money as integer pence.** All quantities `Decimal` with explicit scale. All
timestamps UTC, timezone-aware. The implemented schema is `cafeops/db/models/` —
22 tables. It is spec §4 plus the additions in `ARCHITECTURE.md` §2.

### 4.1 Ingredients and stock

`ingredient` (unit enum **L|KG|ML|G|EACH**, category, tier, tracking_enabled,
waste_factor, current_cost_pence_per_unit + current_cost_source as a denormalised
cache), `ingredient_price` (effective-dated, with `source` INVOICE|ESTIMATE|
SUPPLIER_FEED), `stock_count`, `stock_movement` (append-only), `par_level`,
`drift_observation`.

### 4.2 Composition — the core of this build

`drink_template`, `size_profile` (belongs to a template: XL latte and XL milkshake
are unrelated), `template_component` (role + nullable ingredient + `qty_by_size` JSON
+ `is_substitutable` + effective dating), `variant_axis`, `variant_option`,
`modifier` (action SUBSTITUTE|ADD|SCALE, targets a **role** not an ingredient),
`menu_item` (template_id + size_code + selected_options, or `manual_recipe=True`),
`manual_recipe_line`, `menu_item_cost` (materialised), `legacy_staged_recipe`.

`component_role` is an enum, not a table:
`COFFEE | MILK | BASE | FLAVOUR | TOPPING | PACKAGING | SUNDRY`.

### 4.3 Recipe resolution — `domain/composition.py::resolve_recipe`

```python
def resolve_recipe(item, modifiers, at, *, ingredients) -> ResolvedRecipe
```

1. Start from `template_component` rows valid at `at`, take `qty_by_size[size_code]`.
2. Each `variant_axis`'s selected option fills the role-matched slot. An option with
   its own `qty_by_size` overrides the slot's.
3. Apply modifiers in the order **SUBSTITUTE → SCALE → ADD**.
4. A `SUBSTITUTE` against a slot with `is_substitutable = False` **raises
   `SubstitutionError`** — an error, not a silent no-op.
5. Multiply by `(1 + waste_factor)` for stock depletion. **Not** for menu cost.
   `ResolvedRecipe` exposes `lines` (cost) and `depletion_lines` (stock) as separate
   fields so they cannot be conflated.
6. `manual_recipe` items bypass all of it and read `manual_recipe_line`.

**Effective dating is mandatory** (invariant 3). Resolution takes an `at`; every
component and option carries `effective_from`/`effective_to`. An edit closes the old
rows and opens new ones — never an in-place update. The date narrowing happens in
`db/repositories/composition.py`, before `resolve_recipe` runs.

### 4.4 Sales, suppliers, orders

`sale` (idempotent on `lightspeed_line_id`, `applied_modifiers` JSON, plus `voided`,
`is_refund`, `expanded_at`), `supplier` (+ `order_url`, `agent_instructions` for the
`BROWSER_AGENT` channel), `supplier_product`, `purchase_order`, `po_line`,
`checklist_response`.

### 4.5 Tier semantics

- **A** — high volume, stable, business-stopping if out. Consumption calculated from
  sales. Eligible for auto-ordering.
- **B** — calculated, always surfaced for human review.
- **C** — never calculated. Yes/no checklist only.

**Tier A membership is earned, not assigned** (§5.2). Current roster is 12 items:
Napkin, Whole milk, Coffee beans, 12oz cup+lid, 16oz cup+lid, 8oz cup+lid,
Chocolate powder, Matcha powder — and **Oat milk (barista), which starts at tier B**
until a real Lightspeed payload proves modifiers arrive on sale lines. See
`ARCHITECTURE.md` §2.1.

---

## 5. Algorithms

### 5.1 Theoretical on-hand — IMPLEMENTED

```
on_hand(ingredient, at) = latest stock_count before `at`
                        + Σ stock_movement.qty in (that count, at]
```

Sales become `SALE` movements via expansion, resolving at `sale.sold_at` and
applying `waste_factor`. Expect 0.08–0.12 for milk.

### 5.2 Drift — the trust metric — NOT YET BUILT (Agent C)

```
drift_pct = (theoretical - counted) / max(counted, epsilon) * 100
```

| Drift, last 2 counts | Action |
|---|---|
| < 10% | eligible for `auto_order_enabled = True` |
| 10–15% | propose a `waste_factor` adjustment, stay manual |
| > 15% | force `auto_order_enabled = False`, raise an alert |

**No ingredient enters auto-ordering without two consecutive counts under 10%.**
Enforce in code. This rule is the difference between a useful system and one that
orders £200 of milk nobody needed.

### 5.3 Forecast — NOT YET BUILT (Agent D)

```
base_daily    = EWMA(daily consumption, trailing 28 days, alpha=0.3)
dow_factor[d] = mean(weekday d) / mean(all days), trailing 8 weeks, clamp [0.5, 2.0]
forecast(day)  = base_daily * dow_factor[weekday(day)]
```

Under 14 days of history, flat mean and mark low-confidence. The API returns the
flag; the UI must render it.

### 5.4 Order sizing — NOT YET BUILT (Agent D)

```
cover_days = lead_time_days + days_to_next_delivery_after(target) + safety_days
need   = Σ forecast over the cover window - on_hand - qty on open POs
packs  = ceil(need / pack_size), clamped so on-hand lands in [min_qty, max_qty]
```

Below `min_order_pence`, top up with tier B items ranked by shortest remaining
cover. Report that it happened and why — never silently inflate an order.

### 5.5 Cost rollup — NOT YET BUILT (Agent B)

```
ingredient_price change -> template_component -> menu_item -> margin, P&L COGS
```

Compute on read, cache in `menu_item_cost`, refreshed by a job and on every
composition edit. Never 318 resolutions inside a request handler.

Before committing a composition edit, produce an **impact preview**: affected item
count, cost delta per item, projected monthly COGS delta from the last 30 days of
sales. A domain function, not a UI concern.

---

## 6. Seeding from the legacy workbook — IMPLEMENTED

`uv run cafeops import-legacy --dry-run` runs three passes:

1. **Ingredients** → `ingredient` + `ingredient_price`, `source` from the
   "Supplier / notes" column, defaulting to ESTIMATE. 42 of 113 are estimates.
2. **Flat recipes** → `legacy_staged_recipe`. Verbatim, no interpretation.
3. **Pattern detection, assisted not automatic.** Proposes templates:

```
Proposed template: Flavoured Latte (Coffee beans (house blend))
  matches 66 menu items across 15 flavours, sizes S/M/XL
  common: Coffee beans (COFFEE), Whole milk (MILK), cups, lids, Napkin (SUNDRY)
  varying: flavour (FLAVOUR) -- 15 distinct
  conflicts: 27 items differ in Whole milk qty at size M (0.18, 0.24)  -> review
```

The importer **proposes**, a human confirms, and only then are templates written.
Auto-generating templates from dirty data and treating them as truth is how you get
a system confidently costing drinks wrong. Items fitting no pattern import as
`manual_recipe = True`.

Known dirt is surfaced, not silently fixed: `'card' (£3.00)` at zero cost,
`Syrup Gift Set`, rows noted "VERIFY", `Strawberry bliss`, and a genuine duplicate
(`Rose Hot Chocolate` under two recipe numbers).

---

## 7. Backend layout

```
cafeops/
  config.py
  db/{base,types}.py  models/  repositories/protocols.py
  domain/                   PURE: no SQLAlchemy, no I/O, no config import
    types.py                shared dataclasses + enums   [INTEGRATOR-OWNED]
    units.py                5 units, dimension-safe conversion
    composition.py          resolve_recipe, impact preview
    stock.py                theoretical on-hand
    drift.py tiers.py       (Agent C)
    forecast.py ordering.py (Agent D)
  integrations/lightspeed/  (Agent A)   suppliers/  channel adapters
  services/                 use cases, transaction boundaries
  bot/ jobs/ api/           (Agent E, Phase 2/3)
  seed/                     legacy importer, pattern detection, demo generator
  cli.py
migrations/  docs/phase1/  web/
```

`domain/types.py` and `db/repositories/protocols.py` are **integrator-owned**. An
agent needing a change there raises it; it does not edit them.

---

## 8. Frontend (Phase 3)

The daily interface is the **Telegram bot** (Russian). The web app is the other
half: where you sit down and think about the business, and where composition is
configured. Opened a few times a week, mostly on a laptop. Nothing in it is urgent.
It should not look like an operations console with live tiles and alert badges.

**Dashboard language: English only** (confirmed). Design for the laptop, survive at
375px.

Screens, in build order: **composition editor** (three panes — template list,
per-size component grid, live consequences; impact preview mandatory before commit),
**stock** (theoretical vs counted distinguishable at a glance, by type/weight/
position — not a tooltip), menu margin (velocity × margin scatter), today, week &
month, money, P&L, import review.

Design direction, avoid-list, and API shapes: see the full brief history and
`docs/phase1/`. Key rules: numbers carry the design (tabular figures, deliberately
set); resist the traffic light — reserve colour for crossed thresholds; estimated or
missing costs are shown, flagged, and excluded from aggregates, never defaulted to
zero.

---

## 9. Commands

```bash
uv run alembic upgrade head
uv run cafeops import-legacy --dry-run          # proposed templates + conflicts
uv run cafeops import-legacy --commit
uv run cafeops seed --demo                      # latte template + 60 days of sales
uv run cafeops stock --as-of today [--tier A]   # THEORETICAL on-hand
uv run cafeops ingredients [--tier A]
uv run cafeops expand
uv run cafeops info                             # config + what is NOT built
uv run ruff check . && uv run ruff format --check .
uv run mypy cafeops/domain/ cafeops/services/   # strict, zero type: ignore
```

---

## 10. Build plan

- **Phase 0 — done.** Contracts, models, migration, `domain/types.py`, protocols,
  legacy importer with pattern detection, demo seed, `resolve_recipe`.
- **Phase 1 — agents A–D running.** A: Lightspeed. B: composition engine (impact
  preview, cost rollup, proposal materialisation). C: stock engine (drift, tier
  gate, record_count). D: forecast & ordering. **E: bot & jobs, held** until A–D
  settle.
- **Phase 2 —** wire together, resolve contract drift, end-to-end, legacy import
  for real.
- **Phase 3 —** frontend: composition editor and stock first.

---

## 11. Testing

**Superseded by owner instruction: no tests are written.** See `ARCHITECTURE.md` §1
for what that costs and which invariants are consequently unguarded. Two of the
five proofs this section asked for were moved into places that need no test: the
confirmation invariant is a `CHECK` constraint, and effective dating is a repository
query rather than a rule each caller must remember.

---

## 12. Invariants to encode

1. Nothing is ordered without human confirmation. Ever, in v1.
2. Auto-order eligibility is earned per ingredient through drift history, never set
   manually as a shortcut.
3. Recipe edits are effective-dated. History is never rewritten.
4. Every stock figure shown to a user is labelled theoretical or counted. Never
   blurred.
5. Waste factor affects stock depletion, never menu cost.
6. Estimated costs stay flagged as estimates through every rollup, aggregate and
   export. A missing cost is `None`, never zero.
7. Low-confidence forecasts say so in place of the number, not beside it.
8. All money in integer pence. A float touching money is a bug — `Qty` raises
   `TypeError` on float assignment.
9. The stock ledger is append-only. Corrections are `ADJUSTMENT` movements, never
   updates or deletes.
