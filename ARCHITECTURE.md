# ARCHITECTURE

Decisions taken during Phase 0 that **differ from or extend** `CLAUDE.md`, and why.
If a choice here is wrong, this is the file to argue with.

Status: **v2 Phase 0 complete.** v1 Phase 1 (agents A–D) is merged; the v2 model
extension — batches, shelf life, seasons, multi-supplier sourcing, prep time,
channels, agent log — has landed on top of it. v2 Phase 1 (six agents) not yet started.

The spec has been revised twice. Where this file says "spec §N", it means the CURRENT
`CLAUDE.md`. Sections 8A–8D record findings from v1's fan-out that still hold; 8E–8F
are v2.

---

## 0. Answers that shaped Phase 0

Spec §13's six questions, resolved with the owner before any code:

| Question | Answer | Consequence |
|---|---|---|
| §13.1 Lightspeed variant | **Restaurant K-Series**. Square was the previous POS. | Only a K-Series client is built. The legacy workbook's `Square category` column is historical and not imported. |
| §13.2 Modifiers on sale lines? | **Unknown — build the model, gate the tier.** | The full modifier model exists, but alt milks start at tier B until a real payload proves modifiers arrive. See §2.1. |
| §13.3 Credentials | **Fixtures first.** | Every `lightspeed_*` setting is optional; `cafeops info` prints `fixtures only`. Nothing touches the live POS. |
| §13.4 Supplier | Tesco (walk-in), CakeSmiths (wholesale), Cups Direct (web shop, no API). | A channel abstraction plus a new `BROWSER_AGENT` channel. See §4. |
| §13.5 Tier A | **12 items** (napkins included — they are the single most-used ingredient at 265 recipe lines). | 11 start at tier A; oat milk is held at B. See §2.1. |
| §13.6 Dashboard language | **English only for now.** Bot stays Russian. | No i18n layer. `domain/` is locale-free; Russian lives only in `bot/formatters.py`. |

v2 added five more questions (spec §15). Answered with the owner:

| Question | Answer | Consequence |
|---|---|---|
| §15.3 Shelf lives | **Seed my defaults, flagged ESTIMATE** | 100 of 113 ingredients perishable. Milk's usable window is 5 days. See §8F.1. |
| §15.4 Supplier terms | **Placeholders, loudly flagged** | 6 of 8 suppliers carry `terms_are_placeholders`. See §8F.4. |
| §15.5 Nataly | **UNANSWERED** | Modelled MANUAL, 5-day lead, so it cannot win a sourcing decision. Open question 1. |
| §15.7 Loaded hourly rate | **£14.50/hr** | Labour costing and `margin_per_minute` live. See §8F.5. |
| §15.6 Deliveroo / Just Eat | **Portal login, manual export** | CSV `ChannelSource` first, browser agent second, behind one protocol. |

### The workbook

`sashas_corner_finance [LEGACY].xlsx` from `~/Downloads`, committed as
`sashas_corner_finance__LEGACY_.xlsx`. Confirmed as the right file because its
ingredient frequencies match spec §2 exactly: Napkin 265, Whole milk 230, Coffee
beans 121, 12oz 102, 16oz 101, 8oz 62.

