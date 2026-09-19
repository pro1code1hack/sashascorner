# ARCHITECTURE

Decisions taken during Phase 0 that **differ from or extend** `CLAUDE.md`, and why.
If a choice here is wrong, this is the file to argue with.

Status: **Phase 0 complete and committed.** Phase 1 agents A–D running; Agent E
(bot & jobs) held until their contracts settle.

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

1. **CakeSmiths and Cups Direct terms** — lead time, delivery weekdays, minimum
   order. Currently invented (§8.2). Blocks trustworthy order sizing.
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
7. **The 46% COGS figure is untrustworthy** and will stay so while 42 of 113 prices
   are estimates. Replacing estimates with invoices is the highest-value data task
   available to the owner.
