"""Persist a payment report, and read the takings back.

The service layer owns the write (spec 8). The source fetched and reported; this
decides what lands, and it is the only thing here that can touch the database.

**Idempotent on (date, method, source).** Re-importing an export that overlaps a
previous one updates those days in place rather than doubling them -- the same rule
`channel_sync` follows, and for the same reason: a hand export is re-downloaded far
more often than anyone admits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models.payment import PaymentDay
from cafeops.domain.enums import PaymentBasis, PaymentSourceKind
from cafeops.integrations.payments.base import PaymentReport
from cafeops.services.finance.takings import resolve_takings


@dataclass
class PaymentImportReport:
    since: date
    until: date
    kind: PaymentSourceKind
    inserted: int = 0
    updated: int = 0
    rejected: tuple[str, ...] = ()
    unmapped: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.since}..{self.until} via {self.kind.value}: "
            f"days +{self.inserted}/~{self.updated}, "
            f"{len(self.rejected)} row(s) rejected, {len(self.unmapped)} file(s) refused"
        )


def ingest_payments(session: Session, report: PaymentReport) -> PaymentImportReport:
    """Write what the source found. Nothing is inferred and nothing is summed here."""
    out = PaymentImportReport(
        since=report.since,
        until=report.until,
        kind=report.kind,
        rejected=report.rejected,
        unmapped=report.unmapped,
        notes=list(report.notes),
    )
    for row in report.rows:
        existing = session.scalars(
            select(PaymentDay).where(
                PaymentDay.business_date == row.business_date,
                PaymentDay.method == row.method,
                PaymentDay.source == report.kind,
            )
        ).one_or_none()
        if existing is None:
            session.add(
                PaymentDay(
                    business_date=row.business_date,
                    method=row.method,
                    gross_pence=row.gross_pence,
                    refunds_pence=row.refunds_pence,
                    fees_pence=row.fees_pence,
                    discounts_pence=row.discounts_pence,
                    transactions=row.transactions,
                    source=report.kind,
                    source_ref=row.source_ref,
                )
            )
            out.inserted += 1
        else:
            existing.gross_pence = row.gross_pence
            existing.refunds_pence = row.refunds_pence
            existing.fees_pence = row.fees_pence
            existing.discounts_pence = row.discounts_pence
            existing.transactions = row.transactions
            existing.source_ref = row.source_ref
            out.updated += 1
    session.flush()
    return out


@dataclass(frozen=True, slots=True)
class TakingsWindow:
    """What the cafe took over a window, and how much of it is actually known."""

    since: date
    until: date
    days_reported: int
    #: None when NOTHING reported. A window with no export is not a window in which
    #: the cafe took nothing, and `0` reads as the latter -- the same rule that makes
    #: `net_pence` null rather than partially subtracted (invariant 8).
    gross_pence: int | None
    #: None when ANY day omitted a deduction -- see `PaymentDay.net_pence`.
    net_pence: int | None
    refunds_pence: int | None
    fees_pence: int | None
    discounts_pence: int | None
    transactions: int | None
    by_method: dict[str, int]
    caveats: tuple[str, ...]


def read_takings(session: Session, *, since: date, until: date) -> TakingsWindow:
    """Sum the window, and say what could not be summed.

    Each (day, method) is resolved to ONE row by `PAYMENT_SOURCE_PRECEDENCE`
    (`services/finance/takings.py`): a MANUAL figure and a CSV figure for the same day
    are two reports of the same money, and summing them doubled the day. Lower-precedence
    rows are ignored, and a disagreement over £1 is stated as a caveat.

    A deduction missing on any single day makes the window's net unknowable rather
    than merely smaller: subtracting only the days that reported fees would produce
    a net that is too high and looks entirely plausible.
    """
    resolved, disagreements = resolve_takings(session, since=since, until=until)
    rows = [fig.row for day in resolved.values() for fig in day.by_method.values()]
    shadowed = sum(len(fig.shadowed) for day in resolved.values() for fig in day.by_method.values())
    caveats: list[str] = list(disagreements)
    if shadowed:
        caveats.append(
            f"{shadowed} row(s) from a lower-precedence source were ignored: one figure per "
            "day and method is used, never the sum of several reports of the same money"
        )
    if any(r.basis is PaymentBasis.BANK_DEPOSIT for r in rows):
        caveats.append(
            "some card figures are bank deposits on their deposit date (the legacy workbook), "
            "not what the till took that day"
        )
    gross: int | None = sum(r.gross_pence for r in rows) if rows else None
    by_method: dict[str, int] = {}
    for r in rows:
        by_method[r.method.value] = by_method.get(r.method.value, 0) + r.gross_pence

    def total(attr: str, label: str) -> int | None:
        values: list[int | None] = [getattr(r, attr) for r in rows]
        missing = sum(1 for v in values if v is None)
        if missing:
            caveats.append(
                f"{missing} of {len(rows)} row(s) did not report {label}; the total is "
                "withheld rather than summed from the rows that did"
            )
            return None
        return int(sum(v for v in values if v is not None))

    refunds = total("refunds_pence", "refunds")
    fees = total("fees_pence", "fees")
    discounts = total("discounts_pence", "discounts")
    net = (
        None
        if (gross is None or refunds is None or fees is None or discounts is None)
        else gross - refunds - fees - discounts
    )
    txns = total("transactions", "a transaction count")

    days = len({r.business_date for r in rows})
    window_days = (until - since).days + 1
    if rows and days < window_days:
        caveats.append(
            f"{days} of {window_days} day(s) in the window reported at all; the rest are "
            "absent, not zero, so this cannot be read as a period total"
        )

    return TakingsWindow(
        since=since,
        until=until,
        days_reported=days,
        gross_pence=gross,
        net_pence=net,
        refunds_pence=refunds,
        fees_pence=fees,
        discounts_pence=discounts,
        transactions=txns,
        by_method=by_method,
        caveats=tuple(caveats),
    )


__all__ = ["PaymentImportReport", "TakingsWindow", "ingest_payments", "read_takings"]
