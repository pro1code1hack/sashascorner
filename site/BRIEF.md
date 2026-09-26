# Sasha's Corner — public website brief

Public site for Sasha's Corner, an independent café at **23 Commercial Street,
Dundee DD1 3DD** (opened November 2025). Separate project from the ops system in
`../cafeops` — it shares only the SQLite file. Do not import `cafeops`.

```
site/
  backend/   FastAPI + SQLAlchemy + Alembic (package `sashasite`), API on :8100
  web/       Astro 7 static site + three.js diorama, dev server on :4321 (proxies /api)
```

No test suite (owner's instruction). Verify by running: `npm run build` in `web/`,
and look at pages in a browser or with headless Chrome.

## Verified facts (use only these; never invent)

Source: the café's Google Business Profile, its TV menu boards, and the owner.

- Address: 23 Commercial Street, Dundee DD1 3DD. Phone 07398 433317.
- Hours: Mon–Sat 9am–7pm, Sun 9am–5pm.
- Instagram: https://www.instagram.com/sashascorner_uk
- Dine-in, takeaway, no-contact delivery. Sells through **Deliveroo** and **Just Eat**.
- Google rating 4.9 from 54 reviews. Profile attributes: women-owned, LGBTQ+ friendly.
- What reviewers single out: the huge variety of drinks ("by far the biggest
  selection of drinks in Dundee"), matcha made fresh in front of you, Raff coffee,
  rose latte, friendly service, cosy atmosphere, a good selection of cakes.
- Interior: sage-green walls over dark wood panelling, grey marble-look floor, white
  tables and chairs with velvet cushions, green woven placemats, warm LED strip
  lights, gold stars, framed cat prints, a painted white tree, a chess set and board
  games, the logo line-drawing on the front window.
- Menu: `web/src/data/menu.json`, built from the real boards (157 items, 16
  categories, including breakfast, lunch and brunch waffles). Kyiv cake is
  sold by the slice (£4.00) and whole (£25.00, order ahead).
- **No email address exists yet** (`info.email` is `""`). Contact goes through the
  site's form, which posts to `/api/contact` and reaches the owner on Telegram.

**Not known, so do not state:** who Sasha is, the owners' backgrounds or nationality,
bean origin or roaster, "ceremonial" grade, awards, vegan or gluten-free claims,
parking, wheelchair access, wifi, dog policy, prices not in menu.json, delivery
radius. If a page needs one of these, leave a clearly marked
`<!-- OWNER TO CONFIRM: ... -->` comment and write around it. Don't fake it.

## Design system (non-negotiable)

Read `web/src/styles/global.css` first. The brand colours were measured from the
brand book's vector file:

| token | hex | use |
|---|---|---|
| `--ink` | #0D0D0B | text |
| `--olive-900` | #474531 | dark grounds (hero, footer), headings on light |
| `--olive-700` | #666749 | secondary text, section grounds |
| `--sage` | #899C6A | fills, labels on dark (not body text on paper: contrast) |
| `--coffee` | #9B6038 | labels/links on paper |
| `--caramel` | #D4884E | primary buttons, accents, "hot" states |
| `--paper` | #F3F1EB | page ground |
| `--oat` | #EBE6DD | alternate section ground |

- **Type:** display is `var(--font-display)` (Saira, standing in for the brand's
  Aldo), weight 600, `font-stretch: 88%`. Text is Jost. A light contrasting phrase
  inside a heading uses `.soft`-style `var(--font-light)` weight 200 (see
  `index.astro`). Numbers use `.num` (tabular).
- **Utilities that exist:** `.wrap` (max-width + gutter), `.label` (small tracked
  caps, used only as section markers), `.btn`, `.btn--caramel`, `.btn--ghost`,
  `.field`, `.input`, `.hp` (honeypot), `.sr-only`, `data-reveal` (fade in on scroll),
  and the fluid type steps `--step--1` … `--step-5`.
- **Components that exist:** `Base.astro` layout (props: title, description, tone,
  noindex, extraJsonLd), `LineDraw.astro` (the logo line, drawn on scroll),
  `OpenNow.astro`, `Icon.astro` (instagram, facebook, tiktok, arrow, pin, clock,
  mail), `lib/data.ts` (`menu`, `info`, `signatures`), `lib/format.ts` (`gbp`,
  `clock`, `WEEKDAYS`).
- **Photos:** real ones in `web/public/img/cafe/*.webp`. The brand-book stock
  images in `web/public/img/*.webp` are mood only, never shown as the café's food.
- **Avoid:** identical rounded card grids, KPI tiles, emoji, script or cursive fonts,
  hearts, gradients-for-decoration, stock-looking hero blocks, "Welcome to our
  cozy café" copy, colour outside the palette. Copy is British English, plain and
  specific, and short.
- **Quality floor:** works at 375px with no horizontal scroll, visible focus,
  `prefers-reduced-motion` respected, semantic HTML, alt text on every image, one
  `h1` per page, unique title and meta description, JSON-LD where it fits.

## Shared files: ask, don't edit

`Base.astro`, `Header.astro`, `Footer.astro`, `global.css`, `lib/*`, `Hero.astro`,
`index.astro`, `menu.astro`, `book*.astro`, `scripts/diorama/*` and everything in
`backend/` are owned by the integrator. If you need a change there, describe it in
your final report and don't make it. Page-scoped CSS goes in the page's own
`<style>`. New components go in `web/src/components/<YourPrefix>*.astro`.

## API (same origin, `/api`)

- `GET /api/info`, `GET /api/menu`
- `POST /api/contact` `{name, email, topic: general|order|events|feedback|press|jobs, message (10–2000 chars), website: ""}` → 201 `{ok:true}`; 422 validation; 429 rate limit
- `GET /api/availability?date=&party=`, `POST /api/bookings` (the book page, done)

## Media & image slots (phase 2 contract, shared by several agents)

The owner uploads photos in an admin page and assigns them to named **slots** on
the site, with an order and a focal point. Pages render a slot; an empty slot shows
a branded placeholder that says which photo belongs there.

**Registry:** `backend/config/slots.toml` declares every slot the site uses:
```toml
[[slot]]
key = "about.room.hero"        # page.section[.name], lowercase, dots
label = "About — large room photo"
page = "/about"
aspect = "4/5"                 # CSS aspect-ratio the page renders it at
multiple = false               # true => an ordered list (galleries, strips)
max = 1                        # for multiple, the most the layout shows
hint = "Wide shot of the room from the door, daylight"   # shown in placeholder + admin
```
Pages only render keys that exist here. `sashasite doctor` warns about empty slots.

**Tables** (site_ prefix, own Alembic migration): `site_media` (id, sha256 unique,
original_name, content_type, width, height, bytes, alt, created_at) and
`site_slot_item` (slot_key, position, media_id → site_media, alt_override, focal_x,
focal_y in 0..1, updated_at; unique(slot_key, position)).

**Storage:** `SITE_MEDIA_DIR` (default `site/media/`). Each upload is auto-oriented,
EXIF-stripped and stored as WebP variants at widths 480, 960, 1600 (never upscaled)
plus a 40px blurred placeholder encoded as a data URI in the DB/API. Served at
`/media/{id}-{w}.webp` (FastAPI StaticFiles in dev, and Caddy in prod).
Accept JPEG/PNG/WebP up to 15 MB; reject everything else with 415.

**API**
- `GET /api/slots` (public, cached 30 s) →
  `{slots: {[key]: {label, page, aspect, multiple, max, hint, items: [{media_id, src, srcset, width, height, alt, focal: {x, y}, blur}]}}}`
  where `src` is the 960 variant, `srcset` lists all variants, and `alt` is `alt_override ?? media.alt`.
- Admin (HTTP Basic, password `SITE_ADMIN_PASSWORD`, any username):
  - `GET /api/admin/media` → list with usage (which slots use each item)
  - `POST /api/admin/media` multipart `file`, `alt` → the media item (a duplicate sha256 returns the existing item, 200)
  - `PATCH /api/admin/media/{id}` `{alt}`
  - `DELETE /api/admin/media/{id}` → 409 if a slot uses it, unless `?force=1`
  - `PUT /api/admin/slots/{key}` `{items: [{media_id, alt?, focal: {x, y}}]}` replaces the whole ordered list; 422 if there are more than `max` items or the key is unknown
- CLI: `sashasite media-add FILE --alt "…" [--slot KEY]` and `sashasite slots-export --out ../web/src/data/slots.json` (same shape as GET /api/slots, so the static build carries the photos too).

**Frontend:** `web/src/components/Slot.astro` renders one slot (props: `key`,
`index = 0`, `sizes`, `class`, `eager`) from build-time `slots.json`. It uses a
`<picture>` with srcset, `object-position` from the focal point, and the blur
placeholder as a background. An empty slot renders the placeholder: brand oat
ground, the logo cup line faintly, and the slot label + hint in small text. Runtime:
one small script fetches `/api/slots` and swaps in newer images without a rebuild.
The admin UI lives at `/admin` (noindex, excluded from the sitemap).
