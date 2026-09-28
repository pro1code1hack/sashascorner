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
uv run sashasite menu-meta-seed                                # board copy -> website menu overlay (idempotent)
uv run sashasite info-export --out ../web/src/data/info.json
uv run sashasite media-add PHOTO.jpg --alt "…" [--slot KEY]   # add a photo (and place it)
uv run sashasite media-list                                    # library + where each photo is used
uv run sashasite slots-export --out ../web/src/data/slots.json # for the Astro build
uv run sashasite admin-password [--clear]                      # DEV ONLY (no SITE_SERVICE_KEY): the site's own password
uv run sashasite admin-sessions [--revoke-all]                 # list / sign out every admin browser
uv run ruff check . && uv run ruff format --check . && uv run mypy sashasite
```

No test suite, by owner instruction. Verify by running the server and curling.

## Environment

Read from the process env, the repo-root `.env` (shared with cafeops), then `site/backend/.env`.

| Var | Default | |
|---|---|---|
| `SITE_DATABASE_URL` | `sqlite+pysqlite:///<repo>/cafeops.db` | the shared ops DB |
| `SITE_SERVICE_KEY` | unset | the back office's `X-Site-Service-Key`. **Set: the only way into `/api/admin/*`** (login/password answer 410; one password, the back office's). Unset: the dev cookie fallback below, and `doctor` warns |
| `SITE_OPS_URL` | `http://localhost:5178` | the back office's origin; `/admin*` on the site redirects to `{SITE_OPS_URL}/#/website…` (Caddy reads it too) |
| `SITE_ADMIN_PASSWORD` | unset | **dev fallback only** (ignored with `SITE_SERVICE_KEY`): bootstrap for the site's own sign-in until one is set in the DB (`sashasite admin-password`) |
| `SITE_COOKIE_SECURE` | `0` | `1` = always mark the `sc_admin` cookie `Secure` (it already is on https requests) |
| `SITE_ADMIN_SESSION_DAYS` | `14` | admin session lifetime, extended on use |
| `SITE_LOGIN_RATE_LIMIT_COUNT` / `_WINDOW_SECONDS` | `5` / `60` | admin sign-in attempts per IP (password changes have their own counter, same limit) |
| `SITE_CORS_ORIGINS` | `http://localhost:4321` | comma-separated |
| `SITE_PUBLIC_URL` | `http://localhost:4321` | |
| `SITE_MEDIA_DIR` | `site/media/` | WebP variants; resolved to an absolute path. Gitignored (`site/.gitignore`); back it up with the DB |
| `SITE_MEDIA_BASE_URL` | `/api/media-files` | prefix of every `src`/`srcset`. Keep the default in production too: `/media/*` belongs to the ops back office (its menu photos), and the site's Caddy block already proxies `/api/*` to site-api |
| `SITE_UPLOAD_RATE_LIMIT_COUNT` / `_WINDOW_SECONDS` | `60` / `600` | photo uploads per IP, separate from the form limit |
| `SITE_TRUST_PROXY` | `0` | `1` = rate-limit by first `X-Forwarded-For` hop. Only behind a proxy that overwrites it |
| `CAFEOPS_TELEGRAM_BOT_TOKEN`, `CAFEOPS_TELEGRAM_OWNER_CHAT_ID` | unset | owner notifications (Russian); unset → skipped, logged at INFO |

