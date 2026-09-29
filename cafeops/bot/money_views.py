"""Reads and writes behind `/sale`, `/cash`, `/export` and a sent file. Sync, no Russian.

The money half of `views.py`, kept apart because it consumes a different services
layer: `record_sale`, `transactions_csv`, `finance.trading_days`, `ingest_payments`,
`channel_sync`. Same rules as `views.py` -- every function takes a `Session`, runs on
the `run_sync` thread, writes only through a service, and returns a `viewmodels`
dataclass with no prose in it.

The one thing worth knowing: `preview_import` **writes nothing**. It parses the file,
resolves every row against the menu or the takings tables, and reports what a commit
would do. It is not a write-then-rollback: a rollback inside `run_sync`'s transaction
is how the first version of this leaked two rows into the live ledger under
`bot-preview`'s dry run (`ARCHITECTURE.md` 8V), and a preview that cannot write cannot
leak.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.bot.viewmodels import (
    BasketLineView,
    BasketView,
    CashDayView,
    ExportView,
    ImportResultView,
    MenuPageView,
    MenuPickView,
    RecordedSaleView,
)
from cafeops.config import settings
from cafeops.db.models import Sale
from cafeops.db.models.enums import (
    MANUAL_SALE_CHANNELS,
    PaymentSourceKind,
    SaleChannel,
    SalesChannelName,
)
from cafeops.integrations.channels import CsvChannelSource, UnmappableReportError
from cafeops.integrations.payments import CsvPaymentSource, PaymentSourceUnavailable
from cafeops.jobs.channel_sync import sync_channel
from cafeops.services.finance.common import FinanceConflict
from cafeops.services.finance.trading_days import create_day, sales_day, update_day
from cafeops.services.ingest_payments import ingest_payments
from cafeops.services.record_sale import (
    RecordedSale,
    SaleLineIn,
    line_gross_pence,
    list_menu_categories,
    menu_items,
    noon_of,
    record_sale,
    search_menu_items,
    void_sale,
)
from cafeops.services.record_sale import (
    recorded_sale as _recorded_sale,
)
from cafeops.services.transactions_csv import (
    CsvKind,
    classify_csv,
    export_transactions,
    import_transactions,
)

__all__ = [
    "ITEMS_PER_PAGE",
    "basket_view",
    "build_export",
    "cash_day",
    "commit_import",
    "menu_page",
    "menu_search",
    "pick_item",
    "preview_import",
    "set_cash",
    "stash_upload",
    "void_recorded_sale",
    "write_sale",
]

#: Eight rows of buttons plus paging fits a phone screen without scrolling the
#: keyboard off the message it belongs to.
ITEMS_PER_PAGE = 8

_SIZE_LABEL = {"S": "S", "M": "M", "XL": "XL", "ONE": ""}

#: How far back a payments or channel-report upload may reach. A wide window: the
#: file decides which days it covers, and the window only stops a typo'd year.
UPLOAD_WINDOW = timedelta(days=400)


# ------------------------------------------------------------------ menu ---


def _pick(item: Any) -> MenuPickView:
    return MenuPickView(
        menu_item_id=item.menu_item_id, name=item.name, size=item.size, price_pence=item.price_pence
    )


def menu_page(session: Session, *, category_index: int | None, page: int = 0) -> MenuPageView:
    """The categories (no index) or one page of a category's items.

    Categories are addressed by index into a list re-read on every call, because a
    category name does not fit in a callback payload. The list is stable between two
    taps in practice; if it is not, the index lands on a neighbour and the person sees
    a different heading, never a wrong write -- items are picked by id.
    """
    categories = tuple(list_menu_categories(session))
    if category_index is None:
        return MenuPageView(categories=categories, category_index=None, items=(), page=0, pages=0)
    if category_index >= len(categories):
        return MenuPageView(categories=categories, category_index=None, items=(), page=0, pages=0)
    items = [_pick(i) for i in menu_items(session, category=categories[category_index])]
    pages = max(1, (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    start = page * ITEMS_PER_PAGE
    return MenuPageView(
        categories=categories,
        category_index=category_index,
        items=tuple(items[start : start + ITEMS_PER_PAGE]),
        page=page,
        pages=pages,
    )


def menu_search(session: Session, *, q: str) -> tuple[MenuPickView, ...]:
    return tuple(_pick(i) for i in search_menu_items(session, q=q, limit=ITEMS_PER_PAGE))


def pick_item(session: Session, *, menu_item_id: int) -> MenuPickView | None:
    from cafeops.db.models import MenuItem

    row = session.get(MenuItem, menu_item_id)
    if row is None or not row.active:
        return None
    size = _SIZE_LABEL.get(row.size_code.value, "") if row.size_code else ""
    return MenuPickView(menu_item_id=row.id, name=row.name, size=size, price_pence=row.price_pence)


# ---------------------------------------------------------------- basket ---


def _lines_from_state(session: Session, raw_lines: list[dict[str, Any]]) -> list[BasketLineView]:
    """Names and menu prices are re-read; only ids, quantities and typed prices are
    trusted from FSM state."""
    out: list[BasketLineView] = []
    for raw in raw_lines:
        item = pick_item(session, menu_item_id=int(raw["item_id"]))
        if item is None:
            continue
        qty = Decimal(str(raw["qty"]))
        custom = raw.get("price")
        unit = int(custom) if custom is not None else item.price_pence
        out.append(
            BasketLineView(
                menu_item_id=item.menu_item_id,
                name=item.name,
                size=item.size,
                qty=qty,
                unit_price_pence=unit,
                gross_pence=line_gross_pence(unit, qty),
                price_is_custom=custom is not None,
            )
        )
    return out


def basket_view(
    session: Session, *, channel: str, sold_on: str | None, raw_lines: list[dict[str, Any]]
) -> BasketView:
    return BasketView(
        channel=SaleChannel(channel),
        sold_on=date.fromisoformat(sold_on) if sold_on else None,
        lines=tuple(_lines_from_state(session, raw_lines)),
    )


def _recorded_view(sale: RecordedSale) -> RecordedSaleView:
    return RecordedSaleView(
        receipt_id=sale.receipt_id,
        first_sale_id=sale.lines[0].sale_id,
        channel=sale.channel,
        sold_at=sale.sold_at,
        recorded_by=sale.recorded_by,
        voided=sale.voided,
        lines=tuple(
            BasketLineView(
                menu_item_id=line.menu_item_id,
                name=line.name,
                size=line.size,
                qty=line.qty,
                unit_price_pence=line.unit_price_pence,
                gross_pence=line.gross_pence,
                price_is_custom=False,
            )
            for line in sale.lines
        ),
    )


def write_sale(
    session: Session,
    *,
    channel: str,
    sold_on: str | None,
    raw_lines: list[dict[str, Any]],
    recorded_by: str,
) -> RecordedSaleView:
    """`record_sale` with the basket as typed. A dated receipt lands at local noon."""
    chosen = SaleChannel(channel)
    if chosen not in MANUAL_SALE_CHANNELS:  # pragma: no cover - the keyboard offers only these
        raise ValueError(f"channel {chosen.value} cannot be typed by hand")
    lines = [
        SaleLineIn(
            menu_item_id=int(raw["item_id"]),
            qty=Decimal(str(raw["qty"])),
            unit_price_pence=int(raw["price"]) if raw.get("price") is not None else None,
        )
        for raw in raw_lines
    ]
    when: datetime | None = None
    if sold_on:
        day = date.fromisoformat(sold_on)
        today = datetime.now(UTC).astimezone(settings.tz).date()
        when = None if day == today else noon_of(day)
    sale = record_sale(session, channel=chosen, lines=lines, recorded_by=recorded_by, sold_at=when)
    return _recorded_view(sale)


def void_recorded_sale(session: Session, *, sale_id: int, voided_by: str) -> RecordedSaleView:
    """Void by the id of any of the receipt's rows -- an int fits the button payload."""
    receipt_id = session.scalar(select(Sale.lightspeed_receipt_id).where(Sale.id == sale_id))
    if receipt_id is None:
        raise LookupError(f"no sale {sale_id}")
    return _recorded_view(void_sale(session, receipt_id=receipt_id, voided_by=voided_by))


