"""Expenses: create, edit, soft-delete, restore. Finance spec 1.3, 2.3, 3.5.

Drawings are stored ONCE. An expense with `kind = DRAWINGS` IS a director's-account
out-movement: this module keeps the linked `director_entry` (unique `expense_id`) in step
whenever an expense becomes, stays or stops being DRAWINGS, and when it is deleted or
restored. Nothing else writes those mirrored rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from cafeops.db.models.enums import (
    DirectorEntryType,
    ExpenseGroup,
    ExpenseKind,
    ExpenseMethod,
    FinanceSource,
)
from cafeops.db.models.finance import DirectorEntry, Expense, ExpenseCategory
from cafeops.services.finance.common import (
    UNSET,
    FinanceConflict,
    FinanceRefused,
    Period,
    clean_text,
    editor,
    month_range,
    now_utc,
    require_pence,
)
from cafeops.services.finance.common import Unset as _Unset

__all__ = [
    "NEEDS_REVIEW_RE",
    "ExpenseFilter",
    "ExpenseRow",
    "categories",
    "create_expense",
    "delete_expense",
    "expense_row",
    "kind_from_notes",
    "list_expenses",
    "restore_expense",
    "sync_director_mirror",
    "update_expense",
]

#: "Needs a look": the workbook's own markers for a row somebody should check.
NEEDS_REVIEW_RE = re.compile(r"ask user|verify|\?\?", re.IGNORECASE)
_CAPITAL_RE = re.compile(r"capital expenditure", re.IGNORECASE)
_DRAWINGS_RE = re.compile(r"director drawings|personal", re.IGNORECASE)


def kind_from_notes(notes: str | None) -> ExpenseKind:
    """The design's one-off classification, used by the workbook importer only."""
    text = notes or ""
    if _CAPITAL_RE.search(text):
        return ExpenseKind.CAPITAL
    if _DRAWINGS_RE.search(text):
        return ExpenseKind.DRAWINGS
    return ExpenseKind.OPERATING


@dataclass(frozen=True, slots=True)
class ExpenseRow:
    id: int
    date: date
    category_id: int
    category: str
    group: str
    description: str
    amount_pence: int
    method: str | None
    kind: str
    has_receipt: bool
    notes: str | None
    needs_review: bool
    supplier_id: int | None
    director_entry_id: int | None
    source: str
    source_ref: str | None
    updated_at: datetime
    updated_by: str | None


@dataclass(frozen=True, slots=True)
class ExpenseFilter:
    q: str | None = None
    #: 'all' | 'cogs' | a category id as text
    category: str = "all"
    #: 'all' | OPERATING | CAPITAL | DRAWINGS
    kind: str = "all"
    needs_review: bool = False
    no_receipt: bool = False


def categories(session: Session) -> list[ExpenseCategory]:
    return list(session.scalars(select(ExpenseCategory).order_by(ExpenseCategory.sort)))


def _mirror_ids(session: Session, expense_ids: list[int]) -> dict[int, int]:
    if not expense_ids:
        return {}
    rows = session.execute(
        select(DirectorEntry.expense_id, DirectorEntry.id).where(
            DirectorEntry.expense_id.in_(expense_ids), DirectorEntry.deleted_at.is_(None)
        )
    )
    return {eid: did for eid, did in rows if eid is not None}


def _to_row(e: Expense, director_entry_id: int | None) -> ExpenseRow:
    return ExpenseRow(
        id=e.id,
        date=e.paid_on,
        category_id=e.category_id,
        category=e.category.name,
        group=e.category.expense_group.value,
        description=e.description,
        amount_pence=e.amount_pence,
        method=e.method.value if e.method is not None else None,
        kind=e.kind.value,
        has_receipt=e.has_receipt,
        notes=e.notes,
        needs_review=e.needs_review,
        supplier_id=e.supplier_id,
        director_entry_id=director_entry_id,
        source=e.source.value,
        source_ref=e.source_ref,
        updated_at=e.updated_at,
        updated_by=e.updated_by,
    )


def list_expenses(
    session: Session, period: Period, flt: ExpenseFilter
) -> tuple[list[ExpenseRow], int]:
    """Filtered rows (newest first) and their total."""
    stmt = select(Expense).join(Expense.category).where(Expense.deleted_at.is_(None))
    if period is not None:
        first, last = month_range(period)
        stmt = stmt.where(Expense.paid_on >= first, Expense.paid_on <= last)
    if flt.q:
        needle = f"%{flt.q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Expense.description).like(needle),
                func.lower(func.coalesce(Expense.notes, "")).like(needle),
            )
        )
    if flt.category == "cogs":
        stmt = stmt.where(ExpenseCategory.expense_group == ExpenseGroup.COGS)
    elif flt.category != "all":
        try:
            cat_id = int(flt.category)
        except ValueError as exc:
            raise FinanceRefused("category: 'all', 'cogs' or a category id") from exc
        stmt = stmt.where(Expense.category_id == cat_id)
    if flt.kind != "all":
        try:
            stmt = stmt.where(Expense.kind == ExpenseKind[flt.kind.upper()])
        except KeyError as exc:
            raise FinanceRefused("kind: all, OPERATING, CAPITAL or DRAWINGS") from exc
    if flt.needs_review:
        stmt = stmt.where(Expense.needs_review.is_(True))
    if flt.no_receipt:
        stmt = stmt.where(Expense.has_receipt.is_(False))
    stmt = stmt.order_by(Expense.paid_on.desc(), Expense.id.desc())
    found = list(session.scalars(stmt))
    mirrors = _mirror_ids(session, [e.id for e in found if e.kind is ExpenseKind.DRAWINGS])
    rows = [_to_row(e, mirrors.get(e.id)) for e in found]
    return rows, sum(r.amount_pence for r in rows)