For local dev WITHOUT `SITE_SERVICE_KEY`, `site/backend/.env` (gitignored) may hold
`SITE_ADMIN_PASSWORD=dev-admin`, and with no DB password `dev-admin` signs in (with the
key set this answers 410; call through the back office's `/api/website/*` instead):

```bash
curl -c jar -H 'X-Admin: 1' -H 'Content-Type: application/json' \
     -d '{"password":"dev-admin"}' localhost:8100/api/admin/login
curl -b jar localhost:8100/api/admin/media
```

## Admin API

Contract: `../ADMIN.md` (Auth, Bookings, Messages, Café settings, Dashboard). Code:
`sashasite/auth.py` (password, sessions, CSRF, audit), `sashasite/admin_api.py` (routes),
`sashasite/settings_store.py` (café settings in the DB).

**Auth.** One password, the back office's (owner, 2026-09-28): with `SITE_SERVICE_KEY`
set, `X-Site-Service-Key` is the only way in and everything below about the site's own
password and cookie is refused (login/password → 410). Dev fallback without the key:
one owner password, scrypt-hashed (`hashlib.scrypt`, N=2^15 r=8 p=1, per-hash
salt, params stored in the hash string) in `site_admin_credential`. Sessions: a
`secrets.token_urlsafe(32)` token in the `sc_admin` cookie (HttpOnly, SameSite=Strict,
Path=/api, Secure on https or with `SITE_COOKIE_SECURE=1`); only its sha256 is stored
in `site_admin_session`. 14 days, extended on use (at most one write per 10 minutes).
Every non-GET `/api/admin/*` request needs `X-Admin: 1` or gets 403, the login included.
A 401 never sends `WWW-Authenticate`. Every admin route, the media and slot ones too,
uses the dependency `sashasite.auth.require_admin_session`; there is no Basic auth.

- `POST /api/admin/login {password}` → `{ok}` + cookie; 401 wrong; 429 after 5/min/IP; 503 no password anywhere.
- `POST /api/admin/logout` → 204, revokes this session. `GET /api/admin/me` → `{authenticated, password_source: "db"|"env"}` or 401.
- `POST /api/admin/password {current, new ≥ 10}` → revokes every session, issues a new
  one for the caller, audits, and sends a Russian Telegram notice (background, fail-safe).
  A wrong `current` answers **400** (not 401, which the UI reads as "signed out").

**Audit.** `site_admin_audit(at, action, detail_json, ip)`: logins (and failures),
logout, password changes, booking create/update, message status, settings, media and
slot writes. Other admin modules call `sashasite.auth.audit(action, detail, request=…)`.

**Bookings.** `GET /api/admin/bookings?from=&to=&status=` (status: one of the four, or
`all`/omitted), `GET /api/admin/bookings/day?date=`, `POST /api/admin/bookings`,
`PATCH /api/admin/bookings/{id}`. Web bookings get `source = "web"`, admin ones
`"admin"`. Admin bookings: any minute within opening hours on an open day (422
otherwise), no lead-time, horizon or online `max_party` rule; capacity applies (409)
unless `override_capacity: true` (also accepted by PATCH). A PATCH that adds load (a
move, a bigger party, un-cancelling) re-checks capacity under `BEGIN IMMEDIATE`. The day
view's `slots[].covers_booked` is the covers **seated at that moment**; it also returns
`reason` (the closure note) beside `closed`. Admin actions send no guest or owner message.

**Messages.** `GET /api/admin/messages?status=new|handled|archived|all` (default
`all`, newest first), `PATCH /api/admin/messages/{id} {status}`. `handled` stamps
`handled_at`, `new` clears it, `archived` keeps it. The public form still creates `new`.

**Settings.** `GET/PUT /api/admin/settings` (see "Configuration files"). PUT takes any
of `cafe`, `booking`, `closures`: `cafe` and `booking` merge field by field and are then
validated whole (HH:MM hours, open < close, all 7 weekdays, https social links or empty,
capacity ≥ 1, max_party ≤ covers_per_slot); `closures` replaces the list. Errors are 422
with `loc` rooted at `["body", section]`.

**Dashboard.** `GET /api/admin/summary`. `today.bookings/covers` and `week[]` count every
booking but cancellations; `today.capacity` is `covers_per_slot`; `today.next` is up to 5
confirmed bookings still to start today; `photos_missing` counts empty image slots;
`menu` comes from `sashasite.menu_source.summary()` (falls back to `{source: "board", warnings: 0}`).

## Shared-DB rules

- The site **writes only** `site_*` tables (bookings, messages, media, slots, admin, settings, and the menu overlay `site_menu_category_meta` / `site_menu_item_meta`).
- It **reads** ops `menu_item`, `menu_category` and `season` (and, once the ops redesign
  migration lands, `menu_item.note` / `photo_asset_id`) with plain `text()` selects, for
  the website menu and the drift report. It never writes them, and never imports cafeops.
  Columns and tables that don't exist yet are detected, not assumed.
- Alembic here uses `version_table = site_alembic_version` and an `include_name` /
  `include_object` filter, so autogenerate only ever sees `site_*` tables and cannot
  propose dropping an ops table.
- **The reverse:** cafeops's own `migrations/env.py` now has an `include_name` filter
  that skips `site_*` tables, so an ops `--autogenerate` will not propose dropping them.
  (Added 2026-09-25; it must be committed and deployed with the ops code to take effect.)

## Configuration files

- `config/cafe.toml` — **the seed** for the café settings: name, address, geo, hours,
  socials, booking rules, `closed_dates`. The first time they are read (startup,
  `doctor`, `info-export`), each missing `site_setting` key (`cafe`, `booking`,
  `closures`) is copied from this file; after that **the DB is the source**, edited in
  the admin, and edits to the file are ignored (delete the `site_setting` row to
  re-seed a key). `/api/info`, availability, `info-export`, `doctor` and the startup
  warning all read the DB. The file still owns `confirmed` / `unconfirmed_fields`
  (currently the email, empty until a mailbox exists): `confirmed = false` is served
  as `/api/info → "confirmed": false`, and a WARNING is logged at startup. A closure
  (`{date, note}`) makes availability `closed: true` with the note as `reason`.
- `config/menu_board.toml` — the menu transcribed from the café's own boards (see its
  header). It is the **fallback** menu source (below) and always supplies `extras`.
  It is validated at startup: a price count that doesn't match the category's `sizes`,
  a bad number, or a duplicate id fails loudly, naming the category and item. `-`
  means that size isn't offered. `sashasite doctor` prints a drift report (price
  mismatches, board items with no ops row, ops rows with no board item) using the
  optional `[aliases]` table (board name → ops name). `doctor --full` prints every row.

