from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from cafeops.clock import utcnow
from cafeops.db.types import Qty, UTCDateTime

ZERO = Decimal("0")


def enum_col(py_enum: type, **kw: object) -> SAEnum:
    """Store enums as their member NAME in a plain VARCHAR. Portable.

    No CHECK constraint is emitted (`create_constraint` is left False): SQLAlchemy 2
    validates on the way in (`validate_strings=True`), and a CHECK would turn every
    added member into a table rebuild on SQLite. The cost is that raw SQL can write
    any string; `doctor` is where that would surface.
    """
    return SAEnum(py_enum, native_enum=False, validate_strings=True, **kw)


class TimestampedMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


__all__ = ["ZERO", "Qty", "TimestampedMixin", "UTCDateTime", "enum_col", "utcnow"]
