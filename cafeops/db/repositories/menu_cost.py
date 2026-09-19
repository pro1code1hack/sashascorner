"""The materialised cost cache and the sales volume behind the COGS projection.

Implements `protocols.MenuCostRepository`. Written by `jobs/cost_rollup.py` and by
`services/edit_composition.py`; read by the margin screen, the P&L and the impact
preview. Spec 5.5: never 318 resolutions inside a request handler.

No business logic here. What the cost IS gets decided in `domain/composition.py`;
this module only stores the answer and counts what sold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import MenuItem, MenuItemCost, Sale
from cafeops.domain.labour import UNTIMED, PrepTime, labour_for
from cafeops.domain.types import LabourCost, PriceSource, ResolvedRecipe, SizeCode


@dataclass(frozen=True, slots=True)
class CachedCost:
    """One cached row, with the unknown cost left unknown.

    `protocols.MenuCostRepository.get` returns `(cost, source, has_missing)` and so
    cannot express "the row exists but the cost is unknown" -- the tuple's first
    element is typed `Decimal`. This dataclass can, and the CLI and the margin
    screen need it to, so `get_detail` exists alongside the protocol method.
    """

    menu_item_id: int
    name: str
    size_code: SizeCode | None
    price_pence: int
    cost_pence: Decimal | None
    cost_source: PriceSource | None
    has_missing_cost: bool
    ingredient_count: int
    computed_at: datetime
    resolved_at: datetime
    # --- labour (spec 5.6) -------------------------------------------------
    # All four are None together: no prep time or no rate means no labour figure,
    # and a labour cost from a guessed rate is a guess wearing a number's clothes.
    labour_cost_pence: Decimal | None = None
    prep_seconds: int | None = None
    loaded_hourly_rate_pence: int | None = None
    prep_seconds_is_estimate: bool | None = None

    @property
    def margin_pct(self) -> float | None:
        """None when the cost is unknown or the item is not priced for sale."""
        if self.cost_pence is None or self.price_pence <= 0:
            return None
        return float(
            (Decimal(self.price_pence) - self.cost_pence) / Decimal(self.price_pence) * 100
        )

    @property
    def labour(self) -> LabourCost:
        """The cached row as the domain's labour type. Spec 5.6's three figures.

        Rebuilt from the cache rather than recomputed from the recipe: the rate stored
        on the row is the rate that was in force when the cost was computed, so a
        later rate change does not retroactively rewrite what this item's margin was
        reported as.
        """
        return LabourCost(
            menu_item_id=self.menu_item_id,
            prep_seconds=self.prep_seconds,
            loaded_hourly_rate_pence=self.loaded_hourly_rate_pence,
            ingredient_cost_pence=self.cost_pence,
            price_pence=self.price_pence,
        )

    @property
    def true_margin_pct(self) -> float | None:
        true_margin = self.labour.true_margin_pence
        if true_margin is None or self.price_pence <= 0:
            return None
        return float(true_margin / Decimal(self.price_pence) * 100)

    @property
    def margin_per_minute_pence(self) -> Decimal | None:
        return self.labour.margin_per_minute_pence


class SqlMenuCostRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- the cache --------------------------------------------------------

    def get(self, menu_item_id: int) -> tuple[Decimal, PriceSource, bool] | None:
        """(cost_pence, source, has_missing_cost), or None.

        None means "no usable cached cost" -- either no row, or a row whose cost is
        unknown. Callers that must tell those apart use `get_detail`; collapsing
        them here is safe only because the alternative would be returning a number
        that is not a cost (invariant 6).
        """
        row = self.session.scalar(
            select(MenuItemCost).where(MenuItemCost.menu_item_id == menu_item_id)
        )
        if row is None or row.cost_pence is None or row.cost_source is None:
            return None
        return (row.cost_pence, row.cost_source, row.has_missing_cost)

    def get_detail(self, menu_item_id: int) -> CachedCost | None:
        row = self.session.execute(
            select(MenuItemCost, MenuItem)
            .join(MenuItem, MenuItem.id == MenuItemCost.menu_item_id)
            .where(MenuItemCost.menu_item_id == menu_item_id)
        ).first()
        return None if row is None else _detail(row[0], row[1])

    def list_all(self, *, only_missing: bool = False) -> list[CachedCost]:
        stmt = (
            select(MenuItemCost, MenuItem)
            .join(MenuItem, MenuItem.id == MenuItemCost.menu_item_id)
            .order_by(MenuItem.name, MenuItem.size_code)
        )
        if only_missing:
            stmt = stmt.where(MenuItemCost.has_missing_cost.is_(True))
        return [_detail(cost, item) for cost, item in self.session.execute(stmt).all()]

    def upsert(
        self,
        menu_item_id: int,
        recipe: ResolvedRecipe,
        computed_at: datetime,
        *,
        prep: PrepTime = UNTIMED,
        loaded_hourly_rate_pence: int | None = None,
    ) -> None:
        """Write the resolved cost and the labour figures through, unknowns included.

        `recipe.cost_pence` is None when any ingredient is unpriced and
        `recipe.cost_source` is the WEAKEST source among them. Both are stored as
        they come: an estimate stays an estimate and a missing cost stays missing
        (invariant 6). Nothing here substitutes a zero.

        The labour arguments are keyword-only with defaults so this still satisfies
        `protocols.MenuCostRepository.upsert` -- the protocol is integrator-owned and a
        signature change there would need raising, while an optional keyword needs
        nothing. Omitting them writes NULL labour, which is the honest result of
        calling a cost rollup that was not told the rate.

        `labour_cost_pence`, `prep_seconds` and `loaded_hourly_rate_pence` are written
        or cleared as a SET. Two of three would be a row nobody could interpret: a
        prep time with no rate beside it invites the reader to apply today's rate to
        last month's figure, which is exactly the retroactive rewrite storing the rate
        per row exists to prevent.
        """
        row = self.session.scalar(
            select(MenuItemCost).where(MenuItemCost.menu_item_id == menu_item_id)
        )
        if row is None:
            row = MenuItemCost(menu_item_id=menu_item_id)
            self.session.add(row)
        row.cost_pence = recipe.cost_pence
        row.cost_source = recipe.cost_source
        row.has_missing_cost = recipe.has_missing_cost
        row.ingredient_count = len(recipe.cost_breakdown)
        row.computed_at = computed_at
        row.resolved_at = recipe.resolved_at

        labour = labour_for(
            menu_item_id=menu_item_id,
            price_pence=0,  # price is irrelevant to labour_cost_pence itself
            ingredient_cost_pence=recipe.cost_pence,
            prep=prep,
            loaded_hourly_rate_pence=loaded_hourly_rate_pence,
        )
        cost = labour.labour_cost_pence
        if cost is None:
            # Clear all four rather than leaving a stale number beside a now-absent
            # rate. A half-populated labour row reads as a real one.
            row.labour_cost_pence = None
            row.prep_seconds = None
            row.loaded_hourly_rate_pence = None
            row.prep_seconds_is_estimate = None
            return
        row.labour_cost_pence = cost
        row.prep_seconds = labour.prep_seconds
        row.loaded_hourly_rate_pence = labour.loaded_hourly_rate_pence
        row.prep_seconds_is_estimate = prep.is_estimate

    def delete(self, menu_item_id: int) -> None:
        row = self.session.scalar(
            select(MenuItemCost).where(MenuItemCost.menu_item_id == menu_item_id)
        )
        if row is not None:
            self.session.delete(row)

    # -- volume -----------------------------------------------------------

    def units_sold(self, menu_item_id: int, *, since: date, until: date) -> Decimal:
        return self.units_sold_bulk([menu_item_id], since=since, until=until).get(
            menu_item_id, Decimal("0")
        )

    def units_sold_bulk(
        self, menu_item_ids: Sequence[int], *, since: date, until: date
    ) -> dict[int, Decimal]:
        """Net units sold per item over the LOCAL days [since, until].

        Local days, not UTC (ARCHITECTURE 6.4): a 23:30 BST sale belongs to that
        trading day. Voided lines are excluded and refunds keep their negative
        quantity, so the figure is what was actually sold and kept.

        Summed in Python because `Qty` is TEXT on SQLite and a SQL SUM() would
        coerce it through float (ARCHITECTURE 6.3).
        """
        if not menu_item_ids:
            return {}
        tz = settings.tz
        start = datetime.combine(since, time.min, tzinfo=tz)
        end = datetime.combine(until, time.max, tzinfo=tz)
        rows = self.session.execute(
            select(Sale.menu_item_id, Sale.qty).where(
                Sale.menu_item_id.in_(list(menu_item_ids)),
                Sale.voided.is_(False),
                Sale.sold_at >= start,
                Sale.sold_at <= end,
            )
        ).all()
        out: dict[int, Decimal] = {int(i): Decimal("0") for i in menu_item_ids}
        for menu_item_id, qty in rows:
            out[menu_item_id] = out.get(menu_item_id, Decimal("0")) + qty
        return out


def _detail(cost: MenuItemCost, item: MenuItem) -> CachedCost:
    return CachedCost(
        menu_item_id=cost.menu_item_id,
        name=item.name,
        size_code=item.size_code,
        price_pence=item.price_pence,
        cost_pence=cost.cost_pence,
        cost_source=cost.cost_source,
        has_missing_cost=cost.has_missing_cost,
        ingredient_count=cost.ingredient_count,
        computed_at=cost.computed_at,
        resolved_at=cost.resolved_at,
        labour_cost_pence=cost.labour_cost_pence,
        prep_seconds=cost.prep_seconds,
        loaded_hourly_rate_pence=cost.loaded_hourly_rate_pence,
        prep_seconds_is_estimate=cost.prep_seconds_is_estimate,
    )
