"""Café facts printed on the passes (back of the Apple pass, Google links module).

Read from the website's `site/web/src/data/info.json` when it is there, so the pass and
the site cannot disagree about opening hours; the constants are the fallback for an
image built without the site tree. Weekday 0 is Monday, as in the site's schema.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from cafeops.config import REPO_ROOT

INFO_JSON = REPO_ROOT / "site" / "web" / "src" / "data" / "info.json"
#: Typographic range dash, spelt as an escape so it cannot be mistaken for a hyphen.
DASH = "\u2013"
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class Cafe:
    name: str
    address: str
    phone: str
    lat: float
    lng: float
    hours: str
    website: str


def _clock(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{suffix}" if m == 0 else f"{h12}:{m:02d}{suffix}"


def format_hours(rows: list[dict[str, object]]) -> str:
    """Collapse consecutive days with the same hours: "Mon-Sat 9am-7pm, Sun 9am-5pm".

    (With en dashes in the output; hyphens here only to keep the docstring ASCII.)
    """
    by_day: dict[int, str] = {}
    for row in rows:
        day = int(str(row["weekday"]))
        if row.get("closed"):
            by_day[day] = "closed"
        else:
            by_day[day] = f"{_clock(str(row['open']))}{DASH}{_clock(str(row['close']))}"
    groups: list[tuple[int, int, str]] = []
    for day in range(7):
        text = by_day.get(day, "closed")
        if groups and groups[-1][2] == text and groups[-1][1] == day - 1:
            groups[-1] = (groups[-1][0], day, text)
        else:
            groups.append((day, day, text))
    parts = []
    for start, end, text in groups:
        days = _DAYS[start] if start == end else f"{_DAYS[start]}{DASH}{_DAYS[end]}"
        parts.append(f"{days} {text}")
    return ", ".join(parts)


@lru_cache(maxsize=1)
def cafe() -> Cafe:
    fallback = Cafe(
        name="Sasha's Corner",
        address="23 Commercial Street, Dundee DD1 3DD",
        phone="07398 433317",
        lat=56.4612158,
        lng=-2.9668779,
        hours=f"Mon{DASH}Sat 9am{DASH}7pm, Sun 9am{DASH}5pm",
        website="https://sashascorner.co.uk",
    )
    try:
        info = json.loads(INFO_JSON.read_text(encoding="utf-8"))
        addr = info["address"]
        return Cafe(
            name=info.get("name") or fallback.name,
            address=f"{addr['line1']}, {addr['city']} {addr['postcode']}",
            phone=info.get("phone") or fallback.phone,
            lat=float(info["geo"]["lat"]),
            lng=float(info["geo"]["lng"]),
            hours=format_hours(info["hours"]) or fallback.hours,
            website=fallback.website,
        )
    except (OSError, KeyError, ValueError, TypeError):
        return fallback


__all__ = ["Cafe", "cafe", "format_hours"]
