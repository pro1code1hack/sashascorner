# Back-office redesign — owner decisions (2026-09-26)

These override anything in the four specs (`design-system.md`, `shell-agents.md`,
`stock-orders-suppliers.md`, `recipes-menu-ingredients.md`, `finance.md`) that disagrees.

## Scope
Complete redesign of every screen in the design's nav: Stock, Orders, Agents, Recipes,
Menu items, Ingredients, Suppliers, Overview, Sales, Expenses, Reconcile, Profit & loss,
Director's account, Settings, plus Login and the empty-install / Setup checklist.
Backend and frontend. The design (`docs/design/Cafe Ops v2.dc.html` and the sub-apps) is
the visual spec; CLAUDE.md §13 invariants win over the design wherever they conflict,
using the resolutions recommended in each spec's "Conflicts" section.

## Decisions
1. **Web orders stay read-only.** No "Send to Telegram to confirm" button. The web app
   never creates or confirms a `purchase_order`; `api/routers.py:8-11` stands. Orders
   screen shows drafts, history, and "Waiting in Telegram" for pending ones. Receiving a
   delivery against an already-confirmed order, cancelling, and marking sent are fine.
2. **Uppercase labels: follow the design.** Sidebar group heads and table headers are
   uppercase as designed. CLAUDE.md §10's ban is lifted.
3. **Password change in Settings: full version.** scrypt hash in DB with explicit
   precedence over the env var; session tokens instead of the raw password on every
   request; 5 attempts/min rate limit on login; change requires current password and
   ≥10 chars; a change revokes every session, writes an audit row and sends a Telegram
   notice. Also fix the unlock bug (shell checks `/api/meta`, which is unauthenticated).
4. **Finance data:**
   - Capital injections are shown separately on Director's account and do **not** count
     toward "company owes you".
   - Rename "Square cash" / "Square fees" → "Till cash" / "Card fees" everywhere.
   - Just Eat never paid cash: the £114.92 of Just Eat money in the workbook's cash column
     is imported as Just Eat (card/platform) takings, not cash.
   - VAT: unanswered. Do not claim prices include or exclude VAT; drop the "Prices exclude
     VAT" footnote.
5. **Settings rows:** keep password + Lightspeed status/Sync now; "Show empty-install" →
   "Setup checklist"; drop bot-language select and "Reset demo data" (per shell-agents spec).
6. **Operator identity:** per-device "who's using this" name (not a login), sent with
   counts, receipts, checklist answers, write-offs, decisions.
7. **Agent proposals:** new `agent_proposal` table; the agent may only insert proposals;
   accept/decline is human, recorded, and goes through services.
8. **No tests** (ARCHITECTURE.md §1). Verify by running: ruff, mypy strict on
   domain/ + services/, `tsc`, `vite build`, and exercising endpoints against the real DB.
