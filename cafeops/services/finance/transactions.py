"""Money -> Transactions: what the back office actually recorded, read-only.

Two ledgers, never mixed into one sum:

- **Receipts** -- receipt lines in `sale`, grouped by receipt: the till's (source
  POS_API) and, since DECISIONS 28, the hand-typed ones (MANUAL, CSV_UPLOAD) and the
  loyalty redemptions, each labelled with its `source` and who recorded it. Voided
  receipts are kept and labelled.
- **Takings** -- `payment_day` rows: one day, one method, one source. Several sources
  may report the same (day, method); only the winner by `PAYMENT_SOURCE_PRECEDENCE`
  counts (`used`), the rest are shown so a disagreement is visible, never added.

Nothing here writes. Money is integer pence; a missing deduction is `None`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models.enums import PaymentMethod, PaymentSourceKind, SaleChannel, SaleSource
from cafeops.db.models.menu import MenuItem
from cafeops.db.models.payment import PaymentDay
from cafeops.db.models.sale import Sale
from cafeops.services.finance.common import FinanceRefused
from cafeops.services.finance.takings import rank

__all__ = [
    "ReceiptPage",
    "ReceiptRow",
    "TakingsPage",
    "TakingsRow",
    "list_receipts",
    "list_takings",
]

MAX_PAGE_SIZE = 200
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True, slots=True)
class ReceiptRow:
    receipt_id: str
    date: date
    weekday: str
    time: str
    channel: str
    source: str
    recorded_by: str | None
    lines: int
    items: str
    summary: str
    gross_pence: int
    voided: bool
    refund: bool


@dataclass(frozen=True, slots=True)
class ReceiptPage:
    rows: list[ReceiptRow]
    page: int
    page_size: int
    total_rows: int
    gross_pence: int
    voided_count: int
    first_date: date | None
    last_date: date | None
    caveats: list[str]


@dataclass(frozen=True, slots=True)
class TakingsRow:
    id: int
    date: date
    weekday: str
    method: str
    source: str
    basis: str
    gross_pence: int
    refunds_pence: int | None
    fees_pence: int | None
    discounts_pence: int | None
    net_pence: int | None
    transactions: int | None
    used: bool
    source_ref: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class TakingsPage:
    rows: list[TakingsRow]
    page: int
    page_size: int
    total_rows: int
    used_gross_pence: int
    by_method_pence: dict[str, int]
    shadowed_count: int
    caveats: list[str]


def _check_page(page: int, page_size: int) -> None:
    if page < 1:
        raise FinanceRefused("page: 1 or more")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise FinanceRefused(f"page_size: 1 to {MAX_PAGE_SIZE}")


def _check_range(since: date | None, until: date | None) -> None:
    if since is not None and until is not None and since > until:
        raise FinanceRefused("from: must not be after to")


def _qty_text(q: Decimal) -> str:
    n = q.normalize()
    return format(n, "f") if n != n.to_integral_value() else str(int(n))


def list_receipts(
    session: Session,
    *,
    since: date | None,
    until: date | None,
    channel: str | None = None,
    source: str | None = None,
    q: str | None = None,
    min_pence: int | None = None,
    max_pence: int | None = None,
    include_voided: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> ReceiptPage:
    _check_page(page, page_size)
    _check_range(since, until)
    tz = settings.tz
    stmt = select(Sale, MenuItem.name).join(MenuItem, MenuItem.id == Sale.menu_item_id)
    if source:
        try:
            stmt = stmt.where(Sale.source == SaleSource[source.upper()])
        except KeyError as exc:
            raise FinanceRefused("source: POS_API, MANUAL, CSV_UPLOAD or LOYALTY") from exc
    if since is not None:
        stmt = stmt.where(Sale.sold_at >= datetime.combine(since, time.min, tzinfo=tz))
    if until is not None:
        stmt = stmt.where(
            Sale.sold_at < datetime.combine(until + timedelta(days=1), time.min, tzinfo=tz)
        )
    if channel:
        try:
            stmt = stmt.where(Sale.channel == SaleChannel[channel.upper()])
        except KeyError as exc:
            raise FinanceRefused("channel: EPOS, CASH, DELIVEROO, JUST_EAT, WEB or OTHER") from exc

    grouped: dict[str, list[tuple[Sale, str]]] = {}
    for sale, name in session.execute(stmt):
        grouped.setdefault(sale.lightspeed_receipt_id, []).append((sale, name))

    needle = (q or "").strip().lower()
    rows: list[ReceiptRow] = []
    for rid, lines in grouped.items():
        first = min(s.sold_at for s, _ in lines).astimezone(tz)
        voided = all(s.voided for s, _ in lines)
        if voided and not include_voided:
            continue
        gross = sum(s.gross_pence for s, _ in lines if not s.voided)
        if min_pence is not None and gross < min_pence:
            continue
        if max_pence is not None and gross > max_pence:
            continue
        qty: dict[str, Decimal] = {}
        for s, name in lines:
            if not s.voided:
                qty[name] = qty.get(name, Decimal(0)) + s.qty
        if needle and needle not in rid.lower() and not any(needle in n.lower() for n in qty):
            continue
        parts = [n if v == 1 else f"{_qty_text(v)} x {n}" for n, v in qty.items()]
        total_items = sum(qty.values(), Decimal(0))
        rows.append(
            ReceiptRow(
                receipt_id=rid,
                date=first.date(),
                weekday=_WEEKDAYS[first.weekday()],
                time=first.strftime("%H:%M"),
                channel=lines[0][0].channel.value,
                source=lines[0][0].source.value,
                recorded_by=lines[0][0].recorded_by,
                lines=len(lines),
                items=_qty_text(total_items),
                summary=", ".join(parts) if parts else "(voided)",
                gross_pence=gross,
                voided=voided,
                refund=any(s.is_refund for s, _ in lines),
            )
        )
    rows.sort(key=lambda r: (r.date, r.time, r.receipt_id), reverse=True)
    start = (page - 1) * page_size
    caveats: list[str] = []
    if not grouped:
        caveats.append("No receipts in this window.")
    typed = sum(1 for r in rows if r.source != SaleSource.POS_API.value)
    if typed and not source:
        caveats.append(
            f"{typed} receipt{'s' if typed != 1 else ''} here {'were' if typed != 1 else 'was'} "
            "recorded by hand or from a file, not by the till. Filter by source to separate them."
        )
    return ReceiptPage(
        rows=rows[start : start + page_size],
        page=page,
        page_size=page_size,
        total_rows=len(rows),
        gross_pence=sum(r.gross_pence for r in rows),
        voided_count=sum(1 for r in rows if r.voided),
        first_date=min((r.date for r in rows), default=None),
        last_date=max((r.date for r in rows), default=None),
        caveats=caveats,
    )


def list_takings(
    session: Session,
    *,
    since: date | None,
    until: date | None,
    method: str | None = None,
    source: str | None = None,
    used_only: bool = False,
    min_pence: int | None = None,
    max_pence: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> TakingsPage:
    _check_page(page, page_size)
    _check_range(since, until)
    stmt = select(PaymentDay)
    if since is not None:
        stmt = stmt.where(PaymentDay.business_date >= since)
    if until is not None:
        stmt = stmt.where(PaymentDay.business_date <= until)
    every = list(session.scalars(stmt))

    # The winner per (day, method) is decided over ALL sources, before any filter,
    # so filtering by source never promotes a shadowed row to "used".
    winners: dict[tuple[date, PaymentMethod], PaymentDay] = {}
    for r in every:
        key = (r.business_date, r.method)
        cur = winners.get(key)
        if cur is None or rank(r.source) < rank(cur.source):
            winners[key] = r
    used_ids = {r.id for r in winners.values()}

    try:
        m = PaymentMethod[method.upper()] if method else None
        s = PaymentSourceKind[source.upper()] if source else None
    except KeyError as exc:
        raise FinanceRefused("method or source: unknown value") from exc

    rows: list[TakingsRow] = []
    for r in every:
        if m is not None and r.method is not m:
            continue
        if s is not None and r.source is not s:
            continue
        used = r.id in used_ids
        if used_only and not used:
            continue
        if min_pence is not None and r.gross_pence < min_pence:
            continue
        if max_pence is not None and r.gross_pence > max_pence:
            continue
        rows.append(
            TakingsRow(
                id=r.id,
                date=r.business_date,
                weekday=_WEEKDAYS[r.business_date.weekday()],
                method=r.method.value,
                source=r.source.value,
                basis=r.basis.value,
                gross_pence=r.gross_pence,
                refunds_pence=r.refunds_pence,
                fees_pence=r.fees_pence,
                discounts_pence=r.discounts_pence,
                net_pence=r.net_pence,
                transactions=r.transactions,
                used=used,
                source_ref=r.source_ref,
                notes=r.notes,
            )
        )
    rows.sort(key=lambda t: (t.method, not t.used, t.source))
    rows.sort(key=lambda t: t.date, reverse=True)
    by_method: dict[str, int] = {}
    for t in rows:
        if t.used:
            by_method[t.method] = by_method.get(t.method, 0) + t.gross_pence
    shadowed = sum(1 for t in rows if not t.used)
    caveats: list[str] = []
    if shadowed:
        caveats.append(
            f"{shadowed} row{'s' if shadowed != 1 else ''} lost to a higher-precedence source "
            "for the same day and method. They are shown, not added."
        )
    start = (page - 1) * page_size
    return TakingsPage(
        rows=rows[start : start + page_size],
        page=page,
        page_size=page_size,
        total_rows=len(rows),
        used_gross_pence=sum(by_method.values()),
        by_method_pence=by_method,
        shadowed_count=shadowed,
        caveats=caveats,
    )