def _get(session: Session, expense_id: int, *, include_deleted: bool = False) -> Expense:
    e = session.get(Expense, expense_id)
    if e is None or (e.deleted_at is not None and not include_deleted):
        raise LookupError(f"no expense {expense_id}")
    return e


def expense_row(session: Session, expense_id: int) -> ExpenseRow:
    e = _get(session, expense_id)
    return _to_row(e, _mirror_ids(session, [e.id]).get(e.id))


def _category(session: Session, category_id: int) -> ExpenseCategory:
    cat = session.get(ExpenseCategory, category_id)
    if cat is None:
        raise FinanceRefused(f"category_id: no category {category_id}")
    if not cat.is_active:
        raise FinanceRefused(f"category_id: {cat.name} is no longer in use")
    return cat


def _description(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise FinanceRefused("description: say who was paid or what for")
    if len(text) > 300:
        raise FinanceRefused("description: 300 characters at most")
    return text


def sync_director_mirror(session: Session, e: Expense) -> DirectorEntry | None:
    """Make the director's account agree with this expense. Returns the live mirror."""
    mirror = session.scalars(
        select(DirectorEntry).where(DirectorEntry.expense_id == e.id)
    ).one_or_none()
    wants = e.kind is ExpenseKind.DRAWINGS and e.deleted_at is None
    if not wants:
        if mirror is not None and mirror.deleted_at is None:
            mirror.deleted_at = now_utc()
            mirror.updated_by = e.updated_by
        return None
    if mirror is None:
        mirror = DirectorEntry(
            expense_id=e.id,
            type=DirectorEntryType.DRAWINGS,
            entry_date=e.paid_on,
            description=e.description,
            in_pence=0,
            out_pence=e.amount_pence,
            source=e.source,
            updated_by=e.updated_by,
        )
        session.add(mirror)
    else:
        mirror.deleted_at = None
        mirror.type = DirectorEntryType.DRAWINGS
        mirror.entry_date = e.paid_on
        mirror.description = e.description
        mirror.in_pence = 0
        mirror.out_pence = e.amount_pence
    session.flush()
    return mirror


def create_expense(
    session: Session,
    *,
    paid_on: date,
    category_id: int,
    description: str,
    amount_pence: int,
    method: ExpenseMethod | None,
    kind: ExpenseKind,
    has_receipt: bool = False,
    notes: str | None = None,
    needs_review: bool = False,
    operator: str | None = None,
    source: FinanceSource = FinanceSource.MANUAL,
    source_ref: str | None = None,
) -> Expense:
    require_pence(amount_pence, "amount_pence", allow_zero=False)
    e = Expense(
        paid_on=paid_on,
        category=_category(session, category_id),
        description=_description(description),
        amount_pence=amount_pence,
        method=method,
        kind=kind,
        has_receipt=has_receipt,
        notes=clean_text(notes),
        needs_review=needs_review,
        source=source,
        source_ref=source_ref,
        updated_by=editor(operator),
    )
    session.add(e)
    session.flush()
    sync_director_mirror(session, e)
    return e


def update_expense(
    session: Session,
    expense_id: int,
    *,
    paid_on: date | _Unset = UNSET,
    category_id: int | _Unset = UNSET,
    description: str | _Unset = UNSET,
    amount_pence: int | _Unset = UNSET,
    method: ExpenseMethod | _Unset | None = UNSET,
    kind: ExpenseKind | _Unset = UNSET,
    has_receipt: bool | _Unset = UNSET,
    notes: str | _Unset | None = UNSET,
    needs_review: bool | _Unset = UNSET,
    operator: str | None = None,
) -> Expense:
    e = _get(session, expense_id)
    if not isinstance(amount_pence, _Unset):
        require_pence(amount_pence, "amount_pence", allow_zero=False)
        e.amount_pence = amount_pence
    if not isinstance(paid_on, _Unset):
        e.paid_on = paid_on
    if not isinstance(category_id, _Unset):
        e.category = _category(session, category_id)
    if not isinstance(description, _Unset):
        e.description = _description(description)
    if not isinstance(method, _Unset):
        e.method = method
    if not isinstance(kind, _Unset):
        e.kind = kind
    if not isinstance(has_receipt, _Unset):
        e.has_receipt = has_receipt
    if not isinstance(notes, _Unset):
        e.notes = clean_text(notes)
    if not isinstance(needs_review, _Unset):
        e.needs_review = needs_review
    e.updated_by = editor(operator)
    e.updated_at = now_utc()
    session.flush()
    sync_director_mirror(session, e)
    return e


def _undo_token(e: Expense) -> str:
    assert e.deleted_at is not None
    return f"{e.id}:{int(e.deleted_at.timestamp() * 1000)}"


def delete_expense(session: Session, expense_id: int, *, operator: str | None = None) -> str:
    """Soft delete. Returns the undo token that `restore_expense` needs."""
    e = _get(session, expense_id)
    e.deleted_at = now_utc()
    e.updated_by = editor(operator)
    session.flush()
    sync_director_mirror(session, e)
    return _undo_token(e)


def restore_expense(
    session: Session, expense_id: int, token: str, *, operator: str | None = None
) -> Expense:
    e = _get(session, expense_id, include_deleted=True)
    if e.deleted_at is None:
        raise FinanceConflict(f"expense {expense_id} is not deleted")
    if token != _undo_token(e):
        raise FinanceConflict(
            "that undo no longer matches: the expense was changed or deleted again since"
        )
    e.deleted_at = None
    e.updated_by = editor(operator)
    session.flush()
    sync_director_mirror(session, e)
    return e
