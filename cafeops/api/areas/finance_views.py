"""Finance views: parse, call a service, return a finished response model.

Each function runs inside `in_session` on a worker thread and returns a Pydantic model
built before the session closes. No view computes a figure; the services do.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from cafeops.api.areas.finance_schemas import (
    CardRowOut,
    CashCountIn,
    CashExplainIn,
    CashRowOut,
    ChannelStatementIn,
    ChannelUploadIn,
    ChannelUploadOut,
    DeletedOut,
    DeliveryRowOut,
    DirectorEntryIn,
    DirectorEntryOut,
    DirectorEntryPatch,
    DirectorResponse,
    ExpenseDeletedOut,
    ExpenseIn,
    ExpenseOut,
    ExpensePatch,
    ExpenseRestoreIn,
    ExpensesResponse,
    FinanceAlertsOut,
    FinanceMetaOut,
    FinanceMonthsOut,
    FinanceSettingsIn,
    FinanceSettingsOut,
    OverviewOut,
    PayoutIn,
    PLResponse,
    ReceiptsResponse,
    ReconcileResponse,
    SalesDayIn,
    SalesDayOut,
    SalesDayPatch,
    SalesInsightsOut,
    SalesResponse,
    TakingsLedgerResponse,
)
from cafeops.db.models.enums import (
    DirectorEntryType,
    ExpenseKind,
    ExpenseMethod,
    SalesChannelName,
)
from cafeops.services.finance import (
    channels_month,
    director,
    expenses,
    overview,
    periods,
    reconcile,
    sales_insights,
    trading_days,
    transactions,
)
from cafeops.services.finance.common import (
    UNSET,
    FinanceRefused,
    Unset,
    month_key,
    parse_month,
    parse_period,
)

# --------------------------------------------------------------------------
# shared
# --------------------------------------------------------------------------


def months_view(session: Session) -> FinanceMonthsOut:
    return FinanceMonthsOut.model_validate(periods.finance_months(session))


def meta_view(session: Session) -> FinanceMetaOut:
    return FinanceMetaOut.model_validate(
        {
            "categories": [
                {
                    "id": c.id,
                    "name": c.name,
                    "group": c.expense_group.value,
                    "sort": c.sort,
                    "active": c.is_active,
                }
                for c in expenses.categories(session)
            ],
            "methods": [m.value for m in ExpenseMethod],
            "kinds": [k.value for k in ExpenseKind],
            "director_types": [t.value for t in DirectorEntryType],
            "settings": overview.read_settings(session),
        }
    )


def settings_patch_view(session: Session, body: FinanceSettingsIn) -> FinanceSettingsOut:
    return FinanceSettingsOut.model_validate(
        overview.update_settings(
            session,
            payout_lag_working_days=body.payout_lag_working_days,
            card_fee_bp=body.card_fee_bp,
            operator=body.operator,
        )
    )


def overview_view(session: Session, period: str | None) -> OverviewOut:
    return OverviewOut.model_validate(overview.overview(session, parse_period(period)))


def pl_view(session: Session, since: str | None, until: str | None) -> PLResponse:
    return PLResponse.model_validate(
        overview.profit_and_loss(
            session,
            since=parse_month(since) if since else None,
            until=parse_month(until) if until else None,
        )
    )


def alerts_view(session: Session) -> FinanceAlertsOut:
    return FinanceAlertsOut.model_validate(overview.finance_alerts(session))


def _period_key(period: date | None) -> str:
    return "all" if period is None else month_key(period)


# --------------------------------------------------------------------------
# sales
# --------------------------------------------------------------------------


def sales_view(session: Session, period: str | None) -> SalesResponse:
    p = parse_period(period)
    rows, totals, caveats = trading_days.read_sales(session, p)
    return SalesResponse.model_validate(
        {"period": _period_key(p), "days": rows, "totals": totals, "caveats": caveats}
    )


def sales_create_view(session: Session, body: SalesDayIn) -> SalesDayOut:
    return SalesDayOut.model_validate(
        trading_days.create_day(
            session,
            day=body.date,
            card_pence=body.card_pence,
            cash_pence=body.cash_pence,
            orders_override=body.orders_override,
            note=body.note,
            operator=body.operator,
        )
    )


def _sent[T](
    body: SalesDayPatch | ExpensePatch | DirectorEntryPatch, name: str, value: T
) -> T | Unset:
    return value if name in body.model_fields_set else UNSET


def sales_patch_view(session: Session, day: date, body: SalesDayPatch) -> SalesDayOut:
    if "date" in body.model_fields_set and body.date is None:
        raise FinanceRefused("date: cannot be empty")
    return SalesDayOut.model_validate(
        trading_days.update_day(
            session,
            day,
            new_date=body.date if body.date is not None else UNSET,
            card_pence=_sent(body, "card_pence", body.card_pence),
            cash_pence=_sent(body, "cash_pence", body.cash_pence),
            orders_override=_sent(body, "orders_override", body.orders_override),
            note=_sent(body, "note", body.note),
            operator=body.operator,
        )
    )


def sales_delete_view(session: Session, day: date) -> DeletedOut:
    return DeletedOut(deleted=trading_days.delete_day(session, day).isoformat())


# --------------------------------------------------------------------------
# expenses
# --------------------------------------------------------------------------


def expenses_view(
    session: Session,
    *,
    period: str | None,
    q: str | None,
    category: str,
    kind: str,
    needs_review: bool,
    no_receipt: bool,
) -> ExpensesResponse:
    p = parse_period(period)
    rows, total = expenses.list_expenses(
        session,
        p,
        expenses.ExpenseFilter(
            q=q, category=category, kind=kind, needs_review=needs_review, no_receipt=no_receipt
        ),
    )
    caveats: list[str] = []
    if rows and not any(r.has_receipt for r in rows):
        caveats.append("None of these expenses has a receipt recorded.")
    return ExpensesResponse.model_validate(
        {
            "period": _period_key(p),
            "expenses": rows,
            "count": len(rows),
            "total_pence": total,
            "caveats": caveats,
        }
    )


def _method(raw: str | None) -> ExpenseMethod | None:
    return None if raw is None else ExpenseMethod[raw]


def expense_create_view(session: Session, body: ExpenseIn) -> ExpenseOut:
    e = expenses.create_expense(
        session,
        paid_on=body.date,
        category_id=body.category_id,
        description=body.description,
        amount_pence=body.amount_pence,
        method=_method(body.method),
        kind=ExpenseKind[body.kind],
        has_receipt=body.has_receipt,
        notes=body.notes,
        needs_review=body.needs_review,
        operator=body.operator,
    )
    return ExpenseOut.model_validate(expenses.expense_row(session, e.id))


def expense_patch_view(session: Session, expense_id: int, body: ExpensePatch) -> ExpenseOut:
    fs = body.model_fields_set
    for name in (
        "date",
        "category_id",
        "description",
        "amount_pence",
        "kind",
        "has_receipt",
        "needs_review",
    ):
        if name in fs and getattr(body, name) is None:
            raise FinanceRefused(f"{name}: cannot be empty")
    expenses.update_expense(
        session,
        expense_id,
        paid_on=body.date if body.date is not None else UNSET,
        category_id=body.category_id if body.category_id is not None else UNSET,
        description=body.description if body.description is not None else UNSET,
        amount_pence=body.amount_pence if body.amount_pence is not None else UNSET,
        method=_method(body.method) if "method" in fs else UNSET,
        kind=ExpenseKind[body.kind] if body.kind is not None else UNSET,
        has_receipt=body.has_receipt if body.has_receipt is not None else UNSET,
        notes=body.notes if "notes" in fs else UNSET,
        needs_review=body.needs_review if body.needs_review is not None else UNSET,
        operator=body.operator,
    )
    return ExpenseOut.model_validate(expenses.expense_row(session, expense_id))


def expense_delete_view(
    session: Session, expense_id: int, operator: str | None
) -> ExpenseDeletedOut:
    token = expenses.delete_expense(session, expense_id, operator=operator)
    return ExpenseDeletedOut(deleted=expense_id, undo_token=token)


def expense_restore_view(session: Session, expense_id: int, body: ExpenseRestoreIn) -> ExpenseOut:
    expenses.restore_expense(session, expense_id, body.undo_token, operator=body.operator)
    return ExpenseOut.model_validate(expenses.expense_row(session, expense_id))


# --------------------------------------------------------------------------
# reconcile
# --------------------------------------------------------------------------


def reconcile_view(session: Session, period: str | None) -> ReconcileResponse:
    return ReconcileResponse.model_validate(reconcile.read_reconcile(session, parse_period(period)))


def payout_view(session: Session, sold_on: date, body: PayoutIn) -> CardRowOut:
    return CardRowOut.model_validate(
        reconcile.record_payout(
            session,
            sold_on,
            arrived_pence=body.arrived_pence,
            arrived_on=body.arrived_on,
            operator=body.operator,
        )
    )


def _channel(raw: str) -> SalesChannelName:
    try:
        return SalesChannelName[raw.strip().upper()]
    except KeyError as exc:
        raise FinanceRefused("channel: DELIVEROO or JUST_EAT") from exc


def statement_view(
    session: Session, month: str, channel: str, body: ChannelStatementIn
) -> DeliveryRowOut:
    m = parse_month(month)
    ch = _channel(channel)
    channels_month.upsert_statement(
        session,
        month=m,
        channel=ch,
        gross_pence=body.gross_pence,
        commission_pence=body.commission_pence,
        ads_pence=body.ads_pence,
        operator=body.operator,
    )
    return DeliveryRowOut.model_validate(reconcile.delivery_row(session, m, ch))


def upload_view(session: Session, body: ChannelUploadIn) -> ChannelUploadOut:
    return ChannelUploadOut.model_validate(
        channels_month.upload_csv(
            session, channel=_channel(body.channel), filename=body.filename, text=body.text
        )
    )


def cash_count_view(session: Session, day: date, body: CashCountIn) -> CashRowOut:
    return CashRowOut.model_validate(
        reconcile.record_cash_count(
            session, day, counted_pence=body.counted_pence, counted_by=body.counted_by
        )
    )


def cash_explain_view(session: Session, day: date, body: CashExplainIn) -> CashRowOut:
    return CashRowOut.model_validate(
        reconcile.explain_cash(
            session, day, explanation=body.explanation, explained_by=body.explained_by
        )
    )


# --------------------------------------------------------------------------
# director
# --------------------------------------------------------------------------


def director_view(session: Session) -> DirectorResponse:
    s = director.read_director(session)
    return DirectorResponse.model_validate(
        {
            "entries": s.rows,
            "put_in_pence": s.put_in_pence,
            "taken_out_pence": s.taken_out_pence,
            "capital_in_pence": s.capital_in_pence,
            "loans_in_pence": s.loans_in_pence,
            "loan_balance_pence": s.loan_balance_pence,
            "workbook_balance_pence": s.workbook_balance_pence,
            "mirrored_count": s.mirrored_count,
            "caveats": s.caveats,
        }
    )


def director_create_view(session: Session, body: DirectorEntryIn) -> DirectorEntryOut:
    e = director.create_entry(
        session,
        entry_date=body.date,
        type_=DirectorEntryType[body.type],
        description=body.description,
        in_pence=body.in_pence,
        out_pence=body.out_pence,
        notes=body.notes,
        operator=body.operator,
    )
    return DirectorEntryOut.model_validate(director.row_for(session, e.id))


def director_patch_view(
    session: Session, entry_id: int, body: DirectorEntryPatch
) -> DirectorEntryOut:
    fs = body.model_fields_set
    for name in ("date", "type", "description", "in_pence", "out_pence"):
        if name in fs and getattr(body, name) is None:
            raise FinanceRefused(f"{name}: cannot be empty")
    director.update_entry(
        session,
        entry_id,
        entry_date=body.date if body.date is not None else UNSET,
        type_=DirectorEntryType[body.type] if body.type is not None else UNSET,
        description=body.description if body.description is not None else UNSET,
        in_pence=body.in_pence if body.in_pence is not None else UNSET,
        out_pence=body.out_pence if body.out_pence is not None else UNSET,
        notes=body.notes if "notes" in fs else UNSET,
        operator=body.operator,
    )
    return DirectorEntryOut.model_validate(director.row_for(session, entry_id))


def director_delete_view(session: Session, entry_id: int, operator: str | None) -> DeletedOut:
    return DeletedOut(deleted=str(director.delete_entry(session, entry_id, operator=operator)))


# --------------------------------------------------------------------------
# sales insights (read-only)
# --------------------------------------------------------------------------


def _wire(value: object) -> object:
    """Dataclasses to plain data, with every Decimal quantity as text (10.10)."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _wire(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: _wire(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_wire(v) for v in value]
    return value


