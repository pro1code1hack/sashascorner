# Shell and Agents: implementation spec

Sources (data only): `docs/design/Cafe Ops v2.dc.html` (login, sidebar, banners,
empty-install, Settings) and `docs/design/Agents App v2.dc.html`. The visual tokens and
primitives are in [`design-system.md`](design-system.md); this file uses their names.

Status legend for every datum and action:
**EXISTS**: an endpoint or field returns it today. **DERIVABLE**: the data is in the
database or config and only needs exposing. **MISSING**: new model, service or
endpoint required (a proposal is given).

The design prototype keeps everything in `localStorage` (`sc-shell-v1`,
`sc-agents-v1`, `sc-menu-stock-v1`, `sc-fin-v1`), password included. **None of that
survives into the build.** Every datum below maps to the backend.

---

## 0. Findings that change the build (read first)

1. **The current Unlock screen accepts any password.** `web/src/components/shell.tsx:365`
   validates by fetching `/api/meta`, and `/api/meta` is on the *unauthenticated*
   `open_router` (`cafeops/api/routers.py:130`). A wrong password "unlocks", and the
   first real call then 401s. Validate against an authenticated route instead (the
   `POST /api/auth/session` below, or any `router` GET until that exists).
2. **There is no rate limit on password guesses.** `cafeops/api/security.py:65-99` does
   a constant-time compare and nothing else, and the Caddyfile has no rate limiting.
   The design's "at least 4 characters" rule (`Cafe Ops v2` Settings `savePw`) is
   unsafe on a public domain. See §4.2.
3. **Agent proposals are not persisted.** `AgentProposal` (`cafeops/domain/types.py:1056-1068`)
   exists only in memory during a run; the log keeps a string `proposal_ref`
   (`cafeops/db/models/channel.py:153`, written at `cafeops/agent/runner.py:324`).
   There is no status, no decision and no list. The whole left column of the Agents
   screen is **MISSING**. See §6.
4. **Agents never run on their own.** Agent runs are reachable only from the CLI
   (`cafeops/cli/agent.py`); `jobs/scheduler.py` registers no agent job. The
   design's "In morning digest" result has nothing behind it yet.
5. **The web cannot confirm an order.** `cafeops/api/routers.py:8-11` says so on
   purpose: invariant 1's confirmation happens in Telegram, and the CHECK constraint
   (ARCHITECTURE §5) requires `confirmed_by`. The design's order proposals ("Confirm
   order / Cancel") collide with this. This spec keeps them **read-only, "Waiting in
   Telegram"** (§6.4), pending an owner decision.
6. **The Telegram bot is Russian by construction.** `cafeops/bot/formatters.py:3`: "THE
   ONLY PLACE IN THE CODEBASE THAT HOLDS RUSSIAN", with no English set. A ru/en/both
   switch has nothing to switch to (§4.4).

---

## 1. Existing frontend shell: what to keep

| File | Keep | Replace |
|---|---|---|
| `web/src/lib/api.ts` | `request()` (`:98-119`), `ApiError` (`:88-96`), `API_BASE`/`LIVE` (`:59-60`), fixture mode, key storage in **sessionStorage** under `cafeops.key` (`:62-86`), `WriteResult` handling (`:263`), `preview`/`applyFromToday` (`:370,413`), `confirmSupplierTerms`/`confirmShelfLife`/`materialiseProposal` (`:315-346`) | Add the new endpoints below plus their fixtures. Once §4.2 lands, the stored value becomes a **session token** and is sent as `Authorization: Bearer`, not `X-API-Key` |
| `web/src/App.tsx` | QueryClient defaults (`:14-25`: staleTime 5 min, no refetch on focus, retry 1); the `unlocked` gate (`:29-31`) | `ScreenName` routing by `useState` loses the page on reload. Use hash routes (`#/stock`, `#/agents`, `#/settings`, `#/money/reconcile`) |
| `web/src/components/shell.tsx` | Collapsed-preference pattern with try/catch `localStorage` (`:127-143`); the `s` quick-switcher that ignores keystrokes while typing (`:148-162`); the sign-out behaviour of `KeyState` (`:324-350`: clear key, reload) | All visuals; the `GROUPS` nav (`:28-60`) gives way to the new nav in §2.2; the Unlock validation bug (§0.1); the compatibility shims `Page`/`ScreenTitle` (`:405-428`) |
| `web/src/components/gate.tsx` | All logic: the mandatory impact preview in place, no modal, apply-from-today only | Visuals only |
| `web/src/components/prim.tsx` | `Fig`, `CostFig`, `Qty`, `PenceExact`, `Exact`: the decimal-safe rendering | `EST` (`:66`): dotted underline → italic |
| `web/src/components/ui.tsx` | Semantics of `ScrollX`, `Empty`, `Loading`, `ErrorBox`, `Cell` | Everything visual; drop `StatCard`, `Sparkline`, `DeltaPill`, `TabsUnderline` |
| `web/src/styles.css` | `.scroll-x`, `:focus-visible`, the reduced-motion block | The whole dark `@theme` → the light block in design-system §4 |
| `web/src/screens/Proposals.tsx` | The import-review logic (materialise, conflicts, blocked reasons) | It moves under **Agents** as template proposals (§6.3), with Recipes as the deep editor |

---

## 2. Shell layout

### 2.1 Frame

```
┌ sidebar (232 | 200 | drawer) ┬ main column ─────────────────────────────┐
│ brand block                  │ banner stack (0..2)                      │
│ nav groups + badges          │ page (header + content)                  │
│ ─ flex spacer ─              │                                          │
│ Settings · Sign out          │                                          │
└──────────────────────────────┴──────────────────────────────────────────┘
```

Root: `flex h-dvh bg-surface overflow-hidden`. Main: `flex-1 min-w-0 flex flex-col
min-h-0`. Each page owns its own scroll.

### 2.2 Sidebar

Container: width **232px** (≥ 1280px) or **200px** (900–1279px, the design's `compact`
prop). Styles: `bg-canvas border-r border-line-soft px-2.5 pt-4.5 pb-3.5 flex flex-col
gap-0.5 overflow-y-auto` with the scrollbar hidden.

