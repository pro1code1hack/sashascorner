# Back-office redesign: correctness review

Date: 2026-09-26. Scope: the uncommitted redesign. That covers migration `62aa94a23687`, the new models, `api/areas/*`, the new and changed services, `services/finance/*`, `seed/finance_import.py`, the domain and repository diffs, and `web/src/lib/*`. The review was read-only. Every repro ran on scratch copies of `cafeops.db` migrated to head, and the repo DB was never written.

Verdicts:
- **CONFIRMED** means the defect was reproduced by running the services or TestClient against a scratch DB, or, where marked, by an unambiguous reading of config or schedule.
- **PLAUSIBLE** means it was found by reading the code and not run.

Totals: **6 HIGH, 13 MEDIUM, 18 LOW**.

---

## HIGH

### H1. "Sync now" is not single-flight, so two syncs deplete the same sales twice
- **Where:** `cafeops/services/sync_runs.py:222-268` (`begin_manual_sync`) and `:294-306` (the scheduled path), plus `cafeops/services/expand_recipes.py:191,262`.
- **Defect:** the lock is a check-then-insert on `sync_run` with no constraint, and pysqlite runs SELECTs outside a transaction. `expand_pending` marks sales expanded without checking the row is still unexpanded (`WHERE expanded_at IS NULL`).
- **Scenario:** a double-click on Sync now, or a manual sync overlapping the 02:30 job, gives two RUNNING runs, both with `expand_after=True`. Both expand the same pending sales and write duplicate `SALE` movements, so theoretical stock is depleted twice and drift is corrupted. A sync running longer than 15 minutes is also marked FAILED by the next click while it is still running.
- **CONFIRMED:** forcing the interleaving produced run ids [1, 2], both RUNNING. Two concurrent `expand_pending` calls over 20 pending sales each wrote 120 movements, and all 120 were duplicated.
- **Fix:**
  - Add a partial unique index `ON sync_run(status) WHERE status='RUNNING'`, or take `BEGIN IMMEDIATE` before the check.
  - In `mark_expanded`, use `UPDATE ... WHERE id IN (...) AND expanded_at IS NULL` and roll back unless the rowcount matches.

### H2. A "Went off" write-off double-counts stock the expiry sweep already wrote off
- **Where:** `cafeops/services/record_write_off.py:117-137`, plus `db/repositories/batch.py:104-119` (`open_batches` skips lots with `expired_at` set) and `db/repositories/stock.py:97-110`.
- **Defect:** WENT_OFF allocates FIFO from open batches. Once the sweep has expired a lot, the write-off draws from a fresh lot instead.
- **Scenario:** a 2 L milk batch expires, and the sweep books EXPIRED −2 L. Staff bin the carton and record "Went off 2 L", which takes 2 L from a good batch.
- **Result:** 4 L leave the ledger and a good lot reads 2 L short. `expired_qty_between` returns 4, which doubles both the month's written-off value and the drift expiry attribution.
- **CONFIRMED:** the chain `receive_adhoc` → `mark_expired` → `record_write_off` gave sweep −2, write-off −2 drawn from batch 25, and `expired_qty_between=4`.
- **Fix:** before allocating a WENT_OFF, net it against EXPIRED movements on that ingredient that no write-off has matched yet. Otherwise refuse with "already written off by the expiry sweep on <date>".

### H3. Receiving an already-RECEIVED order books the stock again
- **Where:** `cafeops/services/receive_delivery.py:104` (`_RECEIVABLE` includes RECEIVED), reached through `services/order_actions.py:143-178` and `POST /api/orders/{po_id}/receive`.
- **Defect:** the service accepts repeat receipts. The over-delivery warning compares only this receipt with the expected quantity, not the running total.
- **Scenario:** a double-click or two tabs send "receive 3 packs" twice. That creates two batches and two DELIVERY movements, and `po_line.received_qty` goes 2 → 5 → 8 with no warning. The UI hides the button, but the service does not enforce it.
- **CONFIRMED:** `receive_order(po_id=1, 3 packs)` was called twice; the status stayed RECEIVED, `received_qty` reached 8, and the line had 3 batches.
- **Fix:**
  - Refuse a receipt when the order is RECEIVED; keep RECEIVED receivable only if the bot's late-line path truly needs it.
  - Warn when `received_qty + qty > expected`.
  - Accept an idempotency key.

