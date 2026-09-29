"""`cafeops.domain.composition`, the availability section.

Split from one 2,270-line module on 2026-09-29 (ARCHITECTURE 8Y). Import the public
names from the package; this module is an implementation detail of it.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from cafeops.domain.types import (
    MenuItemSpec,
    SeasonSpec,
    SizeCode,
)

# ==========================================================================
# Seasons (spec 4.3): resolution always answers, the MENU is what closes
# ==========================================================================


def _season_warnings(
    item: MenuItemSpec,
    at: datetime,
    option_seasons: Mapping[int, SeasonSpec] | None,
    item_season: SeasonSpec | None,
) -> list[str]:
    """Say when a resolution is out of season, without changing the answer.

    The wording matters as much as the check. "It still resolves" has to be on the
    warning, because the natural reading of an out-of-season warning is that
    something was skipped -- and nothing was.
    """
    day = at.date()
    out: list[str] = []

    if item_season is not None and not item_season.contains(day):
        out.append(
            f"{item.name!r} is out of season at {day.isoformat()} "
            f"({item_season.name}, {_window(item_season)}); it still resolves -- a sale "
            "that happened consumed what it consumed. The menu lists it as unavailable."
        )

    for option in item.options:
        season = (option_seasons or {}).get(option.option_id)
        if season is None or season.contains(day):
            continue
        out.append(
            f"variant option {option.name!r} is out of season at {day.isoformat()} "
            f"({season.name}, {_window(season)}); it still resolves -- resolution is a "
            "record of what a drink contains, not a decision about whether to sell it."
        )
    return out


def _window(season: SeasonSpec) -> str:
    span = f"{season.starts_on.strftime('%d %b')} to {season.ends_on.strftime('%d %b')}"
    return f"{span}, recurring" if season.is_recurring_annually else span


class Availability(enum.StrEnum):
    """Whether the MENU should offer an item today. Not whether it resolves."""

    AVAILABLE = "AVAILABLE"
    OUT_OF_SEASON = "OUT_OF_SEASON"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True, slots=True)
class OptionSeason:
    """A variant option and the season it belongs to, for the availability check."""

    option_id: int
    name: str
    season: SeasonSpec


@dataclass(frozen=True, slots=True)
class ItemAvailability:
    """The menu's answer for one item on one day, with the reason attached.

    `days_remaining` is the remaining span of the binding season when the item IS
    available, which is what the ordering path caps a cover window against (spec 5.4,
    invariant 4). It is None out of season, because "days left" of a season that is
    not running is not a number.
    """

    menu_item_id: int
    name: str
    size_code: SizeCode | None
    on: date
    availability: Availability
    reasons: tuple[str, ...] = ()
    binding_season: SeasonSpec | None = None
    days_remaining: int | None = None

    @property
    def is_available(self) -> bool:
        return self.availability is Availability.AVAILABLE

    @property
    def label(self) -> str:
        return f"{self.name} {self.size_code.value}" if self.size_code else self.name

    def describe(self) -> str:
        if self.is_available and self.binding_season is not None:
            left = "" if self.days_remaining is None else f", {self.days_remaining} day(s) left"
            return f"available ({self.binding_season.name}{left})"
        if self.is_available:
            return "available"
        return f"{self.availability.value}: {'; '.join(self.reasons)}"


def availability_at(
    *,
    menu_item_id: int,
    name: str,
    size_code: SizeCode | None,
    on: date,
    is_active: bool = True,
    item_season: SeasonSpec | None = None,
    option_seasons: Sequence[OptionSeason] = (),
) -> ItemAvailability:
    """Should the menu offer this item on `on`?

    This is the half of the seasonal question that DOES say no. An item is
    unavailable when it is inactive, when its own season is not running, or when any
    variant option it is built from is out of season -- a Pistachio Latte cannot be
    sold in September because the pistachio option is a spring option, even though
    the latte template runs all year.

    `resolve_recipe` is deliberately not consulted and deliberately unaffected. The
    recipe of an unavailable item is still a fact; its place on today's menu is not.

    When more than one season applies, the one with the FEWEST days remaining binds.
    That is the season that will stop the item first, so it is the one an order has
    to be capped against.
    """
    reasons: list[str] = []
    if not is_active:
        return ItemAvailability(
            menu_item_id=menu_item_id,
            name=name,
            size_code=size_code,
            on=on,
            availability=Availability.INACTIVE,
            reasons=("the menu item is marked inactive",),
        )

    seasons: list[SeasonSpec] = []
    if item_season is not None:
        seasons.append(item_season)
        if not item_season.contains(on):
            reasons.append(f"item season {item_season.name!r} ({_window(item_season)}) is not open")
    for option in option_seasons:
        seasons.append(option.season)
        if not option.season.contains(on):
            reasons.append(
                f"option {option.name!r} is {option.season.name!r} only ({_window(option.season)})"
            )

    if reasons:
        return ItemAvailability(
            menu_item_id=menu_item_id,
            name=name,
            size_code=size_code,
            on=on,
            availability=Availability.OUT_OF_SEASON,
            reasons=tuple(reasons),
        )

    remaining = [(s.days_remaining(on), s) for s in seasons]
    binding = min(
        ((days, s) for days, s in remaining if days is not None),
        key=lambda pair: pair[0],
        default=None,
    )
    return ItemAvailability(
        menu_item_id=menu_item_id,
        name=name,
        size_code=size_code,
        on=on,
        availability=Availability.AVAILABLE,
        binding_season=None if binding is None else binding[1],
        days_remaining=None if binding is None else binding[0],
    )
