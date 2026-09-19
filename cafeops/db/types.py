"""Portable column types.

Two problems this file solves, both of which would otherwise leak SQLite
specifics into application code:

1. SQLite has no native NUMERIC. SQLAlchemy's sqlite dialect round-trips
   Numeric through float, which silently destroys exactness -- unacceptable for
   stock quantities. `Qty` stores the decimal as TEXT on SQLite and as a real
   NUMERIC everywhere else.
2. SQLite has no timezone-aware timestamp. `UTCDateTime` enforces the spec rule
   that every stored timestamp is tz-aware UTC, on the way in and on the way out.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Dialect, Numeric, String
from sqlalchemy.types import TypeDecorator

# Stock quantities: 18 digits, 6 decimal places. Enough for 0.000001 KG and for
# a pallet of cups, with room to spare.
QTY_PRECISION = 18
QTY_SCALE = 6


class Qty(TypeDecorator[Decimal]):
    """An exact Decimal quantity that survives SQLite."""

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = QTY_PRECISION, scale: int = QTY_SCALE) -> None:
        super().__init__(precision=precision, scale=scale, asdecimal=True)
        self._scale = scale

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(40))
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            # Accept int and str. Never float -- a float here is a bug upstream,
            # and quietly accepting it is how exactness dies.
            if isinstance(value, float):
                raise TypeError(
                    "float assigned to a Qty column; use Decimal(str(x)) at the boundary"
                )
            value = Decimal(str(value))
        if dialect.name == "sqlite":
            return format(value, "f")
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp that is always tz-aware UTC in Python."""

    impl = DateTime
    cache_ok = True

    def __init__(self, timezone: bool = True) -> None:
        # `timezone` is accepted because Alembic autogeneration renders the
        # impl's kwargs back into migration files. It is always True here.
        super().__init__(timezone=True)

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected datetime, got {type(value)!r}")
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime assigned to a UTCDateTime column; "
                "all timestamps must be tz-aware UTC"
            )
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
