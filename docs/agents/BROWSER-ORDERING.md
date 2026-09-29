# Browser ordering agents

**Status: designed 2026-09-29, being built.** Backend first; the web screens come later
and build against the endpoints in §7.

The job: when stock says an order is due, fill the supplier's web basket (Tesco,
Amazon, Booker, Brakes, Cups Direct...) from the draft order, stop at the basket, and
show a person what is in it. The person pays. Nothing in this design ever presses
"Place order" (CLAUDE.md §9, invariant 1).

---

## 1. How this is done properly (what the research settled)

Three findings shaped the design; sources at the end.

1. **Use Anthropic's browser toolset, hosted by us.** `browser_toolset_20260801` is GA
   (August 2026) on the Claude API. It is a *client* toolset: the model sends member
   calls (`navigate`, `read_page`, `find`, `left_click`, `form_input`, `screenshot`,
   ...), **our process runs them against our own Playwright browser** and returns the
   results. The model reads the page through the accessibility tree with element refs
   (`[ref_12]`), falling back to screenshots only when it needs to. Accessibility-tree
   agents beat screenshot-only ones by 12–17 points on e-commerce tasks in the 2026
   comparisons; pixels are the fallback, not the interface.
2. **Script the known flow; pay the model only for steps that break.** A model that
   re-reads the same basket page every time is an expensive way to avoid twelve lines
   of Playwright. Each supplier gets a small *portal adapter* (sign-in check, add a
   line by product URL, read the basket) written as plain Playwright with role-based
   locators. When a scripted step fails, that one step goes to the model with the
   adapter's hints, and the job carries on. Every fallback is logged so selectors get
   fixed instead of the token bill growing.
3. **Persistent profiles, human sign-in, no credentials near the model.** The browser
   runs in a persistent Chromium profile per supplier. A person signs in once (with
   their 2FA), the profile keeps the cookies, and the worker reuses it. The model
   never sees a password and the login page is not in its allowlist. When a session
   expires, the job stops as `NEEDS_HUMAN` and the person reconnects.

Alternatives considered and not chosen: Claude Agent SDK / Claude Code sessions
with a Playwright MCP (a coding harness, wrong shape for a background queue, and the
session state lives in the harness rather than our audit tables); Anthropic's
`computer_toolset` (screenshots only, lower reliability, ~same cost); browser-use /
Stagehand (fine libraries, but the toolset gives the same accessibility-tree loop
with no extra dependency and the results go straight into our own tool loop);
Managed Agents (does not offer the browser toolset at GA, and we want the browser
next to the database).

---

## 2. Boundaries (the same four devices as the narration agent, ARCHITECTURE 8G.4)

| Rule | Where it is enforced |
|---|---|
| Nothing is ordered without a person (invariant 1) | `PortalPolicy.forbidden_control_patterns` refuse any click whose accessible name is "Place order", "Pay", "Checkout"...; `forbidden_url_patterns` refuse navigating into checkout; the executor blocks off-allowlist top-level navigations at the network layer. A refused action is a `browser_job_step` with outcome REFUSED and the job continues. |
| The agent never writes stock, orders or composition (invariant 10) | The job writes only `browser_job`, `browser_job_step`, `media_asset` (screenshots), `agent_action_log` and one `agent_proposal` (kind SUPPLIER_BASKET). The purchase order's status is untouched: "Mark sent" stays a person's click. |
| Every action logged | Coarse: one `agent_action_log` row per job (`browser_stage_supplier_basket`). Fine: one `browser_job_step` per scripted step, model action or refusal, with URL and screenshot. |
| Money is bounded | `browser_max_model_calls`, `browser_max_actions`, `browser_max_minutes` per job; a job past its budget ends with what it has. |
| Page content is untrusted | The system prompt says so; the policy does not trust the model to remember it. `javascript_exec`, `file_upload`, `read_console`, `read_network` stay disabled. |
| Credentials | None in the database. `supplier_session.storage_state_enc` is a Playwright storage state (cookies), Fernet-encrypted with `CAFEOPS_BROWSER_SESSION_KEY`, used only to move a laptop sign-in to the server. |

---

## 3. Pieces

