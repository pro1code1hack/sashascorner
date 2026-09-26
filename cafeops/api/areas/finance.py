"""Routes for the finance area ("Money") of the back-office redesign.

docs/design/specs/finance.md 5. Every route needs the shared credential (`ApiAuth`).
Thin on purpose: parse, hand the work to a view on a worker thread, return.

Status codes: 404 unknown date/id (`LookupError`, app-wide handler); 422 a malformed or
rule-breaking body (`ValueError`/`FinanceRefused`, app-wide handler, or Pydantic);
409 a collision (`FinanceConflict`: a duplicate sales date, a field mirrored from an
expense, an export row that typing cannot override) -- translated here because the
app-wide handlers are not this area's to edit.

None of these routes touches purchase orders, stock or composition (invariants 1, 10,
12). Finance writes are edits in place with `updated_by` and soft delete.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from cafeops.api.areas import finance_views as v
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
    ReconcileResponse,
    SalesDayIn,
    SalesDayOut,
    SalesDayPatch,
    SalesResponse,
)
from cafeops.api.runtime import in_session
from cafeops.api.security import ApiAuth
from cafeops.services.finance.common import FinanceConflict

router = APIRouter(dependencies=[ApiAuth], prefix="/api/finance", tags=["money"])

PeriodQ = Annotated[
    str | None, Query(description="YYYY-MM or 'all' (default all).", examples=["2026-04"])
]
OperatorQ = Annotated[str | None, Query(max_length=120, description="Who is doing this.")]


async def _run[T](work: Callable[[Session], T]) -> T:
    try:
        return await in_session(work)
    except FinanceConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# ---------------------------------------------------------------- shared ---


@router.get("/months", response_model=FinanceMonthsOut, summary="Months with finance data.")
async def months() -> FinanceMonthsOut:
    return await _run(v.months_view)


@router.get("/meta", response_model=FinanceMetaOut, summary="Categories, methods, settings.")
async def meta() -> FinanceMetaOut:
    return await _run(v.meta_view)


@router.patch("/settings", response_model=FinanceSettingsOut, summary="Payout lag, card fee.")
async def patch_settings(body: FinanceSettingsIn) -> FinanceSettingsOut:
    return await _run(lambda s: v.settings_patch_view(s, body))


@router.get("/overview", response_model=OverviewOut, summary="The month statement.")
async def get_overview(period: PeriodQ = None) -> OverviewOut:
    return await _run(lambda s: v.overview_view(s, period))


@router.get("/pl", response_model=PLResponse, summary="Profit & loss by month.")
async def get_pl(
    since: Annotated[str | None, Query(alias="from", description="YYYY-MM")] = None,
    until: Annotated[str | None, Query(alias="to", description="YYYY-MM")] = None,
) -> PLResponse:
    return await _run(lambda s: v.pl_view(s, since, until))


@router.get(
    "/alerts",
    response_model=FinanceAlertsOut,
    summary="The cash banner: newest unexplained count over tolerance; takings freshness.",
)
async def alerts() -> FinanceAlertsOut:
    return await _run(v.alerts_view)


# ----------------------------------------------------------------- sales ---


@router.get("/sales", response_model=SalesResponse, summary="One row per trading day.")
async def sales(period: PeriodQ = None) -> SalesResponse:
    return await _run(lambda s: v.sales_view(s, period))


@router.post("/sales", response_model=SalesDayOut, status_code=201, summary="Add a day.")
async def sales_create(body: SalesDayIn) -> SalesDayOut:
    return await _run(lambda s: v.sales_create_view(s, body))


@router.patch("/sales/{day}", response_model=SalesDayOut, summary="Edit or re-date a day.")
async def sales_patch(day: date, body: SalesDayPatch) -> SalesDayOut:
    return await _run(lambda s: v.sales_patch_view(s, day, body))


@router.delete("/sales/{day}", response_model=DeletedOut, summary="Remove a typed day.")
async def sales_delete(day: date) -> DeletedOut:
    return await _run(lambda s: v.sales_delete_view(s, day))


# -------------------------------------------------------------- expenses ---


@router.get("/expenses", response_model=ExpensesResponse, summary="Expenses, filtered.")
async def expenses_list(
    period: PeriodQ = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str, Query(description="all | cogs | <category id>")] = "all",
    kind: Annotated[str, Query(description="all | OPERATING | CAPITAL | DRAWINGS")] = "all",
    needs_review: bool = False,
    no_receipt: bool = False,
) -> ExpensesResponse:
    return await _run(
        lambda s: v.expenses_view(
            s,
            period=period,
            q=q,
            category=category,
            kind=kind,
            needs_review=needs_review,
            no_receipt=no_receipt,
        )
    )


@router.post("/expenses", response_model=ExpenseOut, status_code=201, summary="Add an expense.")
async def expense_create(body: ExpenseIn) -> ExpenseOut:
    return await _run(lambda s: v.expense_create_view(s, body))


@router.patch("/expenses/{expense_id}", response_model=ExpenseOut, summary="Edit an expense.")
async def expense_patch(expense_id: int, body: ExpensePatch) -> ExpenseOut:
    return await _run(lambda s: v.expense_patch_view(s, expense_id, body))


@router.delete(
    "/expenses/{expense_id}", response_model=ExpenseDeletedOut, summary="Soft-delete (undoable)."
)
async def expense_delete(expense_id: int, operator: OperatorQ = None) -> ExpenseDeletedOut:
    return await _run(lambda s: v.expense_delete_view(s, expense_id, operator))


@router.post("/expenses/{expense_id}/restore", response_model=ExpenseOut, summary="Undo a delete.")
async def expense_restore(expense_id: int, body: ExpenseRestoreIn) -> ExpenseOut:
    return await _run(lambda s: v.expense_restore_view(s, expense_id, body))


# ------------------------------------------------------------- reconcile ---


@router.get("/reconcile", response_model=ReconcileResponse, summary="Card, delivery, cash.")
async def get_reconcile(period: PeriodQ = None) -> ReconcileResponse:
    return await _run(lambda s: v.reconcile_view(s, period))


@router.put("/payouts/{sold_on}", response_model=CardRowOut, summary="What the bank received.")
async def put_payout(sold_on: date, body: PayoutIn) -> CardRowOut:
    return await _run(lambda s: v.payout_view(s, sold_on, body))


@router.put(
    "/channel-statements/{month}/{channel}",
    response_model=DeliveryRowOut,
    summary="Type a delivery app's month. All three null removes it.",
)
async def put_statement(month: str, channel: str, body: ChannelStatementIn) -> DeliveryRowOut:
    return await _run(lambda s: v.statement_view(s, month, channel, body))


@router.post(
    "/channel-statements/upload",
    response_model=ChannelUploadOut,
    summary="Import a portal CSV export (sent as text).",
)
async def upload_statement(body: ChannelUploadIn) -> ChannelUploadOut:
    return await _run(lambda s: v.upload_view(s, body))


@router.put("/cash-counts/{day}", response_model=CashRowOut, summary="Record a drawer count.")
async def put_cash_count(day: date, body: CashCountIn) -> CashRowOut:
    return await _run(lambda s: v.cash_count_view(s, day, body))


@router.post(
    "/cash-counts/{day}/explain", response_model=CashRowOut, summary="Explain a difference."
)
async def explain_cash(day: date, body: CashExplainIn) -> CashRowOut:
    return await _run(lambda s: v.cash_explain_view(s, day, body))


# -------------------------------------------------------------- director ---


@router.get("/director", response_model=DirectorResponse, summary="Director's account.")
async def get_director() -> DirectorResponse:
    return await _run(v.director_view)


@router.post("/director", response_model=DirectorEntryOut, status_code=201, summary="Add an entry.")
async def director_create(body: DirectorEntryIn) -> DirectorEntryOut:
    return await _run(lambda s: v.director_create_view(s, body))


@router.patch("/director/{entry_id}", response_model=DirectorEntryOut, summary="Edit an entry.")
async def director_patch(entry_id: int, body: DirectorEntryPatch) -> DirectorEntryOut:
    return await _run(lambda s: v.director_patch_view(s, entry_id, body))


@router.delete("/director/{entry_id}", response_model=DeletedOut, summary="Remove an entry.")
async def director_delete(entry_id: int, operator: OperatorQ = None) -> DeletedOut:
    return await _run(lambda s: v.director_delete_view(s, entry_id, operator))
