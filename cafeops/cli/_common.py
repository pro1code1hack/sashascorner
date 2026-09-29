"""Helpers shared by more than one command module: the console, date parsing, money."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import typer
from rich.console import Console

from cafeops.config import settings
from cafeops.db.base import new_session

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cafeops.domain.types import IngredientSnapshot

# Wide enough that ingredient names and quantities are never truncated.
console = Console(width=120)


@contextmanager
def unit_of_work(*, commit: bool) -> Iterator[Session]:
    """A command's one transaction. `commit=False` is a real dry run: every write the
    body makes is rolled back at the end, so a preview sees exactly what a commit would
    have written and persists none of it (ARCHITECTURE 8V: a dry run must not be a
    rollback the body cannot see). Any exception rolls back and propagates."""
    session = new_session()
    try:
        yield session
        if commit:
            session.commit()
        else:
            session.rollback()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def parse_as_of(raw: str) -> datetime:
    """`today` | `yesterday` | `now` | YYYY-MM-DD | an ISO-8601 instant -> a UTC instant.

    The rule is `api.params.parse_as_of`, shared with every `?as_of=` in the API so the
    two surfaces cannot drift: a bare date (and `today` / `yesterday`) is the END of that
    local day, because "stock as of today" means after today's trade.
    """
    from cafeops.api.params import parse_as_of as shared

    try:
        return shared(raw, tz=settings.tz)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def resolve_ingredient(session: Session, raw: str) -> IngredientSnapshot:
    """Accept an ingredient name or a numeric id. Exact names, no fuzzy matching:
    guessing which ingredient a count belongs to would corrupt the ledger silently."""
    from cafeops.db.repositories.ingredient import SqlIngredientRepository

    repo = SqlIngredientRepository(session)
    found = repo.get(int(raw)) if raw.strip().isdigit() else repo.get_by_name(raw.strip())
    if found is None:
        raise typer.BadParameter(f"no ingredient named {raw!r} (try `cafeops ingredients`)")
    return found


def money(pence: int | Decimal | None, *, signed: bool = False, dp: int = 3) -> str:
    """Pence -> pounds. 'unknown' when the cost is not known.

    Never '£0.00' for an unknown cost: invariant 6 turns on the difference between
    "costs nothing" and "we do not know what it costs".
    """
    if pence is None:
        return "[yellow]unknown[/yellow]"
    value = Decimal(str(pence)) / 100
    sign = "+" if (signed and value >= 0) else ("-" if signed else "")
    return f"{sign}GBP {abs(value) if signed else value:.{dp}f}"


def pence(value: int | Decimal | None, *, signed: bool = False) -> str:
    """A pence figure at one decimal place. 'unknown' when it is not known."""
    if value is None:
        return "[yellow]unknown[/yellow]"
    dec = Decimal(str(value))
    sign = "+" if (signed and dec >= 0) else ("-" if signed else "")
    shown = abs(dec) if signed else dec
    # Drop the decimal above 1000p: a whole cake at 3000p/min does not need a tenth
    # of a penny, and the column is narrower than the number is precise.
    places = 0 if abs(dec) >= 1000 else 1
    return f"{sign}{shown:.{places}f}p"
