# Agent D — Forecast & ordering

**Owns:** `cafeops/domain/forecast.py`, `cafeops/domain/ordering.py`,
`cafeops/services/build_order.py`, and a `cafeops simulate` CLI command

## Build

### `domain/forecast.py` — spec §5.3

```
base_daily    = EWMA(daily consumption, trailing 28 days, alpha=0.3)
dow_factor[d] = mean(consumption on weekday d) / mean(all days),
                trailing 8 weeks, clamped to [0.5, 2.0]
forecast(day)  = base_daily * dow_factor[weekday(day)]
```

Every knob is already in `config.Settings` (`ewma_alpha`, `ewma_window_days`,
`dow_window_weeks`, `dow_factor_min/max`, `min_history_days`). **Take them as
function arguments** — `domain/` must not import `config`.

Input is `list[ConsumptionPoint]` on **local** calendar days
(`StockRepository.daily_consumption` already does the local bucketing; see
`ARCHITECTURE.md` for why local matters to `dow_factor`).

**Days with no sales are zeros, not gaps.** Dropping them inflates `base_daily` and
the café over-orders. Decide explicitly whether a closed day is a zero or an
exclusion, and say which you chose and why.

Under 14 days of history: flat mean, `low_confidence=True`, and a reason in
`confidence_reasons`. Invariant 7 — the message says so **in place of** the number,
not beside it. The fields exist for this.

### `domain/ordering.py` — spec §5.4

```
cover_days = lead_time_days + days_to_next_delivery_after(target) + safety_days
need   = Σ forecast(day) over the cover window - on_hand - qty on open POs
packs  = ceil(need / pack_size), clamped so resulting on-hand lands in [min_qty, max_qty]
```

- `SupplierSpec.delivery_weekdays` is ISO (Mon=1…Sun=7). **An EMPTY tuple means
  "any day"** — that is Tesco, walk-in, lead time 0. Handle it; do not loop forever
  or divide by zero looking for the next delivery.
- Never order negative or zero packs.
- Min-order top-up: tier **B** items ranked by **shortest remaining cover**, until
  `min_order_pence` is met. Set `OrderSuggestion.min_order_topped_up`, mark each
  added line `is_top_up=True`, and explain it in `notes`. Never silently inflate an
  order — a top-up spends the owner's money on something she did not ask for.
- Propagate `low_confidence` into each `SuggestedLine`, and set `clamped` only when
  a clamp actually bit.
- Units: an ingredient stocked in `ML` may be packed in `L`. Use
  `domain/units.convert` — it raises across dimensions rather than silently
  treating 1 L as 1 kg.

### `services/build_order.py`

Creates **`DRAFT`** purchase orders only. `CONFIRMED`/`SENT` need a human and a
`CHECK` constraint will reject the write otherwise (invariant 1).

### `cafeops simulate`

Replay the 60 seeded days and print what would have been ordered each week, per
supplier. This is how anyone will sanity-check your arithmetic, so **show the
working**: cover window, forecast total, on-hand, open-PO qty, need, packs, and any
clamp or top-up. There is no test suite — this output is the proof.

## Caution

CakeSmiths and Cups Direct lead times, delivery weekdays and minimum orders are
**invented placeholders** (`ARCHITECTURE.md`). Your cover window is only as good as
they are. Do not tune anything against them, and say so in your output.
