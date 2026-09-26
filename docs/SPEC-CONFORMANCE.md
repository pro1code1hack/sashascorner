# Spec conformance — §1 to §13, with the command that proves each

Measured against the running system on 2026-09-24, not asserted. Every row names a
command you can re-run. Where the implementation deliberately differs from the brief,
the row says so and points at the reasoning.

---

## §1 Context · §3 Stack and deployment

| Claim | Evidence |
|---|---|
| Python 3.12 / uv / SQLAlchemy 2.0 typed / Alembic / SQLite WAL / Pydantic v2 / aiogram 3 / APScheduler / FastAPI | `uv run cafeops info` |
| ruff + mypy **strict** on `domain/` and `services/`, zero `type: ignore` | `uv run ruff check . && uv run mypy cafeops/domain cafeops/services` → 26 files, clean |
| One VM, Caddy terminating TLS, FastAPI behind it, bot + scheduler as separate units, SQLite on local disk, nightly off-box backup | `docker compose ps` → api, scheduler, bot, backup, caddy. Only caddy publishes a port |
| **No load balancer** (deliberate — one SQLite writer) | `docker-compose.yml`: one `api` service |
| Async only at I/O edges; sync DB access via `Session`; handlers reach DB through `asyncio.to_thread` | `cafeops/bot/deps.py:run_sync` |
| Pragmas WAL / busy_timeout / foreign_keys / synchronous | `cafeops/db/base.py:_set_pragmas` |
| **No test suite** (owner's instruction, ARCHITECTURE §1) | none exists, by design |

## §2 The real shape of the menu

| Spec | Measured | Command |
|---|---|---|
| 114 ingredients (spec says measured 113) | **113** | `sqlite3 … "SELECT COUNT(*) FROM ingredient"` |
| ~1,600 recipe lines → 1,577 after removing a duplicated recipe | **1,577** exactly | `SELECT COUNT(*) FROM legacy_staged_recipe` |
| Sizes S, M, XL, One | **M, ONE, S, XL** | `SELECT DISTINCT size_code FROM menu_item` |
| Coffee beans appears 121× | **121** | frequency query, `cafeops ingredients` |
| 27 proposed templates + 43 one-off groups | **27 / 43** | `cafeops proposals` |

## §4 Domain model

29 tables. `Qty` is a scaled integer on SQLite — see ARCHITECTURE §8E before writing
any query against a quantity column. Money is integer pence; a float touching money
raises (invariant 11).

`cafeops doctor --verbose` exercises the model end to end.

## §5 Algorithms — all seven implemented and running

| § | Algorithm | Proof it runs |
|---|---|---|
| 5.1 | theoretical on-hand = last count + Σ movements | `GET /api/stock` returns `on_hand` with its basis |
| 5.2 | drift + **attribution** (measurement vs expiry) | `cafeops drift`; `/api/stock` carries `drift.verdict`, `mean_abs_drift_pct` |
| 5.3 | EWMA + dow factor, **deseasonalised first** (§8C — the literal reading double-counts weekday, 32.6% swing) | draft lines carry `forecast.qty` + `history_days` |
| 5.3 | seasonal forecast from last year's window × YoY growth | `domain/forecast.py:seasonal_forecast`, called from `build_order.py:242` |
| 5.4 | order sizing capped by **shelf life** and **season** | live draft: 2 shelf-life caps, 1 out-of-season skip, 2 par-floor skips, each with its reason |
| 5.4 | min-order top-up **never perishable** (invariant 5) | `domain/ordering.py` |
| 5.5 | one order **per supplier**, sourcing between alternates | live draft: **8 orders**, 4 `sourcing_choices` |
| 5.6 | labour, true margin, **margin per minute** at £14.50/hr | `/api/margin`: `orderings_agree=false`, largest rank move **293 places** |
| 5.7 | pure `resolve_recipe`, effective-dated, **mandatory impact preview** | `POST /api/templates/1/preview` → `affected_item_count: 9`, `writes_nothing: true` |

## §6 Seeding from the legacy workbook

Three passes, and **pattern detection proposes, never writes**. Known dirt is surfaced,
not silently fixed — `'card' (£3.00)` and `Syrup Gift Set` are still in the data, and
**42 of 113** prices remain ESTIMATE.

43 one-off groups stay `manual_recipe`, which is the correct model for them.

### §4.3 seasons — a covered gap worth knowing about

Both consequences the spec demands are implemented and demonstrably work (Pistachio is
skipped out of season; three Pistachio Lattes are pulled from today's menu). But they
resolve through **variant options only**, so an ingredient reached solely by a manual
recipe is never treated as seasonal — and 312 of 320 items are still manual.

`Pumpkin season` is open right now, drives 8 menu items, and is wired to nothing.
`cafeops doctor` now reports this, and names the fix: confirming a template is what
creates the variant options the season rules can see. ARCHITECTURE §8S.

## §7 Layout · §8 Architecture

Everything writes through the **services layer**. Verified mechanically — the bot, the
API and the agent contain **zero** direct database writes:

```
grep -rln "session.add|session.execute(insert|update|delete)" cafeops/{api,bot,agent}/  →  0, 0, 0
```

## §9 The bounded agent

Three jobs behind a whitelist: browser automation, narration, import assistance.

`cafeops agent prove-boundary` → **OK=1, REFUSED=9, 10 rows logged**. `set_auto_order`
refused as off-allowlist; four raw SQL writes refused because the connection is
read-only. Narration degrades honestly without an API key and says so.

## §10 Frontend — eight screens

Build order from the spec, all present: composition → stock → orders → menu margin →
channels → today / money & P&L → **import review**.

32 acceptance checks (8 screens × 1440/1280/1024/375px): zero horizontal page
overflow, no console errors, live API behind the shared password.

Design: the Tailwind zinc ramp **measured** from finsepa.com rather than invented —
see `docs/phase4/DESIGN-LAW.md` and ARCHITECTURE §8N.

§10.9's API sketch is deliberately not matched field-for-field: the real API nests
`Cost`, `OnHand` and `Forecast` into types that carry their own rules, which is what
makes invariants 6, 8 and 9 impossible for a view to drop. Mapping in §8H.

## §11 Commands

35 commands. `cafeops --help`. Added since the brief: `doctor`, `scheduler-run`,
`supplier`, `shelf-life`, `channels`, `agent`, `pos`, `api-fixtures`.

## §12 Build plan

Phases 0–4 complete. Phase 4 (frontend) was rebuilt from a measured reference after
two rejections — ARCHITECTURE §8N.

## §13 Invariants — each traced to an enforcement site

See ARCHITECTURE §8P for the full table. Two findings from that audit:

- **Invariant 12 had an undocumented exception** (`rebuild_batches(purge=True)` deletes
  derived rows) **and no check at all.** Both fixed; `cafeops doctor` now verifies
  ledger integrity by id-sequence gap. Verified both ways.
- Invariant 10 is enforced at three levels: allowlist dispatch, a read-only engine, and
  `agent_action_log`.

## The paper sketches — audited

Found at `~/Downloads/Untitled-2/` (four photos, one a duplicate). They were attached
to the brief but never reached me as chat images; these are the same pages.

### Sketch 1 — "Models / Workflow"

| On paper | Built |
|---|---|
| self cost · prep time · supplier | `menu_item_cost`, `prep_seconds`, `supplier` ✅ |
| quantity · margin · recipes | `Qty`, `/api/margin`, template + manual recipes ✅ |
| menu items · **seasonal** · supplier | `menu_item`, `season`, `supplier_product` ✅ |
| **expiry date** (ringed, emphasised) | `stock_batch.expires_at`, FIFO by expiry, `EXPIRED` movements ✅ |
| Cakesmith · Brakes · Tesco · Booker · Cups Direct · Monolith · Amazon · Custom (Nataly) | all 8 seeded ✅ |

### Sketch 2 — "AI Agent" over four integrations

| On paper | Built |
|---|---|
| **Lightspeed API** → stock management | `integrations/lightspeed`, `cafeops sync` ✅ |
| → best / least positions | `/api/margin`, margin-per-minute ranking ✅ |
| → **payment reports** | `integrations/payments`, `cafeops payments`, `GET /api/takings`, Money screen ✅ (CSV-first — see below) |
| **Just Eat + Deliveroo API** → advertisement KPI · best positions · views | `/api/channels`: ad spend, ROAS, ranks-well-converts-badly, impressions/menu views ✅ |
| **TG Bot** → cash order | the Russian order-confirmation flow ✅ |
| **AI Agent** driving all four | `cafeops agent`, allowlisted, `prove-boundary` ✅ |

### Sketch 3 — "Architecture"

| On paper | Built |
|---|---|
| Stock Management · Finance Report | Stock screen · Money & P&L ✅ |
| AI Browser Agents | `browser_plan_channel_report`, `browser_stage_supplier_basket` ✅ |
| Cloud Scheduler | APScheduler, 15 jobs, `cafeops jobs` ✅ |
| External Integrations | Lightspeed, Deliveroo, Just Eat, suppliers ✅ |
| Web dashboard · DB · log | 8 screens · SQLite · `agent_action_log` ✅ |
| **LB** (load balancer) | **deliberately not built** |

### The two deltas

**1. Load balancer — a documented deviation, not an oversight.** The sketch has an LB
in front. `CLAUDE.md` §3 rejects it with reasoning: one box, ~40 transactions a day,
one SQLite file with a single writer. A second app instance would contend on that file
and make things *worse*. Caddy reverse-proxies; there is no load balancing. Revisit
only if this becomes multi-site.

**2. Payment reports — now built, CSV-first.** This was the one item on the sketches
with no counterpart in the system, and it is **not in the written brief either** —
§4.5 models `sale`, not takings.

Built the way §4.6 already established for channels: a `PaymentSource` protocol with a
CSV implementation, because the Lightspeed payments endpoint has never been probed and
guessing its shape would produce a mapper that silently mis-reads real money.

`cafeops payments import|report` · `GET /api/takings` · the Money screen. Live figures
today: gross £1,430.05 over 8 reported days, card £1,012.70 / cash £402.35 / voucher
£15.00 — and **`net_pence: null`**, because one sample export omits a discounts column
and subtracting only the deductions that were reported would produce a plausible,
too-high net. Invariant 8 applied to revenue. ARCHITECTURE §8T.

**Still needs the owner:** a real back-office export to map against (the shipped
samples are *shaped like* one, not taken from one), or Lightspeed credentials to probe
the payments endpoint with.

## §15 Still open — these need the owner, not the code

1. **Six suppliers' terms are invented.** `cafeops supplier list` shows which;
   `cafeops supplier confirm` records what they tell you.
2. **100 shelf lives are ESTIMATE defaults.** `cafeops shelf-life list` ranks them by
   what actually moves. Shelf life caps order size, so a wrong one wastes stock or
   causes a stockout.
3. **42 prices are ESTIMATE.** `cafeops set-price … --commit`. This is what makes the
   46% COGS figure untrustworthy (§8.8).
4. **§15.5 — what does "Nataly (custom)" supply, and through what channel?** Still
   unanswered. It currently has no delivery weekdays and no minimum.

None of these can be closed by writing code. Inventing the values would silence the
warnings with fiction, which is worse than the warnings.
