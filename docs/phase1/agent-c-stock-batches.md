# Agent C — Stock and batches (v2)

**Owns:** `cafeops/domain/stock.py`, `domain/drift.py`, `domain/tiers.py`,
`services/expand_recipes.py`, `services/record_count.py`,
`services/receive_delivery.py` (new), `jobs/expiry_sweep.py` (new),
`db/repositories/batch.py` (new)

## Already working — extend, do not restart

- `theoretical_on_hand`, the drift gate (verified across 9 cases, `set_auto_order(True)`
  raises), `record_count`, expansion with per-sale effective dating.
- **v2 FIFO/expiry primitives in `domain/stock.py`**: `allocate_fifo` (by *effective*
  expiry, no-expiry batches last, shortfall returned not raised),
  `find_expiry_losses`, `expiry_movements`, `batch_expiry_for`, `drift_attribution`.
  All verified by hand, including an opened 270-day carton with a 3-day open life being
  consumed before a sealed 10-day one.
- `services/rebuild_batches.py` — a chronological replay that produced 122 batches and
  8 expiry write-offs on the seeded data. **Read its header**: it lists exactly what to
  keep and what to replace.

## Build

### `services/receive_delivery.py` — the real batch creation path

Replaces the replay for live use. Receiving a PO line creates a `stock_batch` with
`received_expires_at` (entered by a human at the door — the bot asks), writes a
`DELIVERY` movement linked to it, and updates `po_line.received_qty`.

### Allocation at sale time, in `expand_recipes`

Expansion currently writes `SALE` movements without touching batches. Make it allocate
FIFO and set `stock_movement.batch_id`. Keep the replay's two rules:

- **Sweep expiry before allocating.** Allocating first quietly sells expired stock and
  the loss never appears.
- **A shortfall is data, not an exception.** The sale happened; refusing to record it
  loses real consumption, and the gap is the signal that a count is wrong or a delivery
  was never entered.

### `jobs/expiry_sweep.py`

Idempotent — `stock_batch.expired_at` exists for this, and a late run must not
double-write a loss. Emits `EXPIRED` movements and an alert list.

### Drift attribution (spec §5.2, v2)

`drift_attribution` is written. Wire it: `DriftObservation.expired_qty_in_window` is
the column, and `StockRepository.expired_qty_between` is the protocol method. **Report
which of the two problems it is** — if write-offs explain most of the gap the fix is to
order less, and if they do not the fix is the recipe. Opposite actions, so one
undifferentiated number tells the owner to do the wrong thing half the time.

### Open-life handling

`open_life_days` shortens a batch's effective expiry once `opened_at` is set. Nothing
sets `opened_at` yet. Decide how it gets set (the bot asking at a count? a CLI?) and say
what you chose — a 270-day oat carton that lasts 5 days open is a real waste source.

## Verify by running it

No tests. Show real output for: a delivery received with an expiry creating a batch; a
sale depleting the soonest-expiring batch; the sweep writing off a short-dated batch and
the value lost; and a drift report where expiry explains most of the gap versus one where
it does not.