```
cafeops/integrations/suppliers/portals/     the scripted half, one module per supplier
  base.py      PortalPolicy, BasketLine, BasketSnapshot, SupplierPortal protocol, REGISTRY
  tesco.py amazon.py booker.py brakes.py cups_direct.py cakesmiths.py monolith.py generic.py
cafeops/agent/browser/                      the model half and the runner
  types.py     BrowserAction, ActionResult, BrowserExecutor protocol, Budget, ModelTask
  executor.py  PlaywrightExecutor: every toolset member against a Page, refs via injected JS
  policy.py    PolicyGuard: allowlist, forbidden controls/URLs, secret redaction
  loop.py      run_model_task(): the Messages API conversation with browser_toolset_20260801
  session.py   profile dirs, storage-state import/export (Fernet), sign-in check
  job.py       run_stage_basket(job) / run_check_session(job): scripted → model fallback → snapshot → proposal
  worker.py    the queue consumer: claim, heartbeat, run, finish; `cafeops browser-worker`
cafeops/services/browser_jobs.py            enqueue/cancel/list, integrations overview, session import
cafeops/db/models/browser_agent.py          supplier_session, browser_job, browser_job_step
cafeops/api/areas/integrations.py           the endpoints in §7
```

A supplier is "integrated" when `portal_for_supplier(supplier)` finds an adapter
(`supplier.channel_config["portal"]`, else a name hint) **and** its `supplier_session`
is CONNECTED. Both are visible on `GET /api/integrations`.

---

## 4. A STAGE_BASKET job, end to end

1. **Queue.** `POST /api/orders/{id}/stage-basket` (or the scheduler after the
   pre-delivery run, for suppliers with `channel_config.auto_stage = true`) inserts a
   `browser_job` with a snapshot of the order lines in `params`. Refused with a reason
   when: worker disabled, no portal adapter, session not CONNECTED, order not
   DRAFT/CONFIRMED, or a job for that order is already active.
2. **Claim.** The worker (`cafeops browser-worker`, its own process/container) polls,
   claims the oldest QUEUED job, marks RUNNING and starts a heartbeat.
3. **Open.** Launch the supplier's persistent profile, navigate to `policy.start_url`,
   `is_signed_in` → if not, finish as NEEDS_HUMAN("sign in expired") and mark the
   session EXPIRED.
4. **Lines.** For each line: `add_line_scripted` → on `PortalStepFailed`, one bounded
   `ModelTask` ("add N packs of X; product page URL; do not touch other lines") through
   the loop → outcome recorded on the line. `PortalNeedsHuman` ends the job.
5. **Basket.** `read_basket` (script; model fallback) → `BasketSnapshot` with lines
   matched to what the basket shows, subtotal seen vs total expected, unexpected
   lines, warnings. Final screenshot → `media_asset`.
6. **Report.** One `agent_action_log` row (AWAITING_HUMAN) and one `agent_proposal`
   (kind SUPPLIER_BASKET, subject `po:{id}`, payload = the snapshot, `basket_url`).
   Job SUCCEEDED (or SUCCEEDED with warnings when incomplete). The proposal's accept
   is "Open basket" (navigate); the person pays on the portal and presses "Mark sent"
   on the order page.

Job statuses: QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED | NEEDS_HUMAN.

---

## 5. The model loop (`loop.py`)

`client.messages.create(model=settings.browser_model, tools=[{"type":
"browser_toolset_20260801", "configs": {...opt-ins off...}, "cache_control":
{"type": "ephemeral"}}], system=[stable text, cached], messages=[...])`, adaptive
thinking (omit the parameter), `output_config={"effort": "medium"}`.

Per turn: for every `tool_use` block with `toolset_name == "browser"`, in order:
policy check → executor → `tool_result` with `toolset_name: "browser"`; stop at the
first failure and answer the rest "Not executed: an earlier action in this turn
failed." (the documented batch rule). Append the **whole** assistant content back
(thinking blocks included). Stop on `end_turn` with the final JSON, on `refusal`, on
budget, or on a `needs_human` outcome. Screenshots are resized to
`browser_screenshot_max_px` before they go to the model; earlier screenshots are
never removed from history (preserved thinking).

---

## 6. Connecting a supplier (sign-in)