## Website menu: source and overlay

Contract: `../ADMIN.md`, "Website menu". Code: `sashasite/menu_source.py` (read path,
seeding, dashboard `summary()`) and `sashasite/menu_admin.py` (admin endpoints).

**Source.** `/api/menu` reports `source: "ops" | "board"`:
- `ops` when the ops table `menu_category` exists **and** at least one active
  `menu_item` is in a category that has a `menu_category` row. (Free-text categories
  left on some rows by the legacy import don't count; otherwise the site would drop to
  ~30 items the moment the ops migration lands.)
- `board` otherwise: `config/menu_board.toml`, exactly as before.

**Ops mode.** Active `menu_item` rows grouped by `name` within `category`; each row is
a size (`size_code`). A name with a single row, or a size of NULL/`ONE`, is "One size".
Prices are `menu_item.price_pence` (the ops cache of the open dated price). Categories
sort by `menu_category.sort_order` (and carry its `kind`); a category with no
`menu_category` row sorts last, and `doctor` warns. Items with no category are
**unassigned**: listed in the admin and by `doctor`, never public. A `season_id` shows
the season's name while today (Europe/London) is inside its window; outside it the
item is hidden from the public menu but still in the admin. `is_recurring_annually`
compares month/day only and handles windows that wrap the new year. Public item ids
are the slug of the name (category-suffixed on a collision, as for the board).

