"""The payment-source protocol and the inert rows it returns.

Mirrors `integrations/channels/base.py` deliberately: a source *fetches and reports*,
it never writes. The service layer decides what to persist (spec 8), which is what
keeps a dry run honest -- `fetch` can be run against a live export with no risk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from cafeops.db.models.enums import PaymentMethod, PaymentSourceKind


class PaymentSourceError(Exception):
    """Something went wrong reaching or reading a payment report."""


class PaymentSourceUnavailable(PaymentSourceError):
    """The source could not be reached at all.

    Distinct from "read it and it was empty": a missing export is an operational
    fact to report, not a day on which the cafe took nothing.
    """


@dataclass(frozen=True, slots=True)
class PaymentDayRow:
    """One day's takings for one method, exactly as the report stated them.

    Every optional field is `None` when the export omitted it. A report that does
    not break out fees is not a report of zero fees (invariant 8).
    """

    business_date: date
    method: PaymentMethod
    gross_pence: int
    refunds_pence: int | None = None
    fees_pence: int | None = None
    discounts_pence: int | None = None
    transactions: int | None = None
    source_ref: str | None = None

    @property
    def net_pence(self) -> int | None:
        """`None` unless every deduction was reported. See `PaymentDay.net_pence`."""
        parts = (self.refunds_pence, self.fees_pence, self.discounts_pence)
        if any(p is None for p in parts):
            return None
        return self.gross_pence - sum(p for p in parts if p is not None)


@dataclass(frozen=True, slots=True)
class PaymentReport:
    """What one fetch found. Inert: nothing here has been written."""

    kind: PaymentSourceKind
    since: date
    until: date
    rows: tuple[PaymentDayRow, ...] = ()
    #: Rows the reader refused, with the reason. Counted, never silently dropped.
    rejected: tuple[str, ...] = ()
    #: Files that could not be mapped to a known shape. Refused, never guessed at.
    unmapped: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def gross_pence(self) -> int:
        return sum(r.gross_pence for r in self.rows)

    @property
    def days(self) -> int:
        return len({r.business_date for r in self.rows})


@runtime_checkable
class PaymentSource(Protocol):
    """Where a payment report comes from.

    One implementation today (`CsvPaymentSource`). A `LightspeedPaymentSource` would
    satisfy this without any caller changing, which is the point of the protocol --
    but it cannot be written until the endpoint's shape is known.
    """

    kind: PaymentSourceKind

    def fetch(self, *, since: date, until: date) -> PaymentReport:
        """Read the window. Writes nothing."""
        ...


def describe(rows: Sequence[PaymentDayRow]) -> str:
    """One sentence for a CLI or a bot message."""
    if not rows:
        return "no payment rows"
    days = len({r.business_date for r in rows})
    gross = sum(r.gross_pence for r in rows)
    methods = ", ".join(sorted({r.method.value for r in rows}))
    return f"{days} day(s), GBP {gross / 100:.2f} gross, methods: {methods}"


__all__ = [
    "PaymentDayRow",
    "PaymentReport",
    "PaymentSource",
    "PaymentSourceError",
    "PaymentSourceUnavailable",
    "describe",
]