def recorded_view(session: Session, *, receipt_id: str) -> RecordedSaleView:
    return _recorded_view(_recorded_sale(session, receipt_id))


# ------------------------------------------------------------------ cash ---


_EXPORT_SOURCES = (PaymentSourceKind.POS_API, PaymentSourceKind.CSV_UPLOAD)


def cash_day(session: Session, *, day: date) -> CashDayView:
    try:
        row = sales_day(session, day)
    except LookupError:
        return CashDayView(day=day, cash_pence=None, card_pence=None, locked_by=None)
    locked = next(
        (
            s.source
            for s in row.sources
            if s.method in ("CASH", "CASH_OFF_TILL")
            and PaymentSourceKind[s.source] in _EXPORT_SOURCES
        ),
        None,
    )
    return CashDayView(
        day=day, cash_pence=row.cash_pence, card_pence=row.card_pence, locked_by=locked
    )


def set_cash(session: Session, *, day: date, pence: int, operator: str) -> CashDayView:
    """The day's one cash figure (DECISIONS 26). Creates the day if it has no row yet."""
    before = cash_day(session, day=day)
    if before.locked_by is not None:
        raise FinanceConflict(f"cash for {day.isoformat()} comes from {before.locked_by}")
    try:
        sales_day(session, day)
    except LookupError:
        create_day(session, day=day, cash_pence=pence, operator=operator)
    else:
        update_day(session, day, cash_pence=pence, operator=operator)
    return cash_day(session, day=day)


