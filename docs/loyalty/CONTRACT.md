# Sasha's Corner Rewards — build contract (integrator-owned)

Read `SPEC.md` (the owner's spec) first, then this. **This file wins where they differ.**
It fits the spec onto the existing repo and fixes every cross-agent interface so six
agents can build in parallel. If you need a change to anything here, do not improvise
a different shape — make the smallest compatible addition and list it in your final
report under "CONTRACT CHANGES".

Repo rules that still apply: **no tests** (owner's instruction — verify by running);
money integer pence; timestamps UTC tz-aware; sync SQLAlchemy sessions, async only at
I/O edges; ruff + mypy strict on `cafeops/domain/` and `cafeops/services/`; every
schema change is an Alembic migration; British English copy; no emoji in UI.

**The working tree has the owner's uncommitted edits** (finance files, `web/src/styles.css`,
`web/src/components/ui/*`, `web/src/screens/money/*`, `site/web/src/pages/index.astro`,
`site/web/src/components/Hero.astro`, site data JSON, Caddyfile, …). Never revert,
reformat or `git checkout`/`git stash` anything. Do not run `ruff format` on files you
did not create. Never commit.

## 0. Decisions on the open questions (defaults, owner can change in admin)

| Question | Default shipped |
|---|---|
| Premium drinks earn one stamp? | Yes, 1 per drink, like the paper card |
| Free 9th drink cap? | Any drink. `loyalty_program.reward_max_price_pence` exists, NULL = no cap |
| Birthday reward at launch? | On (`birthday_reward = true`), toggle in admin |
| Apple account owner | Unknown — nothing in code depends on it |
| Domain | `sashascorner.co.uk` (the site's `SITE_DOMAIN`); config `CAFEOPS_LOYALTY_PUBLIC_URL` |

Deliberate deviations from SPEC.md:
- **Loyalty lives in `cafeops`** (spec: "module of the ops system"). The public site's
  Caddy block forwards `/api/loyalty/*`, `/api/staff/*` and `/wallet/apple/*` to the
  cafeops `api:8000`; every other `/api/*` on the public domain still goes to `site-api`.
- **Admin API is `/api/members/*`** on the ops domain behind the existing shared password
  — NOT under `/api/loyalty/*`, which is public.
- **Dashboard auth is unchanged** (shared password). The new `staff_user` table (roles
  staff/manager/owner, PIN) is used by the scanner, by manager-PIN approvals (manual
  adjust, cooldown override) and by the bot (`telegram_id`).
- **Recovery without an SMS/email provider:** OTP goes by SMTP if `CAFEOPS_SMTP_*` is
  set, by Twilio if `CAFEOPS_TWILIO_*` is set; otherwise the response says
  `delivery: "ask_staff"` and staff recover the card from the scanner ("Find member").
- **Events/RSVP live in `sashasite`** (the site backend already owns bookings and
  contact); they are not loyalty.
- Phase 3 (Lightspeed auto-stamping, points, reward catalogue, multi-card programmes)
  **is built** — see the "Phase 3" section at the end of this file.

## 1. Ownership (who writes which files)

| Agent | Owns (create/edit) |
|---|---|
| **A — loyalty backend** | `cafeops/db/models/loyalty.py`, `cafeops/db/models/staff.py`, the `__init__` re-exports for them, `migrations/versions/b7c1e0a10001_loyalty.py`, `cafeops/domain/loyalty.py`, `cafeops/services/loyalty/**`, `cafeops/api/areas/loyalty*.py`, `cafeops/api/areas/members*.py`, `cafeops/api/areas/staff*.py`, registration in `cafeops/api/app.py` + `api/areas/__init__.py`, loyalty settings in `cafeops/config.py`, loyalty CLI commands in `cafeops/cli.py`, loyalty jobs `cafeops/jobs/loyalty_*.py` + registration in `jobs/scheduler.py`, bot `/member` command + alerts |
| **B — wallet** | `cafeops/integrations/wallet/**` (incl. its own `config.py`), `cafeops/db/models/wallet.py`, `migrations/versions/b7c1e0a10002_wallet.py`, `assets/pass/**` (repo root), wallet CLI group (a separate `cafeops/cli/wallet.py` that `cafeops/cli/__init__.py` mounts — see §6) |
| **C — customer pages** | `site/web/src/pages/rewards.astro`, `c.astro`, `privacy.astro`, `site/web/src/components/Rw*.astro`, `site/web/src/scripts/rewards/**`, `site/web/src/scripts/track.ts`, `site/deploy/Caddyfile.site`, `site/web/astro.config.mjs` (dev proxy only) |
| **D — staff scanner** | `site/web/src/pages/staff.astro`, `site/web/src/components/St*.astro`, `site/web/src/scripts/staff/**`, `site/web/public/staff/**` (manifest, service worker, icons), `site/web/package.json` (the only agent that adds npm deps to the site) |
| **E — dashboard Members** | `web/src/screens/members/**`, `web/src/lib/members-api.ts`, `web/src/lib/types/members.ts`, `web/fixtures/members-*.json`, edits to `web/src/routes.tsx` (add route + nav) |
| **F — events + SEO** | `site/backend/sashasite/events*.py` + its migration + registration in `site/backend/sashasite/app.py` / `cli.py`, `site/web/src/pages/events.astro`, `matcha-dundee.astro`, `bubble-tea-dundee.astro`, `kyiv-cake.astro`, `site/web/src/components/Ev*.astro`/`Seo*.astro`, `site/web/src/data/events.json`, JSON-LD additions (see §9) |

Shared files you may need but do not own (`Base.astro`, `Header.astro`, `Footer.astro`,
`global.css`, `site/web/src/lib/*`, `menu.astro`, `visit.astro`, `faq.astro`): **describe the change in
your final report** and the integrator applies it. Exception: F may add links/JSON-LD to
`menu.astro`, `visit.astro` and `faq.astro` — nobody else touches them.

## 2. Data model (A creates all loyalty/staff tables; B creates wallet tables)

Alembic: A's revision id is **`b7c1e0a10001`**, `down_revision = "62aa94a23687"`.
B's revision id is **`b7c1e0a10002`**, `down_revision = "b7c1e0a10001"`. Use
`batch_alter_table` style only if altering; these are all new tables. Enum columns via
`enum_col` (`native_enum=False`, CHECK constraint) like the rest of the repo; UUIDs as
`String(36)`; timestamps `UTCDateTime`.

### A: `cafeops/db/models/staff.py`
```
staff_user      id PK, name str(80) not null, role enum StaffRole(STAFF|MANAGER|OWNER),
                pin_hash str(255) not null (scrypt, same format as auth_credential),
                telegram_id bigint null unique, active bool default true, created_at
staff_device    id PK, name str(80), token_hash str(64) unique (sha256 hex),
                pairing_code_hash str(64) null, pairing_expires_at null,
                registered_at null, revoked_at null, last_seen_at null, created_at
staff_session   id PK, token_hash str(64) unique, staff_user_id FK, device_id FK,
                created_at, expires_at (created+12h), revoked_at null
```
PINs are 4–6 digits. PIN login is rate-limited per device (5 failures → 5-minute lock),
in-process like `security.py`'s limiter.

### A: `cafeops/db/models/loyalty.py`
```
loyalty_program     id PK, slug str unique ("stamp"), name, stamps_required int=8,
                    max_stamps_per_scan int=3, reward_text str ("Any drink, on us"),
                    reward_max_price_pence int null, birthday_reward bool=true,
                    referral_stamps int=1 (0 disables), active bool, created_at
loyalty_member      id PK, first_name str(40), email str(254) null unique (lower-cased),
                    phone str(20) null unique (E.164, UK default +44),
                    birthday_day int null, birthday_month int null,
                    birthday_set_at datetime null,          -- for the 30-day rule
                    marketing_opt_in bool, opt_in_at null, opt_in_source str null,
                    source str(40) null (the ?src=), referred_by_member_id FK null,
                    terms_accepted_at not null, created_at, last_activity_at,
                    deleted_at null
                    CHECK (email IS NOT NULL OR phone IS NOT NULL OR deleted_at IS NOT NULL)
loyalty_card        id str(36) PK (uuid4 = pass serial), member_id FK, program_id FK,
                    stamps_current int, cycles_completed int, reward_available bool,
                    auth_token str(64) (random, url-safe; Apple authenticationToken AND
                    the web-card bearer), qr_secret str(32), voided_at null,
                    created_at, updated_at   (updated_at bumps on EVERY state change)
                    unique(member_id, program_id)
loyalty_stamp_event id PK, card_id FK, delta int (non-zero), reason enum StampReason
                    (PURCHASE|PAPER_MIGRATION|MANUAL_FIX|REDEEM|REFERRAL|UNDO),
                    note str null, staff_user_id FK null, device_id FK null,
                    undoes_event_id FK self null, created_at
                    -- APPEND-ONLY. stamps_current is a cache of SUM(delta); an undo is a
                    -- new row with reason UNDO and the opposite delta.
loyalty_reward      id PK, card_id FK, kind enum RewardKind(STAMP_CARD|BIRTHDAY|REFERRAL),
                    issued_at, expires_at null, redeemed_at null,
                    redeemed_menu_item_id FK menu_item null, sale_id FK sale null,
                    staff_user_id FK null, voided_at null, birthday_year int null
                    unique(card_id, kind, birthday_year) for BIRTHDAY
loyalty_otp         id PK, member_id FK, code_hash str(64), channel (EMAIL|SMS|STAFF),
                    expires_at (10 min), attempts int, used_at null, created_at
loyalty_campaign    id PK, title, message str(200), segment enum
                    (ALL_OPTED_IN|LAPSED_30|REWARD_READY|NEW_30), is_promo bool=true,
                    scheduled_at null, sent_at null, recipients int null,
                    created_by str, created_at
loyalty_campaign_delivery  id PK, campaign_id FK, member_id FK, sent_at, returned_at null
                    -- "returned" = the member got a stamp within 7 days
wallet_push_outbox  id PK, card_id FK, message str null (lock-screen text, null =
                    silent refresh), attempts int=0, next_attempt_at, done_at null,
                    last_error str null, created_at
```
Reward semantics: when `stamps_current` reaches `stamps_required`, a STAMP_CARD reward is
issued and `stamps_required` is subtracted (carry-over above 8 stays on the card), and
`cycles_completed += 1`. So `stamps_current` is always `< stamps_required` after a stamp;
`reward_available` = any un-redeemed, un-voided, un-expired reward exists. Redeeming
records `loyalty_reward.redeemed_at` and leaves the stamp ledger untouched — the stamps
were consumed when the reward was issued, and the reward row is the audit. (`REDEEM`
stays in the enum but is never written; event deltas are always non-zero.)

Undo (2 minutes, same device or any manager): of a stamp → `UNDO` event with opposite
delta, and if that stamp issued a reward still unredeemed, void that reward and restore
stamps. Of a redemption → clear `redeemed_at`, void the £0 sale (`sale.voided = True`).

### B: `cafeops/db/models/wallet.py`
```
wallet_apple_registration  id PK, device_library_id str, push_token str, card_id FK
                           (loyalty_card.id), created_at, unique(device_library_id, card_id)
wallet_google_object       card_id PK FK, object_id str unique, class_id str,
                           last_synced_at null, last_error str null
```

## 3. Functions other agents call (exact signatures)

A provides (`cafeops/domain/loyalty.py`, pure):
```python
def qr_payload(card_id: str, qr_secret: str, key: bytes) -> str   # "SC1:<card_id>:<hmac8>"
def parse_qr_payload(payload: str, lookup_secret: Callable[[str], str | None], key: bytes) -> str | None
    # returns card_id when the signature verifies, else None. hmac8 = first 8 hex chars
    # of HMAC-SHA256(key, f"{card_id}:{qr_secret}"). Constant-time compare.
```
A provides (`cafeops/services/loyalty/card_view.py`):
```python
@dataclass(frozen=True)
class CardView:
    card_id: str; first_name: str; member_since: date
    program_name: str; stamps_required: int; stamps_current: int
    reward_text: str; rewards: tuple[RewardView, ...]   # available (unredeemed, unexpired) only
    qr_payload: str; auth_token: str; updated_at: datetime; voided: bool
    marketing_opt_in: bool
@dataclass(frozen=True)
class RewardView:
    id: int; kind: str  # "STAMP_CARD"|"BIRTHDAY"|"REFERRAL"
    label: str          # "Free drink ready" / "Birthday drink" / "Referral stamp" …
    issued_at: datetime; expires_at: datetime | None
def card_view(session: Session, card_id: str) -> CardView     # LookupError if missing
def enqueue_wallet_update(session: Session, card_id: str, message: str | None) -> None
    # inserts a wallet_push_outbox row; called by every service that changes a card
```
Lock-screen message texts A enqueues: after a stamp `"You've got {n} stamps"`; when a
reward is issued `"Your free drink is ready"`; birthday issued `"Happy birthday — a drink
on us this week"`; campaigns use the campaign message. Silent refresh (message=None) for
undo, redeem and deletes.

B provides (`cafeops/integrations/wallet/sync.py`):
```python
async def kick() -> None
    # drain due outbox rows now (called by A's routes via FastAPI BackgroundTasks after
    # commit). Must never raise. Uses asyncio.to_thread for DB work.
def drain_outbox_sync(session_factory, *, limit: int = 50) -> int   # used by the scheduler job
```
Backoff: 30 s, 2 min, 10 min, 1 h, 6 h, then give up (last_error kept). Apple: silent APNs
push (HTTP/2, token or cert auth) to every registration of that card. Google: PATCH the
LoyaltyObject; if `message` is set, also `addMessage` with `messageType: TEXT_AND_NOTIFY`.
Not configured → mark done with `last_error = "apple not configured"` etc. — never loops.

B also provides:
```python
# cafeops/integrations/wallet/apple.py
def build_pkpass(view: CardView) -> bytes              # raises WalletNotConfigured
# cafeops/integrations/wallet/google.py
def save_url(view: CardView) -> str                    # raises WalletNotConfigured
# cafeops/integrations/wallet/config.py
wallet_settings.apple_configured: bool; wallet_settings.google_configured: bool
class WalletNotConfigured(RuntimeError)                 # A maps it to HTTP 503 {"error":"wallet_not_configured"}
# cafeops/integrations/wallet/apple_webservice.py
router: APIRouter   # prefix "/wallet/apple/v1", the 5 Apple endpoints. A includes it in app.py.
# cafeops/integrations/wallet/strips.py
def strip_png(stamps: int, required: int, scale: int) -> bytes   # also used for Google heroImage
```
Google hero images must be public URLs: B adds an open route
`GET /api/loyalty/strip/{stamps}-{required}@{scale}x.png` in its router module
`cafeops/integrations/wallet/public_routes.py` (`router`), which A includes too.

## 4. Public API (A) — served on the public domain via Caddy

All JSON. Errors are `{"error": "<code>", "detail": "<sentence for humans>"}`.
Card-scoped routes need the card token: header `X-Card-Token: <auth_token>` or query `?t=`.
Rate-limit join/recover per IP (in-process), honeypot field `website` must be empty.

```
GET  /api/loyalty/program
  -> {name, stamps_required, reward_text, birthday_reward, referral_stamps,
      max_stamps_per_scan, wallets: {apple: bool, google: bool}}

POST /api/loyalty/join
  {first_name, email?, phone?, birthday_day?, birthday_month?, terms: true,
   marketing_opt_in: bool, src?: str, ref?: str (referrer card_id), website: ""}
  -> 201 JoinResult  | 409 {error:"already_member"} (UI offers recovery) | 422 | 429
JoinResult = {card_id, token, web_card_url: "/c/<card_id>#t=<token>",
              apple_pass_url: "/api/loyalty/card/<id>/apple.pkpass?t=<token>" | null,
              google_save_url: "/api/loyalty/card/<id>/google?t=<token>" | null}
  (null when that wallet is not configured)

GET  /api/loyalty/card/{id}            -> CardState
CardState = {card_id, first_name, member_since: "YYYY-MM-DD", program_name,
             stamps_required, stamps_current, reward_text,
             rewards: [{id, kind, label, expires_at: iso|null}],
             qr_payload, marketing_opt_in, updated_at: iso,
             wallets: {apple_pass_url|null, google_save_url|null}}
GET  /api/loyalty/card/{id}/apple.pkpass   -> application/vnd.apple.pkpass | 503
GET  /api/loyalty/card/{id}/google         -> 302 to the pay.google.com save link | 503
PATCH /api/loyalty/card/{id}/preferences {marketing_opt_in: bool} -> CardState
DELETE /api/loyalty/card/{id}              -> 204 (erases member PII, voids card+rewards,
                                              enqueues a final wallet refresh showing "Card deleted")
POST /api/loyalty/recover {contact: str}   -> 202 {delivery: "email"|"sms"|"ask_staff"}
                                              (same answer whether or not the contact exists,
                                               except ask_staff which is config-driven)
POST /api/loyalty/recover/verify {contact, code} -> JoinResult | 400 {error:"bad_code"}
GET  /api/loyalty/unsubscribe?m=<member_id>&s=<hmac> -> small HTML page, opts out
```

## 5. Staff API (A) — public domain, scanner only

Headers: `X-Staff-Device: <device_token>` on every call; plus
`Authorization: Bearer <session_token>` on all but `/device/pair` and `/login`.
```
POST /api/staff/device/pair {pairing_code, device_name} -> {device_token}
POST /api/staff/login {pin} -> {session_token, expires_at, user: {id, name, role}}
        (the PIN identifies the user; PINs are unique among active users)
POST /api/staff/logout -> 204
GET  /api/staff/me -> {user, device: {id, name}, expires_at}
POST /api/staff/scan {payload} -> ScanResult | 404 {error:"unknown_card"} | 400 {error:"bad_signature"}
ScanResult = {card_id, first_name, member_since, stamps_current, stamps_required,
              rewards: [{id, kind, label, expires_at}], stamps_last_10_min,
              last_event: {id, delta, reason, at, staff_name} | null, voided: bool}
POST /api/staff/stamp {card_id, delta: 1..3, manager_pin?: str}
        -> {card: ScanResult, event_id, undo_until, reward_issued: bool}
        | 409 {error:"manager_pin_required", detail} (cooldown: >3 in 10 min would be exceeded)
POST /api/staff/redeem {reward_id, menu_item_id?: int}
        -> {card: ScanResult, reward_id, undo_until}
        | 409 {error:"over_price_cap"} when reward_max_price_pence is set and exceeded
POST /api/staff/migrate {card_id, paper_stamps: 1..7} -> {card, event_id, undo_until, reward_issued}
        (once per card: 409 {error:"already_migrated"})
POST /api/staff/undo {event_id?: int, reward_id?: int} -> {card} | 409 {error:"undo_expired"}
GET  /api/staff/drinks -> {items: [{menu_item_id, name, category, price_pence|null}]}
        (active menu items in drink categories — A decides the filter from menu data)
GET  /api/staff/lookup?q=<phone or email or name> -> {members: [{card_id, first_name, contact_masked}]}
POST /api/staff/recovery-link {card_id} -> {url: "https://…/c/<id>#t=<token>"}
        (staff shows it as a QR for the customer to scan; logged)
```

## 6. Admin API (A) — ops domain only, existing `ApiAuth` (shared password)

```
GET  /api/members?q=&segment=all|lapsed_30|reward_ready|opted_in|new_30&sort=recent|stamps|name&limit=&offset=
  -> {total, members: [MemberRow]}
MemberRow = {member_id, card_id, first_name, email|null, phone|null, source|null,
             created_at, last_activity_at, stamps_current, stamps_required,
             cycles_completed, rewards_redeemed, reward_available, marketing_opt_in,
             wallet: "apple"|"google"|"web"|null}
GET  /api/members/{member_id} -> {member: MemberRow & {birthday: "DD-MM"|null,
             opt_in_at, opt_in_source, referred_by},
             events: [{id, delta, reason, note, staff_name, device_name, created_at}],
             rewards: [{id, kind, issued_at, expires_at, redeemed_at, redeemed_item, staff_name, voided_at}]}
POST /api/members/{member_id}/adjust {delta, reason (min 5 chars), manager_pin} -> detail
DELETE /api/members/{member_id} -> 204 (same erasure as the customer delete)
GET  /api/members/stats?days=90 -> {
   members_total, members_new_in_window, opted_in_share,
   by_source: [{source, members}],
   daily: [{date, new_members, stamps, redemptions}],
   stamp_share_of_transactions: number|null,   // stamps / EPOS receipts, same window
   redemptions_per_week: number, visits_per_member_per_month: number|null,
   members_with_redemption_share: number,
   campaign_return_rate: number|null }
GET  /api/members/program -> program;  PUT /api/members/program {…editable fields, manager_pin}
GET  /api/members/staff -> {users: [{id, name, role, active, telegram_id}]}
POST /api/members/staff {name, role, pin, telegram_id?} ; PATCH /api/members/staff/{id} {name?, role?, pin?, active?, telegram_id?}
GET  /api/members/devices -> {devices: [{id, name, registered_at, last_seen_at, revoked_at}]}
POST /api/members/devices {name} -> {device_id, pairing_code (6 digits), expires_at (15 min)}
DELETE /api/members/devices/{id} -> 204 (revoke; kills its sessions)
GET  /api/members/campaigns -> {campaigns: [...], promo_limit_per_month: 2}
POST /api/members/campaigns {title, message, segment, scheduled_at?, is_promo} -> campaign
POST /api/members/campaigns/{id}/send -> {recipients, skipped_over_limit}
GET  /api/members/alerts -> {alerts: [{at, kind, detail}]}   (recent fraud-pattern alerts)
```

## 7. Jobs, bot, stock (A)
- Scheduler: `loyalty_daily_summary` 19:30 local → Telegram notifier; `loyalty_birthdays`
  daily 06:00 (issue BIRTHDAY rewards 7 days before, expiring 7 days after, respecting the
  30-day rule and once a year); `wallet_outbox` every 1 min calling B's
  `drain_outbox_sync`; `loyalty_retention` weekly (erase members inactive 24 months);
  `loyalty_campaigns` every 5 min (send scheduled campaigns; mark returns).
- Fraud alert: on each stamp, if the staff user gave >20 stamps in the last hour → notifier
  (once per user per hour). Also stored so `/api/members/alerts` can list it.
- Bot: `/member <phone|email>` → name, stamps, rewards, last visit (owner chat only).
- Stock: redeeming with `menu_item_id` writes a `Sale` (qty 1, gross_pence 0,
  `lightspeed_receipt_id = "loyalty"`, `lightspeed_line_id = "loyalty-reward-<id>"`,
  channel OTHER, sold_at now). The nightly expansion depletes it like any sale.
- CLI: `cafeops loyalty staff-add NAME --role manager --pin 1234`, `loyalty device-pair NAME`,
  `loyalty members`, `loyalty seed-demo` (a programme + 3 fake members for dev only),
  `loyalty summary`. Mount B's `wallet` Typer/Click group from
  `cafeops/cli/wallet.py` (imported unconditionally since 2026-09-29; it is in-tree).

Config (A, in `cafeops/config.py`, prefix `CAFEOPS_`): `loyalty_public_url`
(default `https://sashascorner.co.uk`), `loyalty_qr_key` (secret; when unset derive a
stable dev key and `doctor` warns), `smtp_host/port/user/password/from`,
`twilio_account_sid/auth_token/from_number`.
B (in `cafeops/integrations/wallet/config.py`, prefix `CAFEOPS_WALLET_`):
`apple_pass_type_id`, `apple_team_id`, `apple_cert_path`, `apple_key_path`,
`apple_key_password`, `apple_wwdr_path`, `apple_apns_use_sandbox`, `google_issuer_id`,
`google_service_account_path`, `google_class_suffix` ("stamp"), `public_url` (defaults to
the loyalty one).

## 8. Web routing (C owns Caddy + astro dev proxy)
- `site/deploy/Caddyfile.site`: in the site block, before the generic `/api/*` handle:
  `handle /api/loyalty/*`, `handle /api/staff/*`, `handle /wallet/apple/*` →
  `reverse_proxy api:8000` (X-Forwarded-Proto). `/c/*` → rewrite to `/c.html`.
  `/staff` and `/staff/*` → `/staff.html` (and allow the service worker at
  `/staff/sw.js` with `Service-Worker-Allowed: /staff`). No-cache on `/staff*` HTML.
  `.pkpass` needs `Content-Type: application/vnd.apple.pkpass` (the API sets it).
- `astro.config.mjs` dev proxy: `/api/loyalty`, `/api/staff`, `/wallet` →
  `process.env.CAFEOPS_API_URL ?? 'http://127.0.0.1:8000'`, keep `/api` → 8100 for the rest
  (more specific keys first).

## 9. Site pages (C, D, F) — design system is `site/BRIEF.md`
Pass colours are fixed by the spec: background `#E9DCD6` (blush), label `#9B6038`,
foreground `#474531`. Use the blush only for the card itself on the site.
Stamp artwork (owner, 2026-09-28): a unique, own-drawn sticker per slot — cat, seal,
matcha bowl, bubble tea, Kyiv cake, chess knight, rose latte, gold star — plus `reward.svg`
for "Free drink ready". Never the paper card's third-party stickers. Canonical in
`assets/pass/stickers/`; `cafeops wallet assets` copies them to `site/web/public/stickers/`
and `web/public/stickers/`, and `cafeops wallet doctor` flags a copy that has drifted.

Swetrix (C writes `site/web/src/scripts/track.ts`):
`export function track(event: string, props?: Record<string, string>): void` — no-op
unless `import.meta.env.PUBLIC_SWETRIX_PID` is set; loads the Swetrix script lazily; adds
`src` from the current URL/sessionStorage automatically; never sends PII.
Event names exactly as SPEC.md. `src` is persisted to sessionStorage on any page load
with `?src=` (C does this in track.ts; F and D import it).

F adds site-wide `CafeOrCoffeeShop` JSON-LD only if `Base.astro` lacks it — check first
and describe any Base.astro change in the report.

## Phase 3 (built 2026-09-28) — supersedes "§0: Phase 3 … not built"

Migration **`b7c1e0a10003`** (after `b7c1e0a10002`). Seeds nothing new: the existing
`stamp` programme becomes kind STAMPS with phase-1 behaviour. Extra programmes exist only
via the back office or the dev-only `cafeops loyalty seed-demo --programmes`.
Every phase-1 request/response shape is unchanged; all additions below are optional
fields or new routes.

### Data
- `loyalty_program` + `kind` (STAMPS|POINTS), `points_per_pound` (POINTS only; CHECK),
  `eligibility` JSON `{scope: "drinks"|"all", categories[], keywords[], template_ids[]}`
  (NULL = drinks, the phase-1 rule), `description`, `reward_ready_label`, `sort_order`.
  On a POINTS card the stamp fields/ledger hold **points**; `stamps_required` is the
  threshold. Points = `spend_pence * points_per_pound // 100` (integer, rounded down).
- `loyalty_reward_option` — a programme's reward catalogue (name, eligibility, optional
  `max_price_pence`, `active`; never deleted). `loyalty_reward.reward_option_id` records
  what a reward was taken as.
- `loyalty_member.lightspeed_customer_id` (UNIQUE) + `lightspeed_linked_at`,
  `lightspeed_link_source` (`auto_email|auto_phone|staff|back_office`); cleared on erasure.
- `loyalty_pos_receipt` — receipts that named a customer: ids, close time, total, and
  HMACs of the normalised email/phone (no contact stored). `loyalty_pos_award` — what
  each receipt earned on each card (unique per card+receipt).
- One card per member per programme (existing unique key); each card is its own QR,
  web card and wallet pass.

### Lightspeed auto-stamps (`services/loyalty/pos.py`)
- Identity: K-Series Financial API sales with `include=consumer` carry
  `consumer{id, customerId, firstName, lastName, email, phoneNumber1}` — the only customer
  identity on a receipt. `RawReceipt.consumer` parses it (fixtures carry it too).
- `cafeops sync` (and the daily sync) records receipt customers, auto-links a customer
  whose email hash, else phone hash, matches exactly one unlinked live member, then — only
  if `CAFEOPS_LOYALTY_AUTO_STAMP=true` — reconciles, in a SAVEPOINT (a loyalty fault never
  costs the sales). `cafeops loyalty pos-sync [--days N]` does link + reconcile alone.
- Per (card, receipt): STAMPS = eligible item count (voids excluded, refunds net) capped at
  `max_stamps_per_scan`; POINTS = points on eligible spend. PURCHASE event, no staff/device,
  note `lightspeed:<receipt>`. Only receipts closed after the member joined.
- Refund on a later refund-only receipt nets against that customer's most recent earlier
  receipt (≤30 days) that sold the item. A changed answer → UNDO of the old event + a new
  one. Reversal refused (its reward already redeemed) or a manual undo → award frozen `KEPT`.
- **Dedupe window:** a staff PURCHASE scan (or spend) on the same card within
  `CAFEOPS_LOYALTY_POS_DEDUPE_MINUTES` (default 30) either side of the receipt close time
  → the receipt earns nothing (`SKIPPED_STAFF_SCAN`).
- Auto-stamp events are dated when written (Apple's "changed since" needs `updated_at` to
  move forward); the receipt time is on the award row.

### New/extended API
Public: `GET /api/loyalty/programs`; `POST /api/loyalty/join` + `also_join: [slug]` →
`JoinResult.extra_cards[]`; `POST /api/loyalty/card/{id}/programs {program}` → JoinResult
(201, 409 `already_in_program`); `CardState` + `program_slug, program_kind,
points_per_pound, reward_ready_label, program_description, other_cards[] (with their
tokens), joinable[]`.

Staff: `ScanResult` + `program_slug, program_name, program_kind, points_per_pound,
reward_ready_label, max_stamps_per_scan, reward_options[], other_cards[] (ScanResult),
joinable[], lightspeed_linked`. `POST /api/staff/spend {card_id, spend_pence,
manager_pin?}` → StampOut (409 `manager_pin_required` over £50; `stamp` on a points card →
409 `points_card`, `spend` on a stamp card → 409 `stamp_card`). `POST /api/staff/add-card
{card_id, program}`. `GET /api/staff/pos-customers`, `POST /api/staff/link-pos {card_id,
customer_id?|receipt_id?}` (409 `customer_taken`). `RedeemIn.option_id?` (none + several
options: the first covering the item, else 422 `option_required`; item outside the
option/programme eligibility → 409 `not_covered`; option cap, else programme cap →
`over_price_cap`). `GET /api/staff/drinks?reward_id=&option_id=` → + `max_price_pence,
covers`.

Admin: `GET /api/members?program=`, `GET /api/members/stats?program=` (+ `program_slug,
repeat_rate_members, repeat_rate_non_members, repeat_customers_members,
repeat_customers_non_members, repeat_rate_members_by_scans, receipts_with_customer_share,
repeat_rate_reason`); member detail + `cards[], lightspeed{customer_id, linked_at, source,
receipts[]}, auto_stamp`, events/rewards + `program_slug`, rewards + `redeemed_option`;
`AdjustIn.program?`; `PUT|DELETE /api/members/{id}/lightspeed`;
`GET|POST /api/members/programs`, `PUT /api/members/programs/{id}` (manager PIN; kind
locked once anyone has earned → 409 `kind_locked`); `GET /api/members/menu-facets`;
`POST /api/members/eligibility-preview`; `GET /api/members/pos-customers`.

Stats: every figure is for one programme (default the main card). Repeat-visit rate =
share of till customers seen in the window who came on ≥2 local days, members (linked to
a live holder of that programme's card) vs everyone else; null + reason when no receipt
names a customer. Payments are daily totals (no card fingerprints), so non-members are
identifiable only through Lightspeed customers.

### Wallet
One Google class per programme: the main card keeps `<issuer>.<suffix>`; others
`<issuer>.<suffix>-<slug>`, carried inside the save JWT so no console step is needed.
Pass text names the programme; POINTS cards label "Points" and show progress on the
8-slot sticker strip (same `assets/pass/stickers/`).

### Settings
`CAFEOPS_LOYALTY_AUTO_STAMP` (bool, default false), `CAFEOPS_LOYALTY_POS_DEDUPE_MINUTES`
(int, default 30).
