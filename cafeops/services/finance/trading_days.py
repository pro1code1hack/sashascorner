"""Sales tab: one row per trading day. Finance spec 1.2, 2.1, 3.5.

A "day" is the union of `trading_day` (note, orders override) and `payment_day` (card,
till cash, own cash) dates. Money is read through `takings.resolve_takings`, so a day
with a CSV row and a MANUAL row for the same method shows ONE figure.

Writes are MANUAL rows. A typed figure never overwrites an export: the CSV/POS row
still wins by precedence, and the Sales tab shows that input read-only ("from export").
A typed figure over a LEGACY_WORKBOOK row is a new MANUAL row that wins; the workbook
row stays as the record of what the workbook said.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models.enums import (
    FinanceSource,
    PaymentBasis,
    PaymentMethod,
    PaymentSourceKind,
)
from cafeops.db.models.finance import CardPayout, CashCount, TradingDay
from cafeops.db.models.payment import PaymentDay
from cafeops.db.models.sale import Sale
from cafeops.services.finance.common import (
    UNSET,
    FinanceConflict,
    FinanceRefused,
    Period,
    clean_text,
    month_range,
    now_utc,
    require_pence,
)
from cafeops.services.finance.common import Unset as _Unset
from cafeops.services.finance.takings import ResolvedDay, ResolvedFigure, resolve_takings

__all__ = [
    "SALES_METHODS",
    "UNSET",
    "SalesDayRow",
    "SalesSource",
    "SalesTotals",
    "create_day",
    "delete_day",
    "read_sales",
    "sales_day",
    "update_day",
]

#: The three money columns on the Sales tab, in order.
SALES_METHODS: tuple[PaymentMethod, ...] = (
    PaymentMethod.CARD,
    PaymentMethod.CASH,
    PaymentMethod.CASH_OFF_TILL,
)

#: A typed figure may replace these; an export (CSV_UPLOAD, POS_API) is never edited here.
_USER_EDITABLE = frozenset({PaymentSourceKind.MANUAL, PaymentSourceKind.LEGACY_WORKBOOK})

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True, slots=True)
class SalesSource:
    method: str
    source: str
    source_ref: str | None


@dataclass(frozen=True, slots=True)
class SalesEditable:
    card: bool
    cash_till: bool
    cash_off_till: bool


@dataclass(frozen=True, slots=True)
class SalesDayRow:
    date: date
    weekday: str
    card_pence: int | None
    cash_till_pence: int | None
    cash_off_till_pence: int | None
    total_pence: int
    orders: int | None
    orders_source: str | None
    avg_ticket_pence: int | None
    note: str | None
    basis: str
    editable: SalesEditable
    sources: tuple[SalesSource, ...]


@dataclass(frozen=True, slots=True)
class SalesTotals:
    days: int
    card_pence: int
    cash_till_pence: int
    cash_off_till_pence: int
    total_pence: int
    orders: int | None


def _window(period: Period) -> tuple[date | None, date | None]:
    if period is None:
        return None, None
    return month_range(period)


def _pos_orders(session: Session, since: date | None, until: date | None) -> dict[date, int]:
    """COUNT(DISTINCT receipt) of non-voided sales per local day."""
    tz = settings.tz
    stmt = select(Sale.sold_at, Sale.lightspeed_receipt_id).where(Sale.voided.is_(False))
    if since is not None:
        stmt = stmt.where(Sale.sold_at >= datetime.combine(since, time.min, tzinfo=tz))
    if until is not None:
        stmt = stmt.where(
            Sale.sold_at < datetime.combine(until + timedelta(days=1), time.min, tzinfo=tz)
        )
    receipts: dict[date, set[str]] = {}
    for sold_at, receipt in session.execute(stmt):
        receipts.setdefault(sold_at.astimezone(tz).date(), set()).add(receipt)
    return {d: len(r) for d, r in receipts.items()}


def _row(
    day: date,
    resolved: ResolvedDay | None,
    trading: TradingDay | None,
    pos_orders: int | None,
) -> SalesDayRow:
    by = resolved.by_method if resolved is not None else {}
    card = by.get(PaymentMethod.CARD)
    cash = by.get(PaymentMethod.CASH)
    off = by.get(PaymentMethod.CASH_OFF_TILL)
    total = sum(f.gross_pence for f in (card, cash, off) if f is not None)

    orders: int | None
    orders_source: str | None
    if trading is not None and trading.transactions_override is not None:
        orders, orders_source = trading.transactions_override, "override"
    elif pos_orders:
        orders, orders_source = pos_orders, "pos"
    else:
        exported = [
            f.row.transactions for f in (card, cash, off) if f is not None and f.row.transactions
        ]
        if exported:
            orders, orders_source = sum(t for t in exported if t is not None), "payment_export"
        else:
            orders, orders_source = None, None
    avg = None if not orders else (total + orders // 2) // orders

    bases = {f.basis for f in (card, cash, off) if f is not None}
    basis = (
        "MIXED"
        if len(bases) > 1
        else (next(iter(bases)).value if bases else PaymentBasis.TILL.value)
    )
    sources = tuple(
        SalesSource(method=m.value, source=f.source.value, source_ref=f.row.source_ref)
        for m, f in (
            (PaymentMethod.CARD, card),
            (PaymentMethod.CASH, cash),
            (PaymentMethod.CASH_OFF_TILL, off),
        )
        if f is not None
    )

    def editable(fig: ResolvedFigure | None) -> bool:
        return fig is None or fig.source in _USER_EDITABLE

    return SalesDayRow(
        date=day,
        weekday=_WEEKDAYS[day.weekday()],
        card_pence=card.gross_pence if card else None,
        cash_till_pence=cash.gross_pence if cash else None,
        cash_off_till_pence=off.gross_pence if off else None,
        total_pence=total,
        orders=orders,
        orders_source=orders_source,
        avg_ticket_pence=avg,
        note=trading.note if trading is not None else None,
        basis=basis,
        editable=SalesEditable(
            card=editable(card), cash_till=editable(cash), cash_off_till=editable(off)
        ),
        sources=sources,
    )


def read_sales(
    session: Session, period: Period
) -> tuple[list[SalesDayRow], SalesTotals, list[str]]:
    since, until = _window(period)
    resolved, caveats = resolve_takings(session, since=since, until=until)
    tstmt = select(TradingDay)
    if since is not None and until is not None:
        tstmt = tstmt.where(TradingDay.business_date >= since, TradingDay.business_date <= until)
    trading = {t.business_date: t for t in session.scalars(tstmt)}
    pos = _pos_orders(session, since, until)
    days = sorted(set(resolved) | set(trading))
    rows = [_row(d, resolved.get(d), trading.get(d), pos.get(d)) for d in days]
    orders = [r.orders for r in rows if r.orders is not None]
    totals = SalesTotals(
        days=len(rows),
        card_pence=sum(r.card_pence or 0 for r in rows),
        cash_till_pence=sum(r.cash_till_pence or 0 for r in rows),
        cash_off_till_pence=sum(r.cash_off_till_pence or 0 for r in rows),
        total_pence=sum(r.total_pence for r in rows),
        orders=sum(orders) if orders else None,
    )
    if any(r.basis in ("BANK_DEPOSIT", "MIXED") for r in rows):
        caveats.append(
            "Card figures up to March 2026 are Mettle bank deposits dated the day they "
            "landed (about a day after the sale), imported from the finance workbook. "
            "They are not what the till took that day."
        )
    if rows and totals.orders is None:
        caveats.append("No order counts for these days: average ticket cannot be worked out.")
    return rows, totals, caveats


def sales_day(session: Session, day: date) -> SalesDayRow:
    resolved, _ = resolve_takings(session, since=day, until=day)
    trading = session.scalars(
        select(TradingDay).where(TradingDay.business_date == day)
    ).one_or_none()
    if day not in resolved and trading is None:
        raise LookupError(f"no sales row for {day.isoformat()}")
    return _row(day, resolved.get(day), trading, _pos_orders(session, day, day).get(day))


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


def _day_exists(session: Session, day: date) -> bool:
    has_trading = session.scalar(
        select(func.count()).select_from(TradingDay).where(TradingDay.business_date == day)
    )
    has_payment = session.scalar(
        select(func.count()).select_from(PaymentDay).where(PaymentDay.business_date == day)
    )
    return bool(has_trading) or bool(has_payment)


def _trading(session: Session, day: date, operator: str | None) -> TradingDay:
    row = session.scalars(select(TradingDay).where(TradingDay.business_date == day)).one_or_none()
    if row is None:
        row = TradingDay(business_date=day, source=FinanceSource.MANUAL, updated_by=operator)
        session.add(row)
    return row


def _rows_for(session: Session, day: date, method: PaymentMethod) -> list[PaymentDay]:
    return list(
        session.scalars(
            select(PaymentDay).where(PaymentDay.business_date == day, PaymentDay.method == method)
        )
    )


def _set_figure(
    session: Session, day: date, method: PaymentMethod, pence: int | None, operator: str | None
) -> None:
    rows = _rows_for(session, day, method)
    exported = [r for r in rows if r.source not in _USER_EDITABLE]
    if exported:
        src = exported[0].source.value
        raise FinanceConflict(
            f"{method.value} for {day.isoformat()} comes from an export ({src}) and wins over "
            "anything typed here. Correct it at the source and re-import."
        )
    manual = next((r for r in rows if r.source is PaymentSourceKind.MANUAL), None)
    legacy = next((r for r in rows if r.source is PaymentSourceKind.LEGACY_WORKBOOK), None)
    if pence is None:
        # Clearing a figure removes what was typed AND what the workbook said, or the
        # workbook value would silently reappear from behind the cleared one.
        for r in (manual, legacy):
            if r is not None:
                session.delete(r)
        return
    if manual is None:
        session.add(
            PaymentDay(
                business_date=day,
                method=method,
                gross_pence=pence,
                basis=PaymentBasis.TILL,
                source=PaymentSourceKind.MANUAL,
                source_ref=f"typed by {operator}" if operator else "typed on the Sales tab",
            )
        )
    else:
        manual.gross_pence = pence
        manual.imported_at = now_utc()


def _validate(
    card: int | _Unset | None,
    cash: int | _Unset | None,
    off: int | _Unset | None,
    orders: int | _Unset | None,
) -> None:
    for name, value in (
        ("card_pence", card),
        ("cash_till_pence", cash),
        ("cash_off_till_pence", off),
    ):
        if not isinstance(value, _Unset):
            require_pence(value, name)
    if not isinstance(orders, _Unset) and orders is not None:
        if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
            raise FinanceRefused("orders_override: a whole number, zero or more")


def _describe(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]} {day.day} {day.strftime('%b')}"


def create_day(
    session: Session,
    *,
    day: date,
    card_pence: int | None = None,
    cash_till_pence: int | None = None,
    cash_off_till_pence: int | None = None,
    orders_override: int | None = None,
    note: str | None = None,
    operator: str | None = None,
) -> SalesDayRow:
    _validate(card_pence, cash_till_pence, cash_off_till_pence, orders_override)
    if _day_exists(session, day):
        raise FinanceConflict(f"There is already a row for {_describe(day)}.")
    trading = _trading(session, day, operator)
    trading.note = clean_text(note)
    trading.transactions_override = orders_override
    for method, value in zip(
        SALES_METHODS, (card_pence, cash_till_pence, cash_off_till_pence), strict=True
    ):
        if value is not None:
            _set_figure(session, day, method, value, operator)
    session.flush()
    return sales_day(session, day)


def update_day(
    session: Session,
    day: date,
    *,
    new_date: date | _Unset = UNSET,
    card_pence: int | _Unset | None = UNSET,
    cash_till_pence: int | _Unset | None = UNSET,
    cash_off_till_pence: int | _Unset | None = UNSET,
    orders_override: int | _Unset | None = UNSET,
    note: str | _Unset | None = UNSET,
    operator: str | None = None,
) -> SalesDayRow:
    _validate(card_pence, cash_till_pence, cash_off_till_pence, orders_override)
    if not _day_exists(session, day):
        raise LookupError(f"no sales row for {day.isoformat()}")
    if not isinstance(new_date, _Unset) and new_date != day:
        _move_day(session, day, new_date)
        day = new_date
    for method, value in zip(
        SALES_METHODS, (card_pence, cash_till_pence, cash_off_till_pence), strict=True
    ):
        if not isinstance(value, _Unset):
            _set_figure(session, day, method, value, operator)
    if not isinstance(note, _Unset) or not isinstance(orders_override, _Unset):
        trading = _trading(session, day, operator)
        if not isinstance(note, _Unset):
            trading.note = clean_text(note)
        if not isinstance(orders_override, _Unset):
            trading.transactions_override = orders_override
        trading.source = FinanceSource.MANUAL
        trading.updated_by = operator
    session.flush()
    return sales_day(session, day)


def _exports_on(session: Session, day: date) -> list[PaymentDay]:
    return [
        r
        for r in session.scalars(select(PaymentDay).where(PaymentDay.business_date == day))
        if r.source not in _USER_EDITABLE
    ]


def _move_day(session: Session, old: date, new: date) -> None:
    if _day_exists(session, new):
        raise FinanceConflict(f"There is already a row for {_describe(new)}.")
    if _exports_on(session, old):
        raise FinanceConflict(
            f"{_describe(old)} has figures from an export; its date comes from the export "
            "and cannot be changed here."
        )
    for r in session.scalars(select(PaymentDay).where(PaymentDay.business_date == old)):
        r.business_date = new
    for t in session.scalars(select(TradingDay).where(TradingDay.business_date == old)):
        t.business_date = new
    for c in session.scalars(select(CashCount).where(CashCount.business_date == old)):
        c.business_date = new
    for p in session.scalars(select(CardPayout).where(CardPayout.sold_on == old)):
        p.sold_on = new
    session.flush()


def delete_day(session: Session, day: date) -> date:
    """Remove a typed or imported day. A day holding export rows is refused."""
    if not _day_exists(session, day):
        raise LookupError(f"no sales row for {day.isoformat()}")
    if _exports_on(session, day):
        raise FinanceConflict(
            f"{_describe(day)} has figures from an export. Those are not deleted by hand; "
            "correct the export and re-import."
        )
    counted = session.scalars(select(CashCount).where(CashCount.business_date == day)).first()
    if counted is not None:
        raise FinanceConflict(
            f"{_describe(day)} has a cash count ({counted.counted_by}). Clear the count on "
            "Reconcile first, so a signed count is never deleted as a side effect."
        )
    if session.scalars(select(CardPayout).where(CardPayout.sold_on == day)).first() is not None:
        raise FinanceConflict(
            f"{_describe(day)} has a recorded card payout. Clear it on Reconcile first."
        )
    session.execute(delete(PaymentDay).where(PaymentDay.business_date == day))
    session.execute(delete(TradingDay).where(TradingDay.business_date == day))
    session.flush()
    return day


def days_with_rows(session: Session) -> Iterable[date]:
    yield from session.scalars(select(PaymentDay.business_date).distinct())
    yield from session.scalars(select(TradingDay.business_date))