`cafeops portal connect <supplier>` opens a **headed** browser on the operator's
machine in the supplier's profile, waits for the person to sign in (2FA and all),
verifies with `is_signed_in`, and stores the session state. On the server, the
profile directory is under `CAFEOPS_BROWSER_DATA_DIR`; from a laptop,
`cafeops portal export-session` / `POST /api/integrations/{supplier_id}/session`
moves the encrypted storage state across and the worker seeds the server profile
from it on first use. `cafeops portal check <supplier>` (or `POST
.../check`) queues a CHECK_SESSION job that reports signed-in or expired.

---

## 7. Endpoints (backend now, screens later)

```
GET  /api/integrations                              -> { suppliers: [IntegrationStatus] }
POST /api/integrations/{supplier_id}/session        { storage_state_enc | storage_state, account_label, connected_by } -> IntegrationStatus
DELETE /api/integrations/{supplier_id}/session      { by } -> IntegrationStatus            (forget the sign-in)
POST /api/integrations/{supplier_id}/check          { requested_by } -> BrowserJobOut     (queues CHECK_SESSION)
POST /api/integrations/{supplier_id}/auto-stage     { enabled, by } -> IntegrationStatus
POST /api/orders/{po_id}/stage-basket               { requested_by } -> BrowserJobOut     (queues STAGE_BASKET; 409 with reason when refused)
GET  /api/browser-jobs?status=&supplier_id=&po_id=&limit=&before= -> { jobs: [BrowserJobOut], has_more }
GET  /api/browser-jobs/{id}                         -> BrowserJobDetailOut (job + steps + snapshot)
POST /api/browser-jobs/{id}/cancel                  { by } -> BrowserJobOut
GET  /api/browser-jobs/{id}/steps/{seq}/screenshot  -> image/png
```

`IntegrationStatus`: `{ supplier_id, supplier_name, portal: {slug, label, start_url} |
null, session: {status, account_label, connected_at, connected_by, last_ok_at,
last_checked_at, last_error}, auto_stage, worker_enabled, last_job: BrowserJobOut |
null, can_stage: bool, cannot_stage_reason: string | null }`.

---

## 8. Running it

```
CAFEOPS_BROWSER_WORKER_ENABLED=true
CAFEOPS_BROWSER_SESSION_KEY=<python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())">
CAFEOPS_ANTHROPIC_API_KEY=...
uv run playwright install chromium            # once per machine (the worker image does it)
uv run cafeops browser-worker                 # long-running; or the `browser-worker` compose service / systemd unit
uv run cafeops portal list | connect <supplier> | check <supplier> | stage <po_id> | jobs [--job N]
```

Docker: the `browser-worker` service builds from the `browser` target (runtime +
Chromium and its system libraries) and mounts `cafeops_browser` at
`/data/browser`. It runs one job at a time; the SQLite single-writer rule is
unchanged because the worker holds the write lock only around its own rows.

---

## Sources

- Anthropic, *Browser use tool* reference (browser_toolset_20260801):
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/browser-use-tool
- Digital Applied, *Browser Use Is a New Claude Tool, Not a Renamed One* (GA 2026-08-19, member list, security posture):
  https://www.digitalapplied.com/blog/anthropic-browser-use-tool-ga-new-agent-toolset
- The Daily Brief, *Browserbase vs Playwright vs Computer Use: The Script Still Wins* (vendor-portal automation, hybrid fallback pattern, session/credential handling, costs):
  https://www.beri.net/article/browserbase-vs-playwright-vs-claude-computer-use-vendor-portal-automation
- AI Lab Notes, *Browser Automation for AI Agents: MCP, Playwright, and Beyond* (DOM vs vision camps):
  https://codeshrew.github.io/ai-lab-notes/posts/2026-02-08_browser-automation-ai-agents-mcp-playwright/
- Digital Applied, *Browser Automation AI Agents: Playwright vs Stagehand* (reliability by approach):
  https://www.digitalapplied.com/blog/browser-automation-ai-agents-playwright-stagehand-2026
- Microsoft Playwright MCP (the accessibility-snapshot approach the toolset mirrors):
  https://mcp.directory/blog/playwright-browser-mcp-guide-2026

---

## 9. Build notes (2026-09-29)

