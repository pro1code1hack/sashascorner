"""Season reads. Spec 4.3.

Implements `SeasonRepository`. It exists because the ordering path needs two of its
answers and neither can be faked: which season an ingredient belongs to (so its order
can be capped at the remaining window, and its out-of-season history excluded from the
baseline), and what is running on a given day.

`for_ingredient` is the interesting one. A season lives on `variant_option`, not on the
ingredient -- "Pistachio is spring-only" is one row that creates nine sellable items
(spec 4.2) -- so the ingredient's season has to be reached through the option that uses
it. An ingredient used by both a seasonal and a non-seasonal option is **not** seasonal:
whole milk appearing in a pumpkin latte must not inherit pumpkin's window, or the
system stops ordering milk on 1 December.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import MenuItem, Season, VariantOption
from cafeops.domain.types import SeasonSpec

__all__ = ["SqlSeasonRepository"]


def _spec(row: Season) -> SeasonSpec:
    return SeasonSpec(
        season_id=row.id,
        name=row.name,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        is_recurring_annually=row.is_recurring_annually,
    )


class SqlSeasonRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, season_id: int) -> SeasonSpec | None:
        row = self.session.get(Season, season_id)
        return None if row is None else _spec(row)

    def list_all(self) -> list[SeasonSpec]:
        return [_spec(r) for r in self.session.scalars(select(Season).order_by(Season.name))]

    def active_on(self, day: date) -> list[SeasonSpec]:
        """Seasons covering `day`.

        Filtered in Python through `SeasonSpec.contains` rather than with a date
        comparison in SQL. A recurring season that wraps the new year cannot be expressed
        as `starts_on <= day <= ends_on` -- that predicate returns False for every day of
        a 15 Nov - 28 Feb season -- and having two implementations of the wrap rule is how
        they come to disagree (`ARCHITECTURE.md` 8F.6).
        """
        return [spec for spec in self.list_all() if spec.contains(day)]

    def for_menu_item(self, menu_item_id: int) -> SeasonSpec | None:
        row = self.session.get(MenuItem, menu_item_id)
        if row is None or row.season_id is None:
            return None
        return self.get(row.season_id)

    def for_ingredient(self, ingredient_id: int) -> SeasonSpec | None:
        """The season of the variant options that use this ingredient, if they agree.

        `None` when the ingredient is used by any option with no season, or by options in
        two different seasons. Both cases mean the same thing for ordering: this
        ingredient is not exclusively seasonal, so capping its order at one season's
        window would starve every other use of it.
        """
        rows = list(
            self.session.scalars(
                select(VariantOption).where(VariantOption.ingredient_id == ingredient_id)
            )
        )
        if not rows:
            return None
        season_ids = {row.season_id for row in rows}
        if len(season_ids) != 1:
            return None
        season_id = season_ids.pop()
        if season_id is None:
            return None
        return self.get(season_id)

    def seasons_by_ingredient(self) -> dict[int, SeasonSpec]:
        """Every seasonal ingredient in one pass, for an order run.

        Same rule as `for_ingredient`, applied in bulk: an ingredient reached by options
        that disagree, or by any option with no season, is left out of the mapping rather
        than assigned one of them.
        """
        by_ingredient: dict[int, set[int | None]] = {}
        for row in self.session.scalars(select(VariantOption)):
            if row.ingredient_id is None:
                continue
            by_ingredient.setdefault(row.ingredient_id, set()).add(row.season_id)
        specs = {spec.season_id: spec for spec in self.list_all()}
        resolved: dict[int, SeasonSpec] = {}
        for ingredient_id, season_ids in by_ingredient.items():
            if len(season_ids) != 1:
                continue
            season_id = next(iter(season_ids))
            if season_id is None:
                continue
            spec = specs.get(season_id)
            if spec is not None:
                resolved[ingredient_id] = spec
        return resolved