**Brand block** (`px-2 pb-3.5 flex gap-2.5 items-center`):
- A 34px tile, `rounded-[11px] bg-brand text-white`, showing "S" in 800 16px.
- "Sasha's Corner" in 800 16px, `leading-[1.15]`.
- Below it the sync line in `text-xs`, ellipsised: "sales synced 3 hours ago".
  Source: `sync.last_ok_finished_at` (§3.1, MISSING). If Lightspeed is not configured,
  show "Lightspeed not connected" instead. Use `ink-2`, not the design's `ink-3`
  (contrast, design-system §1.2).

**Nav groups.** The design's list, with where each item points:

| Group head | Item | Route | Existing screen / other spec | Badge |
|---|---|---|---|---|
| Every day | Stock | `#/stock` | `screens/Stock.tsx` | none |
| | Orders | `#/orders` | `screens/Orders.tsx` | **orders waiting**, see below |
| | Agents | `#/agents` | new (§6) | **proposals waiting**, see below |
| Menu | Recipes | `#/recipes` | `screens/Composition.tsx` → recipes spec | none |
| | Menu items | `#/menu` | `screens/Margin.tsx` data → recipes spec | none |
| | Ingredients | `#/ingredients` | new (prices, suppliers) | none |
| | Suppliers | `#/suppliers` | supplier terms confirm (EXISTS) | none |
| Money | Overview, Sales, Expenses, Reconcile, Profit & loss, Director's account | `#/money/<tab>` | finance spec; `screens/Money.tsx`, `Channels.tsx` fold in | none |

**`Today` disappears from the nav.** Its content (`cafeops/api/views/today.py:36-263`)
is re-homed: the `act` alerts become Stock filters and banners, and the data-quality
notes (`today.py:202-231`) move into the Setup checklist (§5). Owner, please confirm.
The existing `Channels` screen becomes Money › Reconcile "Delivery apps", per the
finance design.

Group head: `px-2.5 pt-4 pb-1.5`, 11px/700 uppercase `.08em` `text-ink-3` (see the
design-system note on uppercase). Item: `min-h-10 px-2.5 rounded-button text-md flex
justify-between items-center gap-2`. Inactive is `font-medium text-ink`; active is
`bg-surface text-brand-ink font-bold shadow-raised`. Render each item as a `<button>`
or `<a href="#/…">` with `aria-current="page"` when active. The count badge
(`CountBadge`) is right-aligned and shown only when the count is > 0.

**Badge drivers:**

| Badge | Design source | Backend meaning | Status |
|---|---|---|---|
| Orders | `Sd.orders.filter(o => o.status==='Waiting').length` | POs with `status = PENDING_CONFIRM` (`cafeops/db/models/enums.py:113`), i.e. sent to Telegram and not yet confirmed or cancelled | **DERIVABLE** (the table exists; no endpoint lists POs) → served by `GET /api/shell` |
| Agents | `A.proposals.filter(p => p.status==='waiting').length` | `agent_proposal.status = WAITING`, plus import proposals not yet materialised and not blocked (optional, §6.3) | **MISSING** (§6.1) |

Both counts come from one cheap endpoint (§3.4) that the shell polls with
`staleTime: 60s` and invalidates after any accept/decline or confirmation write.

**Footer:** a flex spacer, then **Settings** (same item style, active when on
`#/settings`), then **Sign out** (`min-h-9 px-2.5 text-base text-ink-3`). Sign out
calls `DELETE /api/auth/session` (MISSING, §4.2) if sessions exist, then clears the key
and reloads, as `shell.tsx:337-345` does today.

### 2.3 Responsive (CLAUDE.md §10: must survive 375px)

The design has two frames: iPad **1180px** (`compact=true`) and desktop **1440px**. There
is no phone frame. Proposed breakpoints:

| Width | Sidebar | Page internals |
|---|---|---|
| ≥ 1280px | 232px, always visible | Wide variants: drawer 420px, list 320px, cost panel 380px, card min 180px |
| 900–1279px (covers the 1180 frame) | 200px, always visible | The design's compact variants: drawer 380px, list 290px, cost panel 320px, card min 150–170px, and the category rail becomes a horizontal chip row (`Menu Stock v2`: `wideRail = !compact`, `showRail` chips) |
| < 900px | **Off-canvas.** A 56px top bar appears: hamburger `IconButton` (40px) · "S" tile · current page title (`text-lg`, truncated) · the sum of the Orders and Agents badges on the hamburger as a `CountBadge`. The sidebar slides in as a fixed 280px panel over a `rgba(31,38,51,.24)` scrim. It closes on navigation, scrim tap or Esc, traps focus while open and returns focus to the hamburger | Drawers become full-screen sheets; two-pane layouts (Agents, Ingredients) stack; tables sit in `ScrollX` or switch to stacked rows below 640px |
| < 640px | as above | Banners wrap: text on line 1, the action and × on line 2, right-aligned. The Settings grid goes to one column (label above control). The empty-install step CTA wraps under the text |

At 375px the page itself must never scroll horizontally (DESIGN-LAW data law, kept).
Touch targets are ≥ 40px: raise the banner × from 32 to 40px and the nav items stay at
40px. Honour `prefers-reduced-motion` for the slide-in.

---

## 3. Banners

Rendered by `BannerStack` above the page, from `GET /api/shell → banners[]`, in the
order the server gives. Each has an id, tone, text, action and dismiss.

**Dismissal** is per browser session and per *instance*. Store
`sessionStorage['cafeops.banner.dismissed']` as a set of `banner.instance_key` values,
for example `stale:2026-09-25T02:31Z` or `cash:2026-03-20`. The banner then returns
when the condition becomes a new instance. This matches the design's `closed` state,
which resets on reload.

### 3.1 Stale Lightspeed sync

Design: shown when `Date.now() - cfg.syncAt > 12h`, tone neutral (`bg #f1f3f7`, dot
`#8a93a3`). Text: *"Lightspeed sales last came in {26 hours ago}. Stock estimates and
takings are behind until the next sync."* Action: **Sync now**.