### H4. Editing a modifier with no `modifier_version` row rewrites history (invariant 3)
- **Where:** `cafeops/services/modifier_versions.py:303-313` and `cafeops/db/repositories/composition.py:296-310` (`modifiers(at)`).
- **Defect:** when no version covers `at`, the resolver falls back to the `modifier` cache row. An edit opens a version only from now, but it also updates the cache, and the cache is exactly what that fallback reads for all earlier times.
- **Scenario:** this affects two groups of modifiers.
  - Every modifier created after the migration: `seed/demo.py:349` writes no version row.
  - Any sale ingested later with `sold_at` before the backfill start. The backfill is dated at the earliest `sold_at`, or at `now()` on an empty DB (`62aa94a23687:575-577`), so `cafeops sync --from <earlier>` lands uncovered.

  Re-expansion or sale corrections (`ingest_sales._emit_correction_adjustment`) for those sales then deplete the new ingredient.
- **CONFIRMED:** with modifier 1's version deleted, resolving at 2026-08-01 gave ingredient 3 at 40p. After editing it to ingredient 4 at 60p, the same call returned ingredient 4 at 60p.
- **Fix:**
  - On the first edit, insert a baseline version copied from the cache, from the earliest sale up to the edit time.
  - Make the seed and every modifier creator write a version.
  - Have `modifiers(at)` flag or refuse when `at` predates every version, rather than silently reading the cache.

### H5. The P&L "all" total hides missing expenses (invariant 8)
- **Where:** `cafeops/services/finance/periods.py:252,280-287`, reached through `overview.py:193`.
- **Defect:** for the "all" period, `expenses_missing` is forced False, so a month with takings but no costs counts its revenue as profit.
- **Scenario (real workbook):** the monthly nets sum to −£711.73, and April's net is None. The TOTAL column shows **+£581.32**, because April's £1,293.05 of takings enters without its costs. This is the "April reads as profit" bug that the module docstring says was fixed, back again in the total.
- **CONFIRMED:** `profit_and_loss` was run on the imported scratch DB.
- **Fix:** make total gross and net `None` when any month has `expenses_missing`, or total only the complete months and label it as such.

### H6. Re-running `import-finance` resurrects figures a person cleared, deleted or re-dated
- **Where:** `cafeops/seed/finance_import.py:192-234` (`_upsert_payment`), together with `services/finance/trading_days.py:286-292` (clearing a figure) and `:400-416` (`_move_day`).
- **Defect:** clearing a figure deletes the LEGACY_WORKBOOK `payment_day` row and leaves no record of the deletion. `trading_day.source` stays LEGACY.
- **Scenario:** 2026-04-08 card 6075p is cleared by `update_day(card_pence=None)`. A re-import puts 6075 back (`inserted=1`). After a re-date, the day exists on both dates.
- **CONFIRMED** on a scratch DB.
- **Fix:** keep a tombstone per (date, method), or skip any date whose `trading_day` was touched in the app. Set `source=MANUAL` on every edit, delete and re-date.

---

## MEDIUM

### M1. Editing a supplier link clears the ESTIMATE cost flag (invariant 8)
- **Where:** `cafeops/services/suppliers.py:543-546` (`edit_product`), `:476-479` (`link_product`) and `:395-407` (`_link_source`, which defaults to SUPPLIER_FEED). `prefer_product` and `_promote_next` behave the same way.
- **Scenario:** semi-skimmed milk costs 69.7p/L and is flagged ESTIMATE. Changing the price by 1p, or fixing the pack size, writes an `ingredient_price` row with source SUPPLIER_FEED. Every menu cost and aggregate using milk stops saying "estimated", although nobody confirmed the price.
- **CONFIRMED:** `current_cost_source` went ESTIMATE → SUPPLIER_FEED.
- **Fix:** carry the previous source forward unless the caller explicitly states INVOICE or SUPPLIER_FEED, which is the rule `ingredient_catalog` already follows.

