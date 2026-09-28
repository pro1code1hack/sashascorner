# Back-office redesign — data model reference

The schema the redesign's services and API are built against. Every table and column
below exists in `cafeops/db/models/` and in migration
**`62aa94a23687`** (`migrations/versions/62aa94a23687_back_office_redesign_data_foundation.py`,
`down_revision = "dff1f404b11c"`). Source specs: `finance.md` (FIN), `stock-orders-suppliers.md`
(SOS), `recipes-menu-ingredients.md` (RMI), `shell-agents.md` (SHELL), `DECISIONS.md` (DEC).
Where this file and a spec disagree, this file describes what was built; the
reconciliation is stated in §8.

**Conventions (all tables):** money is `int` pence (`Integer`); quantities are `Qty`
(scaled integer on SQLite, see ARCHITECTURE §8E — compare in SQL freely, never assign a
float); `*_at` are `UTCDateTime` (tz-aware UTC in, tz-aware UTC out; naive raises);
business dates are `Date` (Europe/London trading day). Enums are stored by **member
name** as VARCHAR with **no DB CHECK** (`native_enum=False`; SQLAlchemy 2.0 creates no
constraint) — the Python enum is the only guard, so always write through the model.
Constraint names follow `ck_<table>_<name>`. `*_by` columns hold the **per-device
operator name** (DEC 6: "who's using this", not a login), `String(120)`.

Nothing in this migration is effective-dated except where stated (`menu_item_price`,
`modifier_version`). Finance rows are edited in place with `updated_at`/`updated_by`.

---

## 1. Enums (`cafeops/db/models/enums.py`)

