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
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import MenuItem, Modifier
from cafeops.integrations.lightspeed.client import LightspeedClient
from cafeops.integrations.lightspeed.mapper import (
    MatchOutcome,
    MenuItemRef,
    RawCatalogBatch,
    RawCatalogItem,
    RawReceipt,
    RawReceiptBatch,
    build_modifier_lookup,
    map_receipt_lines,
    match_menu_items,
)
from cafeops.services.ingest_sales import IngestReport, ingest_sale_lines

__all__ = ["SyncResult", "sync_window"]

DEFAULT_FIXTURES_DIR = Path(__file__).parent / "fixtures"
CATALOG_FIXTURE = "catalog_items.json"
RECEIPTS_FIXTURE = "receipts_2026-09.json"


@dataclass
class SyncResult:
    since: date
    until: date
    fixtures: bool
    receipts_seen: int = 0
    lines_seen: int = 0
    lines_in_window: int = 0
    catalog_backfilled: int = 0
    match: MatchOutcome | None = None
    ingest: IngestReport | None = None

    def lines(self) -> list[str]:
        mode = "fixtures" if self.fixtures else "live"
        out = [
            f"window {self.since.isoformat()}..{self.until.isoformat()} ({mode})",
            f"catalog match: {self.match.summary() if self.match else 'not run'}; "
            f"{self.catalog_backfilled} menu_item.lightspeed_id backfilled this run",
            f"receipts seen: {self.receipts_seen}, lines seen: {self.lines_seen}, "
            f"lines in window: {self.lines_in_window}",
            f"ingest: {self.ingest.summary() if self.ingest else 'not run'}",
        ]
        if self.match:
            out.extend(f"  UNRESOLVED ITEM: {m}" for m in self.match.unresolved_menu_items)
            out.extend(f"  AMBIGUOUS ITEM: {m}" for m in self.match.ambiguous_menu_items)
        if self.ingest:
            out.extend(f"  UNRESOLVED SALE LINE: {m}" for m in self.ingest.unresolved_items)
            out.extend(f"  UNRESOLVED MODIFIER: {m}" for m in self.ingest.unresolved_modifiers)
            out.extend(f"  SUBSTITUTION ERROR: {m}" for m in self.ingest.substitution_errors)
        return out


def sync_window(
    session: Session,
    *,
    since: date,
    until: date,
    fixtures: bool = True,
    fixtures_dir: Path | None = None,
    now: datetime | None = None,
) -> SyncResult:
    now = now or datetime.now(UTC)
    result = SyncResult(since=since, until=until, fixtures=fixtures)

    if fixtures:
        catalog, receipts = _load_fixtures(fixtures_dir or DEFAULT_FIXTURES_DIR)
    else:
        catalog, receipts = asyncio.run(_fetch_live(since=since, until=until))

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
    result.ingest = ingest_sale_lines(session, mapped, now=now)
    return result


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
    catalog_path = fixtures_dir / CATALOG_FIXTURE
    receipts_path = fixtures_dir / RECEIPTS_FIXTURE
    catalog = RawCatalogBatch.model_validate(json.loads(catalog_path.read_text())).items
    receipts = RawReceiptBatch.model_validate(json.loads(receipts_path.read_text())).receipts
    return catalog, receipts


# ==========================================================================
# Live (unreachable without credentials -- see client.py)
# ==========================================================================


async def _fetch_live(*, since: date, until: date) -> tuple[list[RawCatalogItem], list[RawReceipt]]:
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
    async with LightspeedClient() as client:
        async for page in client.get_items():
            catalog.extend(RawCatalogBatch.model_validate(page).items)
        async for page in client.get_sales(since=since.isoformat(), until=until.isoformat()):
            receipts.extend(RawReceiptBatch.model_validate(page).receipts)
    return catalog, receipts
