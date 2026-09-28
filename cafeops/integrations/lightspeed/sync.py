"""Idempotent ingestion over a date window.

`sync_window` is the orchestrator `cafeops sync` calls: resolve the menu-item
catalog match (backfilling `menu_item.lightspeed_id` for anything newly
resolved), map the window's receipts, and hand them to
`services/ingest_sales.py` -- the actual transaction boundary.

Fixtures-first (CLAUDE.md, ARCHITECTURE.md 0): `fixtures=True` is the only path
exercised anywhere in this repo. The live path exists (`_fetch_live`) so the
shape is right when credentials ever appear, but it is never reachable without
them -- `LightspeedClient` raises `LightspeedNotConfiguredError` before any
socket opens.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import ManualRecipeLine, MenuItem, Modifier
from cafeops.integrations.lightspeed.client import LightspeedClient, LightspeedPaginationError
from cafeops.integrations.lightspeed.mapper import (
    MappedSaleLine,
    MatchOutcome,
    MenuItemRef,
    RawCatalogBatch,
    RawCatalogItem,
    RawReceipt,
    RawReceiptBatch,
    build_modifier_lookup,
    dedupe_receipts,
    map_receipt_lines,
    match_menu_items,
)
from cafeops.services.ingest_sales import IngestReport, ingest_sale_lines
from cafeops.services.loyalty.pos import PosReport, ReceiptCustomer, pos_pass

__all__ = ["SyncResult", "sync_window"]

DEFAULT_FIXTURES_DIR = Path(__file__).parent / "fixtures"
CATALOG_FIXTURE = "catalog_items.json"
RECEIPTS_FIXTURE = "receipts_2026-09.json"
#: Recorded payloads for the awkward cases, one directory each. See `scenarios.py`.
SCENARIOS_DIR = DEFAULT_FIXTURES_DIR / "scenarios"


@dataclass
class SyncResult:
    since: date
    until: date
    fixtures: bool
    #: True when the caller intends to roll back. Nothing here behaves differently --
    #: the work is done identically and the transaction is discarded by the caller --
    #: so the report is exactly what a committing run would have produced. A dry run
    #: that took a different code path would be inspecting the wrong thing.
    dry_run: bool = False
    receipts_seen: int = 0
    lines_seen: int = 0
    lines_in_window: int = 0
    catalog_backfilled: int = 0
    duplicate_receipts: tuple[str, ...] = ()
    conflicting_receipts: tuple[str, ...] = ()
    double_count_risks: tuple[str, ...] = ()
    partial_reason: str | None = None
    match: MatchOutcome | None = None
    ingest: IngestReport | None = None
    #: Loyalty (phase 3): receipts that named a customer, links, auto-stamps.
    loyalty: PosReport | None = None
    loyalty_error: str | None = None

    def lines(self) -> list[str]:
        mode = "fixtures" if self.fixtures else "live"
        head = f"window {self.since.isoformat()}..{self.until.isoformat()} ({mode})"
        if self.dry_run:
            head += " -- DRY RUN, nothing will be written"
        out = [
            head,
            f"catalog match: {self.match.summary() if self.match else 'not run'}; "
            f"{self.catalog_backfilled} menu_item.lightspeed_id backfilled this run",
            f"receipts seen: {self.receipts_seen}, lines seen: {self.lines_seen}, "
            f"lines in window: {self.lines_in_window}",
            f"ingest: {self.ingest.summary() if self.ingest else 'not run'}",
        ]
        if self.duplicate_receipts:
            out.append(
                f"  {len(self.duplicate_receipts)} receipt(s) arrived twice in this window "
                f"with identical content and were collapsed: "
                f"{', '.join(self.duplicate_receipts[:6])}"
                + (" ..." if len(self.duplicate_receipts) > 6 else "")
                + " -- normal with an overlapping window, not a problem"
            )
        for receipt_id in self.conflicting_receipts:
            out.append(
                f"  CONFLICTING RECEIPT: {receipt_id} arrived more than once in this window "
                "with DIFFERENT content. Both copies were dropped: there is no rule for "
                "choosing between them that is not a guess. Re-run the window once the "
                "receipt has settled."
            )
        if self.partial_reason:
            out.append(f"  PARTIAL WINDOW: {self.partial_reason}")
        if self.match:
            out.extend(f"  UNRESOLVED ITEM: {m}" for m in self.match.unresolved_menu_items)
            out.extend(f"  AMBIGUOUS ITEM: {m}" for m in self.match.ambiguous_menu_items)
        if self.ingest:
            out.extend(f"  UNRESOLVED SALE LINE: {m}" for m in self.ingest.unresolved_items)
            out.extend(f"  UNRESOLVED MODIFIER: {m}" for m in self.ingest.unresolved_modifiers)
            out.extend(f"  FUTURE-DATED LINE: {m}" for m in self.ingest.clock_skew_refused)
            out.extend(f"  CLOCK SKEW: {m}" for m in self.ingest.clock_skew_tolerated)
            out.extend(f"  DUPLICATE LINE: {m}" for m in self.ingest.duplicate_line_ids)
            out.extend(f"  SUBSTITUTION ERROR: {m}" for m in self.ingest.substitution_errors)
        out.extend(f"  DOUBLE-COUNT RISK: {m}" for m in self.double_count_risks)
        if self.loyalty is not None:
            out.extend(self.loyalty.lines())
        if self.loyalty_error:
            out.append(f"  LOYALTY PASS FAILED (sales were still ingested): {self.loyalty_error}")
        return out


def sync_window(
    session: Session,
    *,
    since: date,
    until: date,
    fixtures: bool = True,
    fixtures_dir: Path | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> SyncResult:
    now = now or datetime.now(UTC)
    result = SyncResult(since=since, until=until, fixtures=fixtures, dry_run=dry_run)

    if fixtures:
        catalog, receipts = _load_fixtures(fixtures_dir or DEFAULT_FIXTURES_DIR)
    else:
        catalog, receipts, result.partial_reason = asyncio.run(
            _fetch_live(since=since, until=until)
        )

    # Before anything else: one copy per receipt id. A window whose pages overlap
    # re-delivers receipts, and `daily_sync` re-reads three days on purpose.
    deduped = dedupe_receipts(receipts)
    result.duplicate_receipts = deduped.duplicates
    result.conflicting_receipts = deduped.conflicts
    receipts = list(deduped.receipts)

    result.match, current_lightspeed_ids = _match_and_backfill_catalog(session, catalog, result)

    modifier_rows = session.execute(select(Modifier.id, Modifier.name)).all()
    modifier_lookup = build_modifier_lookup((mid, name) for mid, name in modifier_rows)

    # The lookup sale lines resolve against needs EVERY menu_item that has a
    # lightspeed_id -- from earlier runs as well as this one. Idempotency
    # depends on this: once an item is backfilled, `_match_and_backfill_catalog`
    # correctly stops re-matching it (it is no longer in `outcome.resolved`),
    # so falling back to just that dict here would silently stop resolving
    # sale lines for everything already matched.
    item_lookup = _build_item_lookup(catalog, current_lightspeed_ids)

    result.receipts_seen = len(receipts)
    windowed = [r for r in receipts if _in_window(r, since, until)]
    result.lines_seen = sum(len(r.lines) for r in receipts)
    result.lines_in_window = sum(len(r.lines) for r in windowed)

    mapped = map_receipt_lines(windowed, item_lookup=item_lookup, modifier_lookup=modifier_lookup)
    result.double_count_risks = _double_count_risks(session, mapped)
    result.ingest = ingest_sale_lines(session, mapped, now=now)
    _loyalty_pass(session, windowed, since=since, now=now, result=result)
    return result


def _loyalty_pass(
    session: Session,
    receipts: Sequence[RawReceipt],
    *,
    since: date,
    now: datetime,
    result: SyncResult,
) -> None:
    """Record receipt customers, link members, and (if enabled) auto-stamp.

    In a SAVEPOINT: a loyalty fault must not cost the night's sales, which are the stock
    ledger's input. The failure is reported instead, and the next sync retries (every
    step is idempotent).
    """
    customers = [
        ReceiptCustomer(
            receipt_id=r.id,
            customer_id=r.consumer.key,
            closed_at=r.closed_at,
            email=r.consumer.email,
            phone=r.consumer.phone,
            first_name=r.consumer.first_name,
            last_name=r.consumer.last_name,
            total_pence=r.total_pence,
        )
        for r in receipts
        if r.consumer is not None and r.consumer.key
    ]
    start = datetime.combine(since, time.min, tzinfo=settings.tz).astimezone(UTC)
    try:
        with session.begin_nested():
            result.loyalty = pos_pass(session, customers, since=start, now=now)
    except Exception as exc:
        result.loyalty = None
        result.loyalty_error = f"{type(exc).__name__}: {exc}"


def _double_count_risks(session: Session, lines: Sequence[MappedSaleLine]) -> tuple[str, ...]:
    """Receipts where an ingredient would be depleted twice for one drink.

    ARCHITECTURE.md 8K.3. Two ways an alt milk can reach the ledger, and they are
    not mutually exclusive on a till:

    * the **modifier** path -- a SUBSTITUTE on the drink's MILK slot, which only
      arrives on the real-time endpoints this project decided not to consume;
    * the **proxy** path -- the `Oat milk (+upcharge)` menu item rung as its own
      line, whose manual recipe is the substitution delta (+0.2 L oat, -0.2 L whole).

    If a till does both on one receipt, the oat milk is counted twice and the whole
    milk is credited back twice. So this is detected structurally rather than by
    name: a **proxy item** is any menu item whose manual recipe contains a negative
    line -- that negative is what makes it a delta rather than a drink -- and the
    clash is another line on the same receipt carrying a modifier that supplies the
    same ingredient the proxy adds.

    Reported, never resolved. Which of the two the till "meant" is a question about
    her till layout, and §8K.4 says to ask rather than infer.
    """
    proxy_adds = _proxy_item_ingredients(session)
    if not proxy_adds:
        return ()
    modifier_ingredients = dict(
        session.execute(
            select(Modifier.id, Modifier.ingredient_id).where(Modifier.ingredient_id.isnot(None))
        ).all()
    )
    item_names = dict(session.execute(select(MenuItem.id, MenuItem.name)).all())

    by_receipt: dict[str, list[MappedSaleLine]] = {}
    for line in lines:
        by_receipt.setdefault(line.lightspeed_receipt_id, []).append(line)

    out: list[str] = []
    for receipt_id, receipt_lines in sorted(by_receipt.items()):
        proxied: dict[int, str] = {}
        for line in receipt_lines:
            added = proxy_adds.get(line.menu_item_id or -1)
            if added is not None:
                proxied[added] = item_names.get(line.menu_item_id or -1, "?")
        if not proxied:
            continue
        for line in receipt_lines:
            for modifier_id in line.applied_modifier_ids:
                ingredient_id = modifier_ingredients.get(modifier_id)
                if ingredient_id is None or ingredient_id not in proxied:
                    continue
                out.append(
                    f"receipt {receipt_id}: {proxied[ingredient_id]!r} was rung as its own "
                    f"line AND line {line.lightspeed_line_id} carries a modifier supplying "
                    f"the same ingredient (id {ingredient_id}). Ingredient {ingredient_id} "
                    "will be depleted TWICE and the milk it replaces credited back twice. "
                    "Nothing was reconciled: ask which the till means (ARCHITECTURE.md 8K.3)."
                )
    return tuple(out)


def _proxy_item_ingredients(session: Session) -> dict[int, int]:
    """menu_item_id -> the ingredient its delta recipe ADDS, for proxy items only.

    A proxy item is recognised by having at least one negative manual recipe line.
    Structural on purpose: keying off the `(+upcharge)` in the name would break the
    day somebody tidies the menu, and the name is not where the meaning lives.
    """
    rows = session.execute(
        select(
            ManualRecipeLine.menu_item_id, ManualRecipeLine.ingredient_id, ManualRecipeLine.qty
        ).where(ManualRecipeLine.effective_to.is_(None))
    ).all()
    by_item: dict[int, list[tuple[int, Decimal]]] = {}
    for menu_item_id, ingredient_id, qty in rows:
        by_item.setdefault(menu_item_id, []).append((ingredient_id, qty))
    out: dict[int, int] = {}
    for menu_item_id, entries in by_item.items():
        if not any(qty < 0 for _ing, qty in entries):
            continue
        added = [ing for ing, qty in entries if qty > 0]
        if len(added) == 1:
            out[menu_item_id] = added[0]
    return out


def _in_window(receipt: RawReceipt, since: date, until: date) -> bool:
    local_day = receipt.closed_at.astimezone(settings.tz).date()
    return since <= local_day <= until


def _match_and_backfill_catalog(
    session: Session, catalog: Sequence[RawCatalogItem], result: SyncResult
) -> tuple[MatchOutcome, dict[int, str]]:
    """Returns (this run's match outcome, every menu_item_id -> lightspeed_id
    known after this run -- old plus newly backfilled)."""
    rows = session.execute(
        select(MenuItem.id, MenuItem.name, MenuItem.size_code, MenuItem.lightspeed_id)
    ).all()
    unmatched_rows = [
        MenuItemRef(id=mid, name=name, size_code=size_code)
        for mid, name, size_code, lightspeed_id in rows
        if lightspeed_id is None
    ]
    current: dict[int, str] = {mid: lid for mid, _n, _s, lid in rows if lid is not None}

    outcome = match_menu_items(unmatched_rows, catalog)

    for menu_item_id, lightspeed_id in outcome.resolved.items():
        row = session.get(MenuItem, menu_item_id)
        if row is not None and row.lightspeed_id is None:
            row.lightspeed_id = lightspeed_id
            result.catalog_backfilled += 1
        current[menu_item_id] = lightspeed_id
    return outcome, current


def _build_item_lookup(
    catalog: Sequence[RawCatalogItem], resolved: dict[int, str]
) -> dict[str, int]:
    """catalog item id/sku -> menu_item_id, from the resolved matches."""
    catalog_id_to_menu_item = {
        catalog_id: menu_item_id for menu_item_id, catalog_id in resolved.items()
    }
    lookup: dict[str, int] = dict(catalog_id_to_menu_item)
    for entry in catalog:
        menu_item_id = catalog_id_to_menu_item.get(entry.id)
        if menu_item_id is not None and entry.sku:
            lookup.setdefault(entry.sku, menu_item_id)
    return lookup


# ==========================================================================
# Fixtures
# ==========================================================================


def _load_fixtures(fixtures_dir: Path) -> tuple[list[RawCatalogItem], list[RawReceipt]]:
    """Read a recorded payload directory.

    Two conveniences, both there so a scenario directory can hold only the thing it
    is about:

    * the **catalog** falls back to the default fixtures when the directory has none
      -- 320 items repeated per scenario would be 320 chances for them to drift apart;
    * **every** `receipts*.json` in the directory is read, in name order, and treated
      as consecutive pages of one window. That is what lets a scenario record "the
      same receipt arrived on page 1 and again on page 2" as two files, which is how
      it actually happens.
    """
    if not fixtures_dir.is_dir():
        raise FileNotFoundError(f"{fixtures_dir}: no such recorded-payload directory")
    catalog_path = fixtures_dir / CATALOG_FIXTURE
    if not catalog_path.exists():
        catalog_path = DEFAULT_FIXTURES_DIR / CATALOG_FIXTURE
    catalog = RawCatalogBatch.model_validate(json.loads(catalog_path.read_text())).items

    receipt_paths = sorted(fixtures_dir.glob("receipts*.json"))
    if not receipt_paths:
        raise FileNotFoundError(
            f"{fixtures_dir}: no receipts*.json here. A recorded payload directory needs "
            "at least one page of receipts; the catalog may be inherited, the sales may not."
        )
    receipts: list[RawReceipt] = []
    for path in receipt_paths:
        receipts.extend(RawReceiptBatch.model_validate(json.loads(path.read_text())).receipts)
    return catalog, receipts


# ==========================================================================
# Live (unreachable without credentials -- see client.py)
# ==========================================================================


async def _fetch_live(
    *, since: date, until: date
) -> tuple[list[RawCatalogItem], list[RawReceipt], str | None]:
    """Pull the catalog and the window's sales from the real API.

    Never exercised in this environment: `LightspeedClient` raises
    `LightspeedNotConfiguredError` on the first call because no
    `CAFEOPS_LIGHTSPEED_*` credential is ever set here.

    NOTE (research finding -- see the final report): the financial `get_sales`
    endpoint this uses for the date-range pull does not carry modifier data.
    A live sync limited to this endpoint would silently under-count oat milk
    and any other modifier-tracked ingredient. Fixing that needs a second pass
    over `get_open_checks` for the same window, keyed by receipt id, before
    this function can be trusted for tier-A modifier ingredients -- flagged as
    an open item, not implemented here, because there is no live payload to
    verify the join against.
    """
    catalog: list[RawCatalogItem] = []
    receipts: list[RawReceipt] = []
    partial: str | None = None
    async with LightspeedClient() as client:
        try:
            async for page in client.get_items():
                catalog.extend(RawCatalogBatch.model_validate(page).items)
            async for page in client.get_sales(since=since.isoformat(), until=until.isoformat()):
                receipts.extend(RawReceiptBatch.model_validate(page).receipts)
        except LightspeedPaginationError as exc:
            # Paging stopped because the cursor stopped advancing. The pages already
            # read are good data and worth ingesting -- ingestion is idempotent, so
            # tomorrow's overlapping window picks up the rest. Throwing them away
            # would mean a bad cursor costs a day of sales rather than a warning.
            partial = str(exc)
    return catalog, receipts, partial
