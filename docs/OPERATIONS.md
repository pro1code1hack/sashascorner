# Operations runbook

Who this is for: whoever is on call for Sasha's Corner's stock system. Assumes you have
shell access to the VM (or `docker compose` access on it) and have read the "shortest
path from a clone to a working install" section of [`README.md`](../README.md) once.

Two facts surprise people more than anything else here. Read these two paragraphs even
if nothing else:

> **`cafeops drift --backfill` must be re-run after any reseed.** Drift observations
> derive from physical counts, are wiped along with the database on every reseed, and
> the ordering path depends on that history existing. Skip it and every tier-A
> ingredient looks ineligible for auto-ordering, not because anything is broken but
> because, from the gate's point of view, no count has ever happened.

> **The API fails closed.** With no `CAFEOPS_API_PASSWORD` set, every route except
> `/api/health` answers `503`, not `200` with no auth. If a screen that should show
> data is blank, this is the first thing to check — see "Blank screen" below.

---


### Deploying a dashboard change

The frontend is **compiled into the Caddy image**, not bind-mounted. So a change to
`web/` needs an image rebuild, not just a restart:

```
docker compose build caddy && docker compose up -d caddy
```

This is deliberate. A bind-mounted `web/dist` means whoever deploys has to remember to
run `npm run build` first, and a stale or missing bundle serves the previous release — or
a blank page — with nothing saying so. Baked in, the image is either right or it does not
build.

Verified behaviour: `/` and any deep link such as `/stock` both return 200 and serve
`index.html` (single-page apps own their own routing, so an unknown path must not 404);
`/assets/*` serves the hashed bundle; `/api/*` goes to the app and nothing else does.


## Start here: `cafeops doctor`

Run this first, before reading anything else in this file.

```
uv run cafeops doctor            # findings only
uv run cafeops doctor --verbose  # and what passed
```

It inspects the live database and reports three severities:

- **FAIL** — the system is producing wrong numbers or will not run. Nothing else matters
  until these are clear. Exit code 1.
- **WARN** — a real number is resting on a guess worth replacing. Exit code 0: this is
  safe from cron and will not page anyone.
- **INFO** — context, such as running on fixtures because there are no POS credentials.

Each finding carries the command that fixes it.

### What it checks, and why each one is there

| Check | Why it exists |
|---|---|
| schema | An unmigrated database is the commonest fresh-install failure. Gates everything after it, because without tables every later check would raise. |
| legacy import | Without the workbook nothing can be costed, forecast or ordered. |
| drift history | **`cafeops drift --backfill` must be re-run after any reseed.** Drift observations derive from counts, and without them nothing can earn auto-ordering. |
| batch coverage | This regressed *silently* once. Unbatched stock is invisible to FIFO and the expiry sweep, so it can never expire and never be counted as waste — it just vanishes from the P&L. |
| invariant 1 | An order past DRAFT with no named human means a `CHECK` constraint has been bypassed. Investigate; do not clear it by hand. |
| invariant 8 | A missing cost recorded as `0` flatters every margin and COGS figure it appears in. |
| invariant 2 | Auto-ordering enabled with no `granted_at` means it was set by hand rather than earned. |
| supplier terms / shelf life | The two guesses that quietly decide real order quantities. |
| api password | Unset means the API serves only `/api/health` — **the usual reason a dashboard screen is blank.** |

A check that crashes is reported as a WARN naming the exception, and the remaining
checks still run: a doctor that dies on its third check tells you less than one that
says "this check broke" and carries on.

## 1. What runs, when, and why each is safe to run twice

Five triggers, wired in `cafeops/jobs/scheduler.py` and started as the `scheduler`
process (Docker service `scheduler`, systemd unit `cafeops-scheduler`). All times are
**local** (`CAFEOPS_LOCAL_TIMEZONE`, default `Europe/London`).

