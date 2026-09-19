# Agent C — Stock engine

**Owns:** `cafeops/domain/drift.py`, `cafeops/domain/tiers.py`,
`cafeops/services/record_count.py`, and extension of `cafeops/domain/stock.py`
and `cafeops/services/expand_recipes.py`

## Already done in Phase 0 — extend, do not rewrite

- `domain/stock.py`: `theoretical_on_hand` (spec §5.1), `apply_waste`,
  `depletion_movements`.
- `services/expand_recipes.py`: resolves each sale **at its own `sold_at`** (not
  `now` — invariant 3) and is idempotent on `sale.expanded_at`, set in the same
  transaction as the movements.

Verified: the §5.1 identity holds for every tracked ingredient in the seeded demo.

## Build

### `domain/drift.py` — spec §5.2

```
drift_pct = (theoretical - counted) / max(counted, EPSILON) * 100
```

`EPSILON` is in `domain/types.py`. Return a `DriftResult` — it already carries
`verdict: DriftVerdict` (`ELIGIBLE` <10%, `TUNE_WASTE_FACTOR` 10–15%,
`FORCE_MANUAL` >15%) and `suggested_waste_factor`. Use **absolute** drift for the
gate. In the tuning band, move `waste_factor` *toward* the observed loss rather
than jumping to it — drift is systematic but noisy, and overshooting oscillates.

### `domain/tiers.py` — the gate. This is the rule that matters most here.

> **No ingredient enters auto-ordering without two consecutive counts under 10%.**

Enforce it in code:

- One `ELIGIBLE` observation is **not enough**. Eligibility is a property of the
  *history*, not of a single measurement. `DriftRepository.recent_drift_pcts` exists
  for exactly this.
- `FORCE_MANUAL` **revokes immediately** and raises an alert. Never deferred, never
  rate-limited, never "warn first".
- Nothing outside this module may set `auto_order_enabled = True` (invariant 2).
  Write `par_level.auto_order_granted_at / revoked_at / reason` on every change.
- Promotion B→A is deliberate; A→B must never happen by accident.

The seeded demo deliberately contains ingredients on both sides of the gate:
several tier-A items under 10%, `Napkin` at 12%, `16oz paper cup` at 19%. **Prove
the gate by running it and showing the output** — `16oz paper cup` must be refused,
`Napkin` must stay manual with a tuning suggestion, and the sub-10% items must
become eligible only after their *second* clean count. There is no test suite, so
this printed output is the proof.

### `services/record_count.py`

One transaction: write the `StockCount`, compute theoretical on-hand as of the
count, write the `DriftObservation` (with `waste_factor_at_count`), then run the
gate. A count is the source of truth — it **re-anchors** on-hand, it does not append
a correction.

### Harden expansion

Refunds (negative qty → positive movement), voided lines (already excluded from the
queue), `SubstitutionError` handling (Phase 0 deliberately does **not** mark those
sales expanded — a human must fix the data, and dropping the sale would lose real
consumption), and the `ADJUSTMENT`-only correction path. Never update or delete a
movement.