| Datum | Source | Status |
|---|---|---|
| "last came in": the last *successful sync*, not the last sale | `max(sale.sold_at)` (`cafeops/db/models/sale.py:31`, indexed `:51`) is available, but it is the **wrong clock**: the café closes in the evening and the sync runs at 02:30 (`cafeops/jobs/scheduler.py:83-84`), so by 08:00 the newest sale is always more than 12h old and the banner would fire every morning | **MISSING**: `sync_run` table (below) |
| Threshold 12h | design constant | move to config `CAFEOPS_SYNC_STALE_HOURS=12` (DERIVABLE) |
| Lightspeed connected? | `settings.lightspeed_configured` (`cafeops/config.py:112-113`) | DERIVABLE |
| Sync now | `jobs/daily_sync.run_daily_sync` (`cafeops/jobs/daily_sync.py:81-100`) → `integrations/lightspeed/sync.sync_window` (`sync.py:120-168`) | **MISSING** endpoint (§4.3) |

**When Lightspeed is not configured** (the state in production today, where
`run_daily_sync` skips with a reason at `daily_sync.py:93-98`), do **not** show "last
came in 3 days ago" forever. Show one neutral banner instead: *"Lightspeed isn't
connected, so sales aren't coming in by themselves. Stock is worked out from what was
imported by hand."* Its action is "Settings". It is dismissible once per session.

**Proposed model**, `sync_run` (new, Alembic migration):

```python
class SyncRun(Base):
    __tablename__ = "sync_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str]            # SCHEDULED | MANUAL_WEB | CLI
    source: Mapped[str]             # LIVE | FIXTURES
    started_at: Mapped[datetime]    # UTCDateTime
    finished_at: Mapped[datetime | None]
    status: Mapped[str]             # RUNNING | OK | PARTIAL | SKIPPED | FAILED
    window_since: Mapped[date]; window_until: Mapped[date]
    receipts_seen: Mapped[int | None]; lines_ingested: Mapped[int | None]
    unresolved_count: Mapped[int | None]   # from SyncResult.lines()
    detail: Mapped[str | None]      # skipped_reason / partial_reason / exception
    requested_by: Mapped[str | None]  # "web" | "scheduler" | "cli"
```

`run_daily_sync` and `cafeops sync` write one row each (start, then finish). `PARTIAL`
comes from `SyncResult.partial_reason` (`sync.py:104-105`); `SKIPPED` comes from
`report.skipped_reason`.

### 3.2 Cash discrepancy

Design: the most recent day where the declared cash differs from the till's cash by
**more than £5 (500p)** and nobody has explained it. Tone alert (`bg #fdf1ef`, dot
`#d4554a`). Text: *"Cash was £4.20 short on Fri, 20 Mar 2026 and nobody has explained
it yet."* ("over" when positive). Action: **Look at it** → `#/money/reconcile`.

| Datum | Source | Status |
|---|---|---|
| Cash for a day | `payment_day` with `method = CASH` (`cafeops/db/models/payment.py:46-66`, `enums.py:448`) | DERIVABLE |
| Counted (declared) cash | none: `PaymentDay` has gross, refunds, fees, discounts and transactions, but **no counted figure** | **MISSING** |
| "nobody has explained it" | none | **MISSING** |

**Proposed model**, `cash_count` (owned by the finance spec; the shell only reads it):
`business_date (unique)`, `counted_pence int`, `counted_by str`, `counted_at`,
`explanation text | null`, `explained_by`, `explained_at`. Discrepancy is
`counted_pence - Σ payment_day(CASH).gross_pence` for that date, computed at read time
and never stored. It is **null** (no banner) when either side is missing, so no
figure is invented. The banner shows the newest date with `|diff| > 500` and
`explanation IS NULL`.

### 3.3 Not banners

Keep it to these two. `today.py`'s `act` alerts (drift forced manual, expiry
write-offs due, negative on-hand) are Stock screen content. Turning them into global
banners is the "operations console" CLAUDE.md §10 rejects. One exception: in fixture
mode (`LIVE=false`, `api.ts:60`), show a permanent neutral strip, "Fixture data:
recorded responses, nothing is live", with no action and no close.

### 3.4 `GET /api/shell`: one cheap call for the frame (MISSING)

It must **not** call `stock_view` or `draft_orders_view`, which take seconds. It only
counts rows and reads `sync_run`.

```ts
interface ShellResponse {
  sync: {
    lightspeed_configured: boolean            // config.py:112
    last_ok_finished_at: string | null        // sync_run, status OK|PARTIAL
    last_attempt: { status: 'RUNNING'|'OK'|'PARTIAL'|'SKIPPED'|'FAILED'; finished_at: string|null; detail: string|null } | null
    last_sale_at: string | null               // max(sale.sold_at), for Settings only
    stale_after_hours: number                 // 12
    is_stale: boolean                         // server-decided, so the rule lives in one place
  }
  badges: { orders_waiting: number; proposals_waiting: number }
  banners: Banner[]
  setup: { empty_install: boolean; open_steps: number }   // §5
}
interface Banner {
  id: 'stale_sync' | 'lightspeed_not_connected' | 'cash_discrepancy'
  instance_key: string
  tone: 'stale' | 'alert'
  text: string                 // server-authored sentence (English)
  action: { label: string; kind: 'sync_now' | 'navigate'; route?: string } | null
}
```

---

## 4. Login and Settings

### 4.1 Login screen

Layout: `flex-1 grid place-items-center bg-canvas`. Card: `w-[min(380px,100%-32px)]
rounded-login bg-surface px-8 pt-9 pb-7 shadow-login`.

- An "S" tile (44px, `rounded-[14px] bg-brand`, 800 20px) with `mb-4.5`.
- "Sasha's Corner" in 800 26px -.01em.
- "Back office · 23 Commercial Street" in `text-md text-ink-2 mb-4.5`.
- The label "Password" (`text-base mb-1.5`), then a `PasswordInput` with the
  placeholder "shared password" and autofocus. Enter submits.
- An error line (`min-h-6 text-base text-alert my-1.5`): *"That's not it. Ask whoever
  runs the café."* (design copy). On 503, meaning no password is configured
  (`security.py:46-50,71-75`), show *"The back office has no password set yet. Set
  CAFEOPS_API_PASSWORD on the server."* On 429 (proposed), show *"Too many tries. Wait
  a minute."*
- **Open**: `Button primary block size=lg`.
- Footnote in `text-sm text-ink-3 mt-3.5`: *"One password for everyone on the team.
  Change it in Settings."* **Delete the design's "(Demo: corner)".**

Render it as a `<form>` so password managers work. Validate against an
**authenticated** route (§0.1).

### 4.2 Settings: the shared password

