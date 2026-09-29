"""Query-string parsing shared by every router, so two routes cannot disagree on a word.

Each helper turns one raw query value into what a view takes, or raises an
`HTTPException(422)` whose `detail` names the value and what was expected. They run in
the router, before any database work.

`parse_as_of` is the one rule for "as of" across surfaces: the CLI's
`cli._common.parse_as_of` delegates to it and only re-words the error, so `today` means
the same instant on the command line and over HTTP.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from fastapi import HTTPException

from cafeops.config import settings

__all__ = ["HTTP_422", "as_of", "enum_list", "parse_as_of", "parse_before"]

#: Starlette renamed `HTTP_422_UNPROCESSABLE_ENTITY` to `..._CONTENT` and deprecated the
#: old name. The number is the stable thing; spelling it avoids a warning on one version
#: and an AttributeError on the other.
HTTP_422 = 422

#: Why `parse_as_of` refused, for a surface that wants its own wording.
AS_OF_WORDS = "'now', 'today', 'yesterday' or YYYY-MM-DD"


def parse_as_of(raw: str, *, tz: tzinfo) -> datetime:
    """`now` | `today` | `yesterday` | YYYY-MM-DD | an ISO-8601 instant -> a UTC instant.

    A bare date -- and `today` / `yesterday`, which are bare dates spelled as words --
    means the END of that local day (`time.max`), because "stock as of today" means after
    today's trade, not at midnight before it. An instant is taken as given; one without
    an offset is read in `tz`. Raises `ValueError` naming what it expected.
    """
    text = raw.strip()
    lowered = text.lower()
    if lowered == "now":
        return datetime.now(UTC)
    if lowered in {"today", "yesterday"}:
        day = datetime.now(tz).date()
        if lowered == "yesterday":
            day -= timedelta(days=1)
        return datetime.combine(day, time.max, tzinfo=tz).astimezone(UTC)
    if "t" in lowered or ":" in lowered:
        # Only tried when the string looks like an instant: `datetime.fromisoformat`
        # accepts a bare date too, and reading one as midnight would invert the
        # end-of-day rule above.
        try:
            instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"{raw!r}: expected {AS_OF_WORDS}, or a full ISO-8601 instant like "
                "2026-09-16T12:00:00Z"
            ) from exc
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=tz)
        return instant.astimezone(UTC)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{raw!r}: expected {AS_OF_WORDS}") from exc
    return datetime.combine(parsed, time.max, tzinfo=tz).astimezone(UTC)


def as_of(raw: str | None) -> datetime | None:
    """`?as_of=` for a route; None when absent. 422 on anything `parse_as_of` refuses."""
    if raw is None:
        return None
    try:
        return parse_as_of(raw, tz=settings.tz)
    except ValueError as exc:
        raise HTTPException(status_code=HTTP_422, detail=str(exc)) from exc


def enum_list[E: enum.Enum](
    raw: str | None,
    enum_type: type[E],
    *,
    upper: bool = True,
    detail: Callable[[str], str] | None = None,
) -> tuple[E, ...] | None:
    """A comma-separated list of enum member NAMES -> a tuple; None when absent or empty.

    Blank tokens are skipped. An unknown token is a 422 whose detail is `detail(token)`,
    by default "'X': expected one of A, B, C". `upper` upper-cases each token first.
    """
    if raw is None:
        return None
    out: list[E] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            out.append(enum_type[token.upper() if upper else token])
        except KeyError:
            message = (
                detail(part)
                if detail is not None
                else f"{part!r}: expected one of {', '.join(m.name for m in enum_type)}"
            )
            raise HTTPException(status_code=HTTP_422, detail=message) from None
    return tuple(out) or None


def parse_before(raw: str | None) -> datetime | None:
    """`?before=` as a UTC instant (keyset paging); a naive value is read as UTC."""
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_422, detail=f"{raw!r}: expected an ISO timestamp"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
