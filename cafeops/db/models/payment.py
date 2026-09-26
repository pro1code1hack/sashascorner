"""Daily takings: the money that actually came in.

The owner's architecture sketch puts **"payment reports"** under the Lightspeed box,
beside stock management and best/least positions. It is the one item on those pages
with no counterpart in the written brief -- spec 4.5 models `sale` (what was ordered)
and never models what was *settled*. The two are not the same number, and the gap
between them is refunds, discounts, service charges and card fees.

Without this, `Money & P&L` can only ever be the purchase side: draft spend, supplier
terms and waste. It cannot say what the cafe took, so it cannot show contribution.

## Grain: one row per day per method, not per transaction

A payment report is a settlement summary, not a ledger. Ringing 40 transactions a day
produces 40 receipts already modelled as `sale`; what the back office exports is the
day's totals split by how it was paid. Modelling per-transaction would invite a second,
disagreeing source of truth for the same sales.

## Every row records how it arrived

Same rule as `ChannelMetric` (spec 4.6): a figure typed off a PDF and a figure pulled
from an API deserve different trust, and a silent mix of the two is unauditable.
`PaymentSourceKind.CSV_UPLOAD` is what exists today -- the Lightspeed payments endpoint
has never been probed and its shape is unknown, so nothing here pretends to have come
from it.

## Nullable money means "not reported", never zero

A report that omits fees is not a report of zero fees. Invariant 8 applies here as
everywhere: a missing figure stays missing, is excluded from aggregates, and the
exclusion is stated.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow
from cafeops.db.models.enums import PaymentBasis, PaymentMethod, PaymentSourceKind


class PaymentDay(Base):
    """One day's takings for one payment method.

    Several sources may hold a row for the same (date, method). They are NEVER summed:
    readers resolve with `enums.PAYMENT_SOURCE_PRECEDENCE` (finance spec 3.5).
    """

    __tablename__ = "payment_day"
    __table_args__ = (
        # One row per day per method per source: re-importing the same export
        # updates in place rather than doubling the day's takings.
        UniqueConstraint(
            "business_date", "method", "source", name="uq_payment_day_date_method_source"
        ),
        Index("ix_payment_day_date", "business_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    #: The cafe's trading day, not a UTC instant. A 23:50 sale belongs to that day.
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(enum_col(PaymentMethod), nullable=False)

    #: Taken before anything is deducted. The headline "we took X today".
    gross_pence: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Refunds are stored POSITIVE and subtracted at read time, so a report that
    #: omits them is distinguishable from one reporting none.
    refunds_pence: Mapped[int | None] = mapped_column(Integer)
    #: Card processing / acquirer fees. Invisible on the POS and easy to forget.
    fees_pence: Mapped[int | None] = mapped_column(Integer)
    #: Discounts and comps given away at the till.
    discounts_pence: Mapped[int | None] = mapped_column(Integer)

    transactions: Mapped[int | None] = mapped_column(Integer)

    #: TILL: what the till took that day. BANK_DEPOSIT: a settlement amount on its
    #: deposit date (the workbook's Sep-Mar card column). Finance spec 2.1/4.5 -- a
    #: deposit is never reconciled against itself as if it were takings.
    basis: Mapped[PaymentBasis] = mapped_column(
        enum_col(PaymentBasis),
        nullable=False,
        default=PaymentBasis.TILL,
        server_default=PaymentBasis.TILL.name,
    )

    source: Mapped[PaymentSourceKind] = mapped_column(enum_col(PaymentSourceKind), nullable=False)
    #: Which file or endpoint this came from, so a wrong figure is traceable.
    source_ref: Mapped[str | None] = mapped_column(String(400))
    notes: Mapped[str | None] = mapped_column(Text)

    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    @property
    def net_pence(self) -> int | None:
        """Gross less refunds, fees and discounts.

        `None` when any deduction was not reported: subtracting only the ones that
        happen to be present would flatter the figure, and a flattering net is
        exactly the number nobody should act on.
        """
        parts = (self.refunds_pence, self.fees_pence, self.discounts_pence)
        if any(p is None for p in parts):
            return None
        return self.gross_pence - sum(p for p in parts if p is not None)


__all__ = ["PaymentDay"]
