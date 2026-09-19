# ARCHITECTURE

Decisions taken during Phase 0 that **differ from or extend** `CLAUDE.md`, and why.
If a choice here is wrong, this is the file to argue with.

Status: **Phase 0 complete.** Phase 1 (four parallel agents) not started.

---

## 0. Answers that shaped Phase 0

| Question (spec §8) | Answer | Consequence |
|---|---|---|
| Lightspeed variant | **Restaurant K-Series**; Square was the previous POS | Only a K-Series client gets built. The workbook's `Square category` column is historical and is not imported. |
| Credentials | **Fixtures first** | `lightspeed_*` settings are all optional. `cafeops info` prints `fixtures only`. Nothing touches the live POS. |
| Suppliers | Tesco (walk-in), CakeSmiths (wholesale), Cups Direct (web shop, no API) — "we prob need a generic interface" | A channel abstraction, plus a new `BROWSER_AGENT` channel. See §3. |
| Finance workbook | Exists, may be stale, **is needed** | It is the seed of record. See §4. |
| Tier A | **Minimal 6** | Whole milk, Oat milk (barista), Coffee beans (house blend), 12oz paper cup, 12oz cup lid, 16oz paper cup. |

The workbook used is `sashas_corner_finance (2).xlsx` (30 May 2026) — not
`sashas_corner_finance.xlsx` (21 Apr 2026). Only the later file has the `Recipes`
sheet with per-ingredient detail; the earlier one has a 46-row `Recipe Detail`
stub. A copy is committed as `sashas_corner_finance.xlsx` at the repo root.

---

## 1. Tests: none

**The spec requires tests (§6, and `uv run pytest` in §9). The owner explicitly
instructed "do not write tests we dont need to write them at all". No tests were
written.**

This is the single largest deviation and it has costs worth naming plainly:

- Spec §6's named guarantees are now unproven by any automated check. The
  auto-order gate test, and the "`SENT` is unreachable without `confirmed_by`"
  test, were the two the spec singled out.
- The §3.5 confirmation invariant was therefore pushed **into the schema** as a
  `CHECK` constraint rather than left to a service plus a test (see §5).
- Verification during Phase 0 was done by one-off scripts run against the seeded
  database and then discarded. Those checks passed at the time (spec §3.1's
  identity held for all 55 tracked ingredients; modifier substitution conserved
  volume to the penny) but nothing re-runs them.
- `pytest`, `pytest-asyncio` and `freezegun` are **not** installed, so the §9
  deliverable `uv run pytest passes` is not met and cannot be.

Recommendation: the four algorithms in §3 are pure functions in `domain/` with no
I/O precisely so they can be tested cheaply later. If any testing is ever
reinstated, start there and with the two invariants above.

---

## 2. Additions to the data model

The spec's §2 model is the baseline. Five things were added.

### 2.1 `modifier`, `modifier_effect`, `sale_modifier` — required, not optional

Oat milk is tier A. In K-Series an oat latte is **a latte with an oat modifier**,
not a separate menu item. Without modifier handling:

- oat milk consumption is invisible (it appears in no recipe), and
- whole milk over-depletes by the full volume of every alt-milk drink.

`ModifierEffect` supports `SUBSTITUTE` (move a quantity from one ingredient to
another), `ADD` and `REMOVE`. `domain.stock.apply_modifiers` rewrites a recipe
before expansion. Verified on the seeded data: total milk-family consumption
equals whole-milk recipe demand × 1.10 exactly, i.e. substitution moves volume
rather than creating or destroying it.

### 2.2 `drift_observation`

Spec §3.2 says "Store it" without naming a table. Drift is stored as an
append-only observation rather than recomputed on demand, because the gate reads
drift *history*. A recomputed value would silently change when `waste_factor` is
retuned, and a decision made last week would stop meaning what it meant.
`waste_factor_at_count` is recorded alongside so a retune stays auditable.

### 2.3 `menu_item.size`, `unique(name, size)`