# ----------------------------------------------------------------- files ---


def stash_upload(filename: str, data: bytes) -> Path:
    """Write an upload to its own temp directory. Runs on the event loop; no DB.

    Its own directory, because the payments reader imports *every* CSV in a directory
    and a stray earlier upload beside this one would be imported with it.
    """
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in filename) or "upload.csv"
    if not safe.lower().endswith(".csv"):
        safe += ".csv"
    folder = Path(tempfile.mkdtemp(prefix="cafeops-upload-"))
    path = folder / safe
    path.write_bytes(data)
    return path


def discard_upload(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError:  # pragma: no cover - a temp dir that will not go is not a bot problem
        pass


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def _run_import(
    session: Session, *, path: Path, platform: str | None, recorded_by: str, written: bool
) -> ImportResultView:
    text = _read_text(path)
    found = classify_csv(text)
    name = path.name
    if found.kind is CsvKind.UNKNOWN:
        return ImportResultView(
            kind=found.kind, filename=name, written=False, refused=found.detail or "unknown"
        )

    if found.kind is CsvKind.TRANSACTIONS:
        report = import_transactions(
            session, text=text, filename=name, recorded_by=recorded_by, dry_run=not written
        )
        return ImportResultView(
            kind=found.kind,
            filename=name,
            written=written and report.refused is None,
            refused=report.refused,
            receipts=report.receipts_written,
            lines=report.lines_written,
            gross_pence=report.gross_pence,
            already_recorded=report.receipts_already_recorded,
            by_channel=tuple((SaleChannel(k), v) for k, v in sorted(report.by_channel.items())),
            since=report.since,
            until=report.until,
            rejected=tuple(report.rejected),
        )

    today = datetime.now(UTC).astimezone(settings.tz).date()
    since, until = today - UPLOAD_WINDOW, today
    if found.kind is CsvKind.PAYMENTS:
        source = CsvPaymentSource(path.parent)
        try:
            fetched = source.fetch(since=since, until=until)
        except PaymentSourceUnavailable as exc:
            return ImportResultView(kind=found.kind, filename=name, written=False, refused=str(exc))
        if fetched.unmapped:
            return ImportResultView(
                kind=found.kind, filename=name, written=False, refused="; ".join(fetched.unmapped)
            )
        if written:
            result = ingest_payments(session, fetched)
            inserted, updated = result.inserted, result.updated
            rejected, notes = tuple(result.rejected), tuple(result.notes)
        else:
            inserted, updated = _payment_days_split(session, fetched.rows)
            rejected, notes = tuple(fetched.rejected), tuple(fetched.notes)
        return ImportResultView(
            kind=found.kind,
            filename=name,
            written=written,
            days_inserted=inserted,
            days_updated=updated,
            gross_pence=fetched.gross_pence,
            since=min((r.business_date for r in fetched.rows), default=None),
            until=max((r.business_date for r in fetched.rows), default=None),
            rejected=rejected,
            notes=notes,
        )

    chosen = platform or found.platform
    if chosen is None:
        return ImportResultView(
            kind=found.kind, filename=name, written=False, platform=None, refused=found.detail
        )
    channel = SalesChannelName(chosen)
    source_csv = CsvChannelSource(files=[path], platform=channel)
    try:
        if not written:
            parsed = source_csv.fetch(channel=channel, since=since, until=until)
            days = {r.metric_date for r in parsed.day_rows}
            return ImportResultView(
                kind=found.kind,
                filename=name,
                written=False,
                platform=chosen,
                days_inserted=len(parsed.day_rows),
                items_inserted=len(parsed.item_rows),
                rejected=tuple(f"row {r.line_no}: {r.reason}" for r in parsed.rejected),
                notes=tuple(parsed.warnings),
                since=min(days, default=None),
                until=max(days, default=None),
            )
        synced = sync_channel(
            session, channel=channel, since=since, until=until, primary=source_csv
        )
    except UnmappableReportError as exc:
        return ImportResultView(
            kind=found.kind, filename=name, written=False, platform=chosen, refused=str(exc)
        )
    return ImportResultView(
        kind=found.kind,
        filename=name,
        written=written,
        platform=chosen,
        days_inserted=synced.days_inserted,
        days_updated=synced.days_updated,
        items_inserted=synced.items_inserted,
        items_updated=synced.items_updated,
        rejected=tuple(str(u) for u in synced.unresolved),
        notes=tuple(synced.warnings),
        since=synced.since,
        until=synced.until,
    )


def preview_import(
    session: Session, *, path: Path, platform: str | None, recorded_by: str
) -> ImportResultView:
    """The dry run: what a commit would do, computed without writing. See module note."""
    return _run_import(
        session, path=path, platform=platform, recorded_by=recorded_by, written=False
    )


def commit_import(
    session: Session, *, path: Path, platform: str | None, recorded_by: str
) -> ImportResultView:
    view = _run_import(session, path=path, platform=platform, recorded_by=recorded_by, written=True)
    if view.refused is not None:
        session.rollback()
    return view


def _payment_days_split(session: Session, rows: Any) -> tuple[int, int]:
    """How many of these (day, method) rows the CSV source already has: the split
    `ingest_payments` would report, read rather than written."""
    from cafeops.db.models.payment import PaymentDay

    inserted = updated = 0
    for row in rows:
        exists = session.scalar(
            select(PaymentDay.id).where(
                PaymentDay.business_date == row.business_date,
                PaymentDay.method == row.method,
                PaymentDay.source == PaymentSourceKind.CSV_UPLOAD,
            )
        )
        if exists is None:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


def build_export(session: Session, *, since: date, until: date) -> ExportView:
    out = export_transactions(session, since=since, until=until)
    return ExportView(
        filename=out.filename,
        data=out.text.encode("utf-8"),
        since=out.since,
        until=out.until,
        receipts=out.receipts,
        lines=out.lines,
        gross_pence=out.gross_pence,
        voided_lines=out.voided_lines,
    )
