# Agent D — Forecast, sourcing and ordering (v2)

**Owns:** `cafeops/domain/forecast.py`, `domain/ordering.py`, `domain/sourcing.py` (new),
`services/build_orders.py`, `db/repositories/sourcing.py` (new), `cafeops simulate`

## Already working — extend, do not restart

EWMA + day-of-week factors (**deseasonalised** — read `ARCHITECTURE.md` §8C before
touching `base_daily`), cover windows, pack rounding with `moq_packs`, min-order top-up,
DRAFT-only order creation, `simulate --weeks`. Per-supplier grouping already produces 8
separate orders.

## Build

### 1. The shelf-life cap (spec §5.4, **invariant 4**) — the highest-value item here

```
effective_cover = min(cover_days,
                      shelf_life_days - transit_buffer_days,   if perishable
                      days_remaining_in_season,                if seasonal)
```

`ShelfLifeSpec.usable_days` already computes the first. **Milk's usable window is 5
days against a 9-day cover** — so this cap changes real quantities today, and without it
the system orders nearly double what will keep.

Set `SuggestedLine.cap_reason` and `po_line.cap_reason` whenever the cap bites, and
render it ("capped at 5 days — milk shelf life"). Spec §5.4 is explicit about why: the
user must know the system chose to under-order deliberately, or they will override it
and create the waste the cap prevented.

### 2. Perishables must never be a top-up (**invariant 5**)

Topping up to clear a minimum or a free-delivery threshold may only use
**non-perishable** tier B items. `ShelfLifeSpec.is_perishable` is the test. Buying milk
to save a £5.95 delivery fee is buying waste.

Note that `free_delivery_threshold_pence` and `min_order_pence` are *different
decisions*: one is a condition on ordering at all, the other a price break. Treat them
separately and say which drove a top-up.

### 3. `domain/sourcing.py` — multi-supplier choice (spec §4.4, §5.5)

`SourcingOption`, `SourcingChoice`, `SupplierSplit`, `EmergencyLine`, `SupplierTerms`
are all defined in `domain/types.py`. Prefer the preferred product unless an alternate
is **materially cheaper per unit AND** the switch does not push another supplier's order
below its minimum. Populate `cheaper_rejected` and `forgone_saving_pence` — surface the
trade-off, never resolve it silently.

Seeded data includes two deliberately *worse* alternates (Monolith beans, Amazon cups)
so you have cases where the alternate must lose.

### 4. Tesco emergency routing and its report

Route to Tesco only for what cannot wait for a scheduled delivery. Log every one via
`SourcingRepository.record_emergency_routing` with the retail premium. Spec §4.4: the
accumulated log is the argument for fixing the ordering cadence, so it is data, not a
note.

### 5. Seasonal forecasting (spec §5.3)

Seasonal items use the same calendar window from the previous season scaled by
year-on-year growth; with no prior season, flat-rate the first two weeks and mark
low-confidence. **Out-of-season history must be excluded** — pumpkin in October must not
inflate the July baseline, nor read as "unused for 9 months, drop it".
`SeasonSpec.contains` and `days_remaining` handle year-wrapping seasons already.

### 6. `cutoff_time`

A cutoff missed by ten minutes costs a whole delivery cycle, which is how a Tesco run
happens. Factor it into `days_to_next_delivery_after`.

## Caveats to keep surfacing

Six of eight suppliers have **invented** terms. Your cover window is only as good as
their lead times. `simulate` already prints the caveat; keep it, and make the
placeholder flag visible per order.