Two earlier files in `~/Downloads` are **not** this one and must not be used:
`sashas_corner_finance.xlsx` (21 Apr, a stub) and `sashas_corner_finance (1)/(2).xlsx`
(30 May, identical to each other, with unpopulated costs and only 1163 recipe
lines against this one's 1595).

Counts differ trivially from the spec's stated figures — 113 ingredients vs 114,
314 item×size rows vs 318, 174 base items vs 175, 1595 recipe lines vs "~1,600".
The spec's numbers appear to include a header or blank row. Not worth chasing.

---

## 1. Tests: none

**The spec requires tests (§11, and §14's `uv run pytest passes`). The owner
instructed otherwise — twice, explicitly, most recently when asked directly which
of the two should stand. No tests were written.**

This is the largest deviation and its costs are worth naming plainly:

- Spec §11's five named proofs have no automated enforcement. They were the drift
  gate, `SENT` unreachable without `confirmed_by`, a past-dated resolution
  unaffected by a later edit, and `SUBSTITUTE` against a non-substitutable slot
  raising.
- Two of those five were pushed into places that do not need a test:
  - `SENT`/`CONFIRMED`/`RECEIVED` without a human is a **`CHECK` constraint**
    (§5), not a service convention.
  - Effective dating is a **query concern** in the repository, not something each
    caller must remember to filter.
- The other three were verified manually during Phase 0 by throwaway scripts, then
  discarded. They passed at the time:
  - the §5.1 on-hand identity held for every tracked ingredient;
  - `resolve_recipe` refused a `SUBSTITUTE` into the non-substitutable COFFEE slot;
  - a milk-quantity edit committed today left a 30-day-old resolution at 0.18 L
    while today's became 0.20 L.
  **Nothing re-runs these.** A regression in any of them will be silent.
- `pytest`, `pytest-asyncio`, `hypothesis` and `freezegun` are **not installed**, so
  §14's `uv run pytest passes` is not met and cannot be.

`domain/` is pure with no I/O specifically so that this is cheap to reverse. If
testing is ever reinstated, start with `resolve_recipe`'s effective dating and the
drift gate — they are the two places where a silent wrong answer does real damage.

---

## 2. Additions to the data model

Spec §4 is the baseline. Everything below is an addition, and each earns its place.

### 2.1 Oat milk is on the tier A roster but starts at tier B

The owner chose a 12-item tier A **and** said to gate alt milks until modifiers are
proven to arrive on sale lines. Those two instructions meet here:

- 11 items start at `Tier.A`.
- `Oat milk (barista)` starts at `Tier.B`, enforced by
  `seed/legacy.py::OAT_MILK_HELD`, which also emits a warning on every import
  explaining why.

This is not a fudge — it is exactly spec §4.5's "tier A membership is earned, not
assigned". Oat milk consumption is only visible through modifiers; until a real
payload shows them, calculating it would be inventing numbers. Agent A is tasked
with answering the question, and promotion is a one-line change once it does.

### 2.2 `LegacyStagedRecipe`

Spec §6 pass 2 needs somewhere to put the flat legacy recipes before
interpretation. Kept after import rather than dropped: it is the only record of what
the workbook actually said, and the import-review screen (§8.6) compares proposals
against it.

### 2.3 `DriftObservation`

Spec §5.2 says to store drift without naming a table. Stored as an **append-only
observation** rather than recomputed on demand, because the gate reads drift
*history*. A recomputed value would silently change when `waste_factor` is retuned,
so a decision made last week would stop meaning what it meant.
`waste_factor_at_count` is recorded alongside so a retune stays auditable.

### 2.4 `sale.voided`, `sale.is_refund`, `sale.expanded_at`

Spec §10 requires Agent A to handle voided receipts, partial refunds and re-syncing
an ingested window. Those facts need somewhere to live:

- `voided` — a voided receipt must not deplete stock, and the row is **kept** rather
  than deleted so a re-sync is a no-op instead of a resurrection.
- `is_refund` — refunds carry a negative `qty`, so expansion emits a positive
  movement and the ledger nets out.
- `expanded_at` — the idempotency key for expansion, set in the same transaction as
  the movements.

### 2.5 Audit and explanation fields

- `par_level.auto_order_granted_at / revoked_at / reason` — invariant 2 says
  eligibility is earned. "Who granted this, and on what evidence" needs an answer.
- `purchase_order.sent_at`, `min_order_topped_up`, `notes`, `confidence_notes` and
  `po_line.need_qty`, `is_top_up` — §5.4 requires reporting *that* a top-up
  happened and *why*, and invariant 7 requires low confidence to be spoken aloud.
  Both must survive as far as the Telegram message.
- `ingredient.current_cost_source` alongside the denormalised cost cache — without
  it, any aggregate built on the cache column loses the invoice/estimate
  distinction, and invariant 6 exists precisely to prevent that.
- `menu_item.data_quality_flag`, `legacy_staged_recipe.data_quality_flag` — spec §6
  names specific dirt to surface rather than fix. This is where it surfaces.

### 2.6 `TemplateComponent.superseded_by_id`

Effective dating tells you a row is closed; it does not tell you what replaced it.
This makes the edit history walkable, which the import-review and audit screens
need.

---

## 3. Units: five, not three, and conversion is dimension-safe

Spec §4.1 keeps `ML` and `G` as first-class, and that is right: the workbook stocks
syrup by the litre but recipes and costs it per ml. Forcing one canonical unit would
mean rewriting every recipe quantity at import and losing the unit the owner thinks
in.

The cost of that choice is `domain/units.py`. Its one non-obvious decision:
**`convert` raises `IncompatibleUnitsError` across dimensions** rather than
returning a 1:1 fallback. Litres to kilograms needs a density, which this system
does not model. Failing loudly is correct — a silent 1:1 would put "0.18 kg of milk"
into a ledger that means litres, and nothing downstream could detect it.

`cl` is handled as an alias with a multiplier (1 cl = 10 ml) rather than its own
enum member, since nothing is stocked in centilitres.

---

## 4. Supplier order channels

`OrderChannel` gains **`BROWSER_AGENT`** alongside the spec's `EMAIL|PORTAL|MANUAL`,
and `supplier` gains `order_url`, `agent_instructions`, `channel_config`.

`integrations/suppliers/base.py` defines an abstract `OrderChannelAdapter` with
`prepare()` → `PreparedOrder` and `dispatch()` → `DispatchResult`, plus a registry.
All four channels are implemented so `send_order` has no silent gaps:

| Channel | Supplier | Behaviour |
|---|---|---|
| `MANUAL` | Tesco | Emits a shopping list. Nothing to transmit. |
| `BROWSER_AGENT` | Cups Direct | Builds a numbered instruction script that fills a basket and **stops before checkout**. |
| `EMAIL` | CakeSmiths | Registered, and refuses to transmit until credentials exist — rather than pretending to succeed. |
| `PORTAL` | none today | Same. |

`prepare()` is documented as forbidden to contact anyone, and `PreparedOrder` is
inert by construction. Every adapter returns `requires_human_completion=True`. This
is invariant 1 surviving contact with automation: a browser agent may fill a basket,
but a person presses the last button.

---

## 5. Invariant 1 enforced in the schema

Spec §5.5 (v1) asked for "a database constraint **or** a service-level invariant
with a test". With no tests, the constraint is the only honest option:

```sql
CONSTRAINT ck_po_confirmed_requires_human CHECK (
  (status NOT IN ('CONFIRMED','SENT','RECEIVED'))
  OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL))
CONSTRAINT ck_po_sent_requires_sent_at CHECK (
  (status <> 'SENT') OR (sent_at IS NOT NULL))
```

Verified present in the generated SQLite DDL. No code path — service, repository,
CLI or bot — can advance an order without a recorded human.

---

## 6. Portability and exactness

### 6.1 `Qty` stores Decimal as TEXT on SQLite

SQLite has no native `NUMERIC`, and SQLAlchemy's sqlite dialect round-trips
`Numeric` **through float**, destroying the exactness the spec requires of all
quantities. `db/types.Qty` renders as `String` on SQLite and as real
`NUMERIC(18,6)` elsewhere. Verified: `typeof(stock_movement.qty)` is `text`.

It also **raises `TypeError` if a float is assigned**. A float reaching a quantity
column is a bug upstream, and quietly accepting it is how exactness dies.

### 6.2 `UTCDateTime`

SQLite has no tz-aware timestamp. This decorator refuses naive datetimes on the way
in and attaches UTC on the way out. Ruff's `DTZ` rules are enabled so naive
`datetime` construction fails at lint time.

### 6.3 Movement sums are computed in Python, not `SUM()`

Because the column is TEXT on SQLite, a SQL `SUM()` would coerce through float. At
one café's volume, summing exact `Decimal`s in Python is correct and fast enough. On
Postgres the column is a real `NUMERIC` and this can become a SQL `SUM`. Noted at
the call site.

### 6.4 Consumption is bucketed by **local** calendar day

A 23:30 BST sale belongs to that trading day. Bucketing by UTC would smear the
weekday pattern across midnight and corrupt §5.3's `dow_factor` for exactly the
late-evening trade that distinguishes a Friday from a Monday.

### 6.5 Postgres later

`CAFEOPS_DATABASE_URL` plus an Alembic branch. No `sqlite3`-specific SQL exists in
application code; the only dialect awareness is inside the two `TypeDecorator`s and
the pragma hook. Alembic runs `render_as_batch=True` so future migrations survive
SQLite's inability to `ALTER`.

---

## 7. Composition: what Phase 0 decided

### 7.1 Effective dating is a query concern, not a resolver concern

`resolve_recipe` is pure and **date-agnostic**: it receives a `MenuItemSpec` that
already contains only what was in force at the resolve date. The narrowing happens
in `db/repositories/composition.py`, in SQL.

This split matters. It means invariant 3 is enforced by one query rather than by
every caller remembering to filter, and it means resolution is reviewable without a
database. The date still travels with the call (`resolve_recipe(item, mods, at)`)
because the result records it.

### 7.2 Recipe cost and stock depletion are two separate values

`ResolvedRecipe.lines` are recipe quantities. `ResolvedRecipe.depletion_lines` are
the same quantities with `waste_factor` applied. They are separate fields rather
than a flag, so invariant 5 cannot be violated by passing the wrong argument —
there is no wrong argument to pass.

### 7.3 A missing cost is `None`, never zero

`ResolvedRecipe.cost_pence` returns `None` if **any** ingredient is unpriced, and
`cost_source` returns the *weakest* source among the ingredients — one estimated
ingredient makes the whole item an estimate. Invariant 6 only works if "costs
nothing" and "we do not know what it costs" stay distinguishable all the way up to
the margin screen.

### 7.4 Quantities in JSON are strings

`qty_by_size` is `{"S": "0.12", "M": "0.18"}` — strings, parsed to `Decimal` on
read. JSON has only doubles, and a recipe editor that loses `0.1 + 0.2` is
unacceptable (spec §8).

### 7.5 Unfilled optional slots do not warn

A size-determined packaging slot legitimately has no quantity at other sizes: the
8oz cup slot has one at S and nothing at M or XL. Warning on those produced four
spurious warnings per resolution, which is how real warnings get ignored. Only
`is_required` slots warn.

---

## 8. Pattern detection: what it found, and what it refuses to do

`seed/patterns.py` collapses the 314 item×size rows into **27 proposed templates**
plus 43 one-off groups. Spec §2 predicted ~20 patterns; close enough to confirm the
thesis that the workbook is ~20 patterns multiplied out.

The largest proposals:

```
Flavoured Latte (Coffee beans)   66 items, 15 flavours, S/M/XL
Flavoured Chocolate              33 items, 11 flavours, S/M/XL
Mixed Americano group            24 items,              S/M/XL/One
Flavoured Matcha (Butterfly pea) 19 items,  5 flavours, S/M/XL
Flavoured Matcha (Matcha powder) 18 items,  7 flavours, S/M/XL
```

Four decisions inside it:

1. **It proposes; it never writes.** Phase 0 leaves every menu item
   `manual_recipe=True`. Spec §6 is explicit: auto-generating templates from dirty
   data and treating them as truth is how you get a system confidently costing
   drinks wrong. Materialising a confirmed proposal is Agent B's job.
2. **Roles are inferred, and failures are reported.** The workbook has no role
   column, and pattern detection is impossible without one — "62 lattes are one
   template with the syrup swapped" is only visible once you know which line is the
   syrup. `seed/roles.py` infers by name fragment then category, and returns `None`
   rather than guessing. A mis-roled ingredient produces a wrong template that
   costs every drink using it incorrectly.
3. **Packaging and flavour are abstracted in the signature.** Cups are
   size-determined and syrups are the variant; including them would make every size
   and every flavour its own template.
4. **Conflicts are surfaced, never averaged.** The largest group reports "27 items
   differ in Whole milk qty at size M (0.18, 0.24)" — two real sub-patterns lumped
   together. Silently averaging would bake a wrong recipe into 62 drinks. The
   `Mixed ... group` naming exists so a reviewer can see which proposals want
   splitting.

### 8.1 Two real workbook defects, both reported at import

1. **`Rose Hot Chocolate` is duplicated** — recipe numbers 219/225, 220/226,
   221/227 all resolve to the same `(name, size)`. An earlier version of the
   importer *summed* both copies, which made the drink consume **two cups and
   double the milk**. It now keeps the first, ignores the rest, and warns. The
   counts reconcile exactly: 1595 detail rows − 18 belonging to the ignored
   recipes = 1577 staged.
2. **`White hot chocolate powder`** carried `Unit = ml` for a 1 kg bag of powder in
   the previous workbook revision. This revision's `Unit` column is clean, but the
   importer still prefers the **pack unit** (how a thing is bought and counted) and
   warns on any disagreement rather than silently resolving it.

Neither is fixed in the spreadsheet. The warnings will keep firing until the owner
edits it, which is the intent.

### 8.2 Supplier assignment is inferred, and the terms are invented

The workbook's "Supplier / notes" column is prose about where a *price* came from,
not a supplier name. Assignment is by category: `Packaging`/`Sundries` → Cups
Direct, `Cake*` → CakeSmiths, everything else → Tesco.

**CakeSmiths and Cups Direct lead times, delivery weekdays and minimum orders are
placeholders. They are invented.** §5.4's cover window is only as good as they are,
so the seed warns about them on every run. Confirm with each supplier before
trusting any order size. This is open question 1 below.

### 8.3 Price sources default to ESTIMATE

`_price_source` maps the notes column onto `PriceSource`, defaulting to **ESTIMATE**
rather than INVOICE. Guessing upward would launder a guess into an invoice, and
invariant 6 exists to stop exactly that. 42 of 113 price rows are estimates — which
is why the legacy 46% COGS figure is untrustworthy, and why the margin screen must
exclude them from aggregates rather than quietly including them.

---

## 8A. The drift gate: one interpretation decided, one gap queued

### 8A.1 A count in the 10–15% band REVOKES an existing grant — endorsed

Spec §5.2's table says `10–15% → propose a waste_factor adjustment, stay manual`.
That is unambiguous for an ingredient that is *not* yet auto-ordering. It is
ambiguous for one that already is: does "stay manual" mean "remains ineligible", or
"becomes manual"?

Agent C implemented **revoke**, and that is the right reading. The spec's own
stronger sentence is *"An ingredient never enters auto-ordering without two
consecutive counts under 10%."* A 12% count breaks the streak, so the condition that
justified the grant no longer holds. Leaving auto-ordering on would mean the system
is auto-ordering against evidence it no longer has — which is precisely the failure
the gate exists to prevent, and exactly how you end up with £200 of milk nobody
needed.

The cost is friction: a recovered ingredient needs two fresh clean counts rather
than one. That is the correct direction to be wrong in.

Verified independently against nine cases, including three the agent did not claim:

| Recent drift % (newest first) | Currently on | Action | Alert |
|---|---|---|---|
| `[4.2]` | no | HOLD | – |
| `[4.2, 4.2]` | no | **GRANT** | – |
| `[13.9]` | no | HOLD | – |
| `[13.9, 4.2]` | **yes** | **REVOKE** | no |
| `[23.8]` | no | HOLD (refused) | **yes** |
| `[25.0, 4.2]` | yes | **REVOKE** | **yes** |
| `[4.2, 23.8]` | no | HOLD | – |
| `[-4.2, -4.2]` | no | GRANT (absolute drift) | – |
| `[4.2, 4.2]` tier **B** | no | HOLD (tier B is never auto-ordered) | – |

### 8A.2 Invariant 2 is structurally enforced, not documented

`SqlParLevelRepository.set_auto_order(..., True)` **raises
`AutoOrderGrantRefused`**. Enabling is reachable only via
`domain/tiers.evaluate_gate` → `apply_gate_decision`, and a hand-built
`GateDecision` carrying no gate authorisation is also refused. Revoking is allowed
from anywhere, because that is the safe direction. Verified.

This is the same move as the purchase-order `CHECK` constraint (§5): with no test
suite, the way to protect an invariant is to make violating it unrepresentable
rather than merely forbidden.

### 8A.3 Gap queued for Phase 2: a silent revocation

A revoke in the 10–15% band currently carries `alert=False`, because spec §5.2 only
demands an alert above 15%. But a revocation is a **material change in system
behaviour** — orders that were being drafted automatically stop being drafted. The
owner should be told, even at a lower severity than the >15% "your stock figures are
untrustworthy" alarm.

Not fixed now: `domain/tiers.py` may still be read by a running agent, and `alert`
is consumed by the bot, which has not been built. Phase 2 change.

### 8A.4 Operational lesson: agents must not share a database

One agent rebuilt the shared `cafeops.db` mid-run while another was verifying
against it. The second agent committed a count against an intermediate state
(opening anchors, no movements yet) and read a nonsense **+138%** drift before
noticing, cleaning up the stray row, and re-running in isolation.

No lasting damage, and the agent caught it itself — but the cause was my
instruction, not its mistake: the briefs said "set up your working database" without
saying *whose*. Any future fan-out that touches the database must isolate via
`CAFEOPS_DATABASE_URL`, and `docs/phase1/README.md` should say so.

One consequence to remember: **`uv run cafeops drift --backfill` must be re-run
after any reseed.** Drift observations are derived from counts and are wiped with the
database, and the ordering path depends on them.

---

## 8B. Two Phase 0 importer bugs, found by Agent B and fixed

Both were mine, both silent, and both would have produced a system that looked
configured and behaved as if empty.

### 8B.1 The importer wrote no `manual_recipe_line` rows at all

Every imported item starts `manual_recipe=True` (spec §6: template assignment waits
for a human to confirm a proposal), and spec §4.3 rule 6 says a manual item resolves
through `manual_recipe_line`. Phase 0 staged all 1,577 recipe lines into
`legacy_staged_recipe` and then **never turned them into recipe rows**.

Consequence: 278 of 320 menu items had no resolvable recipe. They cost nothing and
depleted nothing. Nothing errored — the items existed, had prices, and returned an
empty recipe. This is exactly the failure mode the spec warns about, dressed as
success.

Fixed in `seed/legacy.py::_write_manual_recipe_lines`. Duplicate (item, ingredient)
pairs within one recipe are **summed** (two milk rows in one drink are additive);
cross-recipe duplicates cannot reach it because pass 2 already ignores redundant
recipe numbers. Items already driven by a template are skipped rather than given
manual lines, which would double-count.

### 8B.2 Prices were effective-dated at import time

All 113 `ingredient_price` rows carried `effective_from = now`, so **any cost lookup
before today returned `None`** — breaking historical costing, back-dated P&L, and
any rollup at a past date. Since expansion resolves each sale at its own `sold_at`,
this quietly made the entire 60-day history uncostable.

Fixed: `LEGACY_EFFECTIVE_FROM = 2025-11-01`, the café's opening (spec §1). The
workbook describes what was true for the whole trading history we have, so that is
the date it gets. `import_legacy` now takes an `effective_from` override and refuses
a future date.

### 8B.3 The fix validates the whole cost pipeline

With both fixed, **all 314 comparable menu items reproduce the workbook's own
"Cost price (£)" column to within 0.01p:**

```
vs workbook Cost price column, 314 comparable items:
  exact  (<0.01p): 314
  close  (<1p)   : 0
  off    (>=1p)  : 0
```

This is the strongest end-to-end check available without a test suite. It exercises
unit parsing, cross-unit conversion (ml→L, g→kg), price import and per-unit
division, manual recipe line construction, and `resolve_recipe`'s cost breakdown —
and agrees with a figure computed independently in Excel by a different person. A
single wrong conversion factor anywhere would show up here.

Coverage afterwards: **312 of 314 items cost and deplete**; the 2 that do not are
`'card' (£3.00)` and `Syrup Gift Set`, both already on §8's data-quality list, and
both genuinely recipe-less.

### 8B.4 `MenuItemCost.cost_pence` made nullable — endorsed

Agent B made `cost_pence` and `cost_source` nullable (migration `6e170c2a69ab`).
`NOT NULL` forced the rollup to either invent a partial sum or write no row, and
invariant 6 forbids both: a missing cost must stay *visibly* missing rather than
becoming zero or vanishing. The downgrade deletes unknown-cost rows rather than
zero-filling them, which is the right direction.

### 8B.5 Still open: the one-off items' costs are the workbook's, not derived

The 43 one-off groups now have manual recipe lines imported from the workbook, so
they cost correctly. But a "recipe" for a bought-in cake is one line naming the cake
itself — the cost is a purchase price, not a composition. That is correct modelling,
not a gap, but it means **margin on those items is only as good as the workbook's
price**, and 42 of 113 prices are estimates.

---

## 8C. A bug in the spec: §5.3 double-counts the weekday effect

Agent D implemented spec §5.3 literally, measured the consequence, and reported it
rather than silently fixing it. That was the right call, and the finding is real.

### The problem

```
base_daily    = EWMA(daily consumption, trailing 28 days, alpha=0.3)
forecast(day) = base_daily * dow_factor[weekday(day)]
```

With `alpha=0.3` the most recent day carries 30% of the weight. A window ending on a
Saturday therefore pulls Saturday's surge into `base_daily` — and then the
multiplication applies Saturday's factor *again*.

Independently measured on the seeded whole-milk history, sliding the 28-day anchor
across seven consecutive days:

| | `base_daily` range | Swing | 7-day order quantity |
|---|---|---|---|
| Spec as written | 6.675 – 8.854 L/day | **32.6%** | 46.7 – 61.5 L |
| Deseasonalised | 7.281 – 7.868 L/day | **8.1%** | 51.0 – 55.1 L |

A third of the order size decided by which weekday the job happened to run on. For
milk that is roughly 15 litres — bought, not drunk, and thrown away. The residual
8.1% is genuine week-to-week noise, which is what EWMA is there to track.

### The fix, applied

`domain/forecast.py` now smooths a **deseasonalised** series —
`EWMA(x_t / dow_factor[weekday(t)])` — then re-applies the factor when forecasting a
specific day. The weekday effect is counted exactly once, and `base_daily` now means
"typical demand on an average day", which is what multiplying it by a weekday factor
already assumed it meant.

`deseasonalise=False` restores the literal spec and adds a `confidence_reasons` entry
saying the value is anchor-sensitive. Default is the corrected form: an order that
changes by a third depending on the day of the week it was computed is not a forecast.

### Two further spec ambiguities Agent D surfaced

**§5.4's middle term.** `days_to_next_delivery_after(target)` is 1 for a walk-in
supplier and 1 for a Mon–Fri supplier, which silently assumes reordering at *every*
delivery opportunity. Order weekly against a 1-day gap and you order a seventh of
what you need — with the literal reading the whole Tesco order came out empty. There
is now an explicit `reorder_cadence_days` parameter: `None` is the literal spec,
an integer is the caller's real interval. `simulate` uses 7 and labels which reading
produced each window. **The owner should confirm the real reorder cadence** — this is
open question 8 below.

**Top-up distribution.** §5.4 says to top up with tier B items "ranked by shortest
remaining cover", which literally read means filling the shortest-cover item to its
ceiling first. That concentrates the entire shortfall on one product — for milk, it
means buying what will be thrown away. The implementation adds one pack per item per
pass in that order instead, and excludes zero-velocity items by name. Documented as
an interpretation the owner can overrule.

---

## 8D. Two more Phase 0 seed bugs, found by Agent D and fixed

### 8D.1 Par ceilings were sizing the orders, and sizing them to fail

The seed set `max_qty = 3 × pack_size` for everything, ignoring throughput entirely.
Whole milk's ceiling of 10.2 L is about 1.4 days of trade. The result: the forecast
asked for 4 packs, the ceiling cut it to 0, and the system correctly ordered nothing
while warning that a stockout was guaranteed. **8 of 17 moving ingredients had a
ceiling below a single cover window of demand** — the par level, not the forecast,
was deciding this café's orders.

Fixed with `seed/demo.py::size_par_levels`, which runs *after* expansion because it
needs observed consumption:

```
cover_days = lead_time + reorder_cadence + safety_days
min_qty    = safety_days × avg_daily                      (the reorder floor)
max_qty    = cover_days × avg_daily × headroom + pack_size
```

The `+ pack_size` is load-bearing and took a second pass to get right. Sizing the
ceiling to the cover window alone still blocked every purchase whenever the pack is
large relative to throughput: a 500-cup pack against 21 cups/day is 24 days of
stock, so a 12-day ceiling can never be satisfied. The ceiling must accommodate the
worst legitimate case — holding almost a full cover window, then buying one whole
pack — or it forbids the smallest purchase the supplier actually sells.

After: **15 of 15 moving ingredients clear a full cover window, and zero orders are
clamped to nothing.** Whole milk now orders 12 × 3.4 L (£28.44) where it previously
ordered nothing. Ingredients with no measured consumption keep the seeded pack
multiple — inventing a throughput-based par for something that has never sold would
be fabricating demand.

### 8D.2 Opening counts were unit-blind

`_opening_counts` wrote 400 for `EACH` and 40 for everything else. For an ingredient
stocked in `ML` that is 40 millilitres of syrup, not a bottle — and being below the
par floor with no sales history made the order builder briefly want to buy one bottle
of **each of 23 syrups, £207**, none of which had ever sold. Agent D hardened the
clamp so neither `min_qty` nor `max_qty` can *create* a line (only adjust one the
forecast already asked for), which is the right defence regardless. The seed is now
unit-aware as well, so the artefact is gone at source.

Both bugs share a shape worth noting: the seed's job is to produce data a human would
recognise as their café. Numbers that are merely *type-correct* let downstream
algorithms produce confident nonsense, and with no test suite the seeded scenario is
the only thing standing in for reality.

---

## 8E. v2: the Qty storage bug — the most important thing in this file

**`Qty` stored exact Decimals as TEXT on SQLite. Every SQL comparison against a
quantity column was silently wrong.**

SQLite's type ordering places TEXT above all numbers, unconditionally:

```sql
SELECT '0.0000' > 0;   -->   1
```

So `WHERE qty_remaining > 0` matched **every fully depleted batch**. The symptom that
exposed it was absurd on its face — `cafeops stock` reporting milk 54 days overdue
while listing batches containing nothing — but the cause was invisible, because:

- Python-side comparisons on loaded `Decimal`s were always correct, so domain logic,
  every hand-check and every agent's verification script agreed with each other.
- It only misfires in SQL, only on some values (`'0'` compared correctly, `'0.0000'`
  did not), and it never raises.

Exactly one line in the codebase hit it (`services/read_stock.py`). That is luck, not
design: six Phase 1 agents are about to write repository code, and `WHERE qty > 0` is
the most natural thing any of them could type.

### The fix

`Qty` now stores a **scaled integer** on SQLite — the value times `10**6` — and a real
`NUMERIC` elsewhere. Exact *and* correctly ordered, so `WHERE qty > 0` means what it
says. Verified: `0`, `0.0000`, `-4.5`, `0.000001`, `123456.789012` and `1.005` all
round-trip identically, and SQL and Python now return the same rows for both `> 0`
and `< 0`.

Two consequences worth stating:

1. **The declared scale is now a real contract.** A value carrying more than 6 decimal
   places is rounded half-up on the way in. That is what "Decimal with explicit scale"
   (spec 4) already meant; it is simply now enforced rather than assumed. Re-verified
   afterwards: 311 of 314 menu items still reproduce the workbook's cost column to
   within 0.01p, and the 3 that do not are the Pistachio Lattes deliberately
   re-pointed to the seeded template.
2. **Postgres and SQLite now hold different representations**, so moving between them
   is a *converting* Alembic branch rather than a dump-and-load. That is the right
   trade: a migration is a one-off with a human watching, whereas a silently wrong
   comparison is forever.

### The general lesson, for whoever reads this next

The first `Qty` implementation was chosen to protect exactness, and it did. It simply
traded an obvious failure (floats losing pennies) for an invisible one (comparisons
lying). When a custom column type changes the storage representation, the thing to
check is not only "does the value come back intact" but "does every operator still
mean what it means".

---

## 8F. v2 additions and the decisions inside them

### 8F.1 Shelf life is seeded as ESTIMATE, and milk is the one that bites

Not in the workbook (spec 15 q3). The owner chose defaults over blocking, so all 113
ingredients get industry norms written with `shelf_life_source = ESTIMATE` and
surfaced on the data-quality screen — the same treatment prices get, for the same
reason (invariant 8). 100 of 113 are perishable.

The consequential one is milk: **7 days less a 2-day transit buffer is a 5-day usable
window**, which caps a 9-day cover down to 5. Milk will be ordered more often and in
smaller quantities than the forecast alone asks for. That is invariant 4 working, and
it is the single most valuable thing shelf life does here.

`NULL` shelf life means "does not expire" (cups, lids, napkins) — a *statement*, not a
gap. An unknown shelf life on a perishable is a different thing and is reported
separately, because a missing cap silently permits the waste the cap exists to prevent.

### 8F.2 Batches are rebuilt by replaying the ledger, not allocated during expansion

`services/rebuild_batches.py`. Deliveries and sales interleave across 60 days and a
batch cannot be allocated before it is received, but expansion runs over the whole
queue at once — so a single chronological replay is the only way to reach a state that
was actually reachable. It is also idempotent and inspectable.

Marked `PHASE 0 SCOPE NOTE` for Agent C with an explicit keep/replace list: keep FIFO
by effective expiry, the sweep-before-allocate ordering, and shortfall-as-data; replace
the replay itself with `receive_delivery` + allocation at sale time.

Three decisions inside it:

- **Sweep before allocating.** Allocating first would quietly sell expired stock and
  the loss would never appear. Ordering is what makes the waste figure honest.
- **A physical count re-anchors the batches too.** A count re-anchors theoretical
  on-hand (spec 5.1), so leaving batches at their pre-count quantities makes the two
  numbers disagree permanently — the demo showed batches holding 200 cups against a
  counted 105. A surplus is removed oldest-expiring first; a deficit becomes a new
  batch. No movement is written, because the count is already the re-anchor and an
  `ADJUSTMENT` would double-count it.
- **One movement keeps one `batch_id`.** A consumption spanning several lots is
  attributed to the lot that supplied the most. Remaining quantities are exact either
  way; splitting one sale into several ledger rows would misrepresent one event as
  several.

Result on the seeded data: 122 batches, ~17,000 FIFO allocations, and **8 expiry
write-offs worth £190.27** — a waste figure that did not previously exist anywhere.

### 8F.3 FIFO is by expiry, not receipt date

A short-dated delivery goes out before older stock with a longer date, which is what a
person standing at the fridge does. A batch with no expiry sorts *last*: cups should
be consumed only after anything that can spoil. Verified, including the subtle case
where an opened 270-day carton with a 3-day open life is consumed before a sealed
10-day one.

### 8F.4 Eight suppliers, six of them with invented terms

Spec 4.4's real set. Only **Tesco and Amazon** have terms I can state truthfully.
Cakesmiths, Brakes, Booker, Cups Direct, Monolith and Nataly carry
`terms_are_placeholders = True`, which is a column rather than a comment precisely so
every order built from them can say so on its face.

**Nataly is unanswered** (spec 15 q5): what it supplies and through what channel is
unknown. Modelled `MANUAL` with a deliberately long 5-day lead so it cannot silently
win a sourcing decision.

Six alternate sources exist so multi-sourcing has a real decision to make, including
two deliberately *worse* options (Monolith beans, Amazon cups) — the sourcing code
needs a case where the alternate loses, not only cases where it wins.

### 8F.5 Labour: £14.50/hr loaded, and the ranking genuinely inverts

Confirmed with the owner. Verified that `margin_per_minute` reorders the menu exactly
as spec 5.6 predicts: a £4.00 drink at 85% margin taking 3 minutes yields 113p/min,
while one at 70% taking 40 seconds yields **420p/min**. The two views disagree, and
the disagreement is the finding.

All labour figures are `None` when prep time or the rate is unset. A labour cost
derived from a guessed rate is a guess wearing a number's clothes.

### 8F.6 Seasons handle a year-wrapping window

`SeasonSpec.contains` and `days_remaining` handle a recurring season that crosses the
new year (a Nov 15 – Feb 28 winter season correctly contains January 10 and computes
49 days remaining). Getting this wrong would either exclude half a season's history
from forecasting or cap an order at a negative number of days.

### 8F.7 Migrations: three real bugs fixed, then squashed

Three separate problems, all found by trying to run the thing:

1. **`NOT NULL` without a DDL default** — SQLite refuses to add such a column to a
   populated table. Every new non-nullable column now carries `server_default`.
2. **`PRAGMA foreign_keys` is silently ignored inside a transaction**, so it cannot be
   turned off from within a migration. Batch mode rebuilds tables by copy-and-drop,
   which FK enforcement blocks. `create_db_engine(..., enforce_foreign_keys=False)`
   now exists for Alembic, and a fresh FK-enforcing connection runs
   `PRAGMA foreign_key_check` afterwards so a rebuild that left a dangling reference
   fails loudly.
3. **`alembic downgrade` exited 0 having changed nothing.** SQLite reports
   non-transactional DDL, so Alembic does not commit and SQLAlchemy 2.0 rolls back on
   close. `connection.commit()` is now explicit.

A **naming convention** is set on `Base.metadata`. Not cosmetic: batch mode refuses to
move a constraint it cannot name, so without it any future migration altering a table
with an unnamed constraint fails with "Constraint must have a name".

Migrations are then **squashed to one**, because nothing is deployed. From first
deployment onward they are additive only. The round-trip is verified: 29 tables →
`downgrade base` → 1 → `upgrade head` → 29, zero FK violations.

### 8F.8 No load balancer

Spec 3 is right and worth restating: 40 transactions a day, two users, one SQLite file
that permits a single writer. A second app instance would contend on the same file and
make things worse. Reverse proxy yes, load balancer no.

---

## 8G. v2 Phase 1: what the four agents found

All four ran against isolated databases after the shared-database incident in v1
(§8A.4). One stalled; see §8G.5.

### 8G.1 Two agents hit the same design flaw, from opposite ends

The stock agent needed to write a `batch_id` onto a ledger row; the composition agent
needed a `season_id` to reach `resolve_recipe`. Neither field existed on the shared
dataclass, so both did the same thing: passed the value through a **side channel**.

That produces the failure this file already warns about in §7.1 — *every caller must
remember*. A caller that omits the argument loses the behaviour **silently**: no
error, no warning, just a batch-less ledger row or a missing out-of-season notice.

Worse, the stock agent's workaround opened a **second writer into `stock_movement`**.
Two writers into an append-only ledger diverge eventually, and that table is the last
place it should be allowed to happen.

Both fixed by putting the field where it belongs: `MovementSpec.batch_id` and
`VariantOptionSpec.season_id` / `MenuItemSpec.season_id`, populated by the repository.
`SqlStockRepository.append_movements` is the single write path again, and
`append_linked_movements` now delegates to it. Verified both paths persist an identical
`batch_id`.

**The general point:** when two independent agents invent the same workaround, the
contract is wrong, not the agents.

### 8G.2 The stock agent corrected its own first cut

`drift_attribution` operates on the **absolute** gap. That is right for the gate, which
should distrust drift in either direction. It is wrong for *attribution*: a negative
gap means the shelf holds **more** than the ledger knows — an unrecorded delivery, not
waste. Booking it as loss would invent expiry that never happened.

It now separates `unexplained_loss_qty` from `surplus_qty` and reports the surplus as
its own sentence. Worth recording because the distinction is easy to miss and the
consequence is a fabricated waste figure.

### 8G.3 `opened_at` is stamped by the first FIFO draw

Open life shortens a batch's effective expiry — a 270-day oat carton lasts 5 days once
opened — but nothing was setting `opened_at`. The stock agent chose the first FIFO draw,
reasoning that drawing from a carton *is* opening it, and that this is the only
mechanism which fires without anyone remembering anything at 06:30.

It explicitly rejected asking at the weekly count: open-life windows are 3–5 days, so
the date would arrive after the stock it was meant to protect had already expired.
`cafeops open-batch` covers a carton opened out of FIFO order, and never moves an
existing `opened_at` — the first opening starts the clock.

### 8G.4 The agent boundary rests on four devices, not a prompt

Spec §9 says the agent must never write to stock, orders or composition. The channels
agent implemented that as four independent mechanisms rather than an instruction:

1. an explicit tool allowlist — no `getattr`, no dynamic import, no fallback;
2. no writable object in scope (`ToolContext` carries read-only repositories only);
3. a connection that **raises** on any non-read statement, catching ORM flush and raw SQL;
4. an audit writer scoped to the single table it is allowed to append to.

All four demonstrated firing: 9 refusals against 1 success, and after 54 log rows
`stock_movement` was unchanged, every PO still `DRAFT`, and `waste_factor` untouched.

Its narration guard is the part worth copying. Rather than trusting the model not to
compute, it **verifies every figure the text contains** against tool-computed values.
Offered a narration containing an invented "GBP 999", it caught it and marked the whole
output untrustworthy. It also found a bug in its own guard: stripping `%` let `18%` past
a small-integer bypass, and a percentage is always a claim, never an incidental count.

### 8G.5 The ordering agent stalled; its work was verified by hand

It died during its final verification script, after the code had landed. Rather than
trust an unreported agent, each deliverable was re-verified directly:

| Claim | Verified |
|---|---|
| Shelf-life cap (invariant 4) | Milk 68.4 L uncapped over 10d → **33.8 L over 5d**, reason on the line and persisted to `po_line.cap_reason` |
| Perishable-safe top-up (invariant 5) | Two perishables **named and excluded** from a min-order top-up |
| Sourcing, alternate wins | Oat milk switched to Tesco, **24% cheaper per litre, £3.60** on the line |
| Sourcing, alternate loses | The deliberately worse alternates (Monolith beans, Amazon cups) correctly kept |
| Invariant 1 | Every order still `DRAFT` |
| `cutoff_time` | A 12:10 order against a noon cutoff slips one day |

### 8G.6 A season-opening under-order, found while verifying the above

`seasonal_forecast` computed year-on-year growth as `current / prior`. With **no sales
yet this season** that is `0 / prior = 0`, which clamped to the `growth_min` floor of
0.5 and **halved the forecast**.

That fires at exactly the wrong moment: the start of a season, when you are stocking up
for demand that has not appeared yet. The result is a guaranteed stockout every season
opening — the one failure the whole system exists to prevent.

The author had already handled the mirror case (`prior <= 0` returns 1.0, with a
docstring saying *"inventing growth from a division by nothing is worse than admitting
there is no growth figure"*) and simply missed the symmetry. A zero cannot distinguish
"the season has not started" from "we dropped this line", so it is not evidence of
decline.

Fixed: `current <= 0` carries last year's level across unscaled. The same guard now
covers a ratio read off fewer than `min_growth_days` (14) — which the author's own clamp
note already called *"noise before it is a trend"*.

Verified the growth maths is otherwise exact on like-for-like data: **1.00x, 1.20x and
0.50x** for flat, +20% and −50%. An earlier alarming result (a forecast rising while
demand halved) turned out to be an artefact of the test data, not the code — the prior
season had only one month populated, so 41 days of current were being compared against
11 days of prior. Worth recording as a caution: a synthetic scenario that is not
like-for-like will slander correct code.

### 8G.7 Three defects in Phase 0's own files

Found by agents, not by me:

1. **`integrations/suppliers/__init__.py` was empty**, so `adapter_for()` raised
   `LookupError` for every channel unless the caller happened to import the `channels`
   module first — the registry is populated by an import side effect. The package now
   registers its adapters on import.
2. **Cups Direct was seeded as `PORTAL`**, which routed dispatch to the "not configured
   yet" adapter. It is a web shop with no API, so the honest channel is `BROWSER_AGENT`
   — the one that fills a basket and stops before checkout.
3. A NULL `expired_qty_in_window` was reported, but had **already been fixed** by
   another agent landing afterwards. Verified rather than re-fixed, which is the point:
   in a concurrent fan-out, a report is a snapshot of a moving tree.

### 8G.8 Still not exercised: the retail emergency path

`tesco_routing` has zero rows after a committed simulation, and `emergency-report`
correctly says so. The logic declines sensibly when Tesco does not stock an item, but
nothing in the seeded data produces a real retail routing — so spec §4.4's most valuable
report, *how often and how much extra panic-buying costs*, has never been proven to
work. Assigned as follow-up work.

---

## 8H. The API shapes differ from the spec, and why

### 8H.1 An integrator error first

The v2 brief contained an **"API shapes"** block — `GET /api/templates/:id`,
`POST /api/templates/:id/preview`, `GET /api/stock`, `GET /api/orders/draft`,
`GET /api/channels`. When I condensed the brief into `CLAUDE.md` I **dropped that
block**, and then told the API agent that §10 "contains the exact API shapes to design
against".

It checked, found no `/api/` string anywhere in the repo's specs, reported the premise
as false, designed the shapes itself from the domain types and the invariants, and told
me to treat them as a proposal rather than a match. That is the right response to a
brief that does not match the repository, and it is worth recording because the failure
was mine: **summarising a spec lost normative content, and I then cited the summary as
authority.** The block is restored as `CLAUDE.md` §10.9 with a pointer here, and §10.9
now carries a warning that condensing has already lost content once.

### 8H.2 Keeping the divergent shapes, deliberately

Nothing was lost in substance — every spec field has a home, and the implementation
carries considerably more. The structural difference is that it **nests** what the spec
flattened:

| Spec field | Actual location |
|---|---|
| `items[]` | `rows[]` |
| `id` | `ingredient_id` |
| `theoretical_qty` | `on_hand.qty` |
| `last_count_qty` | `on_hand.basis_count_qty` |
| `last_counted_at` | `on_hand.basis_counted_at` |
| `drift_pct` | `drift.drift_pct` |
| `drift_attribution: {measurement_pct, expiry_pct}` | `drift.attribution` — richer: names the CAUSE and the ACTION, not only the split |
| `status: trusted\|drifting\|excluded` | `drift.verdict` + `drift.gate_action` (domain vocabulary) |
| `projected_runout_date` | `run_out.on`, alongside `days`, `daily_rate_qty` and the forecast that produced them |
| `batches[{id,qty_remaining,expires_at,days_left}]` | same, plus `effective_expiry` and `value_pence` |

**The nesting is the reason three invariants cannot be dropped by a careless view.**
The agent built three schema types that carry their own rules:

- `Cost` — `pence: null` and never `0`, plus `is_estimate`, `is_missing`,
  `excluded_from_aggregates` (invariant 8);
- `Forecast` — when low-confidence, `qty` is **absent from the payload**, so a frontend
  cannot render a number it was never sent (invariant 9);
- `OnHand` — `is_theoretical` and `has_count_basis` are required, not optional
  (invariant 6).

Flattening these back into sibling keys would re-open exactly the *every caller must
remember* failure that has now been fixed three times in this codebase (§8G.1). The
spec's shapes are a sketch of what each screen needs; these are a contract. **The
contract wins, and §10.9 is annotated rather than obeyed.**

One genuine gap the mapping exposes: the spec's `status: trusted|drifting|excluded` is a
*presentation* vocabulary, and the API exposes the domain vocabulary instead. A frontend
would have to map `ELIGIBLE → trusted`, `TUNE_WASTE_FACTOR → drifting`,
`FORCE_MANUAL → excluded` — a trivial mapping, and exactly the kind that gets done
inconsistently in two places. Worth adding a `trust_status` field rather than letting
each screen invent it.

### 8H.3 Verified independently

| Invariant | Check |
|---|---|
| 1, no order write path | Only two POST routes exist, both composition. `purchase_order` count unchanged by every endpoint. |
| Preview writes nothing | `template_component` 10 → 10 across a successful preview; `writes_nothing: true` is on the payload. |
| 11, no float money | A float quantity in the preview body is refused with 422. |
| 6 | Every stock row carries `basis_label`; `is_theoretical` true on all of them. |
| 8 | `'card' (£3.00)` and `Syrup Gift Set` return `pence: null`, `is_missing: true`, `excluded_from_aggregates: true`. 280 of 298 ranked items flagged ESTIMATE. |
| 9 | 40 of 54 rows withhold a run-out entirely, with the reason in place of the number. |
| Auth fails closed | With no password set, `/api/health` 200 and every other route **503** — not 200. |

### 8H.4 Two bugs the agent found in its own work

Both worth recording because both are the same species: a figure that reads as
reassurance when it is not.

1. A **capped order line reported the full-window forecast total labelled with the
   capped day count** — a number matching neither window. Now `full_cover_days`,
   `forecast_full_window_qty` and `capped_out_qty` make the cap a quantity rather than
   only prose.
2. **Previewing a superseded component answered "0 items affected, no warnings"** — it
   computed honestly against a closed row, and a reviewer reads that as *"this edit is
   harmless"* and approves it. Now a 409 naming the current row id.

It also added `is_capped` / `cap_reason` to skipped lines, because a cap can shorten the
window until stock already covers it: the cap applies and then there is nothing to
order. Without that, an absent line is indistinguishable from "not needed" — and an
unexplained absence is what gets overridden by hand.

### 8H.5 Money and quantity encoding

Integer pence where the database stores an integer; an **exact decimal string of pence**
where the figure is derived and fractional (`"77.709059"`). Rounding derived costs to
whole pence would make the margin screen disagree with the recipe, and a float would be
invariant 11's bug. Quantities are strings rounded to the **six decimal places
`db/types.Qty` actually stores** (§8E) — the raw arithmetic produces 28 significant
digits, which claims precision the column does not have.

### 8H.6 Caveats carried forward

- **`cafeops seed --demo` is reproducible within a revision, not across one.** Two
  builds either side of the disrupted-supplier-round change gave 117 vs 127 batches.
  §10's "deterministic, verified by rebuilding twice" holds only for a fixed tree, so
  anything pinned to seeded figures — including `web/fixtures` — needs regenerating
  after a seed change.
- **Whether a shelf-life cap produces a line is day-dependent.** On some dates milk is
  already covered over the shortened window, so the cap appears under `skipped`. Both
  fixtures exist so the frontend can build against either.
- **No CSRF, no rate limiting.** A header credential rather than a cookie, behind a
  reverse proxy, two users. Recorded as a decision rather than an omission.

---

## 8I. The one lesson worth carrying out of this project

**A fact that exists only as an English sentence will be re-derived, badly, by every
surface that needs it.**

This failure appeared four times, in four unrelated places, found by four agents who
never spoke to each other:

| Where | The workaround it forced |
|---|---|
| A ledger row needed to name its batch | A **second writer** into `stock_movement`, an append-only table |
| Resolution needed to know an option's season | An optional argument; omit it and the out-of-season warning vanishes silently |
| An emergency line needed a non-negative premium | Every call site clamping it, and one that forgot would make a week of panic-buying look like a *saving* |
| The Russian bot needed to know *why* a line was capped | **A regex over prose this codebase itself wrote** — so a reword upstream silently broke the owner's explanation |

Each was reported as "please add a field", and each time the tempting reading was that
the agent had been lazy. It was the opposite: four independent engineers reaching for the
same workaround is evidence that **the contract was wrong, not the callers.**

The fixes all had the same shape — put the rule on the value:

- `MovementSpec.batch_id`, so there is one write path into the ledger again.
- `season_id` on the composition specs, so the warning cannot be dropped by forgetting
  an argument.
- `EmergencyLine.premium_pence` floors at zero *on the type*, with `raw_premium_pence`
  and `retail_is_cheaper` preserving the signed fact as a separate, arguable finding.
- `CapKind` and `LowConfidenceKind` enums **derived from the same comparisons that build
  the sentence**, never parsed back out of it.

The prose stayed in every case. A human reading a log wants the sentence; code must never
depend on it. The test is simple: *if someone rewords this string, does anything break?*
If yes, the meaning is in the wrong place.

### Why it mattered more here than it usually does

Three of the four sat directly on an invariant, and all three failed *quietly*:

- The silent one (a batch-less ledger row) meant stock that could never expire and could
  never be counted as waste — it would simply have vanished from the P&L.
- The forgettable one (season) meant a seasonal item forecast against 28 days of
  out-of-season history.
- The reworded one (cap kind) meant an unexplained cap, and **invariant 4 cannot survive
  an unexplained cap**: the owner raises the quantity and recreates exactly the waste the
  cap prevented.

None of them would have raised an error. All three would have produced confident,
plausible, wrong numbers — which is the same species as §8E's TEXT comparison and §8C's
double-counted weekday. That species is what this project has to be defended against,
and it is why the no-test decision (§1) is expensive here specifically: nothing
re-derives these answers and notices they changed.

---

## 8J. Baseline: invariants verified on a fresh build

Run after every agent wave, from `rm -f cafeops.db && alembic upgrade head &&
cafeops seed --demo && cafeops drift --backfill`:

```
PASS  1  order needs a named human     0 violations
PASS  2  auto-order earned + audited   6 enabled, all with granted_at
PASS  7  waste_factor set and > 0      0.1 on whole milk
PASS  8  missing cost is NULL not 0    0 zeroed
PASS  8  estimates stay flagged        292 items flagged ESTIMATE
PASS  12 ledger shape sane             no unexplained positive SALE rows
PASS     batches match the ledger      0 ingredients with a coverage gap
PASS     expiry write-offs exist       9 EXPIRED movements
```

The batch-coverage line is the one that regressed silently before (§8G, `record_count`
not re-anchoring), so it is worth checking on every build rather than only when something
looks wrong.

---

## 9. Module layout deviations

| Spec | Actual | Why |
|---|---|---|
| `tests/fixtures/` with factories and a seeded scenario | `cafeops/seed/` | With no test suite, fixtures have no home. The seeded scenario is a shipped CLI feature, which is what §14 actually asks to demonstrate. |
| — | `domain/units.py` | Five units make conversion a real concern with real failure modes. |
| — | `services/read_stock.py` | One place assembles "on-hand + its basis", so invariant 4 is kept once rather than re-derived by the CLI, the bot digest and the API, each getting the labelling subtly different. |
| — | `seed/roles.py`, `seed/patterns.py`, `seed/report.py` | Spec §6 pass 3 is a substantial piece of work and does not belong in one file with the importer. |
| — | `integrations/suppliers/` | See §4. |
| `domain/composition.py`, `domain/stock.py`, `services/expand_recipes.py` are agent-owned | Implemented in Phase 0 | §14's deliverables cannot exist without them. All carry a `PHASE 0 SCOPE NOTE`. Agents extend rather than restart. |

`domain/` is pure as required: no SQLAlchemy import, no I/O, no `config` import.
`mypy --strict` passes on `domain/` and `services/` with **zero `type: ignore`
comments** — an earlier draft had eleven, and fixing the underlying typing was the
right answer rather than silencing them.

### 9.1 Phase 0 did not build

Lightspeed client and sync, drift computation, tier gating, forecasting, order
sizing, the Telegram bot, APScheduler jobs, the read-only API, the React frontend,
`cli simulate`, and the materialisation of any template proposal. `cafeops info`
prints this list so the gap is never mistaken for completeness.

---

## 10. The demo seed

`cafeops seed --demo` produces: the `Flavoured Latte` template with 3 flavours × 3
sizes = 9 sellable items, 3 alt-milk modifiers, 3 suppliers, 113 supplier products,
54 par levels, then 60 days ending yesterday — ~1950 receipts (≈32/day, inside spec
§1's 25–40), ~2830 sale lines, ~17k movements, 60 deliveries and 72 periodic counts.

Deterministic: seeded RNG, verified by rebuilding twice and comparing the on-hand
output digest.

Three deliberate choices:

- **`simulate_restocking` runs after expansion**, reading the SALE movements to
  decide when stock would have run low. Without it the ledger only ever goes down
  and every on-hand figure is a large negative number that teaches nothing.
- **Synthetic counts carry injected drift**, chosen so §5.2's gate has ingredients
  on both sides: several tier-A items under 10%, `Napkin` at 12% (tuning band),
  `16oz paper cup` at 19% (must be refused). Agent C gets a failing case for free
  rather than inventing one.
- **Sales are lattes only**, so `Chocolate powder` and `Matcha powder` show zero
  movements. That is honest rather than a gap: the latte template is the only
  materialised one, and inventing consumption for unmodelled drinks would make the
  drift numbers meaningless.

---

## 11. Open questions

0. **What does "Nataly (custom)" supply, and through what channel?** (spec §15 q5,
   unanswered.) Currently a MANUAL supplier with an invented 5-day lead.
1. **Six suppliers' terms are invented** — Cakesmiths, Brakes, Booker, Cups Direct,
   Monolith, Nataly: lead time, delivery weekdays, cutoff, minimum, free-delivery
   threshold (§8F.4). This is now the largest single blocker to trusting any order
   quantity, because v2 builds one order per supplier and each one's cover window
   depends entirely on its lead time.
1b. **Shelf lives are ESTIMATE defaults, not measurements** (§8F.1). They cap order
   size (invariant 4), so a wrong one either wastes stock or causes a stockout. The
   ~15 perishables that actually move are worth confirming first.
2. **ANSWERED — Do K-Series sale lines carry modifiers? Only on the wrong
   endpoints.** Agent A's research (api-docs.lsk.lightspeed.app,
   api-portal.lsk.lightspeed.app, k-series-support) found a split:

   - The **financial/reporting** endpoints a nightly date-range sync would
     naturally use — `Get Sales`, `Get business day sales`, `Get Receipt by
     External Reference`, the `Transaction Details` webhook — carry **no modifier
     data** in their documented schemas.
   - Modifiers appear only on the **real-time operational** surfaces: `Get All Open
     Checks` and the online-ordering `Order notification`, as
     `modifiers: [{name, quantity}]` — with **no modifier id and no price**.

   Three consequences:

   - **The oat milk hold stays.** A batch sync over `Get Sales` would silently miss
     every modifier — the worst failure mode available, because it looks exactly
     like success. Oat milk reaching tier A requires the integration to *also*
     consume the checks/order-notification stream, which is a separate, stateful,
     real-time concern rather than a nightly job.
   - **Matching must be by modifier NAME, not id.** No endpoint supplies a stable
     `lightspeed_modifier_id`. `modifier.lightspeed_modifier_id` stays in the schema
     (it costs nothing and a future API may populate it) but nothing may depend on
     it. Agent A built the mapper name-matching-first for this reason.
   - This is a genuine product limitation, not an implementation gap. It should be
     stated to the owner plainly: alt-milk consumption cannot be tracked from
     end-of-day sales data alone.

3. **ANSWERED — K-Series does NOT expose ingredient-level recipes.** It has a
   UI-only Recipes feature (Inventory app → Produce → Recipes) with no public REST
   surface. Quoted verbatim from the API portal's inventory-management guide:
   *"Inventory levels are not currently available via the APIs. The only way to
   maintain accurate inventory levels is to monitor the sales data..."* Third-party
   tools (e.g. Apicbase) own recipe/BOM data separately and exchange only sales and
   stock totals with K-Series.

   **This settles the central design question in our favour:** composition is the
   source of truth, not a cache of POS data, and the legacy workbook import is the
   right way to seed it. Spec §13.1's "propose reading them instead of duplicating"
   is moot — there is nothing to read.

   Two caveats Agent A flagged: the OAuth2 token URL in `client.py` is a
   best-effort default (the published tutorial only shows the sandbox realm
   `auth.lsk-demo.app`) and is marked unconfirmed for production; and no published
   K-Series rate limit was found, so the client relies on
   `settings.lightspeed_rate_limit_per_second` plus generic backoff.
4. **Lightspeed product mapping** — mostly solved. Agent A's matcher resolves
   316 of 320 menu items on name+size. The 4 it refuses to guess at are
   `'card' (£3.00)`, `Syrup Gift Set`, `Strawberry bliss` (a probable typo) and
   `Blue Honey Matcha [M]` — three of which §8's data-quality list already names.
   Refusing to guess is correct: a wrongly-matched item depletes the wrong
   ingredient forever.
5. **Two workbook defects** (§8.1) need fixing in the spreadsheet, not in code.
6. **Waste factors are initial guesses**: 0.10 milks, 0.05 beans, 0.03 powders, 0.01
   packaging and sundries. §5.1 says they are tuned from observed drift. Nothing
   tunes them yet — that is Agent C's drift report.
7. **What is the real reorder cadence?** §5.4's literal reading assumes reordering
   at every delivery opportunity (see §8C). `simulate` assumes weekly. If Sasha
   actually walks to Tesco most mornings, the Tesco cover window is ~1–2 days, not 9,
   and her orders should be much smaller and more frequent than the simulation shows.
   This single number changes every quantity on every Tesco order.
8. **The 46% COGS figure is untrustworthy** and will stay so while 42 of 113 prices
   are estimates. Replacing estimates with invoices is the highest-value data task
   available to the owner.