Settings layout: `px-7 py-5`, title `text-2xl mb-3.5`, then
`grid grid-cols-[220px_minmax(0,1fr)] gap-x-5 gap-y-4 max-w-[820px] text-md
items-center`. Labels are `text-ink-2`. Below 640px it becomes one column.

**Rows, and the decision for each in production:**

| Row | Design | Production decision | Backend |
|---|---|---|---|
| Shared password | new-password input + Change + message | **KEEP**, with the safe design below | MISSING |
| Lightspeed | "Connected (demo) · sales last came in X" + Sync now + "pretend it's stale" | **KEEP** status and Sync now; **DROP** "pretend it's stale" (a demo control) | status DERIVABLE; sync MISSING (§4.3) |
| Telegram bot language | select ru/en/both | **DROP the control.** Show read-only: "Russian. The bot has no English text yet." | §4.4 |
| Fresh start | "Show the empty-install screen" | **KEEP, renamed** "Setup checklist". It opens §5 with the done/open state of each step. Read-only and harmless | MISSING `/api/setup` |
| Demo data | "Reset demo data" (arm, then confirm) | **DROP.** Never an HTTP route (below) | n/a |

Proposed read-only additions, one line each, each **DERIVABLE** from `config.py`:
- Telegram bot: "connected" / "no bot token" (`config.py:46`).
- Agent narration: "Claude (`agent_model`)" / "template sentences, no API key"
  (`config.py:93-96`).
- Staff time rate: "£14.50/hr loaded" (`config.py:70`, already served in
  `/api/meta.loaded_hourly_rate_pence`, `views/meta.py:89`).

Put these in the same `GET /api/settings` response (§4.5).

