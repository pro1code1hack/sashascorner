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

## Working database

```bash
rm -f cafeops.db && uv run alembic upgrade head
uv run cafeops seed --demo          # reads ./sashas_corner_finance__LEGACY_.xlsx via .env
uv run cafeops stock --as-of today --tier A
uv run cafeops import-legacy --dry-run
```

Deterministic. 113 ingredients (A=11, B=43, C=59), 113 price rows (42 ESTIMATE),
1577 staged legacy recipe lines, 27 proposed templates, the `Flavoured Latte`
template with 3 flavours × 3 sizes = 9 sellable items, 3 alt-milk modifiers,
~2830 sale lines over 60 days, ~17k movements, 72 periodic counts with
**deliberately varied drift**: several tier-A items under 10%, `Napkin` at 12%
(tuning band), `16oz paper cup` at 19% (must be refused auto-ordering).

## What Phase 0 already gives you

- All 22 models, initial migration, WAL pragmas, `Qty`/`UTCDateTime` portable types
- `domain/types.py` — every shared dataclass and enum
- `domain/units.py` — 5 units, dimension-safe exact conversion
- `domain/stock.py` — `theoretical_on_hand`, `apply_waste`, `depletion_movements`
- `domain/composition.py` — **`resolve_recipe`**, implementing spec §4.3 rules 1–6
- `db/repositories/` — `ingredient`, `composition` (effective dating), `stock`, `sale`
- `services/expand_recipes.py`, `services/read_stock.py`
- `integrations/suppliers/` — `OrderChannelAdapter` + 4 channels
- `seed/` — legacy importer (3 passes), pattern detection, demo generator
- `cli.py` — `import-legacy`, `seed`, `stock`, `expand`, `ingredients`, `info`

Modules marked `PHASE 0 SCOPE NOTE` are yours to extend, not rewrite from scratch
unless you can say why.
