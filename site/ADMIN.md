# Website admin — contract (phase 3)

> **Moved 2026-09-28.** The owner's screens for this API now live in the ops back office
> (Website group, `web/src/screens/website/`, DECISIONS.md §25), which forwards to the
> `/api/admin/*` routes below with `X-Site-Service-Key` instead of the `sc_admin` cookie.
> The API contract below still holds; the Design and Pages sections describe the old
> `/admin` pages on the site.

The owner's admin for the public website, at `/admin/*`. It is separate from the ops
back office in `../cafeops` (being redesigned by another session; do not touch
`cafeops/` or `web/` at the repo root). Read `BRIEF.md` first for facts and rules.

## Design

**Changed 2026-09-28 (owner's call): the admin wears the café's own brand**, not the
back-office blue. Colours are the measured palette in `web/src/styles/global.css`
(olive-900 sidebar and selected states, caramel for the one call to action and "you are
here", paper ground, sage bars), headings and figures in Saira (condensed, 650), text in
Jost. Tokens live in `layouts/Admin.astro` under the *old names* (`--brand`, `--ink-2`…)
so page rules keep working; add `--accent*` / `--on-brand*` for the new roles. Every text
pair there is checked to WCAG AA: keep it so. Layout and behaviour (drawers, 44px
targets, chips, uppercase labels) still follow `../docs/design/specs/design-system.md`. Put the Sasha's Corner logo in the
sidebar. Don't use a dashboard KPI-tile grid. Every screen has to work on a phone
(375px): the owner will check bookings there.

## Auth (replaces HTTP Basic everywhere)

- One owner password, scrypt-hashed in the table `site_admin_credential`, which takes
  precedence over `SITE_ADMIN_PASSWORD` (the env var only bootstraps the first login).
  - CLI: `sashasite admin-password` (prompts, and sets the DB hash).
- Sessions live in `site_admin_session`: store a hash of a random token, never the
  token itself.
  - The cookie is `sc_admin`: HttpOnly, SameSite=Strict, Path=/api. It is `Secure`
    when the request is https or `SITE_COOKIE_SECURE=1`.
  - Sessions last 14 days, extended on use.
- `POST /api/admin/login {password}` → 200 `{ok: true}` + cookie; wrong password → 401.
  Rate limit: 5 attempts per minute per IP → 429.
- `POST /api/admin/logout` → 204, and revokes this session.
- `GET /api/admin/me` → 200 `{authenticated: true, password_source: "db"|"env"}`, or 401.
- `POST /api/admin/password {current, new}` (new must be ≥10 chars) → revokes every
  session, issues a fresh one for the caller, writes an audit row, and sends a Telegram
  notice (in Russian).
- **CSRF:** every non-GET `/api/admin/*` request must send the header `X-Admin: 1`,
  or it gets a 403.
- Audit: `site_admin_audit(at, action, detail_json, ip)` records every write.
- A 401 **never** sends `WWW-Authenticate`.

## Bookings

Statuses: `confirmed | cancelled | arrived | no_show`. `source` is `web | admin`.
- `GET /api/admin/bookings?from=YYYY-MM-DD&to=…&status=` → `[Booking]`, where Booking is
  `{id, reference, name, email, phone, party, date, time, status, notes, source, created_at, cancelled_at}`
- `GET /api/admin/bookings/day?date=` →
  `{date, closed, capacity, slots: [{time, covers_booked, capacity}], bookings: [Booking]}`
- `POST /api/admin/bookings {name, phone?, email?, party, date, time, notes?, override_capacity?}`
  → 201 Booking. This is for phone-in bookings: no lead-time or horizon rule applies, but
  capacity does unless `override_capacity` is set. It sends no guest notification.
- `PATCH /api/admin/bookings/{id} {status?, notes?, party?, date?, time?}` → Booking.
  Moving a booking re-checks capacity (→ 409 if full).

## Messages (contact form)

Status: `new | handled | archived`.
- `GET /api/admin/messages?status=new|handled|archived|all` → `[{id, name, email, topic, message, created_at, status, handled_at}]`
- `PATCH /api/admin/messages/{id} {status}` → the message.

## Café settings (moved from cafe.toml into the DB)

Stored in `site_setting(key, value_json, updated_at)` and seeded from `config/cafe.toml`
the first time it's needed. `/api/info`, availability and `info-export` read the DB.
- `GET /api/admin/settings` →
  `{cafe: {name, phone, email, address{…}, geo{…}, hours[…], socials{…}}, booking: {covers_per_slot, max_party, slot_minutes, duration_minutes, last_seating_before_close_minutes, min_lead_minutes, horizon_days}, closures: [{date, note}], telegram_configured: bool}`
- `PUT /api/admin/settings` with any subset of `cafe`, `booking` and `closures` → the
  full settings, validated. Closures make availability return `closed: true` with the
  note as `reason`.

## Dashboard

`GET /api/admin/summary` →
`{today: {date, closed, bookings, covers, capacity, next: [Booking]}, week: [{date, bookings, covers}], messages_new, photos_missing, menu: {source: "ops"|"board", warnings}}`

## Website menu (read from ops, presentation edited here)

The public `/api/menu` reads the ops menu read-only:
- active `menu_item` rows grouped by `name`, giving the sizes and prices;
- `menu_item.category` joined to `menu_category`, which supplies the order and the
  `DRINKS`/`FOOD` kind;
- `season` for seasonal items.

That source applies once the ops table `menu_category` exists **and** at least one active
item has a category. Until then, the site uses `config/menu_board.toml` (`source: "board"`).
Website-only presentation lives in `site_menu_category_meta(name, slug, blurb, hidden, position)`
and `site_menu_item_meta(item_name, description, signature, hidden, position)`.
- `GET /api/admin/menu` →
  `{source, categories: [{name, slug, kind, blurb, hidden, position, items: [{key, name, sizes: [{code, label, price_pence}], seasonal, web: {description, signature, hidden, position}}]}], unassigned: [items], drift: {price_mismatches: [...], board_only: [...], ops_only: [...]}}`
- `PUT /api/admin/menu/categories/{slug} {blurb?, hidden?}`
- `PUT /api/admin/menu/items/{key} {description?, signature?, hidden?}` (key = the item name)
- `POST /api/admin/menu/order {categories: [slug…], items: {slug: [key…]}}`
- Prices, names and sizes are **read-only** on the site. The UI says "Change prices in
  the back office", because the ops redesign owns that screen.

## Photos

The existing `/api/admin/media*` and `/api/admin/slots*` endpoints stay, but use the
session auth. The photo UI moves into the shell at `/admin/photos`.

## Pages

These pages sit on one shell layout, `web/src/layouts/Admin.astro`, with a sidebar on
desktop and a bottom tab bar on phones:
- `/admin` (sign-in when logged out, otherwise the dashboard)
- `/admin/bookings`, `/admin/messages`, `/admin/menu`, `/admin/photos`, `/admin/settings`

All of them are noindex and are already excluded from the sitemap and robots.