**Why "Reset demo data" must not ship.** Reset means `cafeops seed --demo`, which calls
`services/rebuild_batches.py` with `purge=True`: the one documented exception to the
append-only ledger (CLAUDE.md invariant 12, "a new caller of rebuild_batches is a
decision, not a refactor"). A button that erases the ledger on a shared-password web
app is the wrong place for that exception. If a demo deployment ever wants it, gate it
behind `CAFEOPS_DEMO_MODE=1` **and** keep it CLI-only.

#### Changing the shared password at runtime: proposed design

Today the password is `CAFEOPS_API_PASSWORD`, read from `.env` on each request
(`security.py:53-62`), and sent raw as `X-API-Key` on every call (`api.ts:104`). A
runtime change needs three things the current design lacks: somewhere to store it,
revocation of the old secret everywhere, and protection against guessing.

1. **Storage: a hash, not a value.** New single-row table `auth_credential(id=1,
   password_hash, algo, set_at, set_via)`. Hash with `hashlib.scrypt` (stdlib; n=2^14,
   r=8, p=1, 16-byte salt), stored as `scrypt$n$r$p$salt_b64$hash_b64`. The plaintext is
   never stored or logged.
2. **One source, with an explicit precedence.** The docstring at `security.py:25-28`
   warns about "two declarations of one secret". The rule: if an `auth_credential` row
   exists, it wins; otherwise `CAFEOPS_API_PASSWORD` is the bootstrap. `cafeops doctor`
   (`services/doctor.py:527-537`) reports *which* is in force, and `/api/health`'s
   `auth_configured` is true for either. Break-glass: `cafeops password reset` deletes
   the row. Shell access is already full trust, so the env password takes over again.
3. **Sessions, so a change can revoke.** `POST /api/auth/session {password}` returns
   `{token, expires_at}`. The token is 32 random bytes (`secrets.token_urlsafe`), stored
   as `sha256(token)` in `auth_session(id, token_hash, created_at, last_seen_at,
   expires_at, user_agent)`, with a 30-day absolute and 7-day idle expiry. The browser
   stores the token (not the password) in sessionStorage and sends
   `Authorization: Bearer <token>`. `require_password` (`security.py:65-99`) accepts a
   valid session token, **or** the password via `X-API-Key` for the CLI and the fixture
   dumper (`cafeops api-fixtures`). The KDF runs on that path and at login only; cache
   the last successful verification in process for 60s keyed by an HMAC of the
   presented value, so tools do not pay scrypt per request. `DELETE /api/auth/session`
   is sign-out.
4. **The change endpoint.** `POST /api/auth/password {current_password, new_password}`
   → `200 {token, expires_at}`. It:
   - requires a valid session **and** the current password, so an unattended unlocked
     laptop cannot change it;
   - requires `len(new) >= 10`, `new != current`, and rejects a short denylist
     (`password`, `corner`, `sashas`, the café name). The design's 4-character minimum
     is refused;
   - in one transaction, upserts `auth_credential`, **deletes every `auth_session`
     row**, and issues a fresh token for the caller only;
   - writes an audit row to `settings_change(id, at, key='password', via='web',
     session_id)` with no secret material;
   - sends the owner a Telegram notice through `bot/notify.py`: "Web password changed
     at 14:05", so an unexpected change is seen.
   Every other device then gets 401, and the frontend clears the key and shows Unlock
   with *"The password was changed. Ask whoever runs the café."*
5. **Guess limiting.** An in-process token bucket keyed by client IP (from Caddy's
   `X-Forwarded-For`, trusted only from the proxy) allows 5 failed attempts per minute
   and 30 per hour on `/api/auth/session`, on `X-API-Key` failures and on
   `/api/auth/password`, then answers 429. One process and one box (CLAUDE.md §3) make
   in-process state correct here.
6. **UI copy** (design): input placeholder "new password", button "Change", message
   "Changed. Tell the team." Add a "current password" input before it. Validation
   messages are inline in `text-sm text-ink-2`, and errors in `bad-ink`.

### 4.3 Lightspeed row and "Sync now"

Row content: *"Connected · sales last came in {ago}"* + `Button outline sm` **Sync now**.

- Not configured: *"Not connected. Set CAFEOPS_LIGHTSPEED_CLIENT_ID,
  CAFEOPS_LIGHTSPEED_CLIENT_SECRET, CAFEOPS_LIGHTSPEED_REFRESH_TOKEN,
  CAFEOPS_LIGHTSPEED_BUSINESS_ID on the server."* Give the names (`config.py:31-35`),
  never the values. Sync now is hidden. Connecting is an ops task, not a web form: the
  refresh token is a long-lived credential, and putting it behind the shared password
  widens what one leaked password costs.
- Last attempt failed or partial: add a second line in `text-sm`, `bad-ink` for
  FAILED and `ink-2` for PARTIAL, carrying `sync_run.detail`.

`POST /api/sync` (MISSING) → `202 {run_id}`:
- It returns 409 `{message}` if a `sync_run` is `RUNNING` and started less than 15 min
  ago. The row is the lock, because SQLite has one writer (CLAUDE.md §3); an older
  RUNNING row is marked FAILED "abandoned".
- It returns 412 if Lightspeed is not configured. The web never triggers a fixtures
  sync, because that would re-ingest sample receipts into a real database.
- It runs `run_daily_sync(fixtures=False)` then the nightly expansion
  (`jobs/nightly_expand.py`) in `asyncio.to_thread`, so sales become movements and
  stock updates. Otherwise "Sync now" would bring sales in and leave the stock
  estimate stale, which is the thing the banner promised to fix.
- Rate limit: one manual sync per 5 minutes.

`GET /api/sync/{run_id}` → the `sync_run` row. The UI polls every 2s while RUNNING,
shows "Syncing…" in the button, and invalidates `['shell']`, `['stock']` and
`['takings']` on finish.

### 4.4 Telegram bot language

**Decision: do not ship the select.** The bot's text is Russian-only by design
(`bot/__init__.py:1`, `bot/formatters.py:3`, `bot/viewmodels.py:4-5`), and CLAUDE.md §10
says "Daily interface is the Telegram bot (Russian)". A setting that switches to a
language that does not exist is a lie in the UI.

If the owner wants English later: the key `bot_language ∈ {ru, en, both}` goes in a new
`app_setting(key PK, value JSON, updated_at, updated_by)` table, read by the bot per
message (not cached, so no restart is needed). `formatters.py` needs an English sibling
with the same function names, and `both` means Russian first with English as a second
paragraph. Endpoint: `PUT /api/settings/bot-language {value}`.

### 4.5 `GET /api/settings` (MISSING, all DERIVABLE except the sync fields)

```ts
interface SettingsResponse {
  auth: { source: 'database' | 'environment'; set_at: string | null; active_sessions: number }
  lightspeed: { configured: boolean; env_vars: string[]; last_ok_finished_at: string | null;
                last_sale_at: string | null; last_attempt: ShellResponse['sync']['last_attempt'] }
  telegram: { bot_configured: boolean; owner_chat_configured: boolean; language: 'ru' }
  agent: { narration: 'model' | 'template'; model: string | null }
  labour: { loaded_hourly_rate_pence: number | null }
}
interface PasswordChangeIn { current_password: string; new_password: string }
interface SessionOut { token: string; expires_at: string }
```

---

## 5. Empty-install screen ("Nothing here yet.")

Layout: `flex-1 overflow-y-auto px-11 py-9` (`px-4 py-6` below 640px).

- Headline: "Nothing here yet." (`text-4xl`).
- Lede: "Five things before the numbers mean anything. Do them in order; each one makes
  the next more accurate." (`text-lg text-ink-2 max-w-[640px] mt-1.5 mb-6`).
- Steps: `flex flex-col gap-2.5 max-w-[720px]`. Each step is `Card`: a 30px numbered
  circle (`border border-line rounded-full`), then title (`text-lg` 16px, weight 400
  in the design) and body (`text-base text-ink-2`), then the CTA. The design highlights
  the first CTA only (`on(i === 0)`). Build: highlight the **first step that is not
  done**. Done steps show a ✓ in the circle and their CTA as `outline`.

**When it shows:** automatically when the install is empty, i.e. `doctor`'s legacy
import check fails with no ingredients (`services/doctor.py:164-176`). Otherwise it is
reachable from Settings › Setup checklist, where the same list shows done states.

| # | Title / body (design copy) | Done when | Count in the body | CTA → | Backend |
|---|---|---|---|---|---|
| 1 | Bring in the spreadsheet. "Ingredients, prices, recipes and menu from the finance workbook. It proposes recipes; you confirm them." | `count(ingredient) > 0` (doctor `_check_seed`, `doctor.py:164-181`) | "{n} recipe proposals waiting" = `/api/proposals` not materialised (`views/proposals.py:122-142`, EXISTS) | "Import workbook" → `#/agents?kind=template` once imported. Before import there is **no web import**: `import-legacy` is CLI-only. Show the command in a copy block: `uv run cafeops import-legacy --commit` | DERIVABLE; a web upload is MISSING and **not recommended** now (a writing import behind the shared password) |
| 2 | Add shelf lives for fresh stock. "Not in the workbook. Without them, orders can't be capped to what will keep. {N} still missing." | no perishable with `shelf_life_source = ESTIMATE` | N from doctor `_check_trust_of_inputs` (`doctor.py:482-500`). Note that the backend seeds every shelf life as ESTIMATE (CLAUDE.md §6), so "missing" means **not confirmed**. Say "{N} are still guesses" | "Open Stock" → Stock filtered to unconfirmed shelf lives | DERIVABLE; the write EXISTS: `POST /api/ingredients/{id}/shelf-life` (`routers.py:254-265`) |
| 3 | Confirm supplier terms. "Delivery days, cut-offs, minimums and fees. {N} are still guesses." | no supplier with `terms_are_placeholders = true` | N from doctor `doctor.py:468-481`, or `GET /api/suppliers` (`routers.py:330-337`) | "Open Suppliers" | EXISTS: `POST /api/suppliers/{id}/confirm` (`routers.py:240-251`) |
| 4 | Connect Lightspeed. "So sales come in by themselves and stock is worked out from them." | `lightspeed_configured` and a `sync_run` with status OK | none | "Settings" | config DERIVABLE (`config.py:112`); sync history MISSING (§3.1) |
| 5 | Do a first count. "Tier A first. Every estimate starts from a real count." | every tier A ingredient has at least one `stock_count` | "{n} of 12 tier A counted" | "Start counting" → Stock in count mode, tier A | DERIVABLE (`stock_count`; `StockSummary.unanchored` via `today.py:203`) |

**`GET /api/setup` (MISSING).** It exposes the doctor to the web: `run_doctor` has no API
route today (only `cli.py:3042`). Implement it as `services/setup_status.py`, reusing
doctor's check functions but **not** `_check_batch_coverage`, which walks every
ingredient's on-hand.

```ts
interface SetupResponse {
  empty_install: boolean
  steps: Array<{
    n: 1|2|3|4|5; key: 'import'|'shelf_life'|'supplier_terms'|'lightspeed'|'first_count'
    done: boolean; remaining: number | null      // null = not countable, not 0
    title: string; body: string                   // server copy, counts filled in
    cta: { label: string; route: string } | null
    cli_fix: string | null                        // doctor's `fix`, shown in a copy block
  }>
  warnings: Array<{ severity: 'FAIL'|'WARN'|'INFO'; name: string; detail: string; fix: string }>
}
```

`warnings` carries the rest of the doctor's report (drift backfill, invariant checks,
estimated prices). Show it under the steps as "Other things the system noticed", in
`text-base`. This is also where `today.py`'s data-quality notes land.

