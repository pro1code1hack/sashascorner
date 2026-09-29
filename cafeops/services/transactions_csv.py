"""Transactions as a file: export what was recorded, import what was typed elsewhere.

DECISIONS 28. The Telegram bot's `/export` and `/import`, the API's
`GET /api/finance/transactions/export.csv` and `POST .../transactions/import`, and the
`cafeops transactions` commands all come here. One shape in both directions, so a file
the system wrote can be edited in a spreadsheet and sent back.

Three kinds of CSV reach the bot and `classify_csv` tells them apart by header, never
by filename or by guessing:

* **transactions** -- this module's own shape (a `date`, an `item`, a `qty`);
* **payments** -- a daily-takings export (`date`, `method`, `gross`), handed to
  `services/ingest_payments`;
* **channel report** -- a Deliveroo / Just Eat portal export, handed to the channel
  sync. Which platform it is comes from the columns (`detect_schema`), and when they
  fit both the caller must say.

Anything else is refused with the headers it saw.

Importing is idempotent per file: a receipt's id is derived from the file's content
and the row's group, so sending the same file twice writes nothing the second time and
says so. A row whose item cannot be matched to exactly one active menu item is
rejected by name and the rest of the file still goes in -- an unmatched item silently
becoming "Other" would be a sale of nothing at a real price.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import MenuItem, Sale
from cafeops.db.models.enums import MANUAL_SALE_CHANNELS, SaleChannel, SaleSource, SizeCode
from cafeops.integrations.channels.base import UnmappableReportError
from cafeops.integrations.channels.columns import (
    FieldParseError,
    detect_schema,
    normalise_header,
    parse_date,
    parse_money_pence,
)
from cafeops.integrations.payments.csv_source import ALIASES as PAYMENT_ALIASES
from cafeops.integrations.payments.csv_source import REQUIRED as PAYMENT_REQUIRED
from cafeops.services.record_sale import (
    SaleLineIn,
    SaleRefused,
    line_gross_pence,
    record_sale,
)

__all__ = [
    "EXPORT_COLUMNS",
    "CsvClassification",
    "CsvKind",
    "ExportFile",
    "TransactionsImportReport",
    "classify_csv",
    "export_transactions",
    "import_transactions",
]

EXPORT_COLUMNS: tuple[str, ...] = (
    "receipt",
    "date",
    "time",
    "channel",
    "source",
    "item",
    "size",
    "qty",
    "unit_price",
    "gross",
    "voided",
    "refund",
    "recorded_by",
    "note",
)

#: Import header aliases. Normalised at import like the payments mapper's, so
#: "Unit price" and "unit_price" and "Цена" all land on the same field.
_RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "receipt": ("receipt", "receipt id", "order", "order id", "чек"),
    "date": ("date", "day", "sold on", "дата"),
    "time": ("time", "sold at", "время"),
    "channel": ("channel", "source channel", "канал"),
    "item": ("item", "product", "name", "menu item", "товар", "позиция"),
    "size": ("size", "размер"),
    "qty": ("qty", "quantity", "count", "кол-во", "количество"),
    "unit_price": ("unit price", "price", "unit_price_pence", "цена"),
    "gross": ("gross", "total", "amount", "line total", "сумма"),
    "note": ("note", "notes", "comment", "примечание"),
}
_ALIASES = {f: frozenset(normalise_header(a) for a in names) for f, names in _RAW_ALIASES.items()}
_REQUIRED = ("date", "item")

_CHANNEL_WORDS: dict[str, SaleChannel] = {
    "cash": SaleChannel.CASH,
    "наличные": SaleChannel.CASH,
    "нал": SaleChannel.CASH,
    "deliveroo": SaleChannel.DELIVEROO,
    "just_eat": SaleChannel.JUST_EAT,
    "justeat": SaleChannel.JUST_EAT,
    "just eat": SaleChannel.JUST_EAT,
    "other": SaleChannel.OTHER,
    "другое": SaleChannel.OTHER,
    "epos": SaleChannel.EPOS,
    "till": SaleChannel.EPOS,
}

_SIZE_WORDS: dict[str, SizeCode | None] = {
    "": None,
    "s": SizeCode.S,
    "small": SizeCode.S,
    "m": SizeCode.M,
    "medium": SizeCode.M,
    "xl": SizeCode.XL,
    "l": SizeCode.XL,
    "large": SizeCode.XL,
    "one": SizeCode.ONE,
    "one size": SizeCode.ONE,
}
_SIZE_LABEL = {SizeCode.S: "S", SizeCode.M: "M", SizeCode.XL: "XL", SizeCode.ONE: ""}


class CsvKind(StrEnum):
    TRANSACTIONS = "TRANSACTIONS"
    PAYMENTS = "PAYMENTS"
    CHANNEL_REPORT = "CHANNEL_REPORT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CsvClassification:
    kind: CsvKind
    headers: tuple[str, ...]
    delimiter: str
    #: For a channel report: the platform the columns identify, or None when they fit
    #: more than one and the person has to say.
    platform: str | None = None
    #: Why it is UNKNOWN (or why the platform is undecided), for the reply.
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ExportFile:
    filename: str
    text: str
    since: date
    until: date
    receipts: int
    lines: int
    gross_pence: int
    voided_lines: int


@dataclass
class TransactionsImportReport:
    filename: str
    receipts_written: int = 0
    lines_written: int = 0
    receipts_already_recorded: int = 0
    gross_pence: int = 0
    since: date | None = None
    until: date | None = None
    by_channel: dict[str, int] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)
    #: True when nothing was written because the file, as a whole, was refused.
    refused: str | None = None

    def summary(self) -> str:
        if self.refused:
            return f"{self.filename}: REFUSED -- {self.refused}"
        return (
            f"{self.filename}: {self.receipts_written} receipt(s), {self.lines_written} "
            f"line(s), GBP {self.gross_pence / 100:,.2f}; "
            f"{self.receipts_already_recorded} already recorded, "
            f"{len(self.rejected)} row(s) rejected"
        )


# ---------------------------------------------------------------- export ---


def export_transactions(
    session: Session,
    *,
    since: date,
    until: date,
    channel: SaleChannel | None = None,
    source: SaleSource | None = None,
    include_voided: bool = True,
) -> ExportFile:
    """Every sale line in the window, one row each, newest first. Money as GBP text."""
    if since > until:
        raise ValueError("since is after until")
    tz = settings.tz
    stmt = (
        select(Sale, MenuItem)
        .join(MenuItem, MenuItem.id == Sale.menu_item_id)
        .where(Sale.sold_at >= datetime.combine(since, time.min, tzinfo=tz))
        .where(Sale.sold_at < datetime.combine(until + timedelta(days=1), time.min, tzinfo=tz))
        .order_by(Sale.sold_at.desc(), Sale.lightspeed_receipt_id, Sale.id)
    )
    if channel is not None:
        stmt = stmt.where(Sale.channel == channel)
    if source is not None:
        stmt = stmt.where(Sale.source == source)
    if not include_voided:
        stmt = stmt.where(Sale.voided.is_(False))

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    receipts: set[str] = set()
    lines = gross = voided = 0
    for sale, item in session.execute(stmt):
        local = sale.sold_at.astimezone(tz)
        unit = _unit_price(sale)
        writer.writerow(
            [
                sale.lightspeed_receipt_id,
                local.date().isoformat(),
                local.strftime("%H:%M"),
                sale.channel.value,
                sale.source.value,
                item.name,
                _SIZE_LABEL.get(item.size_code, "") if item.size_code else "",
                _qty_text(sale.qty),
                _gbp(unit),
                _gbp(sale.gross_pence),
                "yes" if sale.voided else "",
                "yes" if sale.is_refund else "",
                sale.recorded_by or "",
                sale.note or "",
            ]
        )
        receipts.add(sale.lightspeed_receipt_id)
        lines += 1
        if sale.voided:
            voided += 1
        else:
            gross += sale.gross_pence
    return ExportFile(
        filename=f"transactions_{since.isoformat()}_{until.isoformat()}.csv",
        text=buffer.getvalue(),
        since=since,
        until=until,
        receipts=len(receipts),
        lines=lines,
        gross_pence=gross,
        voided_lines=voided,
    )


def _gbp(pence: int) -> str:
    return f"{Decimal(pence) / 100:.2f}"


def _qty_text(q: Decimal) -> str:
    n = q.normalize()
    return format(n, "f") if n != n.to_integral_value() else str(int(n))


def _unit_price(sale: Sale) -> int:
    if sale.qty == 0:
        return 0
    return int((Decimal(sale.gross_pence) / sale.qty).quantize(Decimal(1)))


# -------------------------------------------------------------- classify ---


def _sniff_delimiter(first_line: str) -> str:
    best = max((",", ";", "\t", "|"), key=lambda d: first_line.count(d))
    return best if first_line.count(best) > 0 else ","


def _headers(text: str) -> tuple[tuple[str, ...], str]:
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = _sniff_delimiter(first)
    reader = csv.reader(io.StringIO(first), delimiter=delimiter)
    headers = tuple(h.strip() for h in next(reader, []))
    return headers, delimiter


def _map(headers: Sequence[str], aliases: dict[str, frozenset[str]]) -> dict[str, int]:
    norm = [normalise_header(h) for h in headers]
    out: dict[str, int] = {}
    for field_name, names in aliases.items():
        for i, h in enumerate(norm):
            if h in names:
                out[field_name] = i
                break
    return out


def classify_csv(text: str) -> CsvClassification:
    """Which importer this file belongs to, decided by its header row alone."""
    headers, delimiter = _headers(text)
    if not headers:
        return CsvClassification(CsvKind.UNKNOWN, headers, delimiter, detail="the file is empty")

    ours = _map(headers, _ALIASES)
    if all(f in ours for f in _REQUIRED) and ("qty" in ours or "gross" in ours or "size" in ours):
        return CsvClassification(CsvKind.TRANSACTIONS, headers, delimiter)

    payments = _map(headers, PAYMENT_ALIASES)
    if all(f in payments for f in PAYMENT_REQUIRED):
        return CsvClassification(CsvKind.PAYMENTS, headers, delimiter)

    try:
        schema = detect_schema(headers)
    except UnmappableReportError as exc:
        if exc.candidates and "more than one" in str(exc):
            return CsvClassification(
                CsvKind.CHANNEL_REPORT, headers, delimiter, platform=None, detail=str(exc)
            )
        return CsvClassification(
            CsvKind.UNKNOWN,
            headers,
            delimiter,
            detail=(
                "columns match neither a transactions file (date, item, qty), a takings "
                "export (date, method, gross) nor a Deliveroo / Just Eat report"
            ),
        )
    return CsvClassification(
        CsvKind.CHANNEL_REPORT, headers, delimiter, platform=schema.platform.value
    )


# ---------------------------------------------------------------- import ---


def import_transactions(
    session: Session,
    *,
    text: str,
    filename: str,
    recorded_by: str,
    now: datetime | None = None,
    dry_run: bool = False,
) -> TransactionsImportReport:
    """Write the file's rows as hand-typed sales (source CSV_UPLOAD). Does not commit.

    `dry_run=True` does every check and every sum and writes nothing -- the report is
    what a commit would say. It is a separate path rather than a write-then-rollback
    on purpose: a rollback inside a caller's transaction is exactly how the bot's
    preview leaked two rows into the live ledger (`ARCHITECTURE.md` 8V).

    Rows sharing a `receipt` value become one receipt; a row with none is its own.
    Receipt ids are `csv:<content hash>:<group>`, so the same file sent twice adds
    nothing and the report says how many it already had. `channel` is per row and is
    required unless the file has one column of it filled throughout; EPOS is refused
    like everywhere else.
    """
    now = now or datetime.now(UTC)
    report = TransactionsImportReport(filename=filename)
    headers, delimiter = _headers(text)
    mapping = _map(headers, _ALIASES)
    missing = [f for f in _REQUIRED if f not in mapping]
    if not headers or missing:
        report.refused = (
            f"missing column(s) {', '.join(missing) or 'header'}; saw {', '.join(headers)}. "
            "Columns are matched by name: date, item, and then qty, size, unit price, "
            "gross, channel, receipt, time, note."
        )
        return report
    if "channel" not in mapping:
        report.refused = "no channel column (CASH, DELIVEROO, JUST_EAT or OTHER per row)"
        return report

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    next(reader, None)
    items = _active_items(session)

    groups: dict[str, list[SaleLineIn]] = {}
    meta: dict[str, tuple[SaleChannel, datetime, str | None]] = {}
    menu_price: dict[int, int] = {}
    order: list[str] = []
    for number, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(cells) < len(headers):
            cells = [*cells, *[""] * (len(headers) - len(cells))]
        cell = _cell_reader(cells, mapping)
        try:
            when = _row_when(cell("date"), cell("time"))
            channel = _row_channel(cell("channel"))
            item = _row_item(items, cell("item"), cell("size"))
            qty = _row_qty(cell("qty"))
            price = _row_price(cell("unit_price"), cell("gross"), qty)
        except ValueError as exc:
            report.rejected.append(f"row {number}: {exc}")
            continue
        raw_receipt = cell("receipt").strip()
        key = raw_receipt or f"row{number}"
        receipt_id = f"csv:{digest}:{_slug(key)}"
        if receipt_id not in groups:
            groups[receipt_id] = []
            meta[receipt_id] = (channel, when, cell("note").strip() or None)
            order.append(receipt_id)
        elif meta[receipt_id][0] is not channel:
            report.rejected.append(
                f"row {number}: receipt {raw_receipt!r} mixes channels "
                f"({meta[receipt_id][0].value} and {channel.value})"
            )
            continue
        groups[receipt_id].append(SaleLineIn(menu_item_id=item.id, qty=qty, unit_price_pence=price))
        menu_price[item.id] = item.price_pence

    for receipt_id in order:
        lines = groups[receipt_id]
        if not lines:
            continue
        channel, when, note = meta[receipt_id]
        exists = session.scalar(select(Sale.id).where(Sale.lightspeed_receipt_id == receipt_id))
        if exists is not None:
            report.receipts_already_recorded += 1
            continue
        if dry_run:
            total = sum(
                line_gross_pence(
                    line.unit_price_pence
                    if line.unit_price_pence is not None
                    else menu_price[line.menu_item_id],
                    line.qty,
                )
                for line in lines
            )
            count = len(lines)
        else:
            try:
                written = record_sale(
                    session,
                    channel=channel,
                    lines=lines,
                    recorded_by=recorded_by,
                    sold_at=when,
                    note=note,
                    source=SaleSource.CSV_UPLOAD,
                    now=now,
                    receipt_id=receipt_id,
                    max_backdate=None,
                )
            except SaleRefused as exc:
                report.rejected.append(f"receipt {receipt_id}: {exc}")
                continue
            total, count = written.total_pence, len(written.lines)
        report.receipts_written += 1
        report.lines_written += count
        report.gross_pence += total
        report.by_channel[channel.value] = report.by_channel.get(channel.value, 0) + total
        day = when.astimezone(settings.tz).date()
        report.since = day if report.since is None or day < report.since else report.since
        report.until = day if report.until is None or day > report.until else report.until
    return report


def _cell_reader(cells: Sequence[str], mapping: dict[str, int]) -> Callable[[str], str]:
    def read(field_name: str) -> str:
        index = mapping.get(field_name)
        return cells[index] if index is not None and index < len(cells) else ""

    return read


def _slug(raw: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in raw.strip())[:40] or "row"


def _row_when(raw_date: str, raw_time: str) -> datetime:
    try:
        day = parse_date(raw_date.strip())
    except FieldParseError as exc:
        raise ValueError(f"date: {exc}") from exc
    text = raw_time.strip()
    if text:
        try:
            parts = [int(p) for p in text.replace(".", ":").split(":")[:2]]
            clock = time(parts[0], parts[1] if len(parts) > 1 else 0)
        except (ValueError, IndexError) as exc:
            raise ValueError(f"time {text!r}: expected HH:MM") from exc
    else:
        clock = time(12, 0)
    return datetime.combine(day, clock, tzinfo=settings.tz).astimezone(UTC)


def _row_channel(raw: str) -> SaleChannel:
    key = raw.strip().lower().replace("-", " ")
    channel = _CHANNEL_WORDS.get(key) or _CHANNEL_WORDS.get(key.replace(" ", "_"))
    if channel is None:
        raise ValueError(
            f"channel {raw!r}: one of {', '.join(c.value for c in MANUAL_SALE_CHANNELS)}"
        )
    if channel not in MANUAL_SALE_CHANNELS:
        raise ValueError(
            "channel EPOS is the till and comes from the Lightspeed sync; a typed till "
            "line would be counted twice"
        )
    return channel


def _active_items(session: Session) -> list[MenuItem]:
    return list(session.scalars(select(MenuItem).where(MenuItem.active.is_(True))))


def _row_item(items: Sequence[MenuItem], raw_name: str, raw_size: str) -> MenuItem:
    name = raw_name.strip().lower()
    if not name:
        raise ValueError("item: empty")
    size_key = raw_size.strip().lower()
    if size_key not in _SIZE_WORDS:
        raise ValueError(f"size {raw_size!r}: S, M, XL or blank")
    size = _SIZE_WORDS[size_key]
    by_name = [i for i in items if i.name.lower() == name]
    if not by_name:
        raise ValueError(f"item {raw_name!r}: no active menu item with that exact name")
    if size is not None:
        sized = [i for i in by_name if i.size_code is size]
        if len(sized) != 1:
            raise ValueError(f"item {raw_name!r} has no size {raw_size.strip()}")
        return sized[0]
    if len(by_name) == 1:
        return by_name[0]
    one = [i for i in by_name if i.size_code in (None, SizeCode.ONE)]
    if len(one) == 1:
        return one[0]
    sizes = ", ".join(_SIZE_LABEL.get(i.size_code, "?") or "one" for i in by_name if i.size_code)
    raise ValueError(f"item {raw_name!r} comes in sizes {sizes}; the size column is needed")


def _row_qty(raw: str) -> Decimal:
    text = raw.strip().replace(",", ".")
    if not text:
        return Decimal(1)
    try:
        qty = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"qty {raw!r}: not a number") from exc
    if qty <= 0 or not qty.is_finite():
        raise ValueError(f"qty {raw!r}: must be more than zero")
    return qty


def _row_price(raw_unit: str, raw_gross: str, qty: Decimal) -> int | None:
    """Unit price in pence: the unit column, else gross / qty, else the menu price."""
    unit = raw_unit.strip()
    if unit:
        pence = _money(unit, "unit price")
        if pence is not None:
            return pence
    gross = raw_gross.strip()
    if gross:
        pence = _money(gross, "gross")
        if pence is not None:
            # Chosen so that the line's gross, recomputed as price x qty, lands back on
            # the file's figure to the penny for whole quantities.
            unit_pence = int((Decimal(pence) / qty).quantize(Decimal(1)))
            if line_gross_pence(unit_pence, qty) != pence and qty == qty.to_integral_value():
                raise ValueError(
                    f"gross {gross} does not divide by qty {_qty_text(qty)} to a whole penny"
                )
            return unit_pence
    return None


def _money(raw: str, label: str) -> int | None:
    try:
        return parse_money_pence(raw)
    except FieldParseError as exc:
        raise ValueError(f"{label} {raw!r}: {exc}") from exc
