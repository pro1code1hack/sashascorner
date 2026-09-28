"""Reconcile: card payouts vs the bank, delivery apps, cash counted vs the till.
Finance spec 1.4, 2.2, 2.6, 4.3.

Two of the design's figures are corrected here:

* An unrecorded payout is **not** "arrived". The design assumed arrived = expected for
  every past-due day, so every day nobody checked read "ok". Here a past-due day with
  no recorded payout is `not_recorded`, excluded from the arrived total and counted.
* Card figures that are themselves bank deposits (workbook, Sep 2025 - Mar 2026) are
  not reconciled against the bank -- that would be circular. They are `bank_basis`.

The cash difference is computed at read time and is `None` when either side is
missing. A discrepancy over tolerance stays flagged until somebody explains it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models.enums import FinanceSource, PaymentBasis, PaymentMethod, SalesChannelName
from cafeops.db.models.finance import CardPayout, CashCount
from cafeops.services.finance.channels_month import (
    CHANNELS,
    MonthChannel,
    channel_label,
    month_figures,
    months_with_channel_data,
)
from cafeops.services.finance.common import (
    FinanceRefused,
    Period,
    add_working_days,
    clean_text,
    finance_settings,
    local_today,
    month_key,
    month_range,
    now_utc,
    require_pence,
)
from cafeops.services.finance.periods import data_months
from cafeops.services.finance.takings import ResolvedDay, resolve_takings

__all__ = [
    "CardRow",
    "CashRow",
    "DeliveryRow",
    "ReconcileResult",
    "card_row",
    "cash_row",
    "explain_cash",
    "read_reconcile",
    "record_cash_count",
    "record_payout",
]

MISMATCH_NOTE = "less than expected after fees: a refund or chargeback?"
MORE_NOTE = "more than expected after fees: two days paid out together?"
BANK_NOTE = "imported from bank deposits"
NOT_RECORDED_NOTE = "payout not recorded yet"


@dataclass(frozen=True, slots=True)
class CardRow:
    sold_on: date
    card_pence: int
    due_on: date
    expected_pence: int
    arrived_pence: int | None
    arrived_on: date | None
    diff_pence: int | None
    status: str
    note: str | None


@dataclass(frozen=True, slots=True)
class CardSection:
    rows: list[CardRow]
    expected_total_pence: int
    arrived_total_pence: int
    mismatched_days: int
    not_recorded_days: int
    not_yet_due_days: int
    bank_basis_days: int


@dataclass(frozen=True, slots=True)
class DeliveryRow:
    month: str
    label: str
    channel: str
    channel_label: str
    present: bool
    gross_pence: int | None
    commission_pence: int | None
    ads_pence: int | None
    kept_pence: int | None
    kept_bp: int | None
    source: str | None
    note: str | None


@dataclass(frozen=True, slots=True)
class CashRow:
    date: date
    till_pence: int | None
    counted_pence: int | None
    counted_by: str | None
    diff_pence: int | None
    status: str
    explanation: str | None
    explained_by: str | None


@dataclass(frozen=True, slots=True)
class CashSection:
    rows: list[CashRow]
    days_with_cash: int
    net_diff_pence: int
    days_out: int
    days_out_unexplained: int


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    period: str
    payout_lag_working_days: int
    card_fee_bp: int
    cash_tolerance_pence: int
    payout_tolerance_pence: int
    card: CardSection
    delivery: list[DeliveryRow]
    cash: CashSection
    caveats: list[str]


# --------------------------------------------------------------------------
# card
# --------------------------------------------------------------------------


def _expected(card: int, fee_bp: int) -> int:
    """round(card * (1 - fee)), half-up, in integers."""
    return card - (card * fee_bp + 5000) // 10000


def _card_row(
    day: date,
    card: int,
    basis: PaymentBasis,
    payout: CardPayout | None,
    lag: int,
    fee_bp: int,
    tolerance: int,
    today: date,
) -> CardRow:
    due = add_working_days(day, lag)
    expected = _expected(card, fee_bp)
    if basis is PaymentBasis.BANK_DEPOSIT:
        return CardRow(day, card, due, expected, None, None, None, "bank_basis", BANK_NOTE)
    if payout is None:
        status = "not_yet_due" if due > today else "not_recorded"
        return CardRow(
            day,
            card,
            due,
            expected,
            None,
            None,
            None,
            status,
            None if status == "not_yet_due" else NOT_RECORDED_NOTE,
        )
    diff = payout.arrived_pence - expected
    if abs(diff) <= tolerance:
        return CardRow(
            day,
            card,
            due,
            expected,
            payout.arrived_pence,
            payout.arrived_on,
            diff,
            "ok",
            payout.notes,
        )
    return CardRow(
        day,
        card,
        due,
        expected,
        payout.arrived_pence,
        payout.arrived_on,
        diff,
        "mismatch",
        MISMATCH_NOTE if diff < 0 else MORE_NOTE,
    )


def _card_section(
    session: Session, resolved: dict[date, ResolvedDay], since: date | None, until: date | None
) -> CardSection:
    s = finance_settings(session)
    stmt = select(CardPayout)
    if since is not None and until is not None:
        stmt = stmt.where(CardPayout.sold_on >= since, CardPayout.sold_on <= until)
    payouts = {p.sold_on: p for p in session.scalars(stmt)}
    today = local_today()
    rows: list[CardRow] = []
    for day in sorted(resolved):
        fig = resolved[day].by_method.get(PaymentMethod.CARD)
        if fig is None or fig.gross_pence <= 0:
            continue
        rows.append(
            _card_row(
                day,
                fig.gross_pence,
                fig.basis,
                payouts.get(day),
                s.payout_lag_working_days,
                s.card_fee_bp,
                s.payout_tolerance_pence,
                today,
            )
        )
    live = [r for r in rows if r.status != "bank_basis"]
    return CardSection(
        rows=rows,
        expected_total_pence=sum(r.expected_pence for r in live),
        arrived_total_pence=sum(r.arrived_pence or 0 for r in live if r.arrived_pence is not None),
        mismatched_days=sum(1 for r in live if r.status == "mismatch"),
        not_recorded_days=sum(1 for r in live if r.status == "not_recorded"),
        not_yet_due_days=sum(1 for r in live if r.status == "not_yet_due"),
        bank_basis_days=len(rows) - len(live),
    )


def card_row(session: Session, sold_on: date) -> CardRow:
    resolved, _ = resolve_takings(session, since=sold_on, until=sold_on)
    section = _card_section(session, resolved, sold_on, sold_on)
    if not section.rows:
        raise LookupError(f"no card takings on {sold_on.isoformat()}")
    return section.rows[0]


def record_payout(
    session: Session,
    sold_on: date,
    *,
    arrived_pence: int | None,
    arrived_on: date | None = None,
    operator: str | None = None,
) -> CardRow:
    """Record what the bank received for one day's card takings. Null clears it."""
    require_pence(arrived_pence, "arrived_pence")
    resolved, _ = resolve_takings(session, since=sold_on, until=sold_on)
    fig = resolved.get(sold_on)
    card = fig.by_method.get(PaymentMethod.CARD) if fig else None
    if card is None or card.gross_pence <= 0:
        raise LookupError(f"no card takings on {sold_on.isoformat()} to reconcile")
    if card.basis is PaymentBasis.BANK_DEPOSIT:
        raise FinanceRefused(
            f"{sold_on.isoformat()}: this card figure is already a bank deposit; "
            "there is nothing to reconcile it against"
        )
    row = session.scalars(select(CardPayout).where(CardPayout.sold_on == sold_on)).one_or_none()
    if arrived_pence is None:
        if row is not None:
            session.delete(row)
    else:
        if row is None:
            row = CardPayout(
                sold_on=sold_on, arrived_pence=arrived_pence, source=FinanceSource.MANUAL
            )
            session.add(row)
        row.arrived_pence = arrived_pence
        row.arrived_on = arrived_on
        row.updated_by = operator
        row.updated_at = now_utc()
    session.flush()
    return card_row(session, sold_on)