| Job | When | Idempotency key | What happens if it's skipped or runs twice |
|---|---|---|---|
| `daily_sync` | 02:30 | `sale.lightspeed_line_id` (upsert) | Skipped: no new sales ingested that night, caught by the next run. Twice: no-op, already-seen lines are ignored. |
| `nightly_expand` | 03:00 | `sale.expanded_at` | Must run **after** `daily_sync` — expands whatever sync just ingested into stock movements. Skipped: stock looks stale until it catches up. Twice: already-expanded sales are skipped. |
| `expiry_sweep` | 05:30 | `stock_batch.expired_at` | Must run **after** expansion — otherwise the morning's waste figure is yesterday's. Writes an `EXPIRED` movement once per batch; running it twice writes nothing extra. |
| `pre_delivery_orders` | per supplier, derived from `lead_time_days` / `delivery_weekdays` / `cutoff_time` — see `cafeops jobs` with no `--run` to print the live schedule | open `purchase_order` for `(supplier, date)` | Must run **after** the sweep, or drafts are sized against stock that's already gone in the bin. Twice for the same supplier/date: the second run finds an open draft and sends nothing — see the next row. |
| morning digest | 07:30 | none (read-only) | Reads whatever the drafts step produced. Must run **after** it or the digest reports an empty queue every morning. Safe to run any number of times — it writes nothing. |
| `drift_report` | Monday 08:00 | absence of a `drift_observation` row for a count | Weekly rollup, not the gate itself (the gate runs synchronously on every `cafeops count` / bot count). Safe to re-run. |

**A box that was asleep runs each missed job once, late, rather than seven times or
not at all** (`coalesce=True`, up to 6 hours late). That is only safe because every
job's idempotency key above lives in the data — nothing here depends on wall-clock
timing to avoid double-booking.

Fire one job by hand (useful after an incident, or to test a supplier's schedule
without waiting for the cron):

```bash
uv run cafeops jobs                      # print the live schedule, no side effects
uv run cafeops jobs --run drift_report
uv run cafeops jobs --run pre_delivery_orders --order-date 2026-09-18   # replay a past day
```

## 2. Receiving a delivery

Two ways, both write a `stock_batch` with its expiry and a linked `DELIVERY` movement:

- **Telegram (the normal path):** the owner taps through the delivery flow in the bot.
  `cafeops bot-preview delivery --packs 2 --expiry 30.09` reproduces that flow locally
  without a real Telegram token, for testing.
- **CLI, against a confirmed order line:**
  ```bash
  uv run cafeops receive --po-line 42 --packs 2 --expires 2026-10-03 --by "Sasha"
  ```
- **CLI, ad-hoc (a Tesco walk-in with no order behind it):**
  ```bash
  uv run cafeops receive --ingredient "Whole milk" --qty 6 --price-pence 105 --by "Sasha"
  ```

If no expiry is on the carton, omit `--expires`/press "no date on the pack" in the bot
— the batch gets an **assumed** expiry from the ingredient's shelf-life default, and
every place that shows it says `ASSUMED` rather than pretending it was read off the
box. The batch stays **sealed** (`opened_at` is `NULL`) until the first FIFO draw opens
it; that is what starts the shorter open-life clock, not the delivery itself.

## 3. When drift exceeds 15%

