# Agent A — Lightspeed integration

**Owns:** `cafeops/integrations/lightspeed/`, `cafeops/services/ingest_sales.py`

## Build

- `client.py` — async httpx client for **Restaurant K-Series**. OAuth2 refresh-token
  flow, retry with backoff, rate limiting (`settings.lightspeed_rate_limit_per_second`),
  pagination. Every credential is optional in config: **the client must construct and
  fail cleanly when unconfigured** — we are fixtures-first and nothing may touch the
  live POS by default.
- `mapper.py` — API payload → domain. Pydantic v2 models for every external payload.
  Never index a raw dict in service code.
- `sync.py` — idempotent ingestion over a date window.
- `services/ingest_sales.py` — the transaction boundary. Upsert keyed on
  `sale.lightspeed_line_id` (already unique in the schema).
- Recorded fixture payloads under `cafeops/integrations/lightspeed/fixtures/`.

## Must handle

| Case | Required behaviour |
|---|---|
| Re-syncing an already-ingested window | No new rows, no duplicate movements. Rely on the unique `lightspeed_line_id`. |
| Voided receipt | `sale.voided = True`. **Keep the row** — deleting it means a re-sync resurrects it. Voided lines never deplete stock. |
| Partial refund | `is_refund = True` with a **negative** `qty`, so expansion emits a positive movement and the ledger nets out. |
| Modifiers on lines | Populate `sale.applied_modifiers` (JSON list of `modifier.id`) and match on `modifier.lightspeed_modifier_id`. |
| A sale already expanded, then corrected upstream | Do **not** mutate movements. The ledger is append-only (invariant 9) — emit an `ADJUSTMENT`. |

## Two questions you are expected to ANSWER, not assume

1. **Does K-Series expose modifiers on sale lines?** This is spec §13.2 and it was
   left open deliberately. It decides whether oat milk can ever be tier A. Right
   now `Oat milk (barista)` is **held at tier B** by
   `cafeops/seed/legacy.py::OAT_MILK_HELD` precisely because we do not know.
   Research the K-Series API surface, write down what you find with a source, and
   say whether the hold can be lifted. Do not change the tier yourself.
2. **Menu item mapping.** 314 items exist with `lightspeed_id = NULL`. Build the
   matcher (name + size is the natural key) and **report everything you cannot
   resolve** rather than guessing. An unmatched item silently depletes nothing,
   which is the worst possible failure because it looks like success.

Also worth reporting: does K-Series expose ingredient-level recipes? If so,
composition becomes a cache of POS data rather than the source, which matters to
Agent B. Report; do not act.

## Deliverable

A working `uv run cafeops sync --from YYYY-MM-DD --to YYYY-MM-DD --fixtures` that
ingests recorded payloads into the demo DB idempotently, plus a short written
answer to the two questions above. Show the command's real output twice in a row
to demonstrate idempotency.