---

## 6. Agents screen

### 6.1 The proposals model (MISSING): `agent_proposal`

A proposal is **the only thing an agent may produce that changes anything**, and only
after a person accepts it (CLAUDE.md §9, invariant 10). It needs a durable home with a
status.

```python
class AgentProposal(Base):            # db/models/channel.py, beside AgentActionLog
    __tablename__ = "agent_proposal"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime]                       # UTCDateTime
    run_id: Mapped[str] = mapped_column(String(64))    # joins agent_action_log.run_id
    log_id: Mapped[int] = mapped_column(ForeignKey("agent_action_log.id"))
    agent: Mapped[str]         # drift_explainer | import_assistant | channel_reporter | basket_stager
    kind: Mapped[str]          # waste_factor | template_grouping | channel_import | data_fix | supplier_basket
    subject_ref: Mapped[str]   # "ingredient:42", "template:Latte", path, "po:17"
    title: Mapped[str]         # ≤ 80 chars, English
    body: Mapped[str]          # AgentProposal.summary, or verified narration
    payload: Mapped[dict]      # JSON; decimals as strings (invariant 11)
    confidence: Mapped[str | None]   # high | medium | low | none
    figures: Mapped[list[str]]       # ToolResult.figures the body may quote
    status: Mapped[str]        # WAITING | ACCEPTED | DECLINED | SUPERSEDED | APPLY_FAILED
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[str | None]   # "web" | telegram username; required when decided
    decision_note: Mapped[str | None]
    applied_result: Mapped[dict | None]   # what the service did (old -> new, ids)
    __table_args__ = (
        CheckConstraint("status = 'WAITING' OR (decided_at IS NOT NULL AND decided_by IS NOT NULL)",
                        name="ck_agent_proposal_decided_by_human"),
        Index("ix_agent_proposal_status", "status", "created_at"),
    )
```

The CHECK mirrors invariant 1's constraint (ARCHITECTURE §5): nothing leaves WAITING
without a recorded human.

**Who writes it.** The agent's only write path is the audit engine, which today may
touch `agent_action_log` and nothing else (`cafeops/agent/policies.py:189-223`,
`AUDIT_TABLE` `:132`). **Decision required:** allow `INSERT` into `agent_proposal` on
the audit engine. This spec recommends it: a proposal is an audit artefact, not stock,
orders or composition, and `FORBIDDEN_TABLES` (`policies.py:97-129`) stays untouched.
Implement it by changing `AUDIT_TABLE` to a two-member set that allows only `INSERT`
(no `UPDATE`) into `agent_proposal`. Status changes then happen only through the
human-side service `services/agent_proposals.py`, which never runs on the agent's
engine. `AgentRun._record` (`runner.py:314-349`) inserts the proposal in the same
commit as its log row whenever `proposal is not None`. Add an `agent` column to
`agent_action_log` too; today the only label is free-text `purpose`
(`runner.py:561,572`, `commands.py:97,197`).

**Statuses:**

| Status | Meaning | Shown as |
|---|---|---|
| `WAITING` | Created by a PROPOSE tool; nothing has changed | card in "Waiting for you" |
| `ACCEPTED` | A person accepted; the kind's service ran and succeeded | "accepted" in `ink` |
| `DECLINED` | A person declined; nothing changed | "declined" in `ink-3` |
| `SUPERSEDED` | At accept time the subject no longer matched `payload.current` (for example waste_factor already changed, or the template already materialised). Nothing applied | "out of date" in `ink-3` |
| `APPLY_FAILED` | Accepted, but the service raised; the transaction rolled back | "didn't apply" in `bad-ink`; the error goes in `applied_result.error`, and the proposal returns to WAITING on retry |

### 6.2 Endpoints (MISSING)

```
GET  /api/agents/proposals?status=waiting|decided&limit=6   -> AgentProposalsResponse
POST /api/agents/proposals/{id}/accept   {note?: string}    -> DecisionResponse   (409 if not WAITING or SUPERSEDED)
POST /api/agents/proposals/{id}/decline  {note?: string}    -> DecisionResponse
GET  /api/agents/runs?agent=&limit=30&before=<iso>           -> AgentRunsResponse
```

Runs are read with `SqlAgentLogRepository.rows()`/`runs()`
(`cafeops/db/repositories/agent_log.py:131-173`, EXISTS) and grouped by `run_id`.

```ts
type ProposalKind = 'waste_factor' | 'template_grouping' | 'channel_import' | 'data_fix' | 'supplier_basket'
type ProposalStatus = 'WAITING' | 'ACCEPTED' | 'DECLINED' | 'SUPERSEDED' | 'APPLY_FAILED'
interface AgentProposal {
  id: number; created_at: string; agent: string; agent_label: string   // "Drift explainer"
  kind: ProposalKind; subject_ref: string
  title: string; body: string; confidence: 'high'|'medium'|'low'|'none'|null
  accept_label: string; decline_label: string   // server-decided per kind (table below)
  accept_mode: 'apply' | 'navigate'            // navigate = accept opens an editor, applies nothing
  navigate_to: string | null                   // e.g. "#/recipes/proposal/abc123"
  status: ProposalStatus; decided_at: string | null; decided_by: string | null
  run_id: string
}
interface AgentProposalsResponse { waiting: AgentProposal[]; decided: AgentProposal[]; waiting_count: number }
interface DecisionResponse { proposal: AgentProposal; applied: Record<string, string> | null; message: string }
interface AgentRunRow {
  run_id: string; agent: string; agent_label: string; started_at: string; finished_at: string
  tools: Array<{ tool_name: string; kind: 'READ'|'PROPOSE'|'BROWSER'; outcome: 'OK'|'REFUSED'|'FAILED'|'AWAITING_HUMAN';
                 inputs: Record<string, unknown>; output: string | null; refusal_reason: string | null }>
  produced: string                // headline: last narration output, or the proposal title
  result: string                  // derived label, below
  result_tone: 'plain' | 'alert'
  read_summary: string            // "Read: …" line, built from inputs
  wrote_summary: string           // "nothing directly", "nothing directly (proposal only)" …
  model: string | null
}
interface AgentRunsResponse { runs: AgentRunRow[]; agents: Array<{ agent: string; label: string }> }
```