`cafeops drift` (or the bot's drift alert) will already have said so — the gate forces
`auto_order_enabled = False` and raises an alert the moment a count crosses 15%
(`ARCHITECTURE.md` §8A). What to actually do:

1. **Read the attribution before touching anything:**
   ```bash
   uv run cafeops drift --ingredient "Whole milk" --explain
   ```
   This splits the gap into `EXPIRED` write-offs versus unexplained loss. **The two
   have opposite fixes** — over-ordering (too much expiring) needs a smaller order or
   a shorter cover window; a recipe/measurement problem needs the recipe or the
   `waste_factor` corrected. Acting on the wrong one makes the number worse next week.
2. **If it's mostly expiry** (over-ordering): check the shelf-life cap is actually
   biting (`cafeops stock --batches` shows open batches and days left), and consider
   whether the reorder cadence is too generous for that ingredient — see
   `ARCHITECTURE.md` §11, open question 7.
3. **If it's mostly unexplained** (recipe/measurement): check the template's
   `qty_by_size` for that ingredient (`cafeops components <template>`), and check
   whether a `waste_factor` retune is warranted:
   ```bash
   uv run cafeops drift --ingredient "Whole milk" --apply-waste "Whole milk"
   ```
   This adopts the suggested factor as a deliberate retune. **It does not clear the
   gate** — the next two counts have to come back clean on their own before
   auto-ordering can turn back on. That friction is intentional (invariant 2).
4. **The ingredient stays fully manual** until two consecutive counts land under 10%.
   There is no override — `set_auto_order(..., True)` raises outside the gate path by
   construction (`ARCHITECTURE.md` §8A.2), so there is nothing to "force on" even if
   you wanted to.

A count in the 10–15% band **revokes** an existing grant rather than merely warning
(`ARCHITECTURE.md` §8A.1) — if an ingredient that was auto-ordering drops out after a
count, this is why, and it is the correct direction to be wrong in.

## 4. Backups

**A `cp` of the live database is not a backup.** SQLite in WAL mode can have data in
the `-wal` file that hasn't been checkpointed into the main file yet, and a plain copy
of the main file alone can be silently torn. `deploy/backup.sh` instead:

1. Takes a consistent snapshot with `sqlite3 <live> ".backup '<copy>'"` — SQLite's own
   online backup API, safe to run while the app keeps writing.
2. Opens the **copy** (never the live file — that would only prove the app still
   works, not that the backup is sound) and runs `PRAGMA integrity_check`.
3. Refuses to keep, gzip, rotate or ship anything if that check doesn't return `ok`.
4. Ships the verified, gzipped copy off-box via `rsync` if `CAFEOPS_BACKUP_REMOTE` is
   set; otherwise it logs a loud warning every single night that the backup is
   **local-only** and therefore does not protect against losing the box itself.
5. Prunes local copies older than `CAFEOPS_BACKUP_RETAIN_DAYS` (default 14).

Runs nightly at 02:00 UTC by default — the `backup` Docker Compose service (a loop
around the script, `deploy/backup-loop.sh`), or `cafeops-backup.timer` on systemd
(the OS's own timer, the more standard tool, and the reason the systemd path is the
one described in the spec).

**No off-box remote is configured in this repository** — there is no real target to
point `CAFEOPS_BACKUP_REMOTE` at without a credential this codebase has no business
holding. Set it (an `rsync`-reachable host with a deployed SSH key, `user@host:/path`)
before going live. Until then every backup this system produces is one disk failure
away from being gone along with the database it protects — the script's own warning
says exactly that.

### Restoring — proven once, end to end, with output

`deploy/restore.sh <backup-file.db[.gz]> [target-path]`:

1. Decompresses to a scratch file if needed.
2. Runs `PRAGMA integrity_check` on **that candidate file** before asking anything or
   touching the live database — a corrupt backup is refused here, not discovered mid-restore.
3. Asks for an explicit `YES` (skippable with `CAFEOPS_RESTORE_YES=1` for scripted
   drills) before overwriting anything.
4. Copies the *current* live file aside as `<target>.pre-restore-<timestamp>` first, so
   a bad restore is itself recoverable.
5. Removes the old `-wal`/`-shm` sidecars — they belong to the write-ahead log of the
   file just replaced, and leaving them would have SQLite try to replay unrelated
   frames against the freshly restored file.
6. Re-verifies the **restored** file with the same `PRAGMA integrity_check`.

This was run for real against a copy of the seeded database while building this
deployment, not just written and assumed to work:

```
$ deploy/backup.sh
[...] backing up .../live-cafeops.db -> .../.tmp-20260923T171918Z.db (sqlite3 .backup, online/WAL-safe)
[...] verifying the COPY: PRAGMA integrity_check
[...] verifying the copy actually opens and answers a real query
[...] ok: integrity_check=ok, 29 tables present
[...] backup written: .../cafeops-20260923T171918Z.db.gz (872K)
[...] WARNING: CAFEOPS_BACKUP_REMOTE is not set. This backup is LOCAL ONLY, ...

$ deploy/restore.sh cafeops-20260923T171918Z.db.gz restored-cafeops.db
verifying .../cafeops-20260923T171918Z.db.gz before touching anything
candidate verified: integrity_check=ok, 29 tables
restored .../restored-cafeops.db from .../cafeops-20260923T171918Z.db.gz
verifying the RESTORED file opens with the same check
ok
done. Restart cafeops-api / cafeops-bot / cafeops-scheduler (or the docker compose services) now.

$ sqlite3 restored-cafeops.db "SELECT count(*) FROM ingredient;"     -> 113
$ sqlite3 restored-cafeops.db "SELECT count(*) FROM menu_item;"      -> 320
$ sqlite3 restored-cafeops.db "SELECT count(*) FROM stock_movement;" -> 16732
```

A deliberately corrupted file (`head -c 2000` of a live db) was also fed to
`restore.sh` to confirm the negative case: it failed at the integrity-check step
(`database disk image is malformed`), exited non-zero, and **created no target file**
— nothing was overwritten by a bad backup.

**Restore drill cadence:** run this for real (not the truncation trick — an actual
recent nightly backup) at least quarterly, and after any SQLite version or major
dependency bump. A backup nobody has restored recently is a hope, not a backup.

### Recovering from a corrupted live database

1. Stop the three app services (`docker compose stop api bot scheduler`, or
   `systemctl stop cafeops-api cafeops-bot cafeops-scheduler`).
2. Find the most recent backup: locally in `CAFEOPS_BACKUP_DIR` / the `cafeops_backups`
   Docker volume, or on the off-box remote if local disk is what failed.
3. `deploy/restore.sh <that file> <CAFEOPS_DB_PATH>`.
4. Restart the three services.
5. **Run `cafeops drift --backfill`.** The restored database's counts may predate its
   own drift observations depending on when the backup was taken — treat any restore
   like a reseed for this purpose (see the top of this document).

## 5. Blank screen / "nothing is showing up"

In order of likelihood:

1. **`CAFEOPS_API_PASSWORD` is not set.** The API fails closed by design — every route
   but `/api/health` answers `503`. Confirm with:
   ```bash
   curl -s http://localhost/api/health          # should be 200 regardless
   curl -s -o /dev/null -w '%{http_code}\n' http://localhost/api/meta   # 503 = this
   ```
   Fix: set `CAFEOPS_API_PASSWORD` in `.env` (or `/etc/cafeops/cafeops.env` on
   systemd) and restart the `api` service/unit.
2. **Wrong password presented.** `401`, not `503` — the frontend or `curl` sent a
   credential that doesn't match. Check `X-API-Key` (or `Authorization: Bearer`)
   against the value actually in `.env`, not a stale copy.
3. **Caddy can't reach `api`.** `docker compose logs caddy` / `journalctl -u caddy`.
   In Docker, `api` must report `healthy` (`docker compose ps`) before Caddy's
   `depends_on` lets it matter, but Caddy itself will still retry rather than fail
   permanently if `api` is briefly down.
4. **The database is empty or mid-migration.** `cafeops info` reports table counts;
   zero ingredients means `seed`/`import-legacy` was never run against this database
   file (a fresh volume, or `CAFEOPS_DATABASE_URL` pointing somewhere unexpected).
5. **A low-confidence forecast is *supposed* to show nothing.** Invariant 9: the API
   omits `qty` entirely rather than sending a number not to be trusted. If a stock or
   order line shows a reason string instead of a figure, that's correct, not a bug —
   see `CLAUDE.md` §5.3 and §10.9.

## 6. Docker Compose — operational commands

```bash
docker compose ps                       # health of all five services
docker compose logs -f api              # or bot / scheduler / caddy / backup
docker compose restart bot              # e.g. after setting the Telegram token
docker compose exec api cafeops info
docker compose exec api cafeops jobs --run drift_report
```

`api`, `bot`, `scheduler` and `backup` publish **no host port** — only `caddy` does
(80/443). There is no `docker compose port bot ...` to run because there is nothing
there to reach; that is enforced by the compose file's shape, not by a firewall rule
someone could forget.

## 7. systemd — operational commands

```bash
systemctl status cafeops-api cafeops-bot cafeops-scheduler
journalctl -u cafeops-api -f
systemctl restart cafeops-bot                     # after editing /etc/cafeops/cafeops.env
systemctl list-timers cafeops-backup.timer        # confirm the nightly backup is scheduled
systemctl start cafeops-backup.service            # fire a backup by hand, e.g. before a restart
```

Install once (see `README.md` for the full first-run sequence):

```bash
sudo useradd --system --create-home --home-dir /opt/cafeops cafeops
sudo mkdir -p /opt/cafeops/data /opt/cafeops/backups /etc/cafeops
# ... clone/copy the repo to /opt/cafeops, `uv sync` as the cafeops user ...
sudo cp .env.example /etc/cafeops/cafeops.env    # then edit it -- never commit this file
sudo chown -R cafeops:cafeops /opt/cafeops /etc/cafeops/cafeops.env
sudo chmod 600 /etc/cafeops/cafeops.env
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cafeops-api cafeops-bot cafeops-scheduler cafeops-backup.timer
```

Set `CAFEOPS_DATABASE_URL=sqlite+pysqlite:////opt/cafeops/data/cafeops.db` and
`CAFEOPS_DB_PATH=/opt/cafeops/data/cafeops.db` (the latter for the backup script only
— the app itself never reads it) in `/etc/cafeops/cafeops.env`.

## 8. Secrets discipline

- `.env` (Docker) / `/etc/cafeops/cafeops.env` (systemd) are the only places any
  secret lives. Both are outside git (`.gitignore` covers `.env`; `/etc/cafeops` is
  never in the repo at all).
- Nothing in `Dockerfile`, `docker-compose.yml`, `Caddyfile`, or anything in `deploy/`
  contains a credential — every one of them reads from the environment.
- The database volume (`cafeops_data`, or `/opt/cafeops/data`) holds real business
  data (costs, margins). Treat access to it and to the backup volume/remote with the
  same care as `.env`.
