"""The margin screen's data: the menu ranked two ways, and what is unavailable today.

Spec 5.6 and 4.3. Two questions, both answered from the materialised cache rather
than by re-resolving 318 recipes:

1. **Which items earn most?** Answered twice, on purpose. Margin % is what the menu
   was priced on; margin-per-minute is what matters when there is a queue, because
   the scarce resource at 11am is not money, it is the person behind the counter.
   `domain/labour.py` computes both orderings and this module feeds it.
2. **Which items should the menu be offering?** A seasonal option makes its leaves
   unavailable out of season -- while their recipes still resolve, because a sale that
   happened consumed what it consumed. See `domain/composition.py`.

Reads `menu_item_cost`, which is exactly what that cache is for (spec 5.5). It also
cross-checks the cached prep seconds against the live ones and says so when they
disagree: the cache is written by a rollup, and a prep time edited since the last
rollup would otherwise make this screen quietly wrong.

Nothing here writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import DrinkTemplate, MenuItem
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.menu_cost import CachedCost, SqlMenuCostRepository
from cafeops.domain.composition import ItemAvailability, availability_at
from cafeops.domain.labour import ItemLabour, LabourRollup, MarginRanking, rank_menu, rollup_labour
from cafeops.domain.types import LabourCost
from cafeops.domain.units import gbp_code
from cafeops.jobs.cost_rollup import configured_rate_pence

__all__ = ["MarginView", "menu_availability", "menu_margin"]

#: Spec 5.6's velocity window, matching the impact preview's COGS window so the two
#: screens are talking about the same 30 days.
MARGIN_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class MarginView:
    """Everything the margin screen renders, computed once.

    `ranking` holds both orderings; `menu` and `by_template` hold the weighted labour
    rollups. The rate is carried explicitly because it is the single input that makes
    every labour number either real or absent, and a screen showing labour without
    naming the rate behind it is a screen nobody can check.
    """

    ranking: MarginRanking
    menu: LabourRollup
    by_template: tuple[LabourRollup, ...] = ()
    loaded_hourly_rate_pence: int | None = None
    window_days: int = MARGIN_WINDOW_DAYS
    since: date | None = None
    until: date | None = None
    items_costed: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def rate_is_set(self) -> bool:
        return self.loaded_hourly_rate_pence is not None


def menu_margin(
    session: Session,
    *,
    template: str | None = None,
    window_days: int = MARGIN_WINDOW_DAYS,
    until: date | None = None,
    loaded_hourly_rate_pence: int | None = None,
) -> MarginView:
    """Assemble the margin view from the cost cache and the sales volume behind it.

    `loaded_hourly_rate_pence` overrides config, which is what makes "what if the
    rate went to GBP 15.50?" a one-argument question rather than a config edit. It is
    used for the RANKING only -- the cached `labour_cost_pence` on each row keeps the
    rate that was in force when it was computed, because that is the auditable figure
    and overwriting it here would be the retroactive rewrite the per-row rate exists
    to prevent.
    """
    rate = (
        loaded_hourly_rate_pence
        if loaded_hourly_rate_pence is not None
        else configured_rate_pence()
    )
    costs = SqlMenuCostRepository(session)
    composition = SqlCompositionRepository(session)

    rows = costs.list_all()
    if template is not None:
        template_id = composition.template_id_by_name(template)
        if template_id is None:
            raise LookupError(f"no template named {template!r}")
        keep = set(session.scalars(select(MenuItem.id).where(MenuItem.template_id == template_id)))
        rows = [row for row in rows if row.menu_item_id in keep]

    warnings: list[str] = []
    if rate is None:
        warnings.append(
            "NO loaded hourly rate is configured, so every labour figure below is the "
            "one CACHED on each row -- the rate that was in force at the last rollup, "
            "which is why the rate is stored per row. Items last rolled up without a "
            "rate show labour as UNKNOWN. Margin-per-minute is unaffected either way: "
            "it is contribution over prep minutes and does not involve the rate at all "
            "(spec 5.6). Set CAFEOPS_LOADED_HOURLY_RATE_PENCE (the owner confirmed "
            "GBP 14.50/hr) to compute it fresh."
        )
    elif rate != configured_rate_pence():
        warnings.append(
            f"ranked at an OVERRIDE rate of {gbp_code(rate)}/hr, not the configured "
            "rate. The cached labour cost on each row still carries the rate it was "
            "computed at."
        )

    item_ids = [row.menu_item_id for row in rows]
    prep_times = composition.prep_times(item_ids)
    template_ids = _template_ids(session, item_ids)
    template_names = _template_names(session, sorted(set(template_ids.values())))

    until = until or datetime.now(UTC).astimezone(settings.tz).date()
    since = until - timedelta(days=window_days - 1)
    volumes = costs.units_sold_bulk(item_ids, since=since, until=until)

    stale: list[str] = []
    items: list[ItemLabour] = []
    for row in rows:
        prep = prep_times.get(row.menu_item_id)
        if prep is None:  # pragma: no cover -- prep_times answers for every id asked
            continue
        # Compare only when the cached row actually recorded a prep time. A NULL cached
        # prep means the last rollup had no rate and cleared all four labour columns as
        # a set -- that is a missing rate, not a changed prep time, and reporting it as
        # staleness would send the reader to the wrong fix.
        if row.loaded_hourly_rate_pence is not None and prep.seconds != row.prep_seconds:
            stale.append(
                f"{row.name}: cached prep {row.prep_seconds}s but the live value is "
                f"{prep.describe()} -- run `cafeops cost-rollup`"
            )
        template_id = template_ids.get(row.menu_item_id)
        items.append(
            ItemLabour(
                # With no rate configured, the cached labour stands -- it carries the
                # rate it was computed at, and unsetting config does not rewrite
                # history. With a rate, recompute so a what-if override is honoured.
                labour=row.labour if rate is None else _relabour(row, rate),
                name=row.name,
                size_code=row.size_code,
                prep=prep,
                units_sold=volumes.get(row.menu_item_id, Decimal("0")),
                template_id=template_id,
                template_name=None if template_id is None else template_names.get(template_id),
            )
        )

    if stale:
        warnings.append(
            f"{len(stale)} item(s) have a prep time that has changed since the last cost "
            f"rollup, so their labour figures are STALE: {'; '.join(stale[:4])}"
            + (" ..." if len(stale) > 4 else "")
        )

    ranking = rank_menu(items)
    menu_rollup = rollup_labour(items, label="whole menu")

    grouped: dict[int, list[ItemLabour]] = {}
    for item in items:
        if item.template_id is None:
            continue
        grouped.setdefault(item.template_id, []).append(item)
    by_template = tuple(
        rollup_labour(
            group,
            label=template_names.get(tid, f"template #{tid}"),
            template_id=tid,
        )
        for tid, group in sorted(grouped.items())
    )

    return MarginView(
        ranking=ranking,
        menu=menu_rollup,
        by_template=by_template,
        loaded_hourly_rate_pence=rate,
        window_days=window_days,
        since=since,
        until=until,
        items_costed=len(items),
        warnings=tuple(warnings) + ranking.warnings + menu_rollup.warnings,
    )


def menu_availability(
    session: Session,
    *,
    on: date | None = None,
    menu_item_ids: Sequence[int] | None = None,
    unavailable_only: bool = False,
) -> list[ItemAvailability]:
    """What the MENU should offer on `on`, and why not, item by item.

    This is the half of the seasonal question that says no. `resolve_recipe` is not
    consulted and is unaffected: an out-of-season item still has a recipe, still costs
    what it costs, and a sale of one still depletes stock. Only its place on today's
    menu is in question.
    """
    on = on or datetime.now(UTC).astimezone(settings.tz).date()
    composition = SqlCompositionRepository(session)

    stmt = select(MenuItem).order_by(MenuItem.name, MenuItem.size_code)
    if menu_item_ids is not None:
        stmt = stmt.where(MenuItem.id.in_(list(menu_item_ids)))
    items = list(session.scalars(stmt))

    ids = [item.id for item in items]
    item_seasons = composition.item_seasons(ids)
    option_seasons = composition.option_season_details(ids)

    out: list[ItemAvailability] = []
    for item in items:
        answer = availability_at(
            menu_item_id=item.id,
            name=item.name,
            size_code=item.size_code,
            on=on,
            is_active=item.active,
            item_season=item_seasons.get(item.id),
            option_seasons=tuple(option_seasons.get(item.id, ())),
        )
        if unavailable_only and answer.is_available:
            continue
        out.append(answer)
    return out


# --------------------------------------------------------------------------


def _relabour(row: CachedCost, rate_pence: int) -> LabourCost:
    """The cached row's labour recomputed at a different rate, for a what-if ranking."""
    return LabourCost(
        menu_item_id=row.menu_item_id,
        prep_seconds=row.prep_seconds,
        loaded_hourly_rate_pence=rate_pence,
        ingredient_cost_pence=row.cost_pence,
        price_pence=row.price_pence,
    )


def _template_ids(session: Session, menu_item_ids: Sequence[int]) -> dict[int, int]:
    if not menu_item_ids:
        return {}
    rows = session.execute(
        select(MenuItem.id, MenuItem.template_id).where(
            MenuItem.id.in_(list(menu_item_ids)), MenuItem.template_id.is_not(None)
        )
    ).all()
    return {item_id: template_id for item_id, template_id in rows if template_id is not None}


def _template_names(session: Session, template_ids: Sequence[int]) -> dict[int, str]:
    if not template_ids:
        return {}
    rows = session.execute(
        select(DrinkTemplate.id, DrinkTemplate.name).where(DrinkTemplate.id.in_(list(template_ids)))
    ).all()
    return {int(tid): str(name) for tid, name in rows}