**Result label derivation** (server-side, from the log's `outcome`, `AgentToolOutcome`
at `enums.py:163-169`, joined to `agent_proposal`):

| Condition | `result` | tone |
|---|---|---|
| any call FAILED and the run used a BROWSER tool | "Failed. Use a CSV export" | alert |
| any call FAILED | "Failed" | alert |
| any call REFUSED | "Refused: {first reason, 60 chars}". Refusals are the whitelist working (`channel.py:135`); show them, not as errors | plain |
| a proposal exists and is WAITING / ACCEPTED / DECLINED | "Proposal waiting" / "Accepted" / "Declined" | plain |
| BROWSER tool with `stops_at_human` (basket) | "Staged for review" | plain |
| narration only | "Explained" (becomes "In morning digest" once the digest job sends it, §6.6) | plain |

`read_summary` joins the READ tools' inputs into prose ("Read: drift report, Oat milk
counts, write-offs since last count"). `wrote_summary` is always "nothing directly",
plus " (proposal only)" when a PROPOSE tool ran, or " (basket staged, not sent)" for
the basket tool. This is the design's "Wrote to: nothing directly (proposal only)",
made true by construction.

### 6.3 What each proposal kind contains, and its accept/decline flow

| Kind | Produced by | Title / body example | Accept label | Accept does | Decline |
|---|---|---|---|---|---|
| `waste_factor` | `propose_waste_factor` (`agent/tools.py:466-504`, registered `:691-712`) | "Napkins: allow a little more waste" / "Napkins came in 14% short at the last count and none of it was out-of-date stock. Raising the waste allowance from 1% to 3% would bring the estimate in line. Nothing changes until you accept." | Accept | If `payload.current != ingredient.waste_factor` → SUPERSEDED. Else apply. **Gap:** the existing `services/record_count.apply_waste_suggestion` (`record_count.py:530-560`) adopts the *latest drift observation's* suggestion, not an arbitrary value. Either restrict the tool to propose exactly that suggestion (recommended: the agent narrates, it does not choose numbers, CLAUDE.md §9), or add `set_waste_factor(ingredient_id, value, actor, reason)`. Invariant 7 reminder in the confirmation text: "Changes stock depletion only, not menu costs." | Decline |
| `template_grouping` | `propose_template_grouping` (`tools.py:507-536`), **and** the deterministic import proposals (`GET /api/proposals`, `views/proposals.py:122-142`). Recommend showing both here; the import ones are labelled agent "Import assistant" | "12 recipe patterns covering 78 items" or one per template: "Latte: 9 items share one recipe" | "Confirm recipe" | When `blocked_reason` is null: `POST /api/proposals/{proposal_id}/materialise` (EXISTS, `routers.py:209-220`; the service commits its own transaction, `views/proposals.py:145-153`). When it has conflicts or is hollow (`views/proposals.py:64-80`): `accept_mode = navigate`, label "Review in Recipes", no one-tap apply. The agent's own grouping carries names, not a `proposal_id`, so it is always navigate | "Not now" (DECLINED; the import proposal stays listed in Recipes) |
| `channel_import` | `propose_channel_import` (`tools.py:539-`) | "Deliveroo export, 7 days: 34 orders, £143.20" | "Import" | Run the channel CSV import for `payload.path`. Today that is the CLI `cli/channels.py` (`import_cmd`): extract a service function first | Decline |
| `data_fix` | **no tool exists** (the design's "'Strawberry bliss' looks like a typo") | as in the design | per payload: "Merge" / "Keep separate" (design's custom labels) | `accept_mode = navigate` to the item editor; there is no merge service, and merging menu items touches `menu_item` (forbidden to the agent, fine for a human through a service). Add later | "Keep separate" |
| `supplier_basket` | `browser_stage_supplier_basket` (`tools.py:671-687`, `stops_at_human=True`) | "Brakes basket staged: 6 lines, £123.40" | "Open basket" | Navigate to the supplier portal or basket reference. **Never submits** (spec §9) | "Discard" |
| *order confirmation* | not an agent proposal | "Brakes order, £123.40: waiting in Telegram" | none | Shown read-only in "Waiting for you" with a Telegram note; confirmation stays in Telegram per `routers.py:8-11` and ARCHITECTURE §5. **Owner decision** if the web should confirm: it would need `POST /api/orders/{id}/confirm` with `confirmed_by = 'web'`, satisfying the CHECK constraint, and an ARCHITECTURE entry | none |

All accept paths run in `services/agent_proposals.py`. That service is the human side:
it runs on the normal engine, re-checks `status == WAITING` under the write, and sets
`decided_by = 'web'` (later a session label). It stores `applied_result` and commits
**once**. Materialise is the exception because it commits itself: record the decision
after it returns, and on its 409 mark the proposal SUPERSEDED.

### 6.4 Visual and interaction spec

**Header** (`PageHeader`): "Agents", with the subtitle "they read, work things out and
propose; a person confirms".

**Body:** `grid grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]`, each column scrolling on
its own with `px-5 py-4`. The left column has `border-r border-line`. Below 900px it
becomes one column: Waiting first, then Decided and "What agents may do", then Run log.

**Left column**

