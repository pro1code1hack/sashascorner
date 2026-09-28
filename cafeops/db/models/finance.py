"""Finance: the back-office "Money" tabs. docs/design/specs/finance.md 3.3.

Everything here replaces the hand-maintained finance workbook: daily takings notes,
expenses, the director's account, cash counts, card payouts and monthly delivery-app
statements. Takings themselves stay in `payment_day` (one takings table, not two).

Rules shared by every table in this file:

- Money is integer pence. `None` means "not reported", never zero (invariant 8).
- Business dates are `Date` in Europe/London; `*_at` columns are tz-aware UTC.
- Not effective-dated: finance rows are facts about the past, edited in place, with
  `updated_at`/`updated_by` and soft delete (`deleted_at`) where a row can be removed.
- `*_by` columns hold the per-device operator name (DECISIONS.md 6), not a login.
- No VAT column anywhere: VAT is an open question (DECISIONS.md 4). Do not add one.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow
from cafeops.db.models.enums import (
    ChannelSourceKind,
    DirectorEntryType,
    ExpenseGroup,
    ExpenseKind,
    ExpenseMethod,
    FinanceSource,
    SalesChannelName,
)


class TradingDay(Base):
    """The non-money parts of a Sales-tab row: the day's note and an orders override.

    The money lives in `payment_day` (CARD, CASH; legacy CASH_OFF_TILL rows are read
    as part of CASH, never written: DECISIONS 26). A "day" on the
    Sales tab is the union of `trading_day` and `payment_day` dates.
    """

    __tablename__ = "trading_day"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    note: Mapped[str | None] = mapped_column(Text)
    #: Typed order count. Wins over COUNT(DISTINCT receipt) from `sale` and over
    #: `payment_day.transactions` when set.
    transactions_override: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[FinanceSource] = mapped_column(enum_col(FinanceSource), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        CheckConstraint(
            "transactions_override IS NULL OR transactions_override >= 0",
            name="transactions_override_non_negative",
        ),
    )


class ExpenseCategory(Base):
    """The 13 workbook categories (seeded by the migration), grouped COGS/OPEX.

    "Square fees" is seeded as "Card fees" (DECISIONS.md 4); the workbook importer maps
    the old name. Names keep the workbook's em dash ("Stock — coffee").
    """

    __tablename__ = "expense_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    #: Column is `expense_group`, not the spec's `group`: GROUP is an SQL keyword.
    expense_group: Mapped[ExpenseGroup] = mapped_column(enum_col(ExpenseGroup), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )


class Expense(Base):
    """Money out of the business. Only `kind = OPERATING` enters the P&L."""

    __tablename__ = "expense"

    id: Mapped[int] = mapped_column(primary_key=True)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("expense_category.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    amount_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[ExpenseMethod | None] = mapped_column(enum_col(ExpenseMethod))
    kind: Mapped[ExpenseKind] = mapped_column(
        enum_col(ExpenseKind),
        nullable=False,
        default=ExpenseKind.OPERATING,
        server_default=ExpenseKind.OPERATING.name,
    )
    has_receipt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(Text)
    #: "Needs a look": set by the importer from /ask user|verify|\?\?/i, cleared by a person.
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    #: Lets the Orders "shop runs" view stop regex-matching descriptions.
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order.id"))
    source: Mapped[FinanceSource] = mapped_column(enum_col(FinanceSource), nullable=False)
    #: e.g. "Expenses!R42". The workbook importer is idempotent on this.
    source_ref: Mapped[str | None] = mapped_column(String(400))
    #: Soft delete. Every read filters `deleted_at IS NULL`.
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String(120))

    category: Mapped[ExpenseCategory] = relationship()

    __table_args__ = (
        CheckConstraint("amount_pence > 0", name="amount_positive"),
        Index("ix_expense_paid_on", "paid_on"),
        Index("ix_expense_kind_paid_on", "kind", "paid_on"),
        Index("ix_expense_needs_review", "needs_review"),
        Index("ix_expense_source_ref", "source_ref"),
    )


class CashCount(Base):
    """Cash counted in the drawer at the end of a trading day (Reconcile C, banner).

    Expected cash = `payment_day` CASH (+ any legacy CASH_OFF_TILL) gross for the date (TILL basis,
    resolved by source precedence). The difference is computed at read time and never
    stored; it is `None` when either side is missing.
    """

    __tablename__ = "cash_count"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    counted_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Required, like `stock_count.counted_by`: a count is somebody's signature.
    counted_by: Mapped[str] = mapped_column(String(120), nullable=False)
    counted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    #: Why the drawer was over/short. The banner shows the newest unexplained
    #: discrepancy over tolerance.
    explanation: Mapped[str | None] = mapped_column(Text)
    explained_by: Mapped[str | None] = mapped_column(String(120))
    explained_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source: Mapped[FinanceSource] = mapped_column(enum_col(FinanceSource), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("counted_pence >= 0", name="counted_non_negative"),
        # An explanation is somebody's statement, with a time.
        CheckConstraint(
            "explanation IS NULL OR (explained_by IS NOT NULL AND explained_at IS NOT NULL)",
            name="explanation_signed",
        ),
    )


class CardPayout(Base):
    """What the bank actually received for one trading day's card takings.

    No row = not recorded (never "arrived in full"). `arrived_pence = 0` is a recorded
    zero. A payout batching several days needs a split table -- out of scope.
    """

    __tablename__ = "card_payout"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The trading day the card money belongs to (not the arrival date).
    sold_on: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    arrived_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    arrived_on: Mapped[date | None] = mapped_column(Date)
    source: Mapped[FinanceSource] = mapped_column(enum_col(FinanceSource), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(400))
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (CheckConstraint("arrived_pence >= 0", name="arrived_non_negative"),)


class FinanceSetting(Base):
    """Single row (id = 1), seeded by the migration. Thresholds the view and the
    banner share, so the rule lives in one place."""

    __tablename__ = "finance_setting"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    payout_lag_working_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    #: Card fee in BASIS POINTS (175 = 1.75%). Never a float (invariant 11).
    card_fee_bp: Mapped[int] = mapped_column(
        Integer, nullable=False, default=175, server_default="175"
    )
    cash_tolerance_pence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=500, server_default="500"
    )
    payout_tolerance_pence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    #: Stock purchases as % of takings above which Overview flags it (3200 = 32%).
    stock_pct_threshold_bp: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3200, server_default="3200"
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("payout_lag_working_days BETWEEN 0 AND 10", name="payout_lag_range"),
        CheckConstraint("card_fee_bp BETWEEN 0 AND 1000", name="card_fee_range"),
        CheckConstraint("cash_tolerance_pence >= 0", name="cash_tolerance_non_negative"),
        CheckConstraint("payout_tolerance_pence >= 0", name="payout_tolerance_non_negative"),
        CheckConstraint("stock_pct_threshold_bp BETWEEN 0 AND 10000", name="stock_pct_range"),
    )


class ChannelStatement(Base):
    """A delivery app's figures for one month, typed or uploaded (Reconcile B, P&L).

    Read rule (services): statement row if present; else the sum of `channel_metric`
    days in the month only when every day with orders has all three figures; else the
    month is incomplete (`None` + caveat). Missing is never zero.

    `month` is the first day of the month. Enforced by the service, not a CHECK:
    extracting a day-of-month has no portable SQL spelling.
    """

    __tablename__ = "channel_statement"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[SalesChannelName] = mapped_column(enum_col(SalesChannelName), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    gross_pence: Mapped[int | None] = mapped_column(Integer)
    commission_pence: Mapped[int | None] = mapped_column(Integer)
    ad_spend_pence: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[ChannelSourceKind] = mapped_column(enum_col(ChannelSourceKind), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(400))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        UniqueConstraint("channel", "month", name="uq_channel_statement_channel_month"),
        CheckConstraint("gross_pence IS NULL OR gross_pence >= 0", name="gross_non_negative"),
        CheckConstraint(
            "commission_pence IS NULL OR commission_pence >= 0", name="commission_non_negative"
        ),
        CheckConstraint(
            "ad_spend_pence IS NULL OR ad_spend_pence >= 0", name="ad_spend_non_negative"
        ),
    )


class DirectorEntry(Base):
    """One movement on the director's account.

    `balance = sum(in) - sum(out)`. The API splits it: capital injections are equity and are
    shown separately, never in "company owes you" (DECISIONS.md 4).

    Drawings are stored ONCE. An expense with `kind = DRAWINGS` IS a director's-account
    out-movement: the service creates/updates/soft-deletes the linked row here
    (`expense_id`, unique) and mirrors amount/date/description, which are read-only in
    the Director table. The P&L reads `expense` (where drawings are excluded anyway)
    and the director balance reads `director_entry`; no aggregate reads both. This is
    what removes the workbook's 44 double-entered drawings.
    """

    __tablename__ = "director_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[DirectorEntryType] = mapped_column(enum_col(DirectorEntryType), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    in_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    out_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text)
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id"), unique=True)
    source: Mapped[FinanceSource] = mapped_column(enum_col(FinanceSource), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(400))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String(120))

    expense: Mapped[Expense | None] = relationship()

    __table_args__ = (
        CheckConstraint(
            "(in_pence > 0 AND out_pence = 0) OR (out_pence > 0 AND in_pence = 0)",
            name="one_direction",
        ),
        CheckConstraint(
            "(type IN ('CAPITAL_INJECTION', 'LOAN_TO_COMPANY') AND in_pence > 0) "
            "OR (type IN ('DRAWINGS', 'REPAYMENT') AND out_pence > 0)",
            name="type_matches_direction",
        ),
        # Only drawings mirror an expense.
        CheckConstraint("expense_id IS NULL OR type = 'DRAWINGS'", name="expense_link_is_drawings"),
        Index("ix_director_entry_date", "entry_date"),
    )


__all__ = [
    "CardPayout",
    "CashCount",
    "ChannelStatement",
    "DirectorEntry",
    "Expense",
    "ExpenseCategory",
    "FinanceSetting",
    "TradingDay",
]
