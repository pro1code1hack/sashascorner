"""The menu margin screen. Spec 5.6 -- and invariant 8, which is the whole difficulty.

Spec 10: *"menu margin (velocity x margin scatter, y-axis toggle to margin-per-minute;
the disagreement is the finding)"*. So both orderings are returned in full, along with
`ranked` (each item's position in both) and `biggest_disagreements`. No blended score is
offered: averaging margin % with margin-per-minute answers neither question the owner is
asking and hides the disagreement that is the finding.

**Invariant 8 is the reason this view does more than call one service.**
`services.menu_margin` reads the materialised cost cache, and an item whose recipe does
not resolve has no row in that cache at all -- so it is absent rather than flagged, and
an item missing from the margin screen is indistinguishable from an item nobody sells.
In the seeded workbook that is `'card' (GBP 3.00)` and `Syrup Gift Set`, both already on
the data-quality list (ARCHITECTURE 8, 8.1). `uncosted_items` finds them by subtracting
the cache from `menu_item` and returns them with `cost.pence = null` and the reason, so
they are **returned, flagged, and excluded from every total** rather than quietly
dropped. `excluded` does the same job for items that cannot be *ranked* -- no prep time,
no rate, no price -- which is a different absence with a different fix.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.api.encoding import as_pence, as_qty, pct
from cafeops.api.schemas import (
    Cost,
    MarginItemOut,
    MarginResponse,
    RankedItemOut,
    RollupOut,
    UncostedItemOut,
)
from cafeops.api.views.common import cost_unknown
from cafeops.db.models import MenuItem, MenuItemCost
from cafeops.domain.labour import ItemLabour, LabourRollup, RankedItem
from cafeops.domain.types import PriceSource
from cafeops.services.menu_margin import MARGIN_WINDOW_DAYS, menu_margin

__all__ = ["margin_view"]

_UNCOSTED_REASON = (
    "no row in menu_item_cost: this item's recipe does not resolve, so its cost is "
    "UNKNOWN, not zero (invariant 8). It is listed here rather than omitted, and it is "
    "excluded from every total and both rankings on this response. Both items in the "
    "seeded workbook -- 'card' (GBP 3.00) and Syrup Gift Set -- are known data-quality "
    "defects (ARCHITECTURE 8.1) and need fixing in the spreadsheet, not in code."
)


def _item_cost(item: ItemLabour) -> Cost:
    """A `Cost` from a ranked item.

    `ItemLabour` carries the cost but not its source -- `LabourCost` is the arithmetic
    and deliberately knows nothing about provenance. The source therefore comes from the
    cache row, which `margin_view` looks up once and passes in.
    """
    missing = item.ingredient_cost_pence is None
    return Cost(
        pence=as_pence(item.ingredient_cost_pence),
        is_missing=missing,
        excluded_from_aggregates=missing,
        note=(
            "at least one ingredient in this item is unpriced, so its cost is UNKNOWN "
            "and it is excluded from the rankings and the rollups (invariant 8)."
            if missing
            else None
        ),
    )


def _margin_item(item: ItemLabour, sources: dict[int, PriceSource | None]) -> MarginItemOut:
    cost = _item_cost(item)
    source = sources.get(item.menu_item_id)
    return MarginItemOut(
        menu_item_id=item.menu_item_id,
        name=item.name,
        size_code=item.size_code.value if item.size_code is not None else None,
        label=item.label,
        template_id=item.template_id,
        template_name=item.template_name,
        price_pence=item.price_pence,
        cost=cost.model_copy(
            update={
                "source": source.value if source is not None else None,
                "is_estimate": source is PriceSource.ESTIMATE,
            }
        ),
        units_sold=as_qty(item.units_sold) or "0",
        prep_seconds=item.prep.seconds,
        prep_source=item.prep.source.value,
        prep_is_estimate=item.prep.is_estimate,
        labour_cost_pence=as_pence(item.labour.labour_cost_pence),
        margin_pct=pct(item.margin_pct),
        true_margin_pct=pct(item.true_margin_pct),
        true_margin_pence=as_pence(item.labour.true_margin_pence),
        contribution_pence=as_pence(item.contribution_pence),
        margin_per_minute_pence=as_pence(item.margin_per_minute_pence),
    )


def _ranked(entry: RankedItem) -> RankedItemOut:
    return RankedItemOut(
        menu_item_id=entry.item.menu_item_id,
        label=entry.item.label,
        margin_rank=entry.margin_rank,
        margin_per_minute_rank=entry.margin_per_minute_rank,
        rank_delta=entry.rank_delta,
        margin_pct=pct(entry.item.margin_pct),
        margin_per_minute_pence=as_pence(entry.item.margin_per_minute_pence),
        prep_seconds=entry.item.prep.seconds,
    )


def _rollup(rollup: LabourRollup) -> RollupOut:
    return RollupOut(
        label=rollup.label,
        template_id=rollup.template_id,
        items_total=rollup.items_total,
        items_included=rollup.items_included,
        units_total=as_qty(rollup.units_total) or "0",
        staff_hours=as_qty(rollup.staff_hours),
        prep_seconds_total=as_qty(rollup.prep_seconds_total),
        labour_cost_pence_total=as_pence(rollup.labour_cost_pence_total),
        ingredient_cost_pence_total=as_pence(rollup.ingredient_cost_pence_total),
        revenue_pence_total=as_pence(rollup.revenue_pence_total),
        contribution_pence_total=as_pence(rollup.contribution_pence_total),
        true_margin_pence_total=as_pence(rollup.true_margin_pence_total),
        margin_per_minute_pence=as_pence(rollup.margin_per_minute_pence),
        labour_share_of_revenue_pct=pct(rollup.labour_share_of_revenue_pct),
        is_complete=rollup.is_complete,
        excluded=tuple(rollup.excluded),
        summary=rollup.summary(),
    )


def _uncosted(session: Session) -> tuple[UncostedItemOut, ...]:
    """Menu items with no cache row at all. See the module docstring."""
    rows = session.scalars(
        select(MenuItem)
        .where(MenuItem.id.not_in(select(MenuItemCost.menu_item_id)))
        .order_by(MenuItem.name, MenuItem.size_code)
    ).all()
    return tuple(
        UncostedItemOut(
            menu_item_id=item.id,
            name=item.name,
            size_code=item.size_code.value if item.size_code is not None else None,
            price_pence=item.price_pence,
            active=item.active,
            cost=cost_unknown(_UNCOSTED_REASON),
            reason=item.data_quality_flag or _UNCOSTED_REASON,
        )
        for item in rows
    )


def margin_view(
    session: Session,
    *,
    template: str | None = None,
    window_days: int = MARGIN_WINDOW_DAYS,
    until: date | None = None,
    loaded_hourly_rate_pence: int | None = None,
) -> MarginResponse:
    view = menu_margin(
        session,
        template=template,
        window_days=window_days,
        until=until,
        loaded_hourly_rate_pence=loaded_hourly_rate_pence,
    )
    sources: dict[int, PriceSource | None] = dict(
        session.execute(select(MenuItemCost.menu_item_id, MenuItemCost.cost_source)).tuples().all()
    )

    ranking = view.ranking
    by_margin = tuple(_margin_item(item, sources) for item in ranking.by_margin_pct)
    by_minute = tuple(_margin_item(item, sources) for item in ranking.by_margin_per_minute)
    uncosted = _uncosted(session)

    warnings = list(view.warnings)
    if uncosted:
        warnings.append(
            f"{len(uncosted)} menu item(s) have NO cost at all and are listed in "
            "`uncosted_items` rather than ranked: "
            + ", ".join(item.name for item in uncosted)
            + ". Their cost is null, not zero, and they are in no total here (invariant 8)."
        )

    estimated = sum(1 for item in by_margin if item.cost.is_estimate)
    invoiced = sum(1 for item in by_margin if item.cost.source == PriceSource.INVOICE.value)
    if estimated:
        warnings.append(
            f"{estimated} of {len(by_margin)} ranked item(s) are costed from ESTIMATE "
            "prices (42 of 113 ingredient prices are estimates). Spec 8: the 46% COGS "
            "figure is untrustworthy while that stands, and every margin below inherits it."
        )

    return MarginResponse(
        window_days=view.window_days,
        since=view.since,
        until=view.until,
        loaded_hourly_rate_pence=view.loaded_hourly_rate_pence,
        rate_is_set=view.rate_is_set,
        items_costed=view.items_costed,
        rankable_count=ranking.rankable_count,
        orderings_agree=ranking.orderings_agree,
        by_margin_pct=by_margin,
        by_margin_per_minute=by_minute,
        ranked=tuple(_ranked(entry) for entry in ranking.ranked),
        biggest_disagreements=tuple(_ranked(entry) for entry in ranking.biggest_disagreements[:12]),
        excluded=tuple((item.label, reason) for item, reason in ranking.excluded),
        uncosted_items=uncosted,
        menu=_rollup(view.menu),
        by_template=tuple(_rollup(rollup) for rollup in view.by_template),
        estimated_cost_item_count=estimated,
        invoice_cost_item_count=invoiced,
        warnings=tuple(warnings),
    )
