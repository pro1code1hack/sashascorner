# Café Ops — Inventory & Auto-Ordering System

**This file is the project brief. Read it fully before writing any code.**

> **Phase 0 is complete.** See [`ARCHITECTURE.md`](ARCHITECTURE.md) for every
> decision that differs from this brief, and why. Read that file too — several
> deviations are load-bearing, including **no test suite** (owner's instruction)
> and three additions to the §2 data model.
>
> Phase 1 agent briefs live in [`docs/phase1/`](docs/phase1/).

---

## 0. Context

Single-site independent café in Dundee, Scotland. ~£3–5k monthly revenue, ~25–40
transactions/day, 1–2 staff on shift. POS is **Lightspeed Restaurant K-Series**
(Square previously). Ordering today is manual: the owner walks to a shop most
mornings and keeps stock levels in her head.

The system replaces that with: POS sales → theoretical stock depletion →
par-level calculation → a draft purchase order pushed to Telegram for one-tap
human confirmation.

**Primary user is not technical.** The only daily interface is a Telegram bot.
Everything else is back office.

### Goals

1. Nobody has to remember what is running out.
2. A draft order is ready before each supplier delivery day, correctly sized.
3. Theoretical stock stays within 10% of physical counts, and the system reports
   when it does not.
4. Zero stockouts on critical items.

### Non-goals (do not build these)

- Replacing or duplicating Lightspeed's POS functions
- Automatic payment or order submission without human confirmation
- Tracking every one of the ~311 menu items — only the top ~30 by volume
- A web dashboard in v1 (a read-only API is in scope, UI is not)
- Multi-tenant / multi-site anything

---

## 1. Stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | |
| Package/env | `uv` | `uv init`, `uv add`, `uv run` |
| ORM | **SQLAlchemy 2.0**, declarative, fully typed (`Mapped[...]`) | no raw SQL outside migrations |
| Migrations | Alembic | every schema change gets a migration, no exceptions |
| DB | SQLite, WAL mode | see §2 |
| Validation | Pydantic v2 | all external payloads and config |
| HTTP | httpx (async) | Lightspeed client |
| Bot | aiogram 3.x | |
| Scheduling | APScheduler | cron-style jobs |
| Config | pydantic-settings, `.env` | no secrets in code |
| Tests | ~~pytest, pytest-asyncio, freezegun~~ | **not used — see ARCHITECTURE.md §1** |
| Lint/format | ruff | |
| Types | mypy strict on `domain/` and `services/` |

### On async — read this carefully

**Do not make everything async.** SQLite allows one writer at a time. A codebase
where every repository method is `async` buys nothing here and makes testing and
reasoning worse.

- **Async only at I/O edges:** the Lightspeed HTTP client, the Telegram bot handlers.
- **Sync everywhere else:** domain logic, repositories, all DB access via SQLAlchemy's sync `Session`.
- Bot/job handlers call sync service functions via `asyncio.to_thread` when a DB write is involved.
- SQLite pragmas on connect: `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`, `synchronous=NORMAL`.
- Keep transactions short. One unit of work per service call.

Structure it so that swapping SQLite for Postgres later is a config change plus an
Alembic branch — no `sqlite3`-specific SQL in application code.

---

## 2. Data model

All monetary values as **integer pence**, never float. All quantities as
`Decimal` with explicit scale. All timestamps UTC, timezone-aware.

The implemented schema is in `cafeops/db/models/`. It is the brief's model plus
these additions, each justified in `ARCHITECTURE.md` §2:

- `modifier`, `modifier_effect`, `sale_modifier` — **required**, because oat milk
  is tier A and in K-Series an oat latte is a latte with a modifier.
- `drift_observation` — §3.2 says "store it" without naming a table.
- `menu_item.size` + `unique(name, size)` — recipes are per item × size.
- `sale.voided`, `sale.is_refund`, `sale.expanded_at`.
- Audit/explanation fields on `par_level`, `purchase_order`, `po_line`.

### Tier semantics — this is the core design decision

- **A** — high volume, stable consumption, business-stopping if out. Consumption
  is **calculated** from sales. Eligible for auto-ordering.
- **B** — calculated, but always surfaced for human review.
- **C** — never calculated. Yes/no checklist only.

Start with a small tier A. Promoting an item from B to A is a deliberate act
after its drift proves stable. Never the reverse by accident.

**Current tier A (6 items):** Whole milk, Oat milk (barista), Coffee beans (house
blend), 12oz paper cup, 12oz cup lid, 16oz paper cup.

---

## 3. Core algorithms

### 3.1 Theoretical on-hand — IMPLEMENTED (`domain/stock.py`)

```
on_hand_theoretical(ingredient, at) =
    latest stock_count.counted_qty (before `at`)
  + Σ stock_movement.qty where occurred_at > that count and <= at
```

Sales generate `SALE` movements via the recipe expansion job:

```
qty_consumed = sale.qty × recipe_line.qty_per_unit × (1 + ingredient.waste_factor)
```

`waste_factor` absorbs real-world loss (milk foaming, pitcher residue, steaming).
Expect 0.08–0.12 for milk. It is tuned from observed drift, not guessed once.

### 3.2 Drift — the trust metric — NOT YET BUILT (Agent B)

On each physical count:

```
drift_pct = (theoretical - counted) / max(counted, epsilon) × 100
```

Store it. Compute a rolling view per ingredient. Gate on it:

| Drift (last 2 counts) | Action |
|---|---|
| < 10% | eligible for `auto_order_enabled = True` |
| 10–15% | suggest `waste_factor` adjustment, stay manual |
| > 15% | force `auto_order_enabled = False`, raise an alert |

**An ingredient never enters auto-ordering without two consecutive counts under
10%.** This rule is the difference between a useful system and one that orders
£200 of milk nobody needed. Enforce it in code, not in documentation.

### 3.3 Consumption forecast — NOT YET BUILT (Agent C)

```
base_daily = EWMA(daily consumption over trailing 28 days, alpha=0.3)
dow_factor[d] = mean(consumption on weekday d) / mean(consumption all days)
                over trailing 8 weeks, clamped to [0.5, 2.0]
```

Forecast for a specific future day = `base_daily × dow_factor[weekday]`.

Fall back to a flat average when fewer than 14 days of history exist, and say so
in the output.

### 3.4 Order sizing — NOT YET BUILT (Agent C)

```
cover_days   = supplier.lead_time_days
             + days_until_next_delivery_after_target
             + par_level.safety_days

need = Σ forecast(day) for each day in the cover window
     - on_hand_theoretical(now)
     - qty already on open POs

packs = ceil(need / supplier_product.pack_size)
packs = clamp so that resulting on-hand stays within [min_qty, max_qty]
```

If the order total falls below `supplier.min_order_pence`, top up with tier B
items ranked by shortest remaining cover, until the minimum is met. Report
clearly that this happened and why.

### 3.5 Confirmation flow — SCHEMA-ENFORCED

Every order is `DRAFT` → `PENDING_CONFIRM` → human acts → `CONFIRMED` → `SENT`.

No state transition to `SENT` happens without a recorded human `confirmed_by`.
This is a `CHECK` constraint on `purchase_order` (`ARCHITECTURE.md` §5), not a
convention.

---

## 4. Module layout

```
cafeops/
  config.py              # pydantic-settings
  db/
    base.py              # engine, Session factory, pragmas
    types.py             # Qty / UTCDateTime portable column types
    models/              # one module per aggregate
    repositories/        # query objects, no business logic
  domain/                # PURE: no SQLAlchemy, no I/O
    types.py             # shared dataclasses + enums  [INTEGRATOR-OWNED]
    units.py             # unit normalisation
    stock.py             # theoretical on-hand, recipe expansion
    drift.py             # (Agent B)
    tiers.py             # (Agent B)
    forecast.py          # (Agent C)
    ordering.py          # (Agent C)
  integrations/
    lightspeed/          # (Agent A)
    suppliers/           # order channel abstraction
  services/              # use cases, transaction boundaries
  seed/                  # workbook importer + demo generator
  bot/                   # (Agent D)
  jobs/                  # (Agent D)
  api/                   # (Phase 2)
  cli.py                 # typer
migrations/
docs/phase1/             # agent briefs
```

`domain/` must be pure: no SQLAlchemy imports, no I/O. It takes dataclasses in
and returns dataclasses out. This is what makes the algorithms testable without a
database and is non-negotiable.

`domain/types.py` and `db/repositories/protocols.py` are **integrator-owned**. An
agent that needs a change there raises it; it does not edit unilaterally.

---

## 5. Operational rules to encode

1. Nothing is ordered without human confirmation. Ever, in v1.
2. Auto-order eligibility is earned per-ingredient through drift history, never
   set manually as a shortcut.
3. Every stock number shown to a user is labelled theoretical or counted. Never
   blur them.
4. When forecast confidence is low (<14 days history, or drift >15%), say so in
   the message rather than silently producing a number.
5. All money in integer pence. Any float touching money is a bug. (`Qty` raises
   `TypeError` on float assignment.)
6. The ledger is append-only. Corrections are `ADJUSTMENT` movements, never
   updates or deletes.

---

## 6. Commands

```bash
uv run alembic upgrade head                                  # create/migrate the DB
uv run cafeops seed --workbook ./sashas_corner_finance.xlsx  # import the workbook
uv run cafeops seed --workbook ./... --demo                  # + 60 days synthetic sales
uv run cafeops stock --as-of today [--tier A] [--all]        # THEORETICAL on-hand
uv run cafeops ingredients [--tier A]                        # tiers, units, waste, supplier
uv run cafeops expand                                        # expand sales into the ledger
uv run cafeops info                                          # config + what is NOT built
uv run ruff check . && uv run ruff format --check .
uv run mypy cafeops/domain/ cafeops/services/                # strict
```
