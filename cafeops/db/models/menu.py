"""Menu items, manual recipes, and the materialised cost cache."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime, enum_col
from cafeops.db.models.enums import MenuKind, MenuPriceSource, PriceSource, SizeCode

if TYPE_CHECKING:
    from cafeops.db.models.composition import DrinkTemplate
    from cafeops.db.models.ingredient import Ingredient
    from cafeops.db.models.media import MediaAsset


class MenuItem(Base):
    """A concrete sellable thing.

    Either template-driven (template_id + size_code + selected_options) or a
    one-off (manual_recipe=True, reading manual_recipe_line). Spec 6 expects
    roughly 60 of the 175 base items to be manual: cakes, bottled drinks,
    paninis, meal deals. That is correct, not a failure of the model.
    """

    __tablename__ = "menu_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable until matched to a Lightspeed product; unique when present.
    lightspeed_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))

    template_id: Mapped[int | None] = mapped_column(ForeignKey("drink_template.id"))
    size_code: Mapped[SizeCode | None] = mapped_column(enum_col(SizeCode))
    # {axis_id: option_id} as JSON. Keys are stringified ints -- JSON object keys
    # are always strings; the resolver coerces.
    selected_options: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)

    #: CACHE of the open `menu_item_price` row (spec C-3). Written only by the price
    #: service, in the same transaction that opens the dated row. Read history from
    #: `menu_item_price`, never from here.
    price_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    manual_recipe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Overrides the template's prep_seconds_by_size for this item (spec 4.2). The
    # override wins unconditionally -- an iced version of a hot drink genuinely takes
    # a different time, and a per-size template default that could silently beat an
    # explicit per-item number would make this field a lie.
    prep_seconds: Mapped[int | None] = mapped_column(Integer)
    # NULL: nothing recorded. True: an estimate (invariant 8). False: timed.
    prep_seconds_is_estimate: Mapped[bool | None] = mapped_column(Boolean)
    # Spec 4.3: a seasonal item is excluded from out-of-season forecasting and
    # capped by its remaining season days when ordering.
    season_id: Mapped[int | None] = mapped_column(ForeignKey("season.id"))

    # Surfaced on the data-quality screen (spec 6, 8.6). Set for things like the
    # item literally named "'card' (£3.00)" with zero cost.
    data_quality_flag: Mapped[str | None] = mapped_column(String(200))

    #: Free-text note shown in the Menu items drawer (spec M11), e.g. "Wholesale cake
    #: (CakeSmiths or similar)". Applies per product group: the service writes it to
    #: every size row of the same `name`.
    note: Mapped[str | None] = mapped_column(String(400))
    #: Photo (spec A5). Set on every size row of the group; a photo belongs to the
    #: product. `ON DELETE SET NULL` so an unreferenced-asset sweep cannot orphan rows.
    photo_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL")
    )

    template: Mapped[DrinkTemplate | None] = relationship()
    photo: Mapped[MediaAsset | None] = relationship()
    manual_lines: Mapped[list[ManualRecipeLine]] = relationship(
        back_populates="menu_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("name", "size_code", name="uq_menu_item_name_size"),
        Index("ix_menu_item_template", "template_id", "size_code"),
    )

    def __repr__(self) -> str:
        size = self.size_code.value if self.size_code else "-"
        return f"<MenuItem {self.id} {self.name!r} [{size}]>"


class ManualRecipeLine(Base):
    """A recipe line for a menu item with no template. Effective-dated like the rest."""

    __tablename__ = "manual_recipe_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_item.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(UTCDateTime)

    menu_item: Mapped[MenuItem] = relationship(back_populates="manual_lines")
    ingredient: Mapped[Ingredient] = relationship()

    __table_args__ = (Index("ix_manual_recipe_item_from", "menu_item_id", "effective_from"),)


class MenuItemPrice(Base):
    """Effective-dated sell price. Spec C-3 (invariant 3 by analogy).

    A price edit closes the open row (`effective_to = at`) and opens a new one from
    `at` (start of today, never retroactive), then refreshes `menu_item.price_pence`.
    Margin over a past window reads the price in force at each sale, not today's.
    At most one open row per item (partial unique index).
    """

    __tablename__ = "menu_item_price"

    id: Mapped[int] = mapped_column(primary_key=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_item.id"), nullable=False)
    price_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source: Mapped[MenuPriceSource] = mapped_column(enum_col(MenuPriceSource), nullable=False)
    #: Operator name (DECISIONS.md 6). NULL only for BACKFILL / imported rows.
    set_by: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(400))

    menu_item: Mapped[MenuItem] = relationship()

    __table_args__ = (
        CheckConstraint("price_pence >= 0", name="price_non_negative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from", name="window_ordered"
        ),
        CheckConstraint("source <> 'MANUAL' OR set_by IS NOT NULL", name="manual_price_signed"),
        Index("ix_menu_item_price_item_from", "menu_item_id", "effective_from"),
        Index(
            "uq_menu_item_price_one_open",
            "menu_item_id",
            unique=True,
            sqlite_where=text("effective_to IS NULL"),
            postgresql_where=text("effective_to IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MenuItemPrice item={self.menu_item_id} {self.price_pence}p "
            f"from {self.effective_from}>"
        )


class MenuCategory(Base):
    """The Menu items rail's categories, with the design's Drinks/Food/Other kind.

    `menu_item.category` stays a string (no FK): many live values are empty or
    misspelled ("Spring saesonal drinks") and forcing a FK would need a data cleanup
    the owner has not made. Items whose category has no row here group as OTHER.
    """

    __tablename__ = "menu_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    kind: Mapped[MenuKind] = mapped_column(enum_col(MenuKind), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class MenuItemCost(Base):
    """Materialised cost per menu item. Spec 5.5.

    Computed by jobs/cost_rollup.py and on every composition edit. It exists so
    318 resolutions never happen inside a request handler.

    `cost_source` is the WEAKEST source among the item's ingredients: one
    estimated ingredient makes the whole item's cost an estimate. Invariant 6 --
    estimates stay flagged through every rollup and aggregate, so the margin
    screen can exclude them from totals instead of silently defaulting to zero.

    `cost_pence` and `cost_source` are NULLABLE, and that is the whole point: an
    item with an unpriced ingredient is cached as a row whose cost is NULL, not as
    a row whose cost is a partial sum. Invariant 6 says a missing cost is `None`,
    never zero, and a NOT NULL column would have forced this cache to invent a
    number that no downstream reader could tell from a real one. The row still
    exists so the item is *shown and flagged* rather than silently absent.
    """

    __tablename__ = "menu_item_cost"

    id: Mapped[int] = mapped_column(primary_key=True)
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_item.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # Recipe cost. Excludes waste_factor by design -- invariant 5.
    # NULL means "we do not know what this costs", never "it costs nothing".
    cost_pence: Mapped[Decimal | None] = mapped_column(Qty())
    cost_source: Mapped[PriceSource | None] = mapped_column(enum_col(PriceSource))
    # True when any ingredient had no price at all. Such items are shown,
    # flagged, and excluded from aggregates -- never defaulted to zero.
    has_missing_cost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ingredient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- labour (spec 5.6) ----------------------------------------------------
    # `margin_per_minute` is the number that matters at the peak hour, and it
    # reorders the menu ranking against plain margin %: a drink at 85% margin taking
    # three minutes loses to one at 70% taking forty seconds when there is a queue.
    # Both are NULL when prep time or the loaded hourly rate is unset -- the same
    # rule as cost_pence, because a labour figure derived from a guessed rate is a
    # guess wearing a number's clothes.
    labour_cost_pence: Mapped[Decimal | None] = mapped_column(Qty())
    prep_seconds: Mapped[int | None] = mapped_column(Integer)
    #: Loaded hourly rate in force when this was computed, so a rate change is
    #: auditable rather than retroactively rewriting history.
    loaded_hourly_rate_pence: Mapped[int | None] = mapped_column(Integer)
    #: Whether `prep_seconds` above was an estimate when this row was computed.
    #: Cached alongside the number rather than joined back to the template, for the
    #: same reason `cost_source` is: an aggregate built on this cache must be able to
    #: separate estimates from measurements without a second query, or it will not
    #: bother (invariant 8).
    prep_seconds_is_estimate: Mapped[bool | None] = mapped_column(Boolean)

    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # The recipe date this cost was resolved at, so a stale cache is detectable.
    resolved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    menu_item: Mapped[MenuItem] = relationship()

    def __repr__(self) -> str:
        cost = "unknown" if self.cost_pence is None else f"{self.cost_pence}p"
        source = self.cost_source.value if self.cost_source else "MISSING"
        return f"<MenuItemCost item={self.menu_item_id} {cost} {source}>"
