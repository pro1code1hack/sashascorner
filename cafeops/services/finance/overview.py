"""Overview, Profit & loss, the cash alert and the settings row. Finance spec 1.1, 1.5, 1.7.

Pure assembly over `periods`, `reconcile` and `expenses`. Nothing here writes except
`update_settings`.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.clock import utcnow
from cafeops.db.models.finance import CashCount, Expense, ExpenseCategory
from cafeops.domain.units import pounds_figure
from cafeops.services.finance.common import (
    FinanceRefused,
    Period,
    finance_settings,
    month_key,
    month_label,
    month_range,
    ordinal,
)
from cafeops.services.finance.periods import (
    CategoryAmount,
    PeriodFigures,
    data_months,
    period_figures,
    previous_month_figures,
)
from cafeops.services.finance.reconcile import cash_row
from cafeops.services.finance.takings import last_imported_at, resolve_takings

__all__ = [
    "CashAlert",
    "FinanceAlerts",
    "Overview",
    "ProfitLoss",
    "SettingsView",
    "finance_alerts",
    "overview",
    "profit_and_loss",
    "read_settings",
    "update_settings",
]


@dataclass(frozen=True, slots=True)
class DayBar:
    day: int
    date: date
    #: None = nothing entered that day (drawn as a dash, never as a zero bar).
    total_pence: int | None


@dataclass(frozen=True, slots=True)
class MonthBar:
    month: str
    label: str
    revenue_pence: int


@dataclass(frozen=True, slots=True)
class Chart:
    mode: str
    days_in_month: int | None
    first_label: str
    last_label: str
    day_bars: list[DayBar]
    month_bars: list[MonthBar]


@dataclass(frozen=True, slots=True)
class FlaggedExpense:
    expense_id: int
    description: str
    amount_pence: int


@dataclass(frozen=True, slots=True)
class NeedsALook:
    count: int
    flagged: list[FlaggedExpense]
    flagged_total: int
    without_receipt: int


@dataclass(frozen=True, slots=True)
class Overview:
    current: PeriodFigures
    previous: PeriodFigures | None
    avg_per_day_pence: int | None
    chart: Chart
    where_it_went: list[CategoryAmount]
    needs_a_look: NeedsALook
    caveats: list[str]


def _chart(session: Session, period: Period) -> Chart:
    if period is None:
        bars = [
            MonthBar(month_key(m), month_label(m), period_figures(session, m).revenue_pence)
            for m in data_months(session)
        ]
        return Chart(
            mode="months",
            days_in_month=None,
            first_label=bars[0].label if bars else "",
            last_label=bars[-1].label if bars else "",
            day_bars=[],
            month_bars=bars,
        )
    first, last = month_range(period)
    resolved, _ = resolve_takings(session, since=first, until=last)
    n = calendar.monthrange(first.year, first.month)[1]
    day_bars = []
    for i in range(1, n + 1):
        d = first.replace(day=i)
        r = resolved.get(d)
        day_bars.append(DayBar(i, d, None if r is None else r.till_total_pence))
    return Chart("days", n, ordinal(1), ordinal(n), day_bars, [])


def _needs_a_look(session: Session, period: Period) -> NeedsALook:
    base = select(Expense).where(Expense.deleted_at.is_(None))
    if period is not None:
        first, last = month_range(period)
        base = base.where(Expense.paid_on >= first, Expense.paid_on <= last)
    flagged = list(
        session.scalars(
            base.where(Expense.needs_review.is_(True)).order_by(
                Expense.paid_on.desc(), Expense.id.desc()
            )
        )
    )
    no_receipt = (
        session.scalar(
            select(func.count()).select_from(base.where(Expense.has_receipt.is_(False)).subquery())
        )
        or 0
    )
    return NeedsALook(
        count=len(flagged) + (1 if no_receipt else 0),
        flagged=[FlaggedExpense(e.id, e.description, e.amount_pence) for e in flagged[:4]],
        flagged_total=len(flagged),
        without_receipt=int(no_receipt),
    )


def overview(session: Session, period: Period) -> Overview:
    current = period_figures(session, period)
    previous = None if period is None else previous_month_figures(session, period)
    where = sorted(current.cogs_by_category + current.opex_by_category, key=lambda c: -c.pence)[:6]
    avg = (
        None
        if current.trading_days == 0
        else (current.revenue_pence + current.trading_days // 2) // current.trading_days
    )
    return Overview(
        current=current,
        previous=previous,
        avg_per_day_pence=avg,
        chart=_chart(session, period),
        where_it_went=where,
        needs_a_look=_needs_a_look(session, period),
        caveats=list(current.caveats),
    )


@dataclass(frozen=True, slots=True)
class ProfitLoss:
    months: list[str]
    columns: list[PeriodFigures]
    total: PeriodFigures
    cogs_categories: list[str]
    opex_categories: list[str]
    threshold_bp: int
    caveats: list[str]


def profit_and_loss(
    session: Session, *, since: date | None = None, until: date | None = None
) -> ProfitLoss:
    months = [
        m
        for m in data_months(session)
        if (since is None or m >= since) and (until is None or m <= until)
    ]
    columns = [period_figures(session, m) for m in months]
    total = period_figures(session, None)
    order = {c.name: c.sort for c in session.scalars(select(ExpenseCategory))}
    cogs = sorted(
        {c.category for col in columns for c in col.cogs_by_category if c.pence},
        key=lambda n: order.get(n, 99),
    )
    opex = sorted(
        {c.category for col in columns for c in col.opex_by_category if c.pence},
        key=lambda n: order.get(n, 99),
    )
    return ProfitLoss(
        months=[month_key(m) for m in months],
        columns=columns,
        total=total,
        cogs_categories=cogs,
        opex_categories=opex,
        threshold_bp=finance_settings(session).stock_pct_threshold_bp,
        caveats=list(total.caveats),
    )


# --------------------------------------------------------------------------
# alerts (the shell's cash banner)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CashAlert:
    date: date
    diff_pence: int
    direction: str
    message: str
    open_count: int
    tolerance_pence: int


@dataclass(frozen=True, slots=True)
class FinanceAlerts:
    cash: CashAlert | None
    takings_last_imported_at: datetime | None


def finance_alerts(session: Session) -> FinanceAlerts:
    """Newest unexplained cash count out by more than tolerance, and takings freshness."""
    tolerance = finance_settings(session).cash_tolerance_pence
    open_rows = []
    for count in session.scalars(
        select(CashCount)
        .where(CashCount.explanation.is_(None))
        .order_by(CashCount.business_date.desc())
    ):
        row = cash_row(session, count.business_date)
        if row.diff_pence is not None and abs(row.diff_pence) > tolerance:
            open_rows.append(row)
    alert = None
    if open_rows:
        newest = open_rows[0]
        assert newest.diff_pence is not None
        direction = "short" if newest.diff_pence < 0 else "over"
        when = newest.date.strftime("%a, ") + f"{newest.date.day} " + newest.date.strftime("%b %Y")
        alert = CashAlert(
            date=newest.date,
            diff_pence=newest.diff_pence,
            direction=direction,
            message=(
                f"Cash was £{pounds_figure(abs(newest.diff_pence), grouped=True)} "
                f"{direction} on {when} "
                "and nobody has explained it yet."
            ),
            open_count=len(open_rows),
            tolerance_pence=tolerance,
        )
    return FinanceAlerts(cash=alert, takings_last_imported_at=last_imported_at(session))


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SettingsView:
    payout_lag_working_days: int
    card_fee_bp: int
    cash_tolerance_pence: int
    payout_tolerance_pence: int
    stock_pct_threshold_bp: int
    updated_at: datetime
    updated_by: str | None


def read_settings(session: Session) -> SettingsView:
    s = finance_settings(session)
    return SettingsView(
        s.payout_lag_working_days,
        s.card_fee_bp,
        s.cash_tolerance_pence,
        s.payout_tolerance_pence,
        s.stock_pct_threshold_bp,
        s.updated_at,
        s.updated_by,
    )


def update_settings(
    session: Session,
    *,
    payout_lag_working_days: int | None = None,
    card_fee_bp: int | None = None,
    operator: str | None = None,
) -> SettingsView:
    s = finance_settings(session)
    if payout_lag_working_days is not None:
        if not 0 <= payout_lag_working_days <= 10:
            raise FinanceRefused("payout_lag_working_days: 0 to 10 working days")
        s.payout_lag_working_days = payout_lag_working_days
    if card_fee_bp is not None:
        if not 0 <= card_fee_bp <= 1000:
            raise FinanceRefused("card_fee_bp: 0 to 1000 basis points (0% to 10%)")
        s.card_fee_bp = card_fee_bp
    s.updated_by = operator
    s.updated_at = utcnow()
    session.flush()
    return read_settings(session)