def sales_insights_view(
    session: Session,
    *,
    since: date | None,
    until: date | None,
    channel: str | None,
    category: str | None,
    product: str | None,
    size: str | None,
    weekdays: frozenset[int] | None,
    whole: bool,
) -> SalesInsightsOut:
    r = sales_insights.sales_insights(
        session,
        since=since,
        until=until,
        channel=channel,
        category=category,
        product=product,
        size=size,
        weekdays=weekdays,
        whole=whole,
    )
    return SalesInsightsOut.model_validate(_wire(r))


# --------------------------------------------------------------------------
# transactions (read-only)
# --------------------------------------------------------------------------


def receipts_view(
    session: Session,
    *,
    since: date | None,
    until: date | None,
    channel: str | None,
    q: str | None,
    min_pence: int | None,
    max_pence: int | None,
    include_voided: bool,
    page: int,
    page_size: int,
) -> ReceiptsResponse:
    r = transactions.list_receipts(
        session,
        since=since,
        until=until,
        channel=channel,
        q=q,
        min_pence=min_pence,
        max_pence=max_pence,
        include_voided=include_voided,
        page=page,
        page_size=page_size,
    )
    return ReceiptsResponse.model_validate(r)


def takings_ledger_view(
    session: Session,
    *,
    since: date | None,
    until: date | None,
    method: str | None,
    source: str | None,
    used_only: bool,
    min_pence: int | None,
    max_pence: int | None,
    page: int,
    page_size: int,
) -> TakingsLedgerResponse:
    r = transactions.list_takings(
        session,
        since=since,
        until=until,
        method=method,
        source=source,
        used_only=used_only,
        min_pence=min_pence,
        max_pence=max_pence,
        page=page,
        page_size=page_size,
    )
    return TakingsLedgerResponse.model_validate(r)
