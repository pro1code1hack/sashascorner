# Cafe Ops

Stock, composition and auto-ordering for **Sasha's Corner**, a single-site café in
Dundee. Replaces a head full of stock levels and a hand-maintained spreadsheet with:
POS sales → recipe resolution → theoretical stock depletion → shelf-life-constrained
par levels → draft purchase orders, split per supplier, confirmed one-tap in Telegram.

Full domain spec: [`CLAUDE.md`](CLAUDE.md). Decisions and findings that differ from or
extend it: [`ARCHITECTURE.md`](ARCHITECTURE.md) — read that one too before assuming a
detail in this README is the whole story. Day-to-day running of a live deployment:
[`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## What this is, honestly

- **The domain, seed, jobs, bot and read-only API are built and run.** ~25 CLI
  commands, a Telegram bot (Russian — the owner's daily interface), an APScheduler
  process, and a FastAPI read-only API.
- **No frontend yet.** `web/fixtures/` holds 19 real API responses a React app can be
  built against, but the app itself has not landed as of this file. Check `web/` —
  if it now has a `package.json` and a build, another agent has since shipped it and
  this paragraph is stale; there is nothing here to serve it in Docker/Caddy yet
  either way (see `docs/OPERATIONS.md`).
- **No Lightspeed credentials.** Every `lightspeed_*` setting is optional and
  `cafeops sync` defaults to `--fixtures`. Nothing has ever touched the live POS.
- **Six of eight suppliers' terms are invented placeholders** (lead time, delivery
  days, minimum order — Cakesmiths, Brakes, Booker, Cups Direct, Monolith, Nataly).
  Every order built from one says so on its face (`terms_are_placeholders`). Confirm
  them with each supplier before trusting an order quantity — see
  `ARCHITECTURE.md` §11.
- **Shelf lives are seeded ESTIMATEs**, not measurements, for all 113 ingredients.
  They cap order size (invariant 4), so a wrong one either wastes stock or causes a
  stockout. Milk's usable window (5 days) is the one that matters most.
- **No tests.** Owner's instruction, repeated and explicit — see `ARCHITECTURE.md` §1
  for what that costs and how the sharpest invariants were pushed into places that
  don't need a test (a `CHECK` constraint, a repository that raises) instead.

## Stack

Python 3.12, `uv`, SQLAlchemy 2.0, Alembic, SQLite (WAL), FastAPI, aiogram 3,
APScheduler, Anthropic SDK for the bounded agent. See `CLAUDE.md` §3.

**Deployment is one VM: Caddy terminating TLS in front of the API, the bot and the
scheduler as separate processes, SQLite on local disk, a nightly off-box backup. No
load balancer, no Postgres, no Redis, no queue** — 40 transactions a day and one
SQLite file with a single writer make all four actively harmful rather than merely
unnecessary. See `CLAUDE.md` §3's load-balancer paragraph and
`ARCHITECTURE.md` §8F.8 before adding any of them.

## Shortest path from a clone to a working install

```bash
git clone <this repo> && cd sashascorner_automation
uv sync                                    # installs cafeops + its dependencies
cp .env.example .env                       # fill in CAFEOPS_API_PASSWORD at minimum
uv run alembic upgrade head                # creates cafeops.db, 29 tables

# Either the real café's data, or a synthetic demo — pick one:
uv run cafeops seed --workbook sashas_corner_finance__LEGACY_.xlsx --demo
#   ^ imports 113 ingredients / 1,577 recipe lines from the legacy workbook, then
#     layers 60 days of synthetic sales, deliveries and counts on top so there is
#     something to look at immediately. Real café use starts from --workbook alone
#     (no --demo) and lets `cafeops sync` bring in real Lightspeed data instead.

uv run cafeops drift --backfill            # REQUIRED after any reseed -- see below
uv run cafeops info                        # sanity check: counts, what's configured
uv run cafeops stock --as-of today --tier A
uv run cafeops serve                       # the API, http://127.0.0.1:8000/api/docs
```

**`cafeops drift --backfill` must be re-run after any reseed.** Drift observations are
derived from physical counts and are wiped along with the database on every reseed,
and the auto-order gate (`ARCHITECTURE.md` §8A) and the ordering path both depend on
that history existing. Forgetting this step is the single most common way this system
looks broken right after a fresh seed — every tier-A ingredient reads as ineligible
for auto-ordering because, from the gate's point of view, no count has ever happened.

Every command has `--help`. `cafeops --help` lists all ~25; the ones a first run
touches are `import-legacy`, `seed`, `drift`, `stock`, `sync`, `serve`, `bot-run`,
`jobs`, `info`.

## Running it for real: two supported paths

Both are documented in full in `docs/OPERATIONS.md`. In short:

### Docker Compose

```bash
docker compose build
docker compose run --rm api alembic upgrade head
docker compose run --rm api cafeops seed --workbook sashas_corner_finance__LEGACY_.xlsx
docker compose run --rm api cafeops drift --backfill
docker compose up -d
curl -s http://localhost/api/health
```

Four services from one image (`api`, `bot`, `scheduler`, `backup`) plus `caddy`. Only
`caddy` publishes a host port; `api`, `bot`, `scheduler` and `backup` have **no**
`ports:` entry in `docker-compose.yml` at all, so there is no way to reach the bot or
the scheduler from outside the box even by accident — see that file's header comment.

### systemd (the non-Docker path spec §3 names)

Five units in `deploy/systemd/`: `cafeops-api`, `cafeops-bot`, `cafeops-scheduler`
(the three long-running services, each independently restartable), and
`cafeops-backup.service` + `.timer` (the nightly backup). Caddy or nginx is installed
separately as an OS package and proxies to the API on `127.0.0.1:8000`. Full install
steps, unit-by-unit, are in `docs/OPERATIONS.md`.

## Secrets

**No secret is in this image, this compose file, or this git history.** `.env` is
git-ignored; `.env.example` documents every setting `cafeops/config.py` reads, grouped,
with the operationally-mandatory ones marked `[REQUIRED ...]`. The one worth knowing
before anything else:

**`CAFEOPS_API_PASSWORD` unset means the API fails closed** — every route except
`/api/health` answers `503`, not `200` with no auth. That is deliberate (spec §1: a
single shared password, no user management) and is the first thing to check when a
screen that should show data shows nothing. See `docs/OPERATIONS.md` "blank screen".

## Development

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy cafeops/domain/ cafeops/services/     # strict, zero type: ignore
```

No `pytest` — see `ARCHITECTURE.md` §1 for why, and what is verified by hand instead.

## Makefile

`make help` lists one-line aliases for the commands above plus the Docker Compose and
backup/restore ones — nothing in it does anything you couldn't type yourself from
this file or `docs/OPERATIONS.md`.