1. Heading "Waiting for you · {waiting_count}" (`text-xl`, 17px, `mb-2`).
2. Each WAITING proposal is a `Card mb-2.5`:
   - Meta row `flex justify-between text-sm text-ink-2`: `{agent_label}` · `{relative
     time}` ("5 h ago", "just now", "2 d ago": the design's `ago()`).
   - Title `text-lg` (16px, weight 400 in the design; keep 400, since the section head
     is the bold one), `mt-0.5 mb-1`.
   - Body `text-base text-ink-2`. Numbers in the body must be the tool's verified
     `figures` (`runner.py:112` `verify_figures`); do not render a body whose
     narration failed verification. Fall back to the deterministic `summary`.
   - A low-confidence proposal gets a line `text-sm text-ink-2 italic`: "Low
     confidence: worth checking before accepting." It sits **in place of** nothing,
     because the body has no number of its own (invariant 9 spirit).
   - Actions `flex gap-2 mt-2.5`: `Button primary sm` {accept_label}, then `Button
     outline sm` {decline_label}.
   - **Pending:** on click both buttons disable; the pressed one reads "Applying…" or
     "Declining…". Nothing is optimistic, because accept is a real write.
   - **Success:** the card leaves the list and prepends to "Decided recently". A
     one-line confirmation appears in its place for 4s (`text-sm text-ink-2`, e.g.
     "Accepted. Napkins waste allowance is now 3%. Stock estimates use it from the
     next count."), taken from `DecisionResponse.message`. Invalidate `['shell']`,
     `['agents','proposals']`, `['agents','runs']`, and the subject's queries
     (`['stock', id]`, `['proposals']`, `['templates']`).
   - **409 SUPERSEDED:** the card stays with an inline `text-sm text-ink-2` note, "This
     changed since the agent proposed it: {detail}. Nothing was applied.", and one
     button, "Dismiss", which moves it to Decided.
   - **Error:** inline `text-sm text-bad-ink` with the server's message. The buttons
     re-enable.
   - `accept_mode = navigate` shows the accept button as `outline` and routes. The
     proposal stays WAITING until the target screen's own write resolves it (that
     screen calls accept on success).
3. Empty (`noneWaiting`): a `Card text-base text-ink-2`. The design's copy is "Nothing
   waiting. Orders sent from Orders show up here and in Telegram." Given §0.5, change it
   to **"Nothing waiting. Order confirmations happen in Telegram."**
4. "Decided recently" (`text-lg mt-4.5 mb-1.5`): the last 6 by `decided_at`. Each row
   is `flex gap-2.5 text-base py-1 border-b border-line`, with a status word in a
   76px column (`accepted` in `ink`; `declined` and `out of date` in `ink-3`, raise to
   `ink-2` for contrast; `didn't apply` in `bad-ink`), then the title (`flex-1`), then
   the relative time (`text-ink-3`).
5. "What agents may do" (`Card mt-5 text-base`): static copy, kept verbatim from the
   design. It is consistent with CLAUDE.md §9 and invariant 10:
   - **What agents may do:** "Read stock, sales, prices and uploads. Pull reports from
     sites with no API (Deliveroo, Just Eat). Explain numbers in plain words. Propose
     changes."
   - **What they never do:** "Order, pay, or change stock, orders or recipes
     themselves. Anything that spends money stops at a person. Every run is logged on
     the right with what it read and what it produced."

**Right column: Run log**

- Heading row `flex items-center gap-2 flex-wrap mb-2.5`: "Run log" (`text-xl`), then
  filter chips "All" plus one per `agents[]` (`FilterChip`; the design uses solid
  pills, see design-system §5.4). The filter is a query param, not client-only, so
  paging stays correct.
- Header row: `grid grid-cols-[96px_minmax(0,1fr)_minmax(0,1.4fr)_110px] gap-2.5 py-1
  text-sm text-ink-2 border-b border-line`, with columns When · Agent · tool · Produced
  · Result.
- Rows: the same grid, `text-base py-[7px] border-b border-line items-start`, as a
  `<button aria-expanded>` across the row.
  - When: relative time in `text-ink-2`.
  - Agent · tool: `{agent_label}`, a line break, then `{tool_name}` in `text-xs
    text-ink-3`. The design shows tool strings like "browser: partner portal →
    reports"; map `tool_name` to a human label server-side
    (`read_drift_report`→"read: drift report",
    `browser_plan_channel_report`→"browser: partner portal → reports").
  - Produced: `produced`, the narration in quotes when it is a narration.
  - Result: `result` in `ink-2`, or `alert` when `result_tone = alert`.
- Expanded (one at a time, as in the design): `ml-[106px] border-l border-line px-2.5
  py-0.5 text-sm text-ink-2`, showing `Read: {read_summary}`, then `Wrote to:
  {wrote_summary}`, then, when present, `Refused: {refusal_reason}` and `Model:
  {model}`. Below 640px drop the left indent.
- **Human decisions in the log.** The design writes "Owner (web) · confirm" rows into the
  run log. **Do not store them in `agent_action_log`.** That table is "every agent
  action" (`channel.py:127-136`), and the audit engine exists so nothing else writes
  it. Render decisions from `agent_proposal.decided_*` as synthetic rows interleaved by
  time: agent label "You (web)", tool "accept" / "decline", `ink-2` styling. They are
  not expandable.
- Paging: "Show older" (`Button outline sm`, centred) using `before=`.
- Empty: "No agent has run yet. Agents run from the command line for now." until §6.6
  lands.

**Mobile (< 900px):** the run-log grid collapses into stacked rows: line 1 is When
followed by Result, right-aligned; line 2 is Agent · tool; line 3 is Produced.

### 6.5 Fixtures

`cafeops api-fixtures` must emit `agents-proposals.json`, `agents-runs.json`,
`shell.json`, `settings.json` and `setup.json` (plus an `setup-empty.json` variant), so
the screens render in fixture mode (`api.ts` header comment). Seed them from real runs
(`cafeops agent narrate …`, `prove-boundary`), not from the design's `SEED_RUNS`.
`prove-boundary` is useful here: it produces REFUSED rows, which show the log doing its
job.

### 6.6 Wiring agents to run (MISSING, and needed for the screen not to be empty)

Add to `jobs/scheduler.py`:
- **Drift explainer** after the drift report (08:00, `scheduler.py:99-100`): `narrate_drift`
  (`runner.py:559`) plus `propose_waste_factor` for each TUNE_WASTE_FACTOR ingredient.
  The narration text is appended to the morning Telegram digest, which makes the "In
  morning digest" result true.
- **Channel reporter** after the channel sync (02:50, `scheduler.py:93-94`):
  `browser_plan_channel_report` → `propose_channel_import`. It falls back to CSV per
  `config.py:79-88`.
- The cap is `agent_max_tool_calls` (`config.py:100`). Every run is logged by
  `AgentRun.call` (`runner.py:231-312`).