Recipes in the workbook are per **item × size** — "Latte M" and "Latte XL" have
different milk volumes and different cups. Size is part of the identity.
`lightspeed_id` is nullable because the workbook creates items before any of them
have been matched to a K-Series product; it stays unique when present.

### 2.4 `sale.voided`, `sale.is_refund`, `sale.expanded_at`

Spec §5 requires Agent A to handle partial refunds, voided receipts and
re-syncing an already-ingested window. That needs somewhere to record the facts:

- `voided` — a voided receipt must not deplete stock, and the row is **kept**
  rather than deleted so a re-sync is a no-op instead of a resurrection.
- `is_refund` — refund lines carry a negative `qty`, so expansion produces a
  positive movement and the ledger nets out.
- `expanded_at` — the idempotency key for recipe expansion.

### 2.5 Audit and explanation fields

- `par_level.auto_order_granted_at / revoked_at / reason` — operational rule 2
  says eligibility is earned, never set as a shortcut. "Who turned this on and on
  what evidence" needs an answer.
- `purchase_order.min_order_topped_up`, `notes`, `confidence_notes` and
  `po_line.need_qty`, `is_top_up` — §3.4 requires reporting *that* a min-order
  top-up happened and *why*, and operational rule 4 requires low-confidence
  forecasts to say so. Both need to survive as far as the Telegram message.
- `ingredient.category`, `source_note` — provenance carried over from the
  workbook, so a human can see where a price or pack size came from.

---

## 3. Supplier order channels

`OrderChannel` gains **`BROWSER_AGENT`** alongside the spec's
`EMAIL|PORTAL|MANUAL`, and `supplier` gains `order_url`, `agent_instructions` and
`channel_config`.

`integrations/suppliers/base.py` defines an abstract `OrderChannelAdapter` with
`prepare()` → `PreparedOrder` and `dispatch()` → `DispatchResult`, plus a
registry. `channels.py` implements all four so `send_order` has no silent gaps:

| Channel | Supplier | Behaviour |
|---|---|---|
| `MANUAL` | Tesco | Emits a shopping list. Nothing to transmit. |
| `BROWSER_AGENT` | Cups Direct | Builds a numbered instruction script that fills a basket and **stops before checkout**. |
| `EMAIL` | CakeSmiths | Registered, refuses to transmit until Phase 2 gives it credentials — rather than pretending to succeed. |
| `PORTAL` | none today | Same. |

`prepare()` is documented as forbidden to contact anyone, and `PreparedOrder` is
inert by construction — it *describes* what would be submitted. Every adapter
returns `requires_human_completion=True`. This is operational rule 1 surviving
contact with automation: a browser agent can fill a basket, but a person presses
the last button.

---

## 4. The workbook, and what it does not contain

115 ingredients, 314 menu items, 1148 recipe lines, 5 modifiers, 3 suppliers,
115 supplier products, 55 par levels. Import is idempotent on natural keys.

### 4.1 Unit normalisation (`domain/units.py`)

The workbook is internally inconsistent **by design** — a recipe says "30 ml of
syrup" because that is how a barista thinks, while the syrup is bought by the
litre. Everything is normalised at import into `L | KG | EACH` via exact `Decimal`
ratios, so the ledger only ever holds canonical units.

**The pack unit wins over the `Unit` column.** The pack unit is how a thing is
bought and counted; the `Unit` column holds prose in four rows and an outright
typo in a fifth (`White hot chocolate powder` says `ml` for a 1 kg bag of
powder). Trusting it would stock a powder in litres. Disagreements are reported,
not silently resolved.

### 4.2 Two real data defects found, both reported at import

1. **`White hot chocolate powder`** — `Unit` column says `ml`, pack is `1 kg` for
   £6, recipes use kg. The sheet is wrong. Resolved to KG, warned.
2. **`Rose Hot Chocolate` is duplicated** — recipe numbers 219/225, 220/226 and
   221/227 all resolve to the same `(name, size)`. The importer originally
   *summed* both copies, which made the drink consume **two cups and double the
   milk**. It now keeps the first recipe, ignores the rest, and warns. 1163 detail
   rows − 15 belonging to the ignored recipes = 1148 imported lines; the counts
   reconcile exactly.