**Overlay** (`site_menu_category_meta`, `site_menu_item_meta`, keyed by name,
migration `site_0005`). Website-only presentation, applied in both modes:
category `slug` (NULL → derived from the name; board mode keeps the board's slugs),
`blurb` (NULL → the board's, or none), `hidden`, `position`; item `description`,
`signature`, `hidden`, `position`, `use_ops_note`. While an item row exists, its
fields win over the board's. A NULL `position` sorts after every positioned entry, in
the source's own order. The ops `menu_item.note` may be internal, so it is **never**
public unless the row's description is empty **and** `use_ops_note` is set, in which
case it is published as the description. `menu_item.photo_asset_id` gives `photo:
null` for now (TODO: the ops media URL scheme, expected `/media/<sha256>.<ext>`); the
site never serves ops files.

`sashasite menu-meta-seed` copies the board's blurbs, descriptions and signature
flags into the overlay. Idempotent: a name that already has a row is never touched.
Items are seeded under the board name and, where the drift matcher (alias or
normalised name) finds an active ops item, under the ops name too, so the copy
survives the switch to ops.

**Public shape** is unchanged plus top-level `source` and `version` (12 hex chars of a
sha256 over everything but `generated_at`; re-render when it changes) and a per-item
`photo` (always null for now). Cached 60 s; rebuilt at once on a board-file change or
an admin write, so an ops edit shows within a minute.

**Admin** (session cookie; `X-Admin: 1` on writes; every write audited):
- `GET /api/admin/menu` → `{source, warnings, categories: [{name, slug, kind, blurb,
  hidden, position, items: [item]}], unassigned: [item], drift}`; an item is
  `{key, id, name, sizes, seasonal: {name, starts_on, ends_on, recurring_annually,
  in_season} | null, ops_note, has_photo, public, web: {description, signature, hidden,
  position, use_ops_note}}`. `drift` is the board-vs-ops report as JSON.
- `PUT /api/admin/menu/categories/{slug}` `{blurb?, hidden?}` → the category.
- `PUT /api/admin/menu/items/{key}` `{description?, signature?, hidden?, use_ops_note?}`
  → the item (`key` is the name, URL-encoded). A first edit starts from the values
  currently shown, so it changes only the fields sent.
- `POST /api/admin/menu/order` `{categories?: [slug…], items?: {slug: [key…]}}` → the
  admin menu. Listed entries take those positions; the rest of that list's scope
  follow in the source's order. One transaction; any unknown slug/key or duplicate is a
  422 and nothing is written.
- Writes validate slugs and keys against the current source (404 when unknown) and
  answer 503 until the overlay migration has run. Prices, names and sizes are not
  editable here: they belong to the back office.

`doctor` reports the source and its reason, the public category/item counts,
unassigned items, overlay rows for names that are on neither the board nor the
active ops menu, and the drift.

## Booking model

Each booking holds `party` covers for `duration_minutes`. A slot is available when the
peak covers of overlapping bookings (every status but `cancelled`: `arrived` and
`no_show` keep holding theirs) plus the party fit in `covers_per_slot`,
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
  served at `/api/media-files/…` (dev and production alike). Don't expose them at
  `/media/…`: that path is reserved for the ops back office's menu photos.
- **Unknown keys:** an assignment whose key has left `slots.toml` is kept (the
  owner's choice isn't thrown away by a layout edit) but omitted from `/api/slots`;
  `doctor` lists it. If `max` is lowered, `/api/slots` shows the first `max` items.
- **Deleting** a photo in use answers 409 with the slot keys; `?force=1` removes it
  from those slots (closing the gaps in their order) and deletes the files.

Endpoints: `GET /api/slots` (public, `max-age=30`); admin (session cookie + `X-Admin: 1` on writes):
`GET/POST /api/admin/media`, `PATCH/DELETE /api/admin/media/{id}`,
`PUT /api/admin/slots/{key}` (replaces the whole ordered list in one transaction;
422 for an unknown key, too many items or an unknown `media_id`, and returns the slot).
Admin list/upload items carry `usage: [{slot_key, position, label}]` (`label` is null
for a key no longer in the registry).

`doctor` reports empty slots, slots over their max, orphaned assignments and photos
without alt text as warnings, and missing variant files as a failure.

### Production (Caddy)

Nothing extra: the site's Caddy block (`deploy/Caddyfile.site`) proxies `/api/*` to
site-api, which serves the photos with immutable cache headers, so browsers and any
CDN cache them after the first request. Keep `SITE_MEDIA_BASE_URL` at its default.
Do **not** add a `/media/*` route for the site: on the shared box that path belongs
to the ops back office's menu photos (agreed with the back-office redesign,
2026-09-26).
