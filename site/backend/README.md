# sashasite — public website backend

API for the Sasha's Corner website (menu, café info, table bookings, contact form).
Consumed by the Astro frontend in `../web/`. A **separate project** from `cafeops`:
it never imports it, it only shares the SQLite file.

## Run

```bash
cd site/backend
uv sync
uv run alembic upgrade head          # creates site_* tables only
uv run sashasite doctor              # DB, migrations, menu coverage, placeholders, telegram
uv run sashasite serve               # 127.0.0.1:8100, API under /api (docs: /api/docs)
uv run sashasite bookings [--date YYYY-MM-DD]
uv run sashasite menu-export --out ../web/src/data/menu.json   # for the Astro build (SEO menu HTML)
uv run sashasite info-export --out ../web/src/data/info.json
uv run sashasite media-add PHOTO.jpg --alt "…" [--slot KEY]   # add a photo (and place it)
uv run sashasite media-list                                    # library + where each photo is used
uv run sashasite slots-export --out ../web/src/data/slots.json # for the Astro build
uv run ruff check . && uv run ruff format --check . && uv run mypy sashasite
```

No test suite, by owner instruction. Verify by running the server and curling.

## Environment

Read from the process env, the repo-root `.env` (shared with cafeops), then `site/backend/.env`.

| Var | Default | |
|---|---|---|
| `SITE_DATABASE_URL` | `sqlite+pysqlite:///<repo>/cafeops.db` | the shared ops DB |
| `SITE_ADMIN_PASSWORD` | unset | HTTP Basic password for `/api/admin/*`; unset → 503 |
| `SITE_CORS_ORIGINS` | `http://localhost:4321` | comma-separated |
| `SITE_PUBLIC_URL` | `http://localhost:4321` | |
| `SITE_MEDIA_DIR` | `site/media/` | WebP variants; resolved to an absolute path. Gitignored (`site/.gitignore`); back it up with the DB |
| `SITE_MEDIA_BASE_URL` | `/api/media-files` | prefix of every `src`/`srcset`. The default works through the Astro dev proxy; set `/media` in production (see below) |
| `SITE_UPLOAD_RATE_LIMIT_COUNT` / `_WINDOW_SECONDS` | `60` / `600` | photo uploads per IP, separate from the form limit |
| `SITE_TRUST_PROXY` | `0` | `1` = rate-limit by first `X-Forwarded-For` hop. Only behind a proxy that overwrites it |
| `CAFEOPS_TELEGRAM_BOT_TOKEN`, `CAFEOPS_TELEGRAM_OWNER_CHAT_ID` | unset | owner notifications (Russian); unset → skipped, logged at INFO |

For local dev, `site/backend/.env` (gitignored) holds `SITE_ADMIN_PASSWORD=dev-admin`;
any Basic-auth username works, e.g. `curl -u owner:dev-admin localhost:8100/api/admin/media`.

## Shared-DB rules

- The site **writes only** `site_booking`, `site_contact_message`, `site_media` and `site_slot_item`.
- It **reads** ops `menu_item` with a plain `text()` select, only for the doctor's drift report. It never writes it.
- Alembic here uses `version_table = site_alembic_version` and an `include_name` /
  `include_object` filter, so autogenerate only ever sees `site_*` tables and cannot
  propose dropping an ops table.
- **The reverse:** cafeops's own `migrations/env.py` now has an `include_name` filter
  that skips `site_*` tables, so an ops `--autogenerate` will not propose dropping them.
  (Added 2026-09-25; it must be committed and deployed with the ops code to take effect.)

## Configuration files

- `config/cafe.toml` — name, address, geo, hours, socials, booking rules. Fields listed
  in `unconfirmed_fields` (currently the email, which is empty until a mailbox exists,
  and the booking capacity rules) are still guesses. `confirmed = false` is served as
  `/api/info → "confirmed": false`, and a WARNING is logged at startup.
