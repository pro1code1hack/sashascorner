# Phase 1 — parallel agent briefs

Shared contracts landed in Phase 0. Each agent gets `CLAUDE.md`, `ARCHITECTURE.md`
and its own brief.

## Rules for all agents

1. **Stay inside your paths.** Listed at the top of each brief. Do not edit
   another agent's files.
2. **`cafeops/domain/types.py` and `cafeops/db/repositories/protocols.py` are
   INTEGRATOR-OWNED.** Need a new field, a new dataclass, or a changed signature?
   **Stop and report it** in your final summary. Do not edit either file. A
   unilateral change there is how five agents produce four incompatible versions
   of one type.
3. **NO TESTS.** The owner instructed none be written, twice, explicitly. Do not
   add pytest/hypothesis/freezegun. Do not create `tests/`. Verify by *running*
   things: the CLI, a throwaway `uv run python - <<'PY'` script, the seeded demo
   database. Show real output in your final summary.
4. **`domain/` stays pure**: no SQLAlchemy import, no I/O, dataclasses in and out.
   `uv run mypy cafeops/domain/ cafeops/services/` must pass strict with **no new
   `type: ignore`** comments. Fix the typing, do not silence it.
5. **Sync by default.** Async only at I/O edges (httpx, aiogram). DB access is a
   sync `Session`; async callers use `asyncio.to_thread`.
6. `uv run ruff check . && uv run ruff format --check .` must pass before you are
   done.
7. **Every schema change gets an Alembic migration.** No exceptions. Generated
   migration files need `import cafeops.db.types` — the template adds it.
8. Money is **integer pence**. Quantities are `Decimal`. `Qty` raises `TypeError`
   if you assign a float — that is deliberate, do not work around it.
9. Respect the nine invariants in `CLAUDE.md` §12. They are the product, not
   decoration.

## Working database — USE YOUR OWN

Agents must NOT rebuild the shared `cafeops.db`. One agent did, mid-run, while
another was verifying against it; the second read a nonsense +138% drift from an
intermediate state before noticing. Reading the shared DB is fine; rebuilding it is
not.

```bash
export CAFEOPS_DATABASE_URL="sqlite+pysqlite:///$PWD/agentX.db"
rm -f agentX.db; uv run alembic upgrade head
uv run cafeops seed --demo          # reads ./sashas_corner_finance__LEGACY_.xlsx via .env
uv run cafeops drift --backfill     # REQUIRED after any reseed; the ordering path needs it
uv run cafeops stock --as-of today --tier A
uv run cafeops import-legacy --dry-run
# when finished:
rm -f agentX.db agentX.db-wal agentX.db-shm
```

Deterministic. 113 ingredients (A=11, B=43, C=59), 113 price rows (42 ESTIMATE),
1577 staged legacy recipe lines, 27 proposed templates, the `Flavoured Latte`
template with 3 flavours × 3 sizes = 9 sellable items, 3 alt-milk modifiers,
~2830 sale lines over 60 days, ~17k movements, 72 periodic counts with
**deliberately varied drift**: several tier-A items under 10%, `Napkin` at 12%
(tuning band), `16oz paper cup` at 19% (must be refused auto-ordering).

## What v2 Phase 0 already gives you

- **29 models**, one squashed migration (round-trip verified), WAL pragmas
- `db/types.py` — `Qty` (scaled integer on SQLite) and `UTCDateTime`.
  **Read `ARCHITECTURE.md` §8E before writing any query against a quantity column.**
- `domain/types.py` — every shared dataclass and enum, including v2's `BatchSpec`,
  `ShelfLifeSpec`, `SeasonSpec`, `LabourCost`, `SupplierTerms`, `SourcingOption`,
  `SourcingChoice`, `SupplierSplit`, `EmergencyLine`, `ExpiryLoss`,
  `DepletionAllocation`, `AgentProposal`
- `db/repositories/protocols.py` — 15 protocols including v2's `BatchRepository`,
  `SeasonRepository`, `SourcingRepository`, `ChannelRepository`, `AgentLogRepository`
- `domain/units.py` — 5 units, dimension-safe exact conversion (raises across dimensions)
- `domain/stock.py` — on-hand, `allocate_fifo`, `find_expiry_losses`,
  `expiry_movements`, `batch_expiry_for`, `drift_attribution`
- `domain/composition.py` — `resolve_recipe` + impact preview · `domain/drift.py` ·
  `domain/tiers.py` (the gate) · `domain/forecast.py` (deseasonalised) ·
  `domain/ordering.py`
- `services/` — ingest_sales, expand_recipes, record_count, edit_composition,
  build_order, materialise_template, rebuild_batches, read_stock
- `seed/` — legacy importer (3 passes), pattern detection (27 proposals), shelf-life
  defaults, eight suppliers with alternates, seasons, demo generator
- `integrations/lightspeed/` (client, mapper, sync, fixtures) ·
  `integrations/suppliers/` (channel adapters)
- ~25 CLI commands

Modules marked `PHASE 0 SCOPE NOTE` are yours to extend, not restart.

## Not built at all yet

`domain/sourcing.py`, `domain/labour.py`, `integrations/channels/`, `agent/`, `bot/`,
most of `jobs/`, `api/`, `web/`. Seasonal forecasting, the shelf-life and season order
caps, and sourcing choice between alternates.

## v2 seeded scenario

113 ingredients (A=11, B=43, C=59) · **100 perishable** with ESTIMATE shelf lives ·
113 price rows (42 ESTIMATE) · 1,577 staged legacy lines → 1,576 manual recipe lines ·
314 menu items · 27 template proposals · `Flavoured Latte` materialised with 3 flavours
× 3 sizes · **8 suppliers** (6 with placeholder terms) + 6 alternate sources ·
2 seasons · ~2,830 sale lines over 60 days · ~17,000 movements ·
**122 batches, 8 expiry write-offs worth £190.27** · 72 counts with varied drift.

Milk's usable window is **5 days** (7 less a 2-day transit buffer) — the shelf-life cap
that matters.

## Two v2 traps worth knowing before you start

1. **`Qty` comparisons.** `ARCHITECTURE.md` §8E. The old TEXT storage made
   `WHERE qty_remaining > 0` match every depleted batch, because SQLite ranks TEXT
   above all numbers. It is fixed, but the lesson stands: when you filter on a quantity
   in SQL, check the result against the same predicate in Python at least once.
2. **`cafeops drift --backfill` after any reseed.** Drift observations derive from
   counts and are wiped with the database; the ordering path depends on them.