Neither is fixed in the spreadsheet. Both warnings will keep firing until the
owner edits it, which is the intent.

### 4.3 Supplier assignment is inferred

The workbook's "Supplier / notes" column is prose about where a *price* came
from, not a supplier name. Assignment is therefore by category:
`Packaging → Cups Direct`, `Cake*/CakeSmiths → CakeSmiths`, everything else
`→ Tesco`. Crude, and easily corrected per-ingredient later.

**Lead times, delivery weekdays and minimum orders for CakeSmiths and Cups Direct
are placeholders.** They are invented. §3.4's cover window is only as good as
these numbers, so the importer warns about them on every run. Confirm with each
supplier before trusting any order size.

### 4.4 Tier assignment

Tier A is the agreed six. Tier B is the categories whose consumption is worth
calculating but always human-reviewed (syrups, dairy, coffee, tea, chocolate,
specialty, packaging) — 49 items. Everything else is tier C: 60 items, never
calculated, checklist only. `tracking_enabled` is True for A and B, False for C.

Note that tracking is **ingredient-side**, not menu-item-side. All 314 menu items
and all 1148 recipe lines are imported, but only the 55 tracked ingredients
receive ledger movements. This satisfies the spec's non-goal ("only the top ~30
by volume") without throwing away recipe data that already exists.

### 4.5 Known risk for Agent A: `+upcharge` items double-counting

The workbook contains menu items `Oat milk (+upcharge)` and
`Coconut milk (+upcharge)` carrying nominal 0.05 L recipes of their own — a
Square-era way of charging for alt milk. In K-Series the same thing will arrive
as a **modifier**. If both paths fire for one drink, alt milk depletes twice.
Agent A must decide during mapping which one is authoritative; the modifier
almost certainly is.

---

## 5. Confirmation invariant enforced in the schema

Spec §3.5 asks for "a database constraint **or** a service-level invariant with a
test that proves it cannot be bypassed". With no tests, the database constraint
is the only honest option:

```sql
CONSTRAINT ck_po_confirmed_requires_human CHECK (
  (status NOT IN ('CONFIRMED','SENT','RECEIVED'))
  OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL))
CONSTRAINT ck_po_sent_requires_sent_at CHECK (
  (status <> 'SENT') OR (sent_at IS NOT NULL))
```

Verified present in the generated SQLite DDL. No code path — service, repository,
CLI or bot — can move an order to `CONFIRMED`, `SENT` or `RECEIVED` without a
recorded human.

---

## 6. Portability and exactness

### 6.1 `Qty` stores Decimal as TEXT on SQLite

SQLite has no native `NUMERIC`, and SQLAlchemy's sqlite dialect round-trips
`Numeric` **through float**, which silently destroys the exactness the spec
requires of all quantities. `db/types.Qty` is a `TypeDecorator` that renders as
`String` on SQLite and as a real `NUMERIC(18,6)` on anything else. Verified:
`typeof(stock_movement.qty)` is `text`.

It also **raises `TypeError` if a float is assigned**. A float reaching a quantity
column is a bug upstream, and quietly accepting it is how exactness dies.

### 6.2 `UTCDateTime`

SQLite has no tz-aware timestamp. This decorator refuses naive datetimes on the
way in and attaches UTC on the way out, so spec §2's "all timestamps UTC,
timezone-aware" holds in Python regardless of backend. Ruff's `DTZ` rules are
enabled to catch naive `datetime` construction at lint time.

### 6.3 Movement sums are computed in Python, not `SUM()`

Because the column is TEXT on SQLite, a SQL `SUM()` would coerce through float.
At this volume — one café, a few thousand movements a month — summing exact
`Decimal`s in Python is correct and fast enough. On Postgres the column is a real
`NUMERIC` and this can become a SQL `SUM` if it ever needs to. Noted at the call
site.

### 6.4 Consumption is grouped by **local** calendar day

A 23:30 BST sale belongs to that trading day. Grouping by UTC day would smear the
weekday pattern across midnight and corrupt §3.3's `dow_factor` for exactly the
late-evening trade that distinguishes a Friday from a Monday.

