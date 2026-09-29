"""Takings resolved per (day, method) by source precedence. Finance spec 3.5.

`payment_day` is unique on (business_date, method, source), so a MANUAL row and a CSV
row for the same day and method can coexist. They are two reports of ONE amount of
money, never two amounts: summing them doubles the day. Every reader resolves each
(day, method) to the row whose `source` comes first in `PAYMENT_SOURCE_PRECEDENCE`
and ignores the rest, surfacing a disagreement over £1 as a caveat.

This module is the only place that rule is written.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.db.models.payment import PaymentDay
from cafeops.domain.enums import (
    PAYMENT_SOURCE_PRECEDENCE,
    PaymentBasis,
    PaymentMethod,
    PaymentSourceKind,
)

__all__ = [
    "DISAGREEMENT_TOLERANCE_PENCE",
    "ResolvedDay",
    "ResolvedFigure",
    "last_imported_at",
    "rank",
    "resolve_takings",
]

#: Two sources disagreeing by more than this is worth a sentence.
DISAGREEMENT_TOLERANCE_PENCE = 100

_RANK = {kind: i for i, kind in enumerate(PAYMENT_SOURCE_PRECEDENCE)}


def rank(kind: PaymentSourceKind) -> int:
    """Lower wins. A source missing from the precedence tuple loses to all listed ones."""
    return _RANK.get(kind, len(PAYMENT_SOURCE_PRECEDENCE))


@dataclass(frozen=True, slots=True)
class ResolvedFigure:
    """The winning row for one (day, method)."""

    row: PaymentDay
    #: The rows that lost, kept so a caller can say "also reported by ...".
    shadowed: tuple[PaymentDay, ...] = ()

    @property
    def gross_pence(self) -> int:
        return self.row.gross_pence

    @property
    def source(self) -> PaymentSourceKind:
        return self.row.source

    @property
    def basis(self) -> PaymentBasis:
        return self.row.basis


@dataclass(slots=True)
class ResolvedDay:
    business_date: date
    by_method: dict[PaymentMethod, ResolvedFigure] = field(default_factory=dict)

    def gross(self, method: PaymentMethod) -> int | None:
        fig = self.by_method.get(method)
        return None if fig is None else fig.gross_pence

    @property
    def till_total_pence(self) -> int:
        """Card + cash (the Sales-tab total; cash includes any legacy CASH_OFF_TILL row).

        Delivery apps excluded.
        """
        return sum(
            self.gross(m) or 0
            for m in (PaymentMethod.CARD, PaymentMethod.CASH, PaymentMethod.CASH_OFF_TILL)
        )

    @property
    def has_any(self) -> bool:
        return bool(self.by_method)


def resolve_takings(
    session: Session, *, since: date | None = None, until: date | None = None
) -> tuple[dict[date, ResolvedDay], list[str]]:
    """Resolve every (day, method) in the window. Returns days and disagreement caveats."""
    stmt = select(PaymentDay)
    if since is not None:
        stmt = stmt.where(PaymentDay.business_date >= since)
    if until is not None:
        stmt = stmt.where(PaymentDay.business_date <= until)
    grouped: dict[tuple[date, PaymentMethod], list[PaymentDay]] = defaultdict(list)
    for row in session.scalars(stmt):
        grouped[(row.business_date, row.method)].append(row)

    days: dict[date, ResolvedDay] = {}
    caveats: list[str] = []
    for (day, method), rows in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1].value)):
        rows.sort(key=lambda r: rank(r.source))
        winner, losers = rows[0], tuple(rows[1:])
        days.setdefault(day, ResolvedDay(business_date=day)).by_method[method] = ResolvedFigure(
            row=winner, shadowed=losers
        )
        for loser in losers:
            gap = abs(loser.gross_pence - winner.gross_pence)
            if gap > DISAGREEMENT_TOLERANCE_PENCE:
                caveats.append(
                    f"{day.isoformat()} {method.value}: {winner.source.value} says "
                    f"{winner.gross_pence}p, {loser.source.value} says {loser.gross_pence}p; "
                    f"{winner.source.value} is used and the other is ignored, not added"
                )
    return days, caveats


def last_imported_at(session: Session) -> datetime | None:
    """Newest takings write of any kind. The shell's freshness line reads this."""
    return session.scalar(select(func.max(PaymentDay.imported_at)))
