# Agent K — Integrations hardening: the POS edge and the channel edge

**Owns:** `cafeops/integrations/lightspeed/`, `cafeops/integrations/channels/`,
`cafeops/services/ingest_sales.py`, `cafeops/jobs/daily_sync.py`,
`cafeops/jobs/channel_sync.py`

Both integrations work against fixtures and neither has ever met a real API. Your job is
to make the day credentials arrive uneventful, and to answer one question that has been
open since v1.

## Read first

`CLAUDE.md` §4.6 (channel access reality), §4.7 and `ARCHITECTURE.md` §11 items 2 and 3 —
**the K-Series modifier finding**, which is the most consequential thing anyone has
discovered on this project.

## 1. The question: is the real-time modifier stream worth building?

`ARCHITECTURE.md` §11 records, with sources: K-Series exposes modifiers **only** on
real-time endpoints (`Get All Open Checks`, the online-ordering order notification), as
`{name, quantity}` with **no modifier id and no price**. The financial endpoints a
nightly sync would use carry no modifier data at all.

Consequences already accepted: oat milk is **held at tier B** because its consumption is
invisible to an end-of-day sync, and matching must be by modifier **name**.

**Decide whether to build the real-time path, and argue it either way before writing
code.** It is a stateful stream, not a nightly job: it needs polling or a webhook, a
dedupe key for checks that change as a table orders more, and a way to reconcile a check
against the receipt that eventually settles. That is a different class of integration.

**A well-argued "no, and here is what we lose" is a completely acceptable outcome** and
may well be the right one. If you do say no, write down precisely what the café gives up
(alt-milk and extra-shot consumption stays uncalculated, so those ingredients can never
earn auto-ordering) and what cheaper thing could partly substitute — for instance, are
the `+upcharge` menu items (`Oat milk (+upcharge)`, see `ARCHITECTURE.md` §4.5) actually
rung up in practice? If they are, they are a usable proxy that arrives through the
ordinary sales feed. **Check before assuming**; that double-counting risk is recorded and
unresolved.

## 2. Make the POS edge survive first contact

Against recorded fixtures only — **never a live call**:

- A window that spans a token expiry (refresh mid-run and continue, do not restart).
- A 429 with and without `Retry-After`; a 500 that then succeeds; a connection reset
  mid-page.
- A page cursor that repeats or goes missing — pagination must terminate rather than loop.
- A receipt that arrives twice in one window, and a line whose quantity changes between
  syncs (the ledger is append-only: emit an `ADJUSTMENT`, never mutate).
- A clock-skew window where `sold_at` is slightly in the future.
- **`cafeops sync` must be safe to run twice, always.** Prove it again after your changes.

Add a `--dry-run` that reports what would be ingested and writes nothing, because the
first real run should be inspectable before it touches the ledger.

## 3. Make the channel edge survive a real export

The CSV source is the path that works today — the owner has portal logins and exports by
hand. Real exports are messier than fixtures:

- BOM, `;` or `\t` delimiters, CRLF, quoted thousands separators (`"1,234.56"`),
  `£` in a numeric column, a trailing totals row, blank trailing lines, dates as
  `DD/MM/YYYY` **and** `YYYY-MM-DD`.
- A file re-exported with an extra column, or columns reordered.
- An overlapping date range re-imported — must be idempotent on `(channel, date)`.

**Refuse rather than guess**, and say which column and which row. A silently
mis-mapped revenue column becomes a business decision. Keep `net_pence`'s discipline:
`None` when commission or ad spend is unknown, never a partial subtraction.

## Rules

- **Write NO TESTS.** No pytest/hypothesis/freezegun, no `tests/`. Verify by running
  against fixtures and showing real output.
- **Never make a live API call** to Lightspeed, Deliveroo, Just Eat or a supplier portal.
  No credentials exist; if one appears in the environment, do not use it.
- **Do not edit `cafeops/domain/types.py` or `cafeops/db/repositories/protocols.py`** —
  another agent is editing them right now. Report what you need instead.
- **Use an ISOLATED database:**
  `export CAFEOPS_DATABASE_URL="sqlite+pysqlite:///$PWD/agentk.db"`, then
  `rm -f agentk.db && uv run alembic upgrade head && uv run cafeops seed --demo && uv run cafeops drift --backfill`.
  Never rebuild the shared `cafeops.db`. Delete yours when done.
- `uv run ruff check . && uv run ruff format --check .` and
  `uv run mypy cafeops/domain/ cafeops/services/` must pass with zero new `type: ignore`.
- Do not touch `web/`, `cafeops/api/`, `cafeops/bot/` or deployment files.
