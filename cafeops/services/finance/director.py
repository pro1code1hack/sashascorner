"""Director's account. Finance spec 1.6, 2.4; DECISIONS.md 4.

Two figures the workbook blurred are kept apart:

* **Capital injections** are share capital -- equity, not a debt. They are shown
  separately and never count toward "company owes you" (DECISIONS 4).
* **What is owed** = loans to the company, less repayments, less drawings. With no
  loans on record, drawings leave the director owing the company (an overdrawn
  director's loan account), and the screen says so plainly.

Drawings mirrored from an expense (`expense_id` set) are read-only here apart from the
note: amount, date and description belong to the expense (`services/finance/expenses`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.clock import utcnow
from cafeops.db.models.finance import DirectorEntry
from cafeops.domain.enums import DirectorEntryType, FinanceSource
from cafeops.services.finance.common import (
    UNSET,
    FinanceConflict,
    FinanceRefused,
    clean_text,
    editor,
    require_pence,
)
from cafeops.services.finance.common import Unset as _Unset

__all__ = [
    "DirectorRow",
    "DirectorSummary",
    "create_entry",
    "delete_entry",
    "read_director",
    "update_entry",
]

_IN_TYPES = frozenset({DirectorEntryType.CAPITAL_INJECTION, DirectorEntryType.LOAN_TO_COMPANY})


@dataclass(frozen=True, slots=True)
class DirectorRow:
    id: int
    date: date
    type: str
    description: str
    in_pence: int
    out_pence: int
    #: Running "company owes you" after this row. Capital injections do not move it.
    balance_pence: int
    #: Capital rows: equity, not owed.
    counts_toward_owed: bool
    notes: str | None
    expense_id: int | None
    source: str
    source_ref: str | None


@dataclass(frozen=True, slots=True)
class DirectorSummary:
    rows: list[DirectorRow]
    put_in_pence: int
    taken_out_pence: int
    capital_in_pence: int
    loans_in_pence: int
    loan_balance_pence: int
    #: The workbook's "in minus out" (capital included). Kept for reconciliation only.
    workbook_balance_pence: int
    mirrored_count: int
    caveats: list[str]


def _effect(e: DirectorEntry) -> int:
    """How this row moves what the company owes the director."""
    if e.type is DirectorEntryType.CAPITAL_INJECTION:
        return 0
    return e.in_pence - e.out_pence


def read_director(session: Session) -> DirectorSummary:
    entries = list(
        session.scalars(
            select(DirectorEntry)
            .where(DirectorEntry.deleted_at.is_(None))
            .order_by(DirectorEntry.entry_date, DirectorEntry.id)
        )
    )
    running = 0
    rows: list[DirectorRow] = []
    for e in entries:
        running += _effect(e)
        rows.append(
            DirectorRow(
                id=e.id,
                date=e.entry_date,
                type=e.type.value,
                description=e.description,
                in_pence=e.in_pence,
                out_pence=e.out_pence,
                balance_pence=running,
                counts_toward_owed=e.type is not DirectorEntryType.CAPITAL_INJECTION,
                notes=e.notes,
                expense_id=e.expense_id,
                source=e.source.value,
                source_ref=e.source_ref,
            )
        )
    put_in = sum(e.in_pence for e in entries)
    out = sum(e.out_pence for e in entries)
    capital = sum(e.in_pence for e in entries if e.type is DirectorEntryType.CAPITAL_INJECTION)
    loans = sum(e.in_pence for e in entries if e.type is DirectorEntryType.LOAN_TO_COMPANY)
    caveats: list[str] = []
    if capital:
        caveats.append(
            "Capital injections are share capital: they belong to the company and are not "
            "owed back, so they are shown apart and left out of what the company owes you."
        )
    if running < 0:
        caveats.append(
            "Drawings are more than any loans on record, so you owe the company. Ask the "
            "accountant how this is settled (salary, dividend or repayment)."
        )
    return DirectorSummary(
        rows=rows,
        put_in_pence=put_in,
        taken_out_pence=out,
        capital_in_pence=capital,
        loans_in_pence=loans,
        loan_balance_pence=running,
        workbook_balance_pence=put_in - out,
        mirrored_count=sum(1 for e in entries if e.expense_id is not None),
        caveats=caveats,
    )


def _validate(type_: DirectorEntryType, in_pence: int, out_pence: int) -> None:
    require_pence(in_pence, "in_pence")
    require_pence(out_pence, "out_pence")
    if (in_pence > 0) == (out_pence > 0):
        raise FinanceRefused("fill in exactly one of In or Out")
    if type_ in _IN_TYPES and in_pence == 0:
        raise FinanceRefused(f"{type_.value.replace('_', ' ').lower()} is money in: use In")
    if type_ not in _IN_TYPES and out_pence == 0:
        raise FinanceRefused(f"{type_.value.replace('_', ' ').lower()} is money out: use Out")


def _get(session: Session, entry_id: int) -> DirectorEntry:
    e = session.get(DirectorEntry, entry_id)
    if e is None or e.deleted_at is not None:
        raise LookupError(f"no director's account entry {entry_id}")
    return e


def _description(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise FinanceRefused("description: say what the money was")
    return text[:300]


def row_for(session: Session, entry_id: int) -> DirectorRow:
    for r in read_director(session).rows:
        if r.id == entry_id:
            return r
    raise LookupError(f"no director's account entry {entry_id}")


def create_entry(
    session: Session,
    *,
    entry_date: date,
    type_: DirectorEntryType,
    description: str,
    in_pence: int,
    out_pence: int,
    notes: str | None,
    operator: str | None = None,
) -> DirectorEntry:
    _validate(type_, in_pence, out_pence)
    e = DirectorEntry(
        entry_date=entry_date,
        type=type_,
        description=_description(description),
        in_pence=in_pence,
        out_pence=out_pence,
        notes=clean_text(notes),
        source=FinanceSource.MANUAL,
        updated_by=editor(operator),
    )
    session.add(e)
    session.flush()
    return e


_MIRRORED = "comes from the expense it mirrors; change it on the Expenses tab"


def update_entry(
    session: Session,
    entry_id: int,
    *,
    entry_date: date | _Unset = UNSET,
    type_: DirectorEntryType | _Unset = UNSET,
    description: str | _Unset = UNSET,
    in_pence: int | _Unset = UNSET,
    out_pence: int | _Unset = UNSET,
    notes: str | _Unset | None = UNSET,
    operator: str | None = None,
) -> DirectorEntry:
    e = _get(session, entry_id)
    if e.expense_id is not None:
        for name, value, current in (
            ("date", entry_date, e.entry_date),
            ("type", type_, e.type),
            ("description", description, e.description),
            ("in", in_pence, e.in_pence),
            ("out", out_pence, e.out_pence),
        ):
            if not isinstance(value, _Unset) and value != current:
                raise FinanceConflict(f"The {name} of this drawing {_MIRRORED}.")
    new_type = e.type if isinstance(type_, _Unset) else type_
    new_in = e.in_pence if isinstance(in_pence, _Unset) else in_pence
    new_out = e.out_pence if isinstance(out_pence, _Unset) else out_pence
    _validate(new_type, new_in, new_out)
    e.type, e.in_pence, e.out_pence = new_type, new_in, new_out
    if not isinstance(entry_date, _Unset):
        e.entry_date = entry_date
    if not isinstance(description, _Unset):
        e.description = _description(description)
    if not isinstance(notes, _Unset):
        e.notes = clean_text(notes)
    e.updated_by = editor(operator)
    e.updated_at = utcnow()
    session.flush()
    return e


def delete_entry(session: Session, entry_id: int, *, operator: str | None = None) -> int:
    e = _get(session, entry_id)
    if e.expense_id is not None:
        raise FinanceConflict(
            f"This drawing {_MIRRORED.replace('change it', 'delete it or change its type')}."
        )
    e.deleted_at = utcnow()
    e.updated_by = editor(operator)
    session.flush()
    return e.id