### 6.5 Swapping to Postgres

`CAFEOPS_DATABASE_URL` plus an Alembic branch, as the spec asks. No
`sqlite3`-specific SQL exists in application code; the only dialect awareness is
inside the two `TypeDecorator`s and the pragma hook. Alembic runs with
`render_as_batch=True` so future migrations survive SQLite's inability to
`ALTER`.

---

## 7. Module layout deviations

| Spec | Actual | Why |
|---|---|---|
| `tests/fixtures/` with factories and a seeded scenario | `cafeops/seed/` (`workbook.py`, `demo.py`) | With no test suite, fixtures have no home. The seeded scenario is a shipped CLI feature instead, which is what §9 actually asks to demonstrate. |
| — | `domain/units.py` | Unit normalisation is pure domain logic and the workbook forces it. |
| — | `services/read_stock.py` | One place assembles "on-hand + its basis", so operational rule 3 (always labelled theoretical or counted) is kept in one place rather than re-derived by the CLI, the bot digest and the API. |
| — | `integrations/suppliers/` | See §3. |
| `domain/stock.py`, `services/expand_recipes.py` are Agent B's | Implemented minimally in Phase 0 | `cafeops stock --as-of today` and `seed --demo` cannot exist without them. Both carry a `PHASE 0 SCOPE NOTE` header. Agent B extends; drift (`§3.2`) and tier gating are deliberately **not** written. |

`domain/` is pure as required: no SQLAlchemy import, no I/O, dataclasses in and
out. `mypy --strict` passes on `domain/` and `services/`.

### 7.1 Phase 0 also did not build

Lightspeed client and sync, drift computation, tier gating, EWMA/day-of-week
forecasting, order sizing and min-order top-up, the Telegram bot, APScheduler
jobs, the read-only API, `cli simulate`. These are the Phase 1 agents' work.
`cafeops info` prints this list so the gap is never mistaken for completeness.

---

## 8. The demo seed

`cafeops seed --demo` produces 60 days ending yesterday: ~1960 receipts (≈33/day,
inside spec §0's 25–40), ~2860 sale lines, ~12,940 movements. Deterministic
(seeded RNG), so a change in forecast output is a change in the forecaster and
not in the fixture.

Weekday pattern is quiet Monday → busy Saturday, which is what §3.3's
`dow_factor` exists to discover. 22% of milk drinks carry an alt-milk modifier,
so the tier-A oat milk path is genuinely exercised.

Two deliberate design choices:

- **`simulate_restocking` runs after expansion**, reading the SALE movements to
  decide when stock would have run low and writing `DELIVERY` movements. Without
  it the ledger only ever goes down and every on-hand figure is a large negative
  number that teaches nothing.
- **Synthetic weekly counts carry injected drift**, chosen so the demo has
  ingredients on both sides of §3.2's gate: four tier-A items under 10% (should
  become eligible), `12oz cup lid` at 12% (tuning band), `16oz paper cup` at 19%
  (**must be refused**). Agent B therefore gets a failing case for free rather
  than having to invent one.

---

## 9. Open questions

1. **CakeSmiths and Cups Direct terms** — lead time, delivery weekdays, minimum
   order. Currently invented (§4.3). Blocks trustworthy order sizing.
2. **Lightspeed credentials and the K-Series product mapping.** 314 menu items
   exist with no `lightspeed_id`. Someone must match them, or Agent A must match
   on name and report what it cannot resolve.
3. **Does K-Series expose recipes?** Spec §8 asked this. If it does, `recipe_line`
   becomes a cache of POS data rather than the source, and the workbook becomes
   an import-once. Worth checking before Agent B hardens expansion.
4. **Two workbook defects** (§4.2) need fixing in the spreadsheet, not in code.
5. **`+upcharge` vs modifier** (§4.5) — decide before the first live sync.
6. **Waste factors are initial guesses**: 0.10 for milks, 0.05 for beans, 0.01
   for cups. §3.1 says they are tuned from observed drift. Nothing tunes them
   yet; the drift report is Agent B's.