### M2. Unknown delivery commission, ads or write-off value is treated as 0 in net profit (invariant 8)
- **Where:** `cafeops/services/finance/periods.py:278-286`, in `delivery_costs = (comm or 0) + (ads or 0)` and `cogs = stock_bought + (wo.pence or 0)`.
- **Scenario:** all five imported Just Eat months have commission None. Net profit is still a number, overstated by the unknown commission, with only an `incomplete` caveat beside it.
- **CONFIRMED:** the Nov–Mar months have `incomplete=True` but a numeric net.
- **Fix:** return a `None` net when a cost component of counted revenue is unknown. At minimum, return a partial flag that the UI must render in place of the number (invariant 9's pattern).

### M3. The rate limiter is one shared bucket for the whole internet in the compose deployment
- **Where:** `docker-compose.yml:28-40`, with `cafeops/services/auth.py:117-120` and `cafeops/api/security.py:85-94`.
- **Defect:** `CAFEOPS_TRUSTED_PROXIES` is unset, so the peer is always the Caddy container IP, and uvicorn's `forwarded_allow_ips` is 127.0.0.1 only.
- **Scenario:** anyone sends 5 bad passwords a minute to `POST /api/auth/session`. The owner then gets 429 with the correct password, both on sign-in and over X-API-Key, because the limit is checked before the password. Every audit row also records Caddy's IP.
- **CONFIRMED:** with TestClient, after 5 failures on one key, the correct password received 429.
- **Fix:** set `CAFEOPS_TRUSTED_PROXIES` to the compose network CIDR, or give Caddy a fixed IP and trust only that.

### M4. `accept_proposal`'s savepoint commits the applied change before the decision is recorded
- **Where:** `cafeops/services/agent_proposals.py:605` (`session.begin_nested()`).
- **Defect:** on pysqlite in legacy transaction mode, releasing the outermost SAVEPOINT is a real commit (ARCHITECTURE §8R). `materialise_template.py:689-693` works around this; this code does not.
- **Scenario:** accepting a waste-factor or channel-import proposal commits the change. If recording the decision then fails, the change stands while the proposal stays WAITING.
- **CONFIRMED:** after begin_nested → insert → exit → `rollback()`, the row persisted.
- **Fix:** either emit `BEGIN` via the SQLAlchemy pysqlite event workaround globally, or apply without a savepoint inside the outer transaction.

### M5. Retired ingredients are still drafted into orders
- **Where:** `cafeops/services/ingredient_catalog.py:582-600` (`retire_ingredient`) and `db/repositories/ingredient.py:46` (`list_tracked`, which does not filter `retired_at`).
- **Scenario:** after retiring semi-skimmed milk, it still appears in `list_tracked` and is sized by `/api/orders/draft`.
- **CONFIRMED:** 8 retired tracked ingredients were all still listed, and two appeared in the draft's skipped lines.
- **Fix:** set `tracking_enabled=False` on retire, filter `retired_at` in `list_tracked` and `_active_products`, and optionally refuse retiring while an open PO line references the ingredient.

### M6. A partial delivery export is shown as a complete month
- **Where:** `cafeops/services/finance/channels_month.py:98-119`.
- **Defect:** the month total is the sum of whichever daily `channel_metric` rows exist, marked `present=True`, and nothing checks coverage.
- **Scenario:** a Deliveroo CSV containing only 2 April makes April's Deliveroo gross read 1500p, complete, with no caveat.
- **CONFIRMED.**
- **Fix:** record each upload's covered window, and mark the month incomplete unless every day is covered.

### M7. A real Just Eat CSV upload is ignored for Nov 2025 to Mar 2026
- **Where:** `cafeops/services/finance/channels_month.py:82-97` and `seed/finance_import.py:341-374`.
- **Defect:** the importer writes LEGACY_WORKBOOK `channel_statement` rows, and a statement always beats daily metrics.
- **Scenario:** a Just Eat CSV for March (3 days × 2000p, 300p commission) is uploaded "successfully". March still shows 3177p, commission None, source LEGACY.
- **CONFIRMED.**
- **Fix:** only a MANUAL statement should beat metrics; otherwise prefer metrics and add a caveat.

### M8. OTHER, VOUCHER and ACCOUNT takings vanish from revenue with no caveat
- **Where:** `cafeops/services/finance/periods.py:163-166,212`, `trading_days.py:59-63` and `takings.ResolvedDay.till_total_pence`.
- **Defect:** only CARD, CASH and CASH_OFF_TILL are counted, and `csv_source._method` maps unknown words such as "Apple Pay" to OTHER.
- **Scenario:** a 5000p OTHER row added on 2026-03-10 leaves March revenue unchanged at 243721p, with no caveat.
- **CONFIRMED.**
- **Fix:** give those methods their own revenue line, or at least a caveat naming the excluded pence.

### M9. The P&L total ignores the requested range
- **Where:** `cafeops/services/finance/overview.py:192-193`.
- **Defect:** the month columns are filtered to the requested range, but `total = period_figures(session, None)` covers all time.
- **Scenario:** with `?from=2026-01&to=2026-03`, the Total column does not equal the sum of the visible columns.
- **CONFIRMED** by reading the code.
- **Fix:** have `period_figures` accept `[since, until]`.

### M10. Write-offs are added on top of cash-basis stock purchases (double deduction)
- **Where:** `cafeops/services/finance/periods.py:278`, in `cogs = stock_bought + write_offs`.
- **Defect:** the written-off stock was already expensed when it was bought.
- **Scenario:** £100 of milk is bought and £30 of it expires. COGS shows £130, and profit falls by £130 instead of £100.
- **CONFIRMED** by reading the code. The finance spec asks for this, so the fix needs an owner decision.
- **Fix:** show write-offs as an "of which wasted" memo line, not an addition.

### M11. Renaming a flavour to a removed flavour's name crashes or merges lineages
- **Where:** `cafeops/domain/composition.py:1951-1958` and `cafeops/services/recipe_changeset.py:659-668`.
- **Defect:** the rename check compares only against live options, while the resolver identifies an option by (axis, name).
- **Scenario:** remove Caramel, then rename Vanilla to "Caramel". The apply raises an unhandled IntegrityError on `uq_variant_option_axis_name` (a 500). If the rows' `effective_from` differ, the rename succeeds instead, and historical resolution can pick the wrong syrup.
- **CONFIRMED** for the crash; the wrong-syrup part is PLAUSIBLE.
- **Fix:** refuse any name ever used on the axis, or give options an explicit lineage id.

### M12. The Shop runs report double-counts trips and hides unknown premiums
- **Where:** `cafeops/api/areas/stock_views.py:475-555` (`shop_runs_view`).
- **Defects:**
  - A web shop run (a `tesco_routing` row with `paid_pence`) and the matching "Tesco …" finance expense are both listed and summed.
  - CLI emergency routings, which were never bought, are listed as runs.
  - `premium_total_pence` counts an unknown premium as 0 (invariant 8).
  - `runs_count` counts lines, not trips.
  - The totals cover all history while `by_month` is cut.
- **PLAUSIBLE.**
- **Fix:**
  - Link a routing to its expense and de-duplicate.
  - Exclude or label routings that were never bought.
  - Make the premium total nullable, with the priced part shown beside it.
  - Group lines into trips.

### M13. Rotating the env password does not revoke sessions
- **Where:** `cafeops/services/auth.py:373-387` (`_is_live`).
- **Defect:** sessions are not tied to the credential that issued them. In bootstrap mode (no DB hash), changing `CAFEOPS_API_PASSWORD` leaves old sessions valid for up to 30 days, or 7 days idle.
- **PLAUSIBLE.**
- **Fix:** store a credential fingerprint on `auth_session` and reject sessions whose fingerprint no longer matches.

---

## LOW

- **L1. Walk-in stock with unknown cost is stored at 0p (invariant 8).** At `cafeops/services/receive_delivery.py:320-321`, when there is no cached cost, `unit_cost_pence=0`, so `BatchOut.value_pence` and any batch-cost reader show £0. PLAUSIBLE. Fix: make the column nullable and treat NULL as unpriced.
- **L2. Shop runs accept fractional EACH quantities.** At `cafeops/services/order_actions.py:233-258`, the walk-in route refuses 1.5 cups but the shop-run path writes a fractional DELIVERY. PLAUSIBLE. Fix: move the whole-unit check into `receive_adhoc`.
- **L3. Future-dated counts are accepted.** At `cafeops/services/record_count.py:158-160` (via `POST /api/stock/{id}/counts`), the out-of-order guard looks only at counts before now. A future count silently re-anchors on-hand later, and the drift gate runs now against it. PLAUSIBLE. Fix: refuse `counted_at` later than now plus a small skew allowance.
- **L4. A partly received order can be cancelled.** At `cafeops/services/order_actions.py:87-110`, the received batches remain, but the order reads CANCELLED and its remaining lines cannot be received. PLAUSIBLE. Fix: refuse cancel once any `received_qty > 0`, or add a "closed short" state.
- **L5. Demoting A → C leaves `auto_order_enabled=True`.** At `cafeops/services/stock_settings.py:171-172`, the gate never runs again, so invariant 2's state goes stale. It is harmless today because the ingredient becomes untracked. PLAUSIBLE. Fix: revoke through `apply_gate_decision` on a move to C.
- **L6. The stale-sync banner fires every afternoon.** `cafeops/services/shell_status.py:106` uses a 12 h threshold against a nightly 02:30 job (`jobs/scheduler.py:241`), so a false alarm shows daily from about 14:30 and nudges people toward Sync now (H1). CONFIRMED by reading the schedule. Fix: use about 26 h, or "missed the last scheduled run".
- **L7. Deciding a proposal is check-then-act.** At `cafeops/services/agent_proposals.py:388-398,541-548`, two simultaneous accepts both apply and both record a decision. PLAUSIBLE. Fix: `UPDATE ... SET status='APPLYING' WHERE id=? AND status IN ('WAITING','APPLY_FAILED')` and proceed only if rowcount is 1.
- **L8. APPLY_FAILED proposals appear in both the waiting and decided lists.** At `cafeops/services/agent_proposals.py:374-378`. CONFIRMED by reading the code. Fix: exclude WAITING and APPLY_FAILED from the decided query.
- **L9. Photo upload buffers unbounded chunked bodies.** At `cafeops/api/areas/menu.py:285-297`, only `Content-Length` is checked, and Caddy has no `request_body max_size`. This is a memory DoS by an authenticated client. PLAUSIBLE. Fix: read `request.stream()` with a byte cap, and set a Caddy `max_size`.
- **L10. The agent guard allows inserting pre-decided proposals.** At `cafeops/agent/policies.py:218-227`, only `insert_proposal`'s hardcoded WAITING prevents an agent inserting a row with status ACCEPTED and a made-up `decided_by` (invariant 10, defence in depth). PLAUSIBLE. Fix: add a CHECK or guard requiring WAITING and NULL `decided_*` on agent inserts.
- **L11. The stale-preview guard misses in-place edits.** At `cafeops/services/recipe_changeset.py:256-270`, `template_version` omits option names, item names, `item.prep_seconds` and category, so a tab loaded before a rename passes the 409 guard. PLAUSIBLE. Fix: include those fields in the hash.
- **L12. Dated sell prices are written but never read for margins.** `menu_item_price` is ignored by `services/menu_margin.py` and `api/views/margin.py:85,152`, so a price edit re-prices past-window margins, contrary to the model's docstring. Items created by `seed --demo` or `import-legacy` after the migration get no price row. PLAUSIBLE. Fix: write BACKFILL rows and read dated prices, or correct the docstring.
- **L13. The unit-change guard misses tables.** `cafeops/services/ingredient_catalog.py:243-275` (`ingredient_references`) ignores modifier and modifier-version `qty_delta`, `par_level`, `po_line`, `drift_observation` and `tesco_routing`. A G → KG switch turns a 30 G ADD swap into 30 kg. PLAUSIBLE. Fix: count those references too.
- **L14. The director import aborts on a wrong-direction row.** At `cafeops/seed/finance_import.py:536-585`, type is not checked against direction, so a "Repayment" with an IN value hits the CHECK constraint and aborts the whole import instead of going into `refused`. PLAUSIBLE. Fix: reuse `director._validate`.
- **L15. Re-dating a day onto a date holding only a `cash_count` or `card_payout` fails with a 500.** At `cafeops/services/finance/trading_days.py:400-402`, the collision check omits those tables, so an IntegrityError surfaces instead of a 409. PLAUSIBLE. Fix: check them as well.
- **L16. `ChannelUploadIn.text` has no `max_length`.** At `cafeops/api/areas/finance_schemas.py:444-449`, the whole body is parsed before the 5 MB check. Fix: add `Field(max_length=5_000_000)`.
- **L17. BANK_DEPOSIT rows can double-count at a backfill boundary.** In `cafeops/services/finance/takings.py` (`resolve_takings`), legacy BANK_DEPOSIT card rows are dated about one day after the sale. If a POS or CSV backfill partly overlaps Sep 2025 to Mar 2026, the boundary day counts both. PLAUSIBLE. Fix: flag or refuse a deposit row whose neighbouring day has a TILL-basis winner.
- **L18. The `sync_run` abandon window can cut off a live sync.** At `cafeops/services/sync_runs.py`, a run longer than 15 minutes is marked FAILED by the next click while still running. This is a contributing cause of H1 and is fixed by the same lock.

---

## Checked and found sound

- **Invariant 1:** no web route creates or confirms a PO. Mark-sent requires CONFIRMED and sets `sent_at`, and cancel is signed and CHECK-enforced.
- **Invariant 2:** `change_tier` refuses promotion to A without two clean counts, and nothing writes `auto_order_enabled=True`.
- **Invariant 3 (outside H4):** components, options, manual lines, prices and modifier versions are closed and reopened, never updated in place. A same-instant second edit is refused rather than creating a zero-length row, and backdated or future edits are refused.
- **Invariant 12:** write-offs are append-only WASTE or STAFF movements.
- **§8E Qty:** there are no new SQL comparisons or aggregates on Qty, apart from `qty_remaining > 0` filters, which are correct on scaled integers. The migration writes no Qty values unscaled.
- **Materialise preview writes nothing:** row counts in 10 tables and the `menu_item_cost` sum were unchanged after 6 previews.
- **Concurrent recipe apply:** the second apply gets a 409.
- **Auth:**
  - Session tokens work, and a password change revokes all sessions.
  - The DB hash beats the env password.
  - Comparisons are constant-time, and scrypt uses n=2^14, r=8, p=1.
  - Login is limited to 5 attempts per minute.
  - The password never appears in responses, audit rows or the Telegram notice.
  - Only health, meta and the session endpoints are unauthenticated.
- **Media:** the route regex blocks traversal, and uploads are validated by magic bytes (PNG, JPEG and WebP only).
- **Frontend:** the token is kept in sessionStorage, and a 401 signs the user out. Money and quantity parsing is exact, and Apply is enabled only when the preview matches the request.
- **Finance:**
  - Re-importing the workbook changes 0 rows.
  - Just Eat's £114.92 goes to channel statements, not cash.
  - Capital is excluded from "owed", and drawings are not double-counted (44/44 matched).
  - Card-fee basis-point rounding and bank-holiday substitutes are correct.
  - Days are bucketed in Europe/London.
  - Jan–Apr match the workbook's monthly P&L lines.