# --------------------------------------------------------------------------
# cash
# --------------------------------------------------------------------------


def _till_cash(r: ResolvedDay | None) -> int | None:
    """The day's cash (CASH + any legacy CASH_OFF_TILL), TILL basis. None if unreported."""
    if r is None:
        return None
    figs = [r.by_method.get(m) for m in (PaymentMethod.CASH, PaymentMethod.CASH_OFF_TILL)]
    known = [f.gross_pence for f in figs if f is not None and f.basis is PaymentBasis.TILL]
    return sum(known) if known else None


def _cash_row(day: date, till: int | None, count: CashCount | None, tolerance: int) -> CashRow:
    if count is None:
        return CashRow(day, till, None, None, None, "not_counted", None, None)
    diff = None if till is None else count.counted_pence - till
    if diff is None:
        status = "no_till_figure"
    elif diff == 0:
        status = "spot_on"
    elif abs(diff) > tolerance:
        status = "out"
    else:
        status = "within"
    return CashRow(
        day,
        till,
        count.counted_pence,
        count.counted_by,
        diff,
        status,
        count.explanation,
        count.explained_by,
    )


def _cash_section(
    session: Session, resolved: dict[date, ResolvedDay], since: date | None, until: date | None
) -> CashSection:
    tolerance = finance_settings(session).cash_tolerance_pence
    stmt = select(CashCount)
    if since is not None and until is not None:
        stmt = stmt.where(CashCount.business_date >= since, CashCount.business_date <= until)
    counts = {c.business_date: c for c in session.scalars(stmt)}
    days = sorted({d for d, r in resolved.items() if (_till_cash(r) or 0) > 0} | set(counts))
    rows = [_cash_row(d, _till_cash(resolved.get(d)), counts.get(d), tolerance) for d in days]
    out = [r for r in rows if r.status == "out"]
    return CashSection(
        rows=rows,
        days_with_cash=len(rows),
        net_diff_pence=sum(r.diff_pence for r in rows if r.diff_pence is not None),
        days_out=len(out),
        days_out_unexplained=sum(1 for r in out if r.explanation is None),
    )