| Enum | Members | New / changed |
|---|---|---|
| `PaymentMethod` | CASH, **CASH_OFF_TILL**, CARD, VOUCHER, ACCOUNT, OTHER | CASH is the day's one **Cash** figure. CASH_OFF_TILL ("Own cash") is **retired** (DECISIONS 26): kept for old rows and migrations, never written, read as part of Cash. |
| `PaymentSourceKind` | CSV_UPLOAD, POS_API, MANUAL, **LEGACY_WORKBOOK** | + LEGACY_WORKBOOK |
| `PAYMENT_SOURCE_PRECEDENCE` (tuple constant) | POS_API > CSV_UPLOAD > MANUAL > LEGACY_WORKBOOK | new; see §2.1 |
| `PaymentBasis` | TILL, BANK_DEPOSIT | new |
| `ChannelSourceKind` | CSV_UPLOAD, BROWSER_AGENT, PARTNER_API, MANUAL, **LEGACY_WORKBOOK** | + LEGACY_WORKBOOK (Just Eat money from the workbook's cash column, DEC 4) |
| `ExpenseGroup` | COGS, OPEX | new |
| `ExpenseKind` | OPERATING, CAPITAL, DRAWINGS | new |
| `ExpenseMethod` | CARD, BANK_TRANSFER, DIRECT_DEBIT, STANDING_ORDER, CASH, CASH_WITHDRAWAL, **OTHER** | new (+OTHER vs FIN, §8) |
| `FinanceSource` | MANUAL, LEGACY_WORKBOOK, BANK_CSV, CSV_UPLOAD | new |
| `DirectorEntryType` | CAPITAL_INJECTION, LOAN_TO_COMPANY, DRAWINGS, REPAYMENT | new |
| `WriteOffReason` | WENT_OFF, SPILLED, STAFF, OTHER | new (SOS C11) |
| `ExpirySource` | ENTERED, ASSUMED | new (SOS §6 "optionally") |
| `MenuKind` | DRINKS, FOOD, OTHER | new |
| `MenuPriceSource` | MANUAL, POS_SYNC, LEGACY_WORKBOOK, BACKFILL | new |
| `AuthEvent` | LOGIN_OK, LOGIN_FAILED, LOGIN_RATE_LIMITED, SIGN_OUT, PASSWORD_CHANGED, PASSWORD_CHANGE_REFUSED, PASSWORD_RESET, SESSIONS_REVOKED | new |
| `SyncTrigger` | SCHEDULED, MANUAL_WEB, CLI | new |
| `SyncSource` | LIVE, FIXTURES | new |
| `SyncStatus` | RUNNING, OK, PARTIAL, SKIPPED, FAILED | new |
| `ProposalKind` | WASTE_FACTOR, TEMPLATE_GROUPING, CHANNEL_IMPORT, DATA_FIX, SUPPLIER_BASKET | new — API renders lowercase (`waste_factor`) per SHELL §6.2 |
| `ProposalStatus` | WAITING, ACCEPTED, DECLINED, SUPERSEDED, APPLY_FAILED | new |
| `ProposalConfidence` | HIGH, MEDIUM, LOW, NONE | new — API renders lowercase |

`cafeops/domain/types.py` (integrator-owned) was **not** touched; domain code that needs
the new enums should import them from `cafeops.db.models.enums` until the integrator
re-exports them.

---

## 2. Finance (`cafeops/db/models/finance.py`, plus `payment.py`)

### 2.1 `payment_day` — changed (FIN 3.3)

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `basis` | `PaymentBasis` | no | `TILL` (server default) | BANK_DEPOSIT = workbook Sep 2025–Mar 2026 card figures (a deposit on its deposit date). |
| `method` | widened | | | accepts CASH_OFF_TILL (legacy rows only; nothing writes it — DECISIONS 26) |
| `source` | widened | | | accepts LEGACY_WORKBOOK |

**One cash figure per day** (DECISIONS 26): the day's Cash is a `payment_day` row with
`method = CASH`. "Own cash" (`CASH_OFF_TILL`) is retired: never written; any old row is
summed into Cash on every read, and typing a new cash figure removes it. The day's note and orders override are in `trading_day` (below), not here.

**No double-counted takings — a read rule, not a column.** `payment_day` is unique on
`(business_date, method, source)`, so MANUAL and CSV rows for one day/method can
coexist. Every reader must resolve per `(business_date, method)` by taking the row whose
`source` comes first in `enums.PAYMENT_SOURCE_PRECEDENCE` and **ignoring** the others
(surface a >£1 disagreement as a caveat). Never `SUM(gross_pence)` across sources.
`services/ingest_payments.read_takings` still sums across sources and must be fixed by
whoever builds `takings_read`. A stored "superseded" flag was rejected: it goes stale
the moment a higher-precedence import lands.

### 2.2 `trading_day` — new

The non-money half of a Sales-tab row. A Sales "day" = union of `trading_day` and `payment_day` dates.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `business_date` | Date | no | **unique** |
| `note` | Text | yes | workbook column K |
| `transactions_override` | int | yes | CHECK `>= 0`. Wins over COUNT(DISTINCT receipt) from `sale` and `payment_day.transactions`. |
| `source` | `FinanceSource` | no | |
| `source_ref` | String(400) | yes | e.g. `Daily Sales!R12` |
| `created_at`, `updated_at` | UTCDateTime | no | `updated_at` has `onupdate` |
| `updated_by` | String(120) | yes | operator |

### 2.3 `expense_category` — new, seeded

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `name` | String(60) | no | unique |
| `expense_group` | `ExpenseGroup` | no | (spec called it `group`, an SQL keyword) |
| `sort` | int | no | default 0 |
| `is_active` | bool | no | default true |

Seeded by the migration, in this order (`sort` 1–13), em dash U+2014 kept:
COGS — `Stock — coffee`, `Stock — dairy`, `Stock — syrups`, `Stock — cakes`,
`Stock — food`, `Stock — packaging`, `Stock — other`; OPEX — `Rent`, `Utilities`,
`Wages`, `Marketing`, **`Card fees`** (workbook "Square fees", renamed per DEC 4 — the
importer must map "Square fees" → "Card fees"), `Other`.

### 2.4 `expense` — new

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | int PK | | | |
| `paid_on` | Date | no | | indexed |
| `category_id` | FK `expense_category.id` | no | | |
| `description` | String(300) | no | | |
| `amount_pence` | int | no | | CHECK `> 0` (`ck_expense_amount_positive`) |
| `method` | `ExpenseMethod` | **yes** | | nullable: workbook methods are free text |
| `kind` | `ExpenseKind` | no | OPERATING | only OPERATING enters the P&L |
| `has_receipt` | bool | no | false | receipts are a boolean only (no file) |
| `notes` | Text | yes | | |
| `needs_review` | bool | no | false | "Needs a look"; importer sets from `/ask user|verify|\?\?/i` |
| `supplier_id` | FK `supplier.id` | yes | | |
| `purchase_order_id` | FK `purchase_order.id` | yes | | |
| `source` | `FinanceSource` | no | | |
| `source_ref` | String(400) | yes | | indexed; importer idempotency key (`Expenses!R42`) |
| `deleted_at` | UTCDateTime | yes | | **soft delete — every read filters `deleted_at IS NULL`** |
| `created_at`, `updated_at` | UTCDateTime | no | now | |
| `updated_by` | String(120) | yes | | |

Indexes: `ix_expense_paid_on`, `ix_expense_kind_paid_on (kind, paid_on)`,
`ix_expense_needs_review`, `ix_expense_source_ref`. No VAT column (DEC 4).

### 2.5 `director_entry` — new

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | int PK | | | |
| `entry_date` | Date | no | | indexed |
| `type` | `DirectorEntryType` | no | | |
| `description` | String(300) | no | | |
| `in_pence` | int | no | 0 | |
| `out_pence` | int | no | 0 | |
| `notes` | Text | yes | | |
| `expense_id` | FK `expense.id` | yes | | **unique** |
| `source` | `FinanceSource` | no | | |
| `source_ref` | String(400) | yes | | |
| `deleted_at` | UTCDateTime | yes | | soft delete |
| `created_at`, `updated_at` | UTCDateTime | no | | |
| `updated_by` | String(120) | yes | | |

CHECKs: `one_direction` — exactly one of in/out is > 0; `type_matches_direction` —
CAPITAL_INJECTION / LOAN_TO_COMPANY are `in`, DRAWINGS / REPAYMENT are `out`;
`expense_link_is_drawings` — `expense_id IS NULL OR type = 'DRAWINGS'`.

**Drawings are stored once.** An `expense` with `kind = DRAWINGS` *is* the director's
out-movement; the expense service creates / updates / soft-deletes the linked
`director_entry` (mirroring date, amount, description) whenever an expense's kind
becomes or stops being DRAWINGS. The mirrored row is read-only in the Director table
("from expenses"). The P&L reads `expense`; the director balance reads
`director_entry`; nothing sums both. The workbook importer matches its 44 Director
Account drawings to the imported expenses (date + description + amount) and links
them rather than inserting twice.

**Capital injections** (DEC 4) are distinguishable by `type = CAPITAL_INJECTION` and
must be excluded from "company owes you": owed = Σ LOAN_TO_COMPANY in − Σ REPAYMENT out
− Σ DRAWINGS out (sign conventions are the service's; the point is that
CAPITAL_INJECTION never enters it). The API returns `capital_in_pence` separately.

### 2.6 `cash_count` — new

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `business_date` | Date | no | **unique** |
| `counted_pence` | int | no | CHECK `>= 0` |
| `counted_by` | String(120) | **no** | a count is a signature (FIN had it nullable; §8) |
| `counted_at` | UTCDateTime | no | |
| `explanation` | Text | yes | why over/short |
| `explained_by` | String(120) | yes | (from SHELL 3.2; FIN lacked it) |
| `explained_at` | UTCDateTime | yes | |
| `source` | `FinanceSource` | no | |
| `updated_at` | UTCDateTime | no | |

CHECK `explanation_signed`: `explanation IS NULL OR (explained_by IS NOT NULL AND explained_at IS NOT NULL)`.
Expected cash = resolved `payment_day` CASH (+ any legacy CASH_OFF_TILL) gross for the date (TILL
basis). Discrepancy is computed at read time, never stored, and is `None` when either
side is missing. Banner: newest date with `|diff| > finance_setting.cash_tolerance_pence`
and `explanation IS NULL`.

### 2.7 `card_payout` — new

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `sold_on` | Date | no | **unique** — the trading day the card money belongs to |
| `arrived_pence` | int | no | CHECK `>= 0`. No row = not recorded (never "arrived in full"). |
| `arrived_on` | Date | yes | |
| `source` | `FinanceSource` | no | |
| `source_ref` | String(400) | yes | |
| `notes` | Text | yes | |
| `updated_at` | UTCDateTime | no | |
| `updated_by` | String(120) | yes | |

### 2.8 `finance_setting` — new, single row seeded

`id` int PK, CHECK `id = 1` (`single_row`). Seeded row values:

| Column | Type | Seeded | CHECK |
|---|---|---|---|
| `payout_lag_working_days` | int | 2 | 0–10 |
| `card_fee_bp` | int | 175 (= 1.75%, basis points) | 0–1000 |
| `cash_tolerance_pence` | int | 500 | ≥ 0 |
| `payout_tolerance_pence` | int | 100 | ≥ 0 |
| `stock_pct_threshold_bp` | int | 3200 (= 32%) | 0–10000 |
| `updated_at` | UTCDateTime | migration time | |
| `updated_by` | String(120) | NULL | |

### 2.9 `channel_statement` — new

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `channel` | `SalesChannelName` | no | DELIVEROO / JUST_EAT |
| `month` | Date | no | first day of month — **service-enforced** (no portable SQL for day-of-month) |
| `gross_pence`, `commission_pence`, `ad_spend_pence` | int | yes | NULL = not reported; each CHECK `IS NULL OR >= 0` |
| `source` | `ChannelSourceKind` | no | |
| `source_ref` | String(400) | yes | |
| `notes` | Text | yes | |
| `created_at`, `updated_at` | UTCDateTime | no | |
| `updated_by` | String(120) | yes | |

UNIQUE `uq_channel_statement_channel_month (channel, month)`. Read rule (FIN 3.3):
statement row if present, else Σ `channel_metric` over the month only if every day with
orders has all three figures, else the month is incomplete (`None` + caveat). The
design's invented Deliveroo months must never be seeded.

---

## 3. Stock, orders, suppliers

### 3.1 `stock_movement` — changed (SOS C11, DEC 6)

| Column | Type | Null | Notes |
|---|---|---|---|
| `reason_code` | `WriteOffReason` | yes | only on human write-offs |
| `recorded_by` | String(120) | yes | operator for human-entered movements (write-offs, deliveries, adjustments); NULL on SALE / EXPIRED |

CHECKs:
- `reason_matches_type`: `reason_code IS NULL OR (type='STAFF' AND reason_code='STAFF') OR (type='WASTE' AND reason_code IN ('WENT_OFF','SPILLED','OTHER'))`
- `write_off_signed`: `reason_code IS NULL OR recorded_by IS NOT NULL`
- `other_needs_note`: `reason_code <> 'OTHER' OR note IS NOT NULL`

Mapping from the UI: "Went out of date" → `WASTE`/`WENT_OFF` (never `EXPIRED`, which is
derived and purged by `rebuild_batches`); "Spilled" → `WASTE`/`SPILLED`; "Staff" →
`STAFF`/`STAFF`; "Other" → `WASTE`/`OTHER` + note. Drift attribution should count
WENT_OFF as expiry-type loss. Ledger remains append-only (invariant 12).

### 3.2 `stock_batch` — changed

| Column | Type | Null | Notes |
|---|---|---|---|
| `expiry_source` | `ExpirySource` | yes | ENTERED / ASSUMED. Migration backfilled ASSUMED where `note LIKE 'EXPIRY ASSUMED%'`; others NULL (unknown). Services should set it on every new batch and may stop writing the note prefix. |
| `received_by` | String(120) | yes | previously only inside `note` prose |

`rebuild_batches` recreates batches: it must carry these through when it rebuilds.

### 3.3 `par_level` — changed

| Column | Type | Null | Notes |
|---|---|---|---|
| `min_qty_set_by` | String(120) | yes | who last set "Reorder at" (`min_qty`) from the web |
| `min_qty_set_at` | UTCDateTime | yes | |

Setting the floor never touches `auto_order_enabled` (invariant 2).

### 3.4 `ingredient_tier_change` — new (SOS C1)

Append-only. One row per human tier move.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `ingredient_id` | FK `ingredient.id` | no | |
| `tier_before`, `tier_after` | `Tier` | no | CHECK `tier_before <> tier_after` |
| `changed_at` | UTCDateTime | no | |
| `changed_by` | String(120) | no | |
| `reason` | Text | no | |
| `would_clear_gate` | bool | yes | `domain.tiers.would_clear_gate` at change time |

Index `(ingredient_id, changed_at)`. The service writes this and `ingredient.tier` in
one transaction; it never flips `auto_order_enabled`.

### 3.5 `purchase_order` — changed (SOS 4.2, DEC 1)

| Column | Type | Null | Notes |
|---|---|---|---|
| `sent_by` | String(120) | yes | web "Mark sent"; the bot path may leave NULL (no CHECK) |
| `cancelled_at` | UTCDateTime | yes | |
| `cancelled_by` | String(120) | yes | |
| `cancel_reason` | String(400) | yes | |

CHECK `cancelled_requires_human`: `status <> 'CANCELLED' OR (cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL)`.
Cancel allowed from DRAFT / PENDING_CONFIRM / CONFIRMED, refused from SENT / RECEIVED
(service rule). Per DEC 1 there is **no** `requested_by` / send-to-Telegram column:
the web never creates or confirms a PO. The migration would stamp any pre-existing
CANCELLED row with `cancelled_by = '(not recorded)'` (there were none).

### 3.6 `supplier` — changed (SOS 4.3, C12)

| Column | Type | Null | Notes |
|---|---|---|---|
| `kind` | String(40) | yes | display only ("Wholesale", "Bakery"…); ordering branches on `order_channel` |
| `notes` | Text | yes | |
| `archived_at` | UTCDateTime | yes | "Delete" = archive; archived suppliers drop out of sourcing |
| `archived_by` | String(120) | yes | |

CHECK `archive_signed`: `archived_at IS NULL OR archived_by IS NOT NULL`. `contact` and
`order_url` already existed. No `contact_method` column (SOS C13 says only if the owner
asks).

### 3.7 `supplier_product` — changed

`archived_at` UTCDateTime null, `archived_by` String(120) null, CHECK `archive_signed`
as above. Unlinking archives (open PO lines reference it); archived products drop out of
sourcing.

### 3.8 `tesco_routing` — changed ("Log as bought", SOS ShopRunIn)

| Column | Type | Null | Notes |
|---|---|---|---|
| `retailer` | String(80) | yes | where ("Tesco") |
| `bought_by` | String(120) | yes | |
| `paid_pence` | int | yes | what was actually paid for this line; NULL = not recorded, never 0 |

---

## 4. Recipes, menu, ingredients

### 4.1 `menu_item_price` — new, effective-dated (RMI C-3)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `menu_item_id` | FK `menu_item.id` | no | no cascade: items are never hard-deleted |
| `price_pence` | int | no | CHECK `>= 0` |
| `effective_from` | UTCDateTime | no | |
| `effective_to` | UTCDateTime | yes | NULL = open. CHECK `effective_to IS NULL OR effective_to > effective_from` |
| `source` | `MenuPriceSource` | no | |
| `set_by` | String(120) | yes | CHECK `manual_price_signed`: `source <> 'MANUAL' OR set_by IS NOT NULL` |
| `note` | String(400) | yes | |

Index `(menu_item_id, effective_from)`; **partial unique index
`uq_menu_item_price_one_open` on `menu_item_id` WHERE `effective_to IS NULL`** — at most
one open price per item. A price edit: close the open row (`effective_to = at`), open a
new one from `at` (start of today, never retroactive), and set `menu_item.price_pence`
in the same transaction. **`menu_item.price_pence` is now a cache of the open row**;
margin over a past window must read the row in force at `sale.sold_at`.

Backfill: one open row per `menu_item` with `source = BACKFILL`, `price_pence` = the
current `menu_item.price_pence`, `effective_from` = earliest `sale.sold_at` in the DB
(or migration time if no sales), `note = 'carried from menu_item.price_pence by migration 62aa94a23687'`.
BACKFILL's `effective_from` is the start of known history, not when the price was set.

### 4.2 `menu_item` — changed

| Column | Type | Null | Notes |
|---|---|---|---|
| `note` | String(400) | yes | per product group: the service writes it to every size row of the same `name` |
| `photo_asset_id` | FK `media_asset.id` ON DELETE SET NULL | yes | set on every size row of the group |

Relationship `MenuItem.photo -> MediaAsset | None`.

### 4.3 `media_asset` — new (RMI A5)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `sha256` | String(64) | no | **unique**, lowercase hex of the stored bytes; filename stem |
| `content_type` | String(40) | no | CHECK IN (`image/webp`, `image/jpeg`, `image/png`) |
| `bytes` | int | no | CHECK `> 0 AND <= 2097152` (2 MB) |
| `width`, `height` | int | yes | |
| `created_at` | UTCDateTime | no | |
| `uploaded_by` | String(120) | yes | |

Property `MediaAsset.filename` → `"{sha256}.{webp|jpg|png}"`. File path on disk
`/media/menu/{filename}`, URL `/media/menu/{filename}` (Caddy, immutable). Bytes are
**not** in SQLite. Distinct from the website's `site_media` table.

### 4.4 `menu_category` — new (RMI M2)

`id` PK · `name` String(80) unique · `kind` `MenuKind` not null · `sort_order` int default 0.
`menu_item.category` **stays a free string, no FK** (live values are empty or
misspelled); items whose category has no row here group as OTHER. Not seeded.

### 4.5 `modifier` — changed; `modifier_version` — new (RMI C-6, R21)

`modifier.price_is_estimate` bool **nullable** (three-state like
`prep_seconds_is_estimate`: NULL = no provenance recorded — all existing rows; True =
guess, render italic; False = confirmed). `modifier`'s action/target_role/ingredient_id/
qty_delta/qty_multiplier/price_pence/price_is_estimate/is_active are now a **cache of
the open version**; identity (`name`, `lightspeed_modifier_id`, both unique) stays on
`modifier` so sales keep resolving.

`modifier_version`:

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `modifier_id` | FK `modifier.id` | no | |
| `action` | `ModifierAction` | no | |
| `target_role` | `ComponentRole` | no | |
| `ingredient_id` | FK `ingredient.id` | yes | |
| `qty_delta`, `qty_multiplier` | Qty | yes | |
| `price_pence` | int | no | CHECK `>= 0` |
| `price_is_estimate` | bool | yes | |
| `is_active` | bool | no | default true |
| `effective_from` | UTCDateTime | no | |
| `effective_to` | UTCDateTime | yes | CHECK ordered |
| `changed_by` | String(120) | yes | NULL on backfill |

Index `(modifier_id, effective_from)`; partial unique `uq_modifier_version_one_open`
(one open version per modifier). Backfill: one open version per modifier copying its
current columns, `effective_from` = same instant as §4.1. The resolver is **not yet**
changed to read versions — whoever builds modifier writes must switch
`resolve_recipe` / expansion to the version valid at `sold_at`.

### 4.6 `recipe_change` — new (RMI R23 / A2 history)

One row per applied composition/price change, written by the apply services in the same
transaction. The dated rows remain the source of truth for *what*; this records *who*
and the readable lines for `GET /api/templates/{id}/history`.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `template_id` | FK `drink_template.id` | yes | |
| `menu_item_id` | FK `menu_item.id` | yes | CHECK `has_subject`: at least one set |
| `change_kind` | String(40) | no | `template_changeset` / `manual_lines` / `menu_price` / `modifier` / `materialise` |
| `effective_from` | UTCDateTime | no | |
| `actor` | String(120) | no | |
| `summary` | Text | no | `TemplateChangesetApplied.summary` |
| `lines` | JSON list[str] | no | server-built diff lines |
| `created_at` | UTCDateTime | no | |

### 4.7 `ingredient` / `ingredient_price` — changed (RMI B3, C-5)

- `ingredient.retired_at` UTCDateTime null, `ingredient.retired_by` String(120) null,
  CHECK `retire_signed`. "Delete" = retire; refused while referenced by open recipe
  lines (service). `ingredient.category` already existed (String(80)); no category table.
- `ingredient_price.recorded_by` String(120) null — who recorded a price (NULL for
  seeded/imported rows). `supplier_id` already existed.

---

## 5. Shell, auth, sync, agents

### 5.1 `auth_credential` — new (SHELL 4.2, DEC 3)

Single row, CHECK `id = 1`. **Precedence: if the row exists it wins; otherwise
`CAFEOPS_API_PASSWORD` is the bootstrap.** `cafeops password reset` deletes the row.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | always 1 |
| `password_hash` | String(255) | no | `scrypt$n$r$p$salt_b64$hash_b64` (n=2^14, r=8, p=1, 16-byte salt) |
| `algo` | String(20) | no | CHECK `= 'scrypt'` |
| `set_at` | UTCDateTime | no | |
| `set_via` | String(20) | no | `web` / `cli` |
| `set_by` | String(120) | yes | operator name |

### 5.2 `auth_session` — new

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `token_hash` | String(64) | no | **unique**, lowercase hex sha256 of the bearer token |
| `created_at`, `last_seen_at` | UTCDateTime | no | |
| `expires_at` | UTCDateTime | no | absolute expiry (30 days) |
| `revoked_at` | UTCDateTime | yes | |
| `revoked_reason` | String(40) | yes | `sign_out` / `password_changed` / `password_reset` / `expired_sweep`; CHECK `(revoked_at IS NULL) = (revoked_reason IS NULL)` |
| `user_agent` | String(300) | yes | |
| `client_ip` | String(64) | yes | |

Valid iff `revoked_at IS NULL AND now < expires_at AND now < last_seen_at + 7 days`
(idle rule in the service). A password change **revokes** (sets `revoked_at`) every row
rather than deleting (SHELL said delete; §8). Index `(revoked_at, expires_at)`.

### 5.3 `auth_audit` — new

`id` PK · `at` UTCDateTime · `event` `AuthEvent` · `via` String(20) (`web`/`cli`/`api_key`)
· `session_id` FK `auth_session.id` null · `client_ip` String(64) null · `actor`
String(120) null · `detail` String(400) null. Indexes `(at)`, `(event, at)`. Never holds
a password or token. Replaces SHELL's `settings_change` table (§8).

**Login rate limiting is not in the DB** (SHELL 4.2 §5): in-process token bucket,
5 failures/min and 30/hour per client IP. `LOGIN_RATE_LIMITED` audit rows may be
written, but the limiter must not depend on them.

### 5.4 `sync_run` — new (SHELL 3.1, 4.3)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `trigger` | `SyncTrigger` | no | |
| `source` | `SyncSource` | no | web only ever LIVE |
| `started_at` | UTCDateTime | no | |
| `finished_at` | UTCDateTime | yes | CHECK `(status = 'RUNNING') = (finished_at IS NULL)` |
| `status` | `SyncStatus` | no | default RUNNING |
| `window_since`, `window_until` | Date | no | CHECK `window_until >= window_since` |
| `receipts_seen`, `lines_ingested`, `unresolved_count` | int | yes | |
| `detail` | Text | yes | skipped / partial reason / exception |
| `requested_by` | String(120) | yes | operator for MANUAL_WEB, else `scheduler` / `cli` |

Indexes `(status, started_at)`, `(started_at)`. The RUNNING row is the lock for
`POST /api/sync` (409 if RUNNING and < 15 min old; older → mark FAILED "abandoned").
Stale banner reads the newest row with status OK or PARTIAL.

### 5.5 `agent_proposal` — new (SHELL 6.1, DEC 7)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int PK | | |
| `created_at` | UTCDateTime | no | |
| `run_id` | String(64) | no | joins `agent_action_log.run_id`; indexed |
| `log_id` | FK `agent_action_log.id` | no | the PROPOSE call, inserted in the same commit |
| `agent` | String(60) | no | `drift_explainer` / `import_assistant` / `channel_reporter` / `basket_stager` |
| `kind` | `ProposalKind` | no | |
| `subject_ref` | String(200) | no | `ingredient:42`, `template:Latte`, a path, `po:17` |
| `title` | String(120) | no | spec wants ≤ 80 chars (service) |
| `body` | Text | no | |
| `payload` | JSON dict | no | decimals **as strings**; carries `current` for SUPERSEDED detection |
| `confidence` | `ProposalConfidence` | yes | |
| `figures` | JSON list[str] | no | |
| `status` | `ProposalStatus` | no | default WAITING |
| `decided_at` | UTCDateTime | yes | |
| `decided_by` | String(120) | yes | operator name / Telegram username |
| `decision_note` | Text | yes | |
| `applied_result` | JSON dict | yes | `{"error": ...}` for APPLY_FAILED |

CHECK `ck_agent_proposal_decided_by_human`: `status = 'WAITING' OR (decided_at IS NOT NULL AND decided_by IS NOT NULL)`.
Indexes `(status, created_at)`, `(run_id)`, `(kind, subject_ref)`. The agent's audit
engine may only INSERT (policy change in `agent/policies.py` is not part of this
migration); status changes happen only in `services/agent_proposals.py`.

`agent_action_log.agent` String(60) null — new, labels which agent ran.

---

## 6. Migration `62aa94a23687` — what it does

Upgrade: create the 19 tables; add the columns above; widen `payment_day.method`,
`payment_day.source`, `channel_metric.source`, `channel_item_metric.source`; add the
CHECKs on existing tables (`purchase_order`, `stock_movement`, `supplier`,
`supplier_product`, `ingredient`) — added by hand because Alembic autogenerate does not
compare CHECKs; seed `expense_category` (13) and `finance_setting` (1); backfill
`menu_item_price`, `modifier_version`, `stock_batch.expiry_source`. No transactional
data is seeded.

Downgrade: **refuses** (RuntimeError) while any `payment_day` row uses CASH_OFF_TILL,
LEGACY_WORKBOOK or basis ≠ TILL, any channel row uses LEGACY_WORKBOOK, or any
`stock_movement.reason_code` is set; otherwise drops everything in reverse.

Note: the repo-root `cafeops.db` is at `3c41d7a9e2b0`, two revisions behind head
(`dff1f404b11c` payment_day, then this). It was not migrated.

---

## 7. Not added, deliberately

- **VAT** anywhere (DEC 4). **Receipt files** (boolean only).
- **Login rate-limit table** (in-process by spec).
- **`app_setting` / bot language** (DEC 5 drops the select).
- **`purchase_order.requested_by` / send-to-Telegram state** (DEC 1: web is read-only for creating/confirming).
- **`template_modifier` join table** (RMI C-7: per-template toggles contradict the role model; read-only).
- **`variant_option.is_active`** (RMI R17: derived from generated `menu_item.active`).
- **Second flavour ingredient on `variant_option`** (RMI C-18: stays a manual item).
- **Persisted staff hourly rate** (RMI C-6: what-if only; the rate stays in config).
- **`supplier.contact_method`** (SOS C13: only if the owner asks).
- **Payout split table** for bank payouts batching several days (FIN: out of scope).
- **`finance_edit` audit log** (FIN: not required by the design).

## 8. Reconciliations (spec disagreements, conservative choice taken)

1. **Double-counted takings** (FIN 3.5): a precedence *rule* (`PAYMENT_SOURCE_PRECEDENCE`), not a column. FIN proposed none either; the caller's brief asked for "whatever precedence field" — the spec has only the rule.
2. **Own cash / note / orders override**: FIN puts own cash in `payment_day` (CASH_OFF_TILL — since retired, DECISIONS 26: one CASH figure per day) and note/override in `trading_day`; followed FIN rather than adding them as `payment_day` columns (a note is per day, `payment_day` is per day *per method*).
3. **Enum CHECK recreation** (FIN 3.4 step 1): not needed — these enums have no DB CHECK; only VARCHAR length was widened.
4. **`expense_category.group`** → `expense_group` (SQL keyword).
5. **`expense.method`** nullable and `ExpenseMethod.OTHER` added: the workbook's methods are free text; unmappable values are kept and labelled, not forced.
6. **`cash_count.counted_by`**: FIN nullable, SHELL `str` → NOT NULL. `explained_by` added from SHELL (FIN lacked it) with a CHECK that an explanation is signed.
7. **`director_entry`**: FIN's CHECKs kept; added `expense_link_is_drawings` so only drawings can mirror an expense.
8. **`channel_statement.month` day = 1**: FIN wanted a CHECK; no portable SQL for day-of-month, so service-enforced.
9. **`ChannelSourceKind.LEGACY_WORKBOOK`** added (neither spec listed it) so the Just Eat workbook money (DEC 4) is labelled as legacy, not MANUAL.
10. **Auth table names**: SHELL's `auth_credential`, `auth_session` kept (the brief's "credential"/"api_session"); SHELL's `settings_change` became `auth_audit` (the brief's name, wider event set). Sessions are **revoked** on password change, not deleted (SHELL said delete) — keeps the audit able to say what was ended.
11. **Modifier dating**: RMI offered `modifier_version` *or* `effective_from/to` on `modifier`; chose the version table because `modifier.name` and `lightspeed_modifier_id` are unique and are the sale-resolution key.
12. **Modifier "price is a guess"**: RMI said "price source column"; built as a nullable bool `price_is_estimate`, matching `prep_seconds_is_estimate` (PriceSource's INVOICE/SUPPLIER_FEED are purchase-side words).
13. **Menu price backfill** `effective_from` = earliest sale (else now), source BACKFILL — never presented as a real change date.
14. **Operator names (DEC 6)** stored where the brief's flows write: `stock_movement.recorded_by`, `stock_batch.received_by`, `par_level.min_qty_set_by/_at`, `ingredient_tier_change.changed_by`, `purchase_order.sent_by/cancelled_by`, `supplier(_product).archived_by`, `ingredient.retired_by`, `ingredient_price.recorded_by`, `menu_item_price.set_by`, `modifier_version.changed_by`, `recipe_change.actor`, `tesco_routing.bought_by`, `cash_count.counted_by/explained_by`, finance `updated_by`, `agent_proposal.decided_by`, `sync_run.requested_by`, `media_asset.uploaded_by`. Counts (`stock_count.counted_by`) and checklist answers (`checklist_response.responded_by`) already had them.
15. **Tier change and recipe history tables** (`ingredient_tier_change`, `recipe_change`) are additions beyond the specs' explicit lists: SOS C1 requires a recorded name + reason, and RMI's history endpoint returns an `actor` that no existing row stores.
