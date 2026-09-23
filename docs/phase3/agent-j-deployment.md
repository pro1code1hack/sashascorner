# Agent J — Deployment, operations and the first-run story

**Owns:** `Dockerfile`, `docker-compose.yml`, `deploy/`, `README.md`, `Makefile`,
`.env.example`, and a new `docs/OPERATIONS.md`

There is no way to run this on a box yet, and no document that tells a person how to
start. That is the gap between "the code works" and "the café uses it".

## Read first

`CLAUDE.md` §3 (stack and deployment — **read the load-balancer paragraph carefully**),
§11 (commands), §13 (invariants), `ARCHITECTURE.md` §6 (portability) and §8F.7
(migrations: three real bugs, and why Alembic runs with FK enforcement off).

## What spec §3 actually asks for

> One VM. Caddy or nginx terminating TLS, FastAPI behind it, the bot and the scheduler as
> **separate systemd units**, SQLite on local disk with a **nightly off-box backup**.

And explicitly **no load balancer**: 40 transactions a day, two users, one SQLite file
with a single writer. A second app instance would contend on the same file and make
things worse. Do not add one, and do not add Postgres, Redis or a queue. If you think
something is needed, say why rather than adding it.

## Deliverables

1. **`Dockerfile`** — Python 3.12, `uv`, non-root user, the SQLite file on a mounted
   volume rather than inside the image. Multi-stage if it genuinely helps.
2. **`docker-compose.yml`** — three services from one image with different commands:
   `api` (`cafeops serve`), `bot` (`cafeops bot-run`), `scheduler`. Plus Caddy
   terminating TLS in front of `api` only. **The bot and scheduler must not be reachable
   from outside.**
3. **`deploy/` systemd units** for the non-Docker path, since spec §3 names systemd.
   Restart policies, and the DB on local disk.
4. **A nightly off-box backup that is actually safe.** `cp` of a live SQLite file in WAL
   mode is not a backup — use `sqlite3 .backup` or `VACUUM INTO`, and **verify the copy
   opens and passes `PRAGMA integrity_check`** before rotating. A backup nobody has
   restored is a hope, not a backup, so include a documented restore path and prove it
   works once.
5. **`README.md`** — what this is, and the shortest path from a clone to a working
   install: migrate, import the legacy workbook, seed, run. Honest about what is not
   built (no frontend yet unless another agent has landed it, no Lightspeed credentials,
   six suppliers' terms invented).
6. **`docs/OPERATIONS.md`** — the runbook. Which jobs run when and why they are
   idempotent; **`cafeops drift --backfill` must be re-run after any reseed**; how to
   receive a delivery; what to do when drift exceeds 15%; how to restore; what to check
   when a screen is blank (the API fails closed with no `CAFEOPS_API_PASSWORD` — every
   route but `/api/health` answers 503, which is deliberate).
7. **`.env.example`** brought up to date — every setting that now exists, grouped, with
   the ones that are mandatory marked. Check `cafeops/config.py` for the real list.

## Secrets discipline

No secret in an image, a compose file, or git. `.env` is already git-ignored; keep it
that way. `CAFEOPS_API_PASSWORD` unset means the API refuses to serve — document that as
a feature, not a fault.

## Rules

- **Write NO TESTS.** Verify by actually building and running: `docker compose build`,
  bring the stack up, curl `/api/health`, show the output. If Docker is unavailable in
  this environment, say so plainly and verify everything you can without it rather than
  claiming it works.
- Do not touch `cafeops/`, `web/` or `migrations/` — other agents own those. Changes you
  need there, report instead.
- `uv run ruff check .` must still pass.
