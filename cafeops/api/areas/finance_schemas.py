"""Wire shapes for the Money tabs. docs/design/specs/finance.md 5.

Money is integer pence everywhere (these are stored or integer-derived figures). A
nullable money field means "not reported / unknown", never zero (invariant 8).
Percentages are basis points (integers). Dates are Europe/London business dates.
Every read response carries `caveats`: print them, do not summarise them.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CardRowOut",
    "CashAlertOut",
    "CashCountIn",
    "CashExplainIn",
    "CashRowOut",
    "ChannelStatementIn",
    "ChannelUploadIn",
    "ChannelUploadOut",
    "DeletedOut",
    "DeliveryRowOut",
    "DirectorEntryIn",
    "DirectorEntryOut",
    "DirectorEntryPatch",
    "DirectorResponse",
    "ExpenseDeletedOut",
    "ExpenseIn",
    "ExpenseOut",
    "ExpensePatch",
    "ExpenseRestoreIn",
    "ExpensesResponse",
    "FinanceAlertsOut",
    "FinanceMetaOut",
    "FinanceMonthsOut",
    "FinanceSettingsIn",
    "FinanceSettingsOut",
    "OverviewOut",
    "PLResponse",
    "PayoutIn",
    "PeriodFiguresOut",
    "ReconcileResponse",
    "SalesDayIn",
    "SalesDayOut",
    "SalesDayPatch",
    "SalesResponse",
]

Pence = int
ExpenseMethodName = Literal[
    "CARD", "BANK_TRANSFER", "DIRECT_DEBIT", "STANDING_ORDER", "CASH", "CASH_WITHDRAWAL", "OTHER"
]
ExpenseKindName = Literal["OPERATING", "CAPITAL", "DRAWINGS"]
DirectorTypeName = Literal["CAPITAL_INJECTION", "LOAN_TO_COMPANY", "DRAWINGS", "REPAYMENT"]


class Out(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# shared
# --------------------------------------------------------------------------


class FinanceMonthsOut(Out):
    months: list[str] = Field(description="YYYY-MM ascending: any month with takings or expenses.")
    default_month: str | None = Field(description="Latest month with takings.")


class CategoryOut(Out):
    id: int
    name: str
    group: Literal["COGS", "OPEX"]
    sort: int
    active: bool


class FinanceSettingsOut(Out):
    payout_lag_working_days: int
    card_fee_bp: int = Field(description="Basis points: 175 = 1.75%.")
    cash_tolerance_pence: Pence
    payout_tolerance_pence: Pence
    stock_pct_threshold_bp: int
    updated_at: dt.datetime
    updated_by: str | None


class FinanceSettingsIn(In):
    payout_lag_working_days: int | None = Field(default=None, ge=0, le=10)
    card_fee_bp: int | None = Field(default=None, ge=0, le=1000)
    operator: str | None = Field(default=None, max_length=120)


class FinanceMetaOut(Out):
    categories: list[CategoryOut]
    methods: list[ExpenseMethodName]
    kinds: list[ExpenseKindName]
    director_types: list[DirectorTypeName]
    settings: FinanceSettingsOut


class CategoryAmountOut(Out):
    category: str
    pence: Pence


class PeriodFiguresOut(Out):
    period: str
    label: str
    short_label: str
    trading_days: int
    expense_count: int
    card_pence: Pence
    cash_till_pence: Pence
    cash_off_till_pence: Pence
    delivery_gross_pence: Pence | None = Field(
        description="Known delivery-app customer totals. Null when none known."
    )
    delivery_missing: list[str] = Field(description="'Deliveroo: Oct 25 to Apr 26 (7 months)'.")
    revenue_pence: Pence = Field(description="Till takings plus KNOWN delivery gross.")
    cogs_by_category: list[CategoryAmountOut]
    stock_bought_pence: Pence
    written_off_pence: Pence | None
    written_off_is_estimate: bool
    cogs_pence: Pence
    stock_pct_bp: int | None
    stock_pct_warn: bool
    gross_profit_pence: Pence | None = Field(
        description="Null when the month has takings but no running costs entered."
    )
    opex_by_category: list[CategoryAmountOut]
    opex_pence: Pence
    delivery_commission_pence: Pence | None
    delivery_ads_pence: Pence | None
    net_profit_pence: Pence | None
    net_warn: bool
    capital_pence: Pence
    drawings_pence: Pence
    expenses_missing: bool
    card_is_bank_deposits: bool
    incomplete: bool
    caveats: list[str]


# --------------------------------------------------------------------------
# overview / P&L
# --------------------------------------------------------------------------


class DayBarOut(Out):
    day: int
    date: dt.date
    total_pence: Pence | None = Field(description="Null = nothing entered: draw a dash.")


class MonthBarOut(Out):
    month: str
    label: str
    revenue_pence: Pence


class ChartOut(Out):
    mode: Literal["days", "months"]
    days_in_month: int | None
    first_label: str
    last_label: str
    day_bars: list[DayBarOut]
    month_bars: list[MonthBarOut]


class FlaggedExpenseOut(Out):
    expense_id: int
    description: str
    amount_pence: Pence


class NeedsALookOut(Out):
    count: int
    flagged: list[FlaggedExpenseOut]
    flagged_total: int
    without_receipt: int


class OverviewOut(Out):
    current: PeriodFiguresOut
    previous: PeriodFiguresOut | None
    avg_per_day_pence: Pence | None
    chart: ChartOut
    where_it_went: list[CategoryAmountOut]
    needs_a_look: NeedsALookOut
    caveats: list[str]


class PLResponse(Out):
    months: list[str]
    columns: list[PeriodFiguresOut]
    total: PeriodFiguresOut
    cogs_categories: list[str]
    opex_categories: list[str]
    threshold_bp: int
    caveats: list[str]


# --------------------------------------------------------------------------
# sales
# --------------------------------------------------------------------------


class SalesSourceOut(Out):
    method: str
    source: str
    source_ref: str | None


class SalesEditableOut(Out):
    card: bool
    cash_till: bool
    cash_off_till: bool


class SalesDayOut(Out):
    date: dt.date
    weekday: str
    card_pence: Pence | None
    cash_till_pence: Pence | None
    cash_off_till_pence: Pence | None
    total_pence: Pence
    orders: int | None
    orders_source: Literal["override", "pos", "payment_export"] | None
    avg_ticket_pence: Pence | None
    note: str | None
    basis: Literal["TILL", "BANK_DEPOSIT", "MIXED"]
    editable: SalesEditableOut
    sources: list[SalesSourceOut]


class SalesTotalsOut(Out):
    days: int
    card_pence: Pence
    cash_till_pence: Pence
    cash_off_till_pence: Pence
    total_pence: Pence
    orders: int | None


class SalesResponse(Out):
    period: str
    days: list[SalesDayOut]
    totals: SalesTotalsOut
    caveats: list[str]


class SalesDayIn(In):
    date: dt.date
    card_pence: Pence | None = Field(default=None, ge=0)
    cash_till_pence: Pence | None = Field(default=None, ge=0)
    cash_off_till_pence: Pence | None = Field(default=None, ge=0)
    orders_override: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=2000)
    operator: str | None = Field(default=None, max_length=120)


class SalesDayPatch(In):
    """Only the fields sent change. An explicit null clears a figure."""

    date: dt.date | None = None
    card_pence: Pence | None = Field(default=None, ge=0)
    cash_till_pence: Pence | None = Field(default=None, ge=0)
    cash_off_till_pence: Pence | None = Field(default=None, ge=0)
    orders_override: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=2000)
    operator: str | None = Field(default=None, max_length=120)


class DeletedOut(Out):
    deleted: str


# --------------------------------------------------------------------------
# expenses
# --------------------------------------------------------------------------


class ExpenseOut(Out):
    id: int
    date: dt.date
    category_id: int
    category: str
    group: Literal["COGS", "OPEX"]
    description: str
    amount_pence: Pence
    method: ExpenseMethodName | None
    kind: ExpenseKindName
    has_receipt: bool
    notes: str | None
    needs_review: bool
    supplier_id: int | None
    director_entry_id: int | None
    source: str
    source_ref: str | None
    updated_at: dt.datetime
    updated_by: str | None


class ExpensesResponse(Out):
    period: str
    expenses: list[ExpenseOut]
    count: int
    total_pence: Pence
    caveats: list[str]


class ExpenseIn(In):
    date: dt.date
    category_id: int
    description: str = Field(min_length=1, max_length=300)
    amount_pence: Pence = Field(gt=0)
    method: ExpenseMethodName | None = None
    kind: ExpenseKindName = "OPERATING"
    has_receipt: bool = False
    notes: str | None = Field(default=None, max_length=2000)
    needs_review: bool = False
    operator: str | None = Field(default=None, max_length=120)


class ExpensePatch(In):
    date: dt.date | None = None
    category_id: int | None = None
    description: str | None = Field(default=None, max_length=300)
    amount_pence: Pence | None = Field(default=None, gt=0)
    method: ExpenseMethodName | None = None
    kind: ExpenseKindName | None = None
    has_receipt: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)
    needs_review: bool | None = None
    operator: str | None = Field(default=None, max_length=120)


class ExpenseDeletedOut(Out):
    deleted: int
    undo_token: str


class ExpenseRestoreIn(In):
    undo_token: str
    operator: str | None = Field(default=None, max_length=120)


# --------------------------------------------------------------------------
# reconcile
# --------------------------------------------------------------------------


class CardRowOut(Out):
    sold_on: dt.date
    card_pence: Pence
    due_on: dt.date
    expected_pence: Pence
    arrived_pence: Pence | None = Field(description="Null = not recorded. Never assumed.")
    arrived_on: dt.date | None
    diff_pence: Pence | None
    status: Literal["not_yet_due", "not_recorded", "ok", "mismatch", "bank_basis"]
    note: str | None


class CardSectionOut(Out):
    rows: list[CardRowOut]
    expected_total_pence: Pence
    arrived_total_pence: Pence
    mismatched_days: int
    not_recorded_days: int
    not_yet_due_days: int
    bank_basis_days: int


class DeliveryRowOut(Out):
    month: str
    label: str
    channel: Literal["DELIVEROO", "JUST_EAT"]
    channel_label: str
    present: bool
    gross_pence: Pence | None
    commission_pence: Pence | None
    ads_pence: Pence | None
    kept_pence: Pence | None
    kept_bp: int | None
    source: str | None
    note: str | None


class CashRowOut(Out):
    date: dt.date
    till_pence: Pence | None
    counted_pence: Pence | None
    counted_by: str | None
    diff_pence: Pence | None
    status: Literal["not_counted", "no_till_figure", "spot_on", "within", "out"]
    explanation: str | None
    explained_by: str | None


class CashSectionOut(Out):
    rows: list[CashRowOut]
    days_with_cash: int
    net_diff_pence: Pence
    days_out: int
    days_out_unexplained: int


class ReconcileResponse(Out):
    period: str
    payout_lag_working_days: int
    card_fee_bp: int
    cash_tolerance_pence: Pence
    payout_tolerance_pence: Pence
    card: CardSectionOut
    delivery: list[DeliveryRowOut]
    cash: CashSectionOut
    caveats: list[str]


class PayoutIn(In):
    arrived_pence: Pence | None = Field(ge=0)
    arrived_on: dt.date | None = None
    operator: str | None = Field(default=None, max_length=120)


class ChannelStatementIn(In):
    gross_pence: Pence | None = Field(default=None, ge=0)
    commission_pence: Pence | None = Field(default=None, ge=0)
    ads_pence: Pence | None = Field(default=None, ge=0)
    operator: str | None = Field(default=None, max_length=120)


class ChannelUploadIn(In):
    """The CSV text itself (the browser reads the file). No multipart dependency."""

    channel: Literal["DELIVEROO", "JUST_EAT"]
    filename: str = Field(max_length=200)
    text: str


class ChannelUploadOut(Out):
    inserted: int
    updated: int
    rejected: list[str]
    unmapped: list[str]
    notes: list[str]


class CashCountIn(In):
    counted_pence: Pence | None = Field(ge=0)
    counted_by: str | None = Field(default=None, max_length=120)


class CashExplainIn(In):
    explanation: str = Field(min_length=1, max_length=2000)
    explained_by: str = Field(min_length=1, max_length=120)


# --------------------------------------------------------------------------
# director
# --------------------------------------------------------------------------


class DirectorEntryOut(Out):
    id: int
    date: dt.date
    type: DirectorTypeName
    description: str
    in_pence: Pence
    out_pence: Pence
    balance_pence: Pence = Field(
        description="Running 'company owes you' after this row; capital does not move it."
    )
    counts_toward_owed: bool
    notes: str | None
    expense_id: int | None = Field(description="Set = mirrored from an expense; read-only here.")
    source: str
    source_ref: str | None


class DirectorResponse(Out):
    entries: list[DirectorEntryOut]
    put_in_pence: Pence
    taken_out_pence: Pence
    capital_in_pence: Pence = Field(description="Share capital: equity, not owed back.")
    loans_in_pence: Pence
    loan_balance_pence: Pence = Field(
        description="+ company owes you, - you owe the company. Capital excluded (DECISIONS 4)."
    )
    workbook_balance_pence: Pence = Field(description="The workbook's in - out, capital included.")
    mirrored_count: int
    caveats: list[str]


class DirectorEntryIn(In):
    date: dt.date
    type: DirectorTypeName
    description: str = Field(min_length=1, max_length=300)
    in_pence: Pence = Field(default=0, ge=0)
    out_pence: Pence = Field(default=0, ge=0)
    notes: str | None = Field(default=None, max_length=2000)
    operator: str | None = Field(default=None, max_length=120)


class DirectorEntryPatch(In):
    date: dt.date | None = None
    type: DirectorTypeName | None = None
    description: str | None = Field(default=None, max_length=300)
    in_pence: Pence | None = Field(default=None, ge=0)
    out_pence: Pence | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)
    operator: str | None = Field(default=None, max_length=120)


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------


class CashAlertOut(Out):
    date: dt.date
    diff_pence: Pence = Field(description="Signed: counted - till.")
    direction: Literal["short", "over"]
    message: str
    open_count: int = Field(description="Unexplained discrepancies over tolerance, in total.")
    tolerance_pence: Pence


class FinanceAlertsOut(Out):
    cash: CashAlertOut | None
    takings_last_imported_at: dt.datetime | None
