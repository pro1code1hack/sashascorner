"""Period figures: Overview and Profit & loss share them. Finance spec 2.8, 4.6.

The design's `monthCalc`, with the completeness rules it lacked:

* A month with takings but no running costs entered does not have a profit; it has
  an unknown one. `gross_profit_pence` / `net_profit_pence` are `None` and the reason
  is a caveat, so April 2026 no longer reads as £1,293 of profit.
* Delivery-app months nobody uploaded are missing, not zero. Revenue is the known part
  and the period is marked `incomplete` with the missing months named.
* Commission or ads not reported are left out of costs and named, never counted as 0.
* Write-offs valued at estimated costs are flagged; unpriced ones make the figure None.
* Capital and drawings never enter the P&L; they are reported beside it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.clock import local_today
from cafeops.config import settings
from cafeops.db.models.finance import Expense, ExpenseCategory, TradingDay
from cafeops.db.models.payment import PaymentDay
from cafeops.domain.enums import ExpenseGroup, ExpenseKind, PaymentBasis, PaymentMethod
from cafeops.services.finance.channels_month import (
    CHANNELS,
    channel_label,
    month_figures,
)
from cafeops.services.finance.common import (
    Period,
    finance_settings,
    month_key,
    month_label,
    month_long,
    month_range,
    prev_month,
)
from cafeops.services.finance.takings import resolve_takings
from cafeops.services.finance.write_offs import valued_write_offs

__all__ = [
    "CategoryAmount",
    "FinanceMonths",
    "PeriodFigures",
    "data_months",
    "finance_months",
    "period_figures",
]


@dataclass(frozen=True, slots=True)
class CategoryAmount:
    category: str
    pence: int


@dataclass(slots=True)
class PeriodFigures:
    period: str
    label: str
    short_label: str
    trading_days: int
    expense_count: int
    card_pence: int
    #: The day's one cash figure summed (CASH + any legacy CASH_OFF_TILL; DECISIONS 26).
    cash_pence: int
    delivery_gross_pence: int | None
    delivery_missing: list[str]
    revenue_pence: int
    cogs_by_category: list[CategoryAmount]
    stock_bought_pence: int
    written_off_pence: int | None
    written_off_is_estimate: bool
    cogs_pence: int
    stock_pct_bp: int | None
    stock_pct_warn: bool
    gross_profit_pence: int | None
    opex_by_category: list[CategoryAmount]
    opex_pence: int
    delivery_commission_pence: int | None
    delivery_ads_pence: int | None
    net_profit_pence: int | None
    net_warn: bool
    capital_pence: int
    drawings_pence: int
    expenses_missing: bool
    card_is_bank_deposits: bool
    incomplete: bool
    caveats: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FinanceMonths:
    months: list[str]
    default_month: str | None


def data_months(session: Session) -> list[date]:
    """Every month with takings, a trading-day row or a (live) expense. Ascending."""
    months: set[date] = set()
    months |= {d.replace(day=1) for d in session.scalars(select(PaymentDay.business_date))}
    months |= {d.replace(day=1) for d in session.scalars(select(TradingDay.business_date))}
    months |= {
        d.replace(day=1)
        for d in session.scalars(select(Expense.paid_on).where(Expense.deleted_at.is_(None)))
    }
    return sorted(months)


def finance_months(session: Session) -> FinanceMonths:
    months = data_months(session)
    with_takings = {
        d.replace(day=1)
        for d in session.scalars(select(PaymentDay.business_date).where(PaymentDay.gross_pence > 0))
    }
    default = max(with_takings) if with_takings else (months[-1] if months else None)
    return FinanceMonths(
        months=[month_key(m) for m in months],
        default_month=month_key(default) if default else None,
    )


def _pct_bp(part: int, whole: int) -> int | None:
    if whole <= 0:
        return None
    return (part * 10000 + whole // 2) // whole


def _join(labels: list[str]) -> str:
    if len(labels) <= 3:
        return ", ".join(labels)
    return f"{labels[0]} to {labels[-1]} ({len(labels)} months)"


def period_figures(session: Session, period: Period) -> PeriodFigures:
    months_all = data_months(session)
    if period is None:
        months = months_all
        if months:
            since, until = months[0], month_range(months[-1])[1]
        else:
            # No data yet: an empty window, and it IS read (`resolve_takings` below), so it
            # is the cafe's day rather than the machine's.
            since = until = local_today(settings.tz)
        label, short = "Everything so far", "Total"
        key = "all"
    else:
        months = [period]
        since, until = month_range(period)
        label, short = month_long(period), month_label(period)
        key = month_key(period)
    settings_row = finance_settings(session)
    caveats: list[str] = []

    # --- takings ---------------------------------------------------------------
    resolved, disagreements = resolve_takings(session, since=since, until=until)
    caveats.extend(disagreements)
    card = cash = 0
    trading_days = 0
    bank_basis = False
    days_by_month: dict[date, int] = defaultdict(int)
    for day, r in resolved.items():
        card += r.gross(PaymentMethod.CARD) or 0
        # One cash figure per day: a legacy own-cash row is folded in, never dropped.
        cash += (r.gross(PaymentMethod.CASH) or 0) + (r.gross(PaymentMethod.CASH_OFF_TILL) or 0)
        if r.till_total_pence > 0:
            trading_days += 1
            days_by_month[day.replace(day=1)] += 1
        if any(f.basis is PaymentBasis.BANK_DEPOSIT for f in r.by_method.values()):
            bank_basis = True
    if bank_basis:
        caveats.append(
            "Card takings before April 2026 are bank deposits on the day they landed, "
            "from the finance workbook, not till takings."
        )

    # --- delivery apps ---------------------------------------------------------
    gross_known: list[int] = []
    comm_known: list[int] = []
    ads_known: list[int] = []
    missing: dict[str, list[str]] = defaultdict(list)
    unreported: dict[str, list[str]] = defaultdict(list)
    for m in months:
        for ch in CHANNELS:
            fig = month_figures(session, m, ch)
            name = channel_label(ch)
            if not fig.present:
                missing[name].append(month_label(m))
                continue
            if fig.gross_pence is not None:
                gross_known.append(fig.gross_pence)
            else:
                unreported[f"{name} customer totals"].append(month_label(m))
            if fig.commission_pence is not None:
                comm_known.append(fig.commission_pence)
            else:
                unreported[f"{name} commission"].append(month_label(m))
            if fig.ads_pence is not None:
                ads_known.append(fig.ads_pence)
            else:
                unreported[f"{name} ad spend"].append(month_label(m))
    delivery_gross = sum(gross_known) if gross_known else None
    delivery_comm = sum(comm_known) if comm_known else None
    delivery_ads = sum(ads_known) if ads_known else None
    delivery_missing = [f"{name}: {_join(ms)}" for name, ms in missing.items()]
    for name, ms in missing.items():
        caveats.append(f"{name} not uploaded for {_join(ms)}: missing, not zero.")
    for what, ms in unreported.items():
        caveats.append(f"{what} not reported for {_join(ms)}; left out, not counted as zero.")

    revenue = card + cash + (delivery_gross or 0)

    # --- expenses --------------------------------------------------------------
    exp_rows = session.execute(
        select(
            Expense.amount_pence,
            Expense.kind,
            Expense.paid_on,
            ExpenseCategory.name,
            ExpenseCategory.expense_group,
            ExpenseCategory.sort,
        )
        .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .where(Expense.deleted_at.is_(None), Expense.paid_on >= since, Expense.paid_on <= until)
    ).all()
    by_cat: dict[tuple[ExpenseGroup, int, str], int] = defaultdict(int)
    capital = drawings = 0
    operating_months: set[date] = set()
    for amount, kind, paid_on, name, group, sort in exp_rows:
        if kind is ExpenseKind.CAPITAL:
            capital += amount
        elif kind is ExpenseKind.DRAWINGS:
            drawings += amount
        else:
            by_cat[(group, sort, name)] += amount
            operating_months.add(paid_on.replace(day=1))
    cogs_cats = [
        CategoryAmount(name, v)
        for (g, _s, name), v in sorted(by_cat.items(), key=lambda kv: kv[0][1])
        if g is ExpenseGroup.COGS and v
    ]
    opex_cats = [
        CategoryAmount(name, v)
        for (g, _s, name), v in sorted(by_cat.items(), key=lambda kv: kv[0][1])
        if g is ExpenseGroup.OPEX and v
    ]
    stock_bought = sum(c.pence for c in cogs_cats)
    opex = sum(c.pence for c in opex_cats)

    months_without_costs = [m for m in sorted(days_by_month) if m not in operating_months]
    expenses_missing = period is not None and bool(months_without_costs)
    if months_without_costs:
        names = [month_long(m) for m in months_without_costs]
        if period is not None:
            caveats.insert(
                0,
                f"No running costs entered for {names[0]}: stock, rent and the rest are "
                "missing, so there is no profit figure for it yet.",
            )
        else:
            caveats.insert(
                0,
                f"{', '.join(names)} has takings but no running costs entered; "
                "this total counts its takings without its costs.",
            )

    # --- write-offs ------------------------------------------------------------
    wo = valued_write_offs(session, since, until)
    if wo.pence is None:
        caveats.append(
            f"{wo.unpriced} write-off(s) have no cost at all, so stock written off is unknown "
            "and left out of cost of stock."
        )
    elif wo.is_estimate and wo.pence:
        caveats.append("Stock written off is valued at estimated prices.")

    cogs = stock_bought + (wo.pence or 0)
    delivery_costs = (delivery_comm or 0) + (delivery_ads or 0)
    if expenses_missing:
        gross_profit: int | None = None
        net: int | None = None
        stock_pct = None
    else:
        gross_profit = revenue - cogs
        net = gross_profit - opex - delivery_costs
        stock_pct = _pct_bp(cogs, revenue)
    incomplete = bool(missing or unreported or months_without_costs or wo.pence is None)

    return PeriodFigures(
        period=key,
        label=label,
        short_label=short,
        trading_days=trading_days,
        expense_count=len(exp_rows),
        card_pence=card,
        cash_pence=cash,
        delivery_gross_pence=delivery_gross,
        delivery_missing=delivery_missing,
        revenue_pence=revenue,
        cogs_by_category=cogs_cats,
        stock_bought_pence=stock_bought,
        written_off_pence=wo.pence,
        written_off_is_estimate=wo.is_estimate,
        cogs_pence=cogs,
        stock_pct_bp=stock_pct,
        stock_pct_warn=stock_pct is not None and stock_pct > settings_row.stock_pct_threshold_bp,
        gross_profit_pence=gross_profit,
        opex_by_category=opex_cats,
        opex_pence=opex,
        delivery_commission_pence=delivery_comm,
        delivery_ads_pence=delivery_ads,
        net_profit_pence=net,
        net_warn=net is not None and net < 0,
        capital_pence=capital,
        drawings_pence=drawings,
        expenses_missing=expenses_missing,
        card_is_bank_deposits=bank_basis,
        incomplete=incomplete,
        caveats=caveats,
    )


def previous_month_figures(session: Session, month: date) -> PeriodFigures | None:
    """The calendar previous month, or None when it has no data at all."""
    prev = prev_month(month)
    if prev not in set(data_months(session)):
        return None
    return period_figures(session, prev)
