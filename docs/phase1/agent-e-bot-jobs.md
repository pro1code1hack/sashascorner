# Agent E — Bot & jobs (runs after A–D)

**Owns:** `cafeops/bot/`, `cafeops/jobs/` (except `cost_rollup.py`)

Held back deliberately: this agent consumes what A–D produce, so it runs once their
contracts have settled rather than against moving targets.

## Build

### `bot/` — aiogram 3.x

Weekly full count, twice-weekly tier-A express count, tier-C checklist, order
confirmation with inline +/− pack adjustment, morning digest.

- **Every user-facing string lives in `bot/formatters.py`, in Russian.** Nothing
  user-facing anywhere else. `domain/units.format_qty` is deliberately locale-free
  (`L`/`ml`/`kg`/`g`/`pcs`) for back-office output; the **web dashboard is English
  only** (confirmed with the owner), so Russian is the bot's alone.
- Handlers are async; they call **sync** services through `asyncio.to_thread`. Do
  not make repositories async.
- Reach services through `db/repositories/protocols.py`.

### The four invariants that are yours to keep

1. **Invariant 4 — every number is labelled.** `OnHand.is_theoretical` is always
   True; `OnHand.has_count_basis` tells you whether a physical count sits behind
   it. A figure with no count basis is a bare movement sum — say so. Never print a
   theoretical number as though it were counted.
2. **Invariant 7 — low confidence replaces the number.** `ForecastResult.low_confidence`,
   `SuggestedLine.confidence_reasons` and `PurchaseOrder.confidence_notes` carry it
   to you.
3. **Invariant 1 — nothing ordered without confirmation.** Record `confirmed_by`. A
   `CHECK` constraint rejects the write otherwise, which is the intended failure.
4. **Min-order top-ups must be visible.** If `min_order_topped_up` is set, name the
   lines added only to reach the supplier minimum. The owner must never discover
   she bought syrup to clear a £50 floor.

### `jobs/` — APScheduler

`scheduler.py`, `daily_sync`, `nightly_expand`, `pre_delivery_order`,
`drift_report`. Every job idempotent: a missed run firing late must not
double-write. `sale.expanded_at` gives you that for expansion — think about the
others.

Schedule `pre_delivery_order` from `supplier.lead_time_days` and
`delivery_weekdays`, not a hardcoded day. Tesco has an **empty** `delivery_weekdays`
(walk-in, any day).

### Channels

`integrations/suppliers/base.py` has `OrderChannelAdapter`; `channels.py` implements
`MANUAL` (Tesco), `BROWSER_AGENT` (Cups Direct), `EMAIL`, `PORTAL`. Use
`adapter_for(channel)` — do not special-case suppliers. `BROWSER_AGENT` stops at a
filled basket with `requires_human_completion=True`; the bot must say "basket
ready, press the button", never "ordered".
