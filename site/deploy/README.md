# Deploying the website next to cafeops

The site runs on the same box as the ops system, behind the same Caddy, against the
same SQLite file. Nothing in the ops `docker-compose.yml`, `Dockerfile` or `Caddyfile`
is replaced: this directory adds an override file and a Caddy snippet, and the ops
Caddyfile needs one `import` line.

```
                    :443  caddy (ops image)
                     |-- CAFEOPS_DOMAIN  /api/* -> api:8000        dashboard  (ops)
                     `-- SITE_DOMAIN     /api/* -> site-api:8100   /srv/site/public (site)
site-static (one-shot) --copies--> [site_static volume] <--ro-- caddy
site-api --rw--> [cafeops_data volume]/cafeops.db <--rw-- api, bot, scheduler
```

| file | what it is |
|---|---|
| `../Dockerfile` | build context `site/`. Targets: `api` (sashasite on :8100, uid 1000) and `static` (the built HTML plus this Caddy snippet) |
| `compose.site.yml` | compose override: adds `site-api` and `site-static`, and mounts `site_static` read-only into the ops `caddy` |
| `Caddyfile.site` | the site's Caddy block. Shipped inside the `static` image and copied into the volume as `/srv/site/site.caddy` |

## How the static files reach Caddy

`site-static` is a small Alpine image holding `dist/` and the snippet. On `up`, it copies
both into the `site_static` named volume and exits 0. `caddy` mounts that volume at
`/srv/site` (read-only) and waits for `service_completed_successfully` before starting.
The HTML and the config that serves it therefore always come from the same build.

The copy only adds and overwrites. It never deletes first, so a visitor holding the
previous HTML can still load the previous hashed `/_astro/*` files during a deploy.
Old hashed files pile up slowly. To prune, which you rarely need to do:
`docker compose ... run --rm --entrypoint sh site-static -c 'rm -rf /srv/site/public'`
followed by `up site-static`.

## Wiring (one-time, done by whoever owns the ops files)

1. **Root `Caddyfile`**: add this as the first line, at top level and outside the
   `{$CAFEOPS_DOMAIN...}` block:

   ```caddyfile
   import /srv/site/*.caddy
   ```

   It has to be a **glob**. When no site is deployed (plain `docker compose up`
   without the override), it matches nothing and Caddy starts as before. A literal
   path would make Caddy refuse to start and take the ops dashboard down with it.
   Because the Caddyfile is baked into the ops image, rebuild it after the edit:
   `docker compose build caddy`.

2. **Repo-root `.env`**: add the following.

   ```dotenv
   SITE_DOMAIN=sashascorner.co.uk            # Caddy: which hostname the site block answers
   SITE_PUBLIC_URL=https://sashascorner.co.uk # build: canonical URLs, og:url, sitemap
   SITE_CORS_ORIGINS=https://sashascorner.co.uk
   SITE_ADMIN_PASSWORD=<long random>          # /api/admin/*; unset -> 503 (fails closed)
   # CAFEOPS_TELEGRAM_BOT_TOKEN / CAFEOPS_TELEGRAM_OWNER_CHAT_ID are already there for
   # the ops bot; the site reuses them for booking and contact-form notifications.
   ```

   `SITE_DATABASE_URL` and `SITE_TRUST_PROXY` are set in `compose.site.yml`. They are
   not secrets, and getting them wrong breaks things, so they don't belong in `.env`.

3. **DNS**: point both `sashascorner.co.uk` and `www.sashascorner.co.uk` at the box. Caddy
   gets the certificates itself. `www` redirects permanently to the apex.

## First deploy

```bash
C="docker compose -f docker-compose.yml -f site/deploy/compose.site.yml"

$C build site-api site-static caddy
# The site's own migrations: creates site_booking / site_contact_message and
# records them in site_alembic_version. Never touches an ops table.
$C run --rm site-api alembic upgrade head
$C run --rm site-api sashasite doctor      # DB, migrations, menu coverage, telegram
$C up -d                                    # ops services + site-api + site-static + caddy
curl -fsS https://sashascorner.co.uk/api/health
```

## Every later deploy

```bash
$C build site-api site-static
$C run --rm site-api alembic upgrade head   # no-op when there is nothing new
$C up -d site-api site-static               # site-static re-copies the release
$C restart caddy                            # only if Caddyfile.site changed
```

Menu or café-facts changes (`backend/config/*.toml`) need **both** targets rebuilt. The
API reads the TOML at runtime, but the HTML was rendered from it at build time, because
the `static` build runs `sashasite menu-export` / `info-export` first. If you rebuild
only one of them, the page and `/api/menu` disagree about prices.

## Warnings

- **Shared database, two Alembic histories.** The site uses `site_alembic_version`
  and its `env.py` only ever sees `site_*` tables. The ops `migrations/env.py` now has
  the mirror-image filter, which excludes `site_*` from ops autogenerate. **That filter
  is an uncommitted change in the working tree at the time of writing.** Until it is
  committed and deployed, `alembic revision --autogenerate` in cafeops will propose
  `drop_table('site_booking')` and `drop_table('site_contact_message')`. Read any
  generated ops migration before running it. (`site/backend/README.md` still says the
  filter doesn't exist. That was true before and needs updating.)
- **Same uid.** `site-api` runs as uid/gid 1000, the same as the ops `cafeops` user,
  because both write `cafeops.db` and its `-wal`/`-shm` files. If you change one,
  change the other.
- **Backups already cover it.** The site tables live in `cafeops.db`, so the ops
  `backup` service's nightly copy includes bookings and contact messages.
- **`SITE_TRUST_PROXY=1` is only safe behind this Caddy.** Caddy overwrites
  `X-Forwarded-For` with the real peer address, and `site-api` publishes no port. If
  you give it a host port, anyone can spoof the rate-limit key.
- **`public/robots.txt` hard-codes** `https://sashascorner.co.uk/sitemap-index.xml`.
  If you set `SITE_PUBLIC_URL` to anything else, edit that line as well.