def cash_row(session: Session, day: date) -> CashRow:
    resolved, _ = resolve_takings(session, since=day, until=day)
    count = session.scalars(select(CashCount).where(CashCount.business_date == day)).one_or_none()
    return _cash_row(
        day, _till_cash(resolved.get(day)), count, finance_settings(session).cash_tolerance_pence
    )


def record_cash_count(
    session: Session, day: date, *, counted_pence: int | None, counted_by: str | None
) -> CashRow:
    """Record the drawer count for a day. Null clears it. A count is signed."""
    require_pence(counted_pence, "counted_pence")
    row = session.scalars(select(CashCount).where(CashCount.business_date == day)).one_or_none()
    if counted_pence is None:
        if row is not None:
            session.delete(row)
            session.flush()
        return cash_row(session, day)
    who = clean_text(counted_by)
    if who is None:
        raise FinanceRefused("counted_by: a cash count needs the name of whoever counted it")
    if day > local_today():
        raise FinanceRefused("a cash count cannot be for a day that has not happened yet")
    now = now_utc()
    if row is None:
        row = CashCount(
            business_date=day,
            counted_pence=counted_pence,
            counted_by=who,
            counted_at=now,
            source=FinanceSource.MANUAL,
        )
        session.add(row)
    else:
        if row.counted_pence != counted_pence:
            # A different figure is a different count: an explanation of the old
            # difference does not explain the new one.
            row.explanation = row.explained_by = None
            row.explained_at = None
        row.counted_pence = counted_pence
        row.counted_by = who
        row.counted_at = now
    session.flush()
    return cash_row(session, day)


def explain_cash(
    session: Session, day: date, *, explanation: str, explained_by: str | None
) -> CashRow:
    row = session.scalars(select(CashCount).where(CashCount.business_date == day)).one_or_none()
    if row is None:
        raise LookupError(f"no cash count on {day.isoformat()} to explain")
    text = clean_text(explanation)
    who = clean_text(explained_by)
    if text is None:
        raise FinanceRefused("explanation: say why the drawer was over or short")
    if who is None:
        raise FinanceRefused("explained_by: an explanation needs a name")
    row.explanation = text
    row.explained_by = who
    row.explained_at = now_utc()
    session.flush()
    return cash_row(session, day)


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------


def _delivery_row(fig: MonthChannel) -> DeliveryRow:
    return DeliveryRow(
        month=month_key(fig.month),
        label=f"{fig.month.month:02d}/{fig.month.year % 100:02d}",
        channel=fig.channel.value,
        channel_label=channel_label(fig.channel),
        present=fig.present,
        gross_pence=fig.gross_pence,
        commission_pence=fig.commission_pence,
        ads_pence=fig.ads_pence,
        kept_pence=fig.kept_pence,
        kept_bp=fig.kept_bp,
        source=fig.source,
        note=fig.note,
    )


def delivery_row(session: Session, month: date, channel: SalesChannelName) -> DeliveryRow:
    return _delivery_row(month_figures(session, month, channel))


def read_reconcile(session: Session, period: Period) -> ReconcileResult:
    s = finance_settings(session)
    since, until = (None, None) if period is None else month_range(period)
    resolved, caveats = resolve_takings(session, since=since, until=until)
    card = _card_section(session, resolved, since, until)
    if period is None:
        with_data = months_with_channel_data(session)
        months = [m for m in data_months(session) if m in with_data]
    else:
        months = [period]
    delivery = [_delivery_row(month_figures(session, m, ch)) for m in months for ch in CHANNELS]
    cash = _cash_section(session, resolved, since, until)
    if card.bank_basis_days:
        caveats.append(
            f"{card.bank_basis_days} card day(s) are bank deposits imported from the workbook; "
            "they cannot be checked against the bank and are left out of the totals."
        )
    if card.not_recorded_days:
        caveats.append(
            f"{card.not_recorded_days} past-due payout(s) have not been recorded. They are not "
            "assumed to have arrived."
        )
    return ReconcileResult(
        period="all" if period is None else f"{period.year:04d}-{period.month:02d}",
        payout_lag_working_days=s.payout_lag_working_days,
        card_fee_bp=s.card_fee_bp,
        cash_tolerance_pence=s.cash_tolerance_pence,
        payout_tolerance_pence=s.payout_tolerance_pence,
        card=card,
        delivery=delivery,
        cash=cash,
        caveats=caveats,
    )