**Verified by running, not by reading.** With no Anthropic key on the dev machine, the
model loop was exercised with a fake client (policy refusal of a "Checkout" click, batch
halt rule, redaction, budget, refusal, API error) and the whole pipeline was run through a
real headless Chromium against a local fake shop: queue → worker → sign-in check →
scripted adds → basket read → screenshot → `SUPPLIER_BASKET` proposal, database restored
afterwards. Live read-only smoke against the public pages: Amazon loads clean, Cups Direct
is Shopify (adapter uses `/cart.js`), **Tesco serves "Access Denied" to a fresh headless
context (Akamai)**.

Consequences and open items:

- **Tesco needs the headed, connected profile.** `cafeops portal connect Tesco` on the
  owner's laptop, then `export-session` / `import-session` to the server. If the server's
  headless launch is still blocked with the imported profile, the fallback is a headed
  Chromium under Xvfb in the worker container (`CAFEOPS_BROWSER_HEADLESS=false` plus
  `xvfb-run`), which is not wired yet.
- **Portals are resolved through the supplier, never by slug alone.** Generic and
  Monolith adapters are templates that `PortalRegistry.for_supplier` binds to
  `supplier.channel_config` (`hosts`, `start_url`, `basket_url`, `login_url`); the job
  refuses to run when the supplier now resolves to a different portal than the one it was
  queued for.
- Booker, Brakes and Cakesmiths use the shared sign-in heuristic and best-effort basket
  reader until their first `CHECK_SESSION` job shows the real header; every add on them
  goes to the model with the adapter's hints.
- The agent narration tool cannot enqueue a job (it only holds the read-only bundle);
  the queue entry points are the order page (`POST /api/orders/{id}/stage-basket`),
  `cafeops portal stage`, and the scheduler's auto-stage hook.
- Frontend: not built. The screens go on Suppliers (connection state, Connect/Check,
  auto-stage), the order page (Stage basket, job progress, basket snapshot with the
  screenshot) and Agents (the proposal card exists already); fixtures are in
  `web/fixtures/integrations.json` and `web/fixtures/browser-jobs.json`.

---

## 10. Tiers: build the cart, don't navigate to it

**Added 2026-09-29.** The first build drove every basket through a browser: open the
profile, sign-in check, one product page per line, read the basket. That is the right
shape for a portal that offers nothing else, and the wrong one for a portal that will
hand you the basket as a URL. A STAGE_BASKET job now climbs a ladder and stops at the
first rung that puts the lines in; only what is left goes to the next. Every rung ends
at the same place: a `BasketSnapshot`, a SUPPLIER_BASKET proposal, a person who pays.

| Tier | Where | What it needs | Adapter capability |
|---|---|---|---|
| 0 `cart_link` | `services/browser_jobs.enqueue_stage_basket`, inline | nothing: no worker, no stored sign-in | `cart_link(lines) -> CartLinkPlan \| None` |
| 1 `quick_order` | `agent/browser/job.run_stage_basket` | worker + CONNECTED session | `quick_order_url`, `quick_order(page, lines) -> QuickOrderResult`, `quick_order_hints()` |
| — `browser` | `run_stage_basket`, per line | worker + CONNECTED session (+ a model for what breaks) | `add_line_scripted`, `read_basket`, `agent_hints` (every adapter) |
| 2 headed | `agent/browser/session.open_supplier_browser` | a display (Xvfb in the container, a screen on a laptop) | `prefers_headed = True` |

