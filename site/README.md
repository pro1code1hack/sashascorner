# Sasha's Corner website

Public site for Sasha's Corner, 23 Commercial Street, Dundee. This is a separate
project from the ops system in `../cafeops`. The only thing they share is the SQLite
file. Before changing anything, read `BRIEF.md`: it holds the facts you may state, the
design system, and which files belong to the integrator.

```
site/
  backend/   FastAPI + SQLAlchemy + Alembic (package `sashasite`), API on :8100
  web/       Astro static site + three.js diorama, dev server on :4321 (proxies /api)
  deploy/    compose override + Caddy snippet for production (see deploy/README.md)
  Dockerfile build context for both production images
```

There is no test suite, by the owner's instruction. To verify a change, build it and
look at it.

## Develop

Use two terminals.

```bash
# 1. API
cd site/backend
uv sync
uv run alembic upgrade head        # site_* tables only, in the shared cafeops.db
uv run sashasite serve             # http://127.0.0.1:8100/api  (docs: /api/docs)

# 2. Pages
cd site/web
npm install
npm run dev                        # http://localhost:4321, /api proxied to :8100
```

`SITE_API_URL` points the dev proxy somewhere other than `127.0.0.1:8100`. Backend
settings (`SITE_*`) are listed in `backend/README.md`.

## Build

```bash
cd site/backend
uv run sashasite menu-export --out ../web/src/data/menu.json   # menu baked into the HTML
uv run sashasite info-export --out ../web/src/data/info.json   # address, hours, socials
cd ../web
npm run build                      # -> web/dist (static HTML, sitemap, robots.txt)
npm run preview                    # serve dist locally
```

Run the two exports first whenever the TOML changes. Otherwise the crawlable HTML shows
yesterday's menu while `/api/menu` shows today's. The Docker build does this for you.
`SITE_PUBLIC_URL` sets the canonical origin at build time. It defaults to
`https://sashascorner.co.uk`.

## Deploy

The site runs on the ops box, next to cafeops, behind the same Caddy:

```bash
docker compose -f docker-compose.yml -f site/deploy/compose.site.yml build site-api site-static
docker compose -f docker-compose.yml -f site/deploy/compose.site.yml run --rm site-api alembic upgrade head
docker compose -f docker-compose.yml -f site/deploy/compose.site.yml up -d
```

Doing this the first time needs one line in the root `Caddyfile` and a few `.env`
values. `deploy/README.md` covers that, plus the shared-database warnings.

## Where content lives

| what | where | notes |
|---|---|---|
| Café facts: name, phone, address, geo, hours, socials, booking rules | `backend/config/cafe.toml` | `unconfirmed_fields` lists what is still a guess |
| Menu and prices | `backend/config/menu_board.toml` | The source of truth for the site, transcribed from the café's TV boards. Validated at startup. `-` means that size isn't sold |
| Real photos | `web/public/img/cafe/*.webp` | The only images that may show the café's food or rooms |
| Mood images from the brand book | `web/public/img/*.webp` | Stock pictures, never presented as the café's food. No page uses them at present |
| Logo files | `web/public/brand/` | `logo.svg`, `logo-reverse.svg` (on olive), `logo-mono.svg`, `lineart.svg` |
| Icons, social card | `web/public/favicon.svg`, `favicon-32.png`, `apple-touch-icon.png`, `icon-*.png`, `site.webmanifest`, `og.png` | Generated from the logo mark. Regenerate them if the logo changes |
| Diorama poster | `web/public/img/diorama-poster.webp` | Screenshot of the 3D café, shown before WebGL loads and when WebGL isn't available. Re-shoot it after changing the scene |
| Page copy | `web/src/pages/*.astro` | |

## After launch: the owner's checklist

- [ ] **Register the domain.** The site assumes `https://sashascorner.co.uk`. If you
      buy a different one, set `SITE_PUBLIC_URL`, `SITE_DOMAIN` and
      `SITE_CORS_ORIGINS` in `.env`, and change the `Sitemap:` line in
      `web/public/robots.txt`. Point both the bare domain and `www` at the server.
- [ ] **Add the website to the Google Business Profile.** The profile currently shows
      "Add website". Enter the site's address there, and for the booking link use `/book`.
- [ ] **Set up a mailbox** on the domain, e.g. hello@sashascorner.co.uk. Then put it in
      `email = ""` in `backend/config/cafe.toml`, remove `"email"` from
      `unconfirmed_fields`, and rebuild. Until you do, the site has no email address
      and all contact goes through the form.
- [ ] **Set up the Telegram notifications.** Put `CAFEOPS_TELEGRAM_BOT_TOKEN` and
      `CAFEOPS_TELEGRAM_OWNER_CHAT_ID` in the root `.env`. Without them, bookings and
      contact messages are saved but nobody is told about them. To check, run
      `sashasite doctor`, then send yourself a test message through the form.
- [ ] **Supply the licensed brand fonts.** Put `Aldo-SemiBold.woff2` and
      `RivalSans-ExtraLight.woff2` in `web/public/fonts/`. Until then the site stands
      in Saira and Jost.
- [ ] **Replace any remaining mood images** with real photos of the café. Put them in
      `web/public/img/cafe/` as WebP, about 1400px on the long side.
- [ ] **Confirm the booking rules** in `cafe.toml` (covers per slot, duration, lead
      time). Then set `confirmed = true`.
- [ ] **Set `SITE_ADMIN_PASSWORD`** so that `/api/admin/*` can be used.
- [ ] **Submit the sitemap** (`/sitemap-index.xml`) in Google Search Console once the
      domain is live.
