"""Takings for the Money screen -- the missing half of it.

`Money & P&L` could only ever show the purchase side (draft spend, supplier terms,
waste) because nothing modelled what the cafe *took*. This is the other half, and it
is deliberately not called a P&L: it is settled takings, which is one input to one.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from cafeops.api.schemas import TakingsResponse
from cafeops.config import settings
from cafeops.services.ingest_payments import read_takings

__all__ = ["takings_view"]

SOURCE_NOTE = (
    "Takings come from a back-office payment export (CSV). The Lightspeed payments "
    "endpoint has never been probed and its shape is unknown, so nothing here claims "
    "to have come from the POS API."
)


def takings_view(session: Session, *, days: int = 30, until: date | None = None) -> TakingsResponse:
    # The cafe's local trading day, not UTC's: a 23:50 sale belongs to that day.
    end = until or datetime.now(settings.tz).date()
    start = end - timedelta(days=days - 1)
    w = read_takings(session, since=start, until=end)
    return TakingsResponse(
        since=w.since,
        until=w.until,
        window_days=days,
        days_reported=w.days_reported,
        gross_pence=w.gross_pence,
        refunds_pence=w.refunds_pence,
        fees_pence=w.fees_pence,
        discounts_pence=w.discounts_pence,
        net_pence=w.net_pence,
        transactions=w.transactions,
        by_method_pence=dict(w.by_method),
        caveats=w.caveats,
        source_note=SOURCE_NOTE,
    )