**Tier 0, the cart link.** Amazon's add-to-cart form and Shopify's cart permalink
(Cups Direct) build a pre-filled basket from product ids and quantities. Opening that
URL in the owner's own, already-signed-in browser *is* the staged basket: no
automation, no cookies on the server, no bot detection to lose to. So when the adapter
returns a plan that covers every line, `enqueue_stage_basket` does the whole job right
there: the `browser_job` row is inserted already SUCCEEDED (`worker_id="inline"`,
`model=None`), with one `browser_job_step` (`script:cart_link`, the plan's label, the
URL), a snapshot whose lines are `added` by `cart_link` at the wanted packs with **no
prices** (`unit_price_seen_pence` and `subtotal_seen_pence` are None, never zero --
the link does not see prices, and the one warning says so), and the proposal whose
"Open basket" is the link. The proposal body says it is a link that fills the basket in
the owner's own browser and that the person checks prices there. `IntegrationStatus.
can_stage` is therefore True for a `cart_link` portal even with the worker off and no
session; `cannot_stage_reason` talks about the worker and the sign-in only for portals
without one. A plan that covers only some lines (an item with no ASIN in its URL, a
variant lookup that failed) is stored in `params["cart_link"]` and the job is queued
as before: the worker opens the link first (`script:cart_link_open`), which pre-fills
those lines in its own browser, and carries on with the rest.

**Tier 1, the quick-order pad.** Booker and Brakes have a product-code form: paste
codes and quantities, submit once, the basket fills. One scripted step
(`script:quick_order`) does every remaining line that has a SKU in one form and reads
back which codes the pad accepted; a rejected code (`QuickOrderResult.rejected`) keeps
its line pending, with the pad's own words as the note, and falls through to the
per-line path. When the pad itself is not where or what the adapter expects
(`PortalStepFailed`), ONE model task (`loop.quick_order_task`) drives the pad for all
of those lines with `quick_order_hints()` and answers per line (`added` /
`not_found` / `gave_up`); `gave_up` lines fall through as well. A pad is not checkout:
the system prompt says so, and the policy refuses the controls regardless.

**The browser tier** is §4 unchanged: `add_line_scripted` per line, the model for the
step that broke, then `read_basket`, screenshot, snapshot.

**Tier 2, headed for hostile sites.** Tesco (Akamai) serves "Access Denied" to a
headless Chromium however well the profile is signed in. Its adapter sets
`prefers_headed`; `open_supplier_browser` then launches a visible window when there is
a display (`DISPLAY` or `WAYLAND_DISPLAY`) and otherwise raises `PortalNeedsHuman`
*before opening anything*, so the job ends NEEDS_HUMAN with "refuses headless
browsers; run the worker with a display (xvfb ...) or stage this order by hand" and
zero browser launches. On the server the display is Xvfb: the `browser` image stage
installs `xvfb` and `xauth`, and its entrypoint, `deploy/browser-worker-entrypoint.sh`,
execs `xvfb-run -a --server-args="-screen 0 1280x900x24" cafeops browser-worker`
when `CAFEOPS_BROWSER_HEADLESS` is false and no `DISPLAY` is set (the compose
`browser-worker` service passes the variable through from `.env`, default true; the
systemd unit carries the equivalent `ExecStart` in a comment).

**What the result records.** `result["tier"]` is the best tier that put at least one
line in (`cart_link` > `quick_order` > `browser`), `result["tiers_used"]` the tiers
that ran, and each line's `added_by` is one of `cart_link` | `quick_order` | `script`
| `model`. The API carries `tiers` on `IntegrationOut` and `PortalOut` (best first;
`browser` is always last), `prefers_headed` on `PortalOut`, and `tier` on
`BrowserJobOut`, whose `result_summary` now reads "cart link ready: 3 items" /
"quick order pad: 5 of 6 codes accepted" / "browser: 6 of 6 lines in basket, £48.20
seen". `cafeops portal list` shows the tiers column; `cafeops portal stage` prints the
tier used and the basket (or cart-link) URL.

**Why not always the browser?** Because every tier above it removes a way to fail. A
cart link cannot be blocked by bot detection, cannot expire, and costs nothing; a
quick-order pad is one form instead of six product pages; a headed window is what the
site will actually serve. The browser stays the floor, not the plan.

**Verified by running (2026-09-29).** Fake adapters against the real database
(restored to the same row counts afterwards): a complete cart link staged inline with
the worker off and no session (SUCCEEDED, `tier="cart_link"`, one step, a WAITING
proposal whose `basket_url` is the link, one audit row); a partial link queued with
`params["cart_link"]`, then run through cart-link open, a pad that accepted two codes
and rejected one, and the per-line path for the rejected line (`tiers_used` all three,
`added_by` = cart_link, cart_link, quick_order, quick_order, script); a `prefers_headed`
portal with no `DISPLAY` ending NEEDS_HUMAN with the xvfb reason and zero launches (a
CHECK_SESSION on it likewise, session row untouched). The real-Chromium e2e on the
local fake shop still passes on the browser tier (`tier="browser"`). The browser
image built with `xvfb-run` and the entrypoint present; `CAFEOPS_BROWSER_HEADLESS=false`
starts the worker under Xvfb.