- `config/menu_board.toml` — **the website's menu source of truth**, transcribed from
  the café's own boards (see its header). `/api/menu` is served from it (cached 60 s,
  rebuilt immediately when the file's mtime changes). It is validated at startup: a
  price count that doesn't match the category's `sizes`, a bad number, or a duplicate
  id fails loudly, naming the category and item. `-` means that size isn't offered.
  The ops `menu_item` table is a stale snapshot of the legacy workbook and is **not**
  used for the menu; `sashasite doctor` prints a drift report (price mismatches, board
  items with no ops row, ops rows with no board item) using the optional `[aliases]`
  table (board name → ops name). `doctor --full` prints every row.

## Booking model

Each booking holds `party` covers for `duration_minutes`. A slot is available when the
peak covers of overlapping confirmed bookings plus the party fit in `covers_per_slot`,
the start is at least `min_lead_minutes` away, the date is within `horizon_days`, and
the café is open. Creation re-checks inside a `BEGIN IMMEDIATE` transaction, so
concurrent requests cannot both take the last seats (409 for the loser). All
business-hours logic uses `Europe/London`; timestamps are stored UTC.

## Media library and image slots

Contract: `../BRIEF.md`, "Media & image slots". The owner uploads photos in `/admin`
and places them in named **slots**; pages render a slot, or a branded placeholder
when it is empty.

- **Registry:** `config/slots.toml` (owned by the page layout, not by this code)
  declares every slot: `key` (`page.section[.name]`, lowercase), `label`, `page`,
  `aspect` (`N/M`), `multiple`, `max` (exactly 1 when `multiple = false`), `hint`.
  Validated at startup and reloaded when the file's mtime changes; a broken edit
  answers 500 and logs, rather than serving a stale registry.
- **Processing** (`sashasite/media.py`, Pillow): the type is sniffed from the bytes
  (JPEG/PNG/WebP only, else 415); max 15 MB (413; the request body is capped a
  little above that before it is parsed) and 60 megapixels (413, checked from the
  header before decoding). The image is turned upright from EXIF, converted to sRGB
  RGB (transparency flattened onto `--paper` #F3F1EB) and written as WebP q80 at
  widths 480/960/1600, never upscaled: a narrower original gets its own width as
  the largest variant, so the widths are e.g. `480,960,1050`. No metadata is kept.
  A ~24 px blurred WebP is stored in the DB as a data URI (`blur`). Identical bytes
  (sha256) are stored once: re-uploading returns the existing item with 200.
  Files are written atomically (temp file + rename).
- **Files:** `{SITE_MEDIA_DIR}/{id}-{w}.webp`. A variant is never rewritten and
  `site_media` uses AUTOINCREMENT, so an id is never reused: the files are served
  `Cache-Control: public, max-age=31536000, immutable`. Only names of that form are
  served. FastAPI serves them at `/media/…` and again at `/api/media-files/…`,
  because the Astro dev server proxies only `/api`.
- **Unknown keys:** an assignment whose key has left `slots.toml` is kept (the
  owner's choice isn't thrown away by a layout edit) but omitted from `/api/slots`;
  `doctor` lists it. If `max` is lowered, `/api/slots` shows the first `max` items.
- **Deleting** a photo in use answers 409 with the slot keys; `?force=1` removes it
  from those slots (closing the gaps in their order) and deletes the files.

Endpoints: `GET /api/slots` (public, `max-age=30`); admin (Basic auth):
`GET/POST /api/admin/media`, `PATCH/DELETE /api/admin/media/{id}`,
`PUT /api/admin/slots/{key}` (replaces the whole ordered list in one transaction;
422 for an unknown key, too many items or an unknown `media_id`, and returns the slot).
Admin list/upload items carry `usage: [{slot_key, position, label}]` (`label` is null
for a key no longer in the registry).

`doctor` reports empty slots, slots over their max, orphaned assignments and photos
without alt text as warnings, and missing variant files as a failure.

### Production (Caddy)

Serve the files directly and point the URLs at them:

```caddy
handle_path /media/* {
    root * /srv/site/media            # = SITE_MEDIA_DIR
    @other not path_regexp ^/[1-9][0-9]*-[1-9][0-9]*\.webp$
    respond @other 404
    header Cache-Control "public, max-age=31536000, immutable"
    file_server
}
```

and set `SITE_MEDIA_BASE_URL=/media` for the API **and** when running
`slots-export` for the static build. (Leaving the default also works as long as
`/api/*` is proxied to the API, just with Python serving the bytes.)
