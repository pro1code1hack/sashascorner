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
