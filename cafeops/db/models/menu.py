"""Menu items, manual recipes, and the materialised cost cache."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime, enum_col
from cafeops.db.models.enums import PriceSource, SizeCode

if TYPE_CHECKING:
    from cafeops.db.models.composition import DrinkTemplate
    from cafeops.db.models.ingredient import Ingredient


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

    price_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    manual_recipe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Surfaced on the data-quality screen (spec 6, 8.6). Set for things like the
    # item literally named "'card' (£3.00)" with zero cost.
    data_quality_flag: Mapped[str | None] = mapped_column(String(200))

    template: Mapped[DrinkTemplate | None] = relationship()
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


class MenuItemCost(Base):
    """Materialised cost per menu item. Spec 5.5.

    Computed by jobs/cost_rollup.py and on every composition edit. It exists so
    318 resolutions never happen inside a request handler.

    `cost_source` is the WEAKEST source among the item's ingredients: one
    estimated ingredient makes the whole item's cost an estimate. Invariant 6 --
    estimates stay flagged through every rollup and aggregate, so the margin
    screen can exclude them from totals instead of silently defaulting to zero.
    """

    __tablename__ = "menu_item_cost"

    id: Mapped[int] = mapped_column(primary_key=True)
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_item.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # Recipe cost. Excludes waste_factor by design -- invariant 5.
    cost_pence: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    cost_source: Mapped[PriceSource] = mapped_column(enum_col(PriceSource), nullable=False)
    # True when any ingredient had no price at all. Such items are shown,
    # flagged, and excluded from aggregates -- never defaulted to zero.
    has_missing_cost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ingredient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # The recipe date this cost was resolved at, so a stale cache is detectable.
    resolved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    menu_item: Mapped[MenuItem] = relationship()

    def __repr__(self) -> str:
        return (
            f"<MenuItemCost item={self.menu_item_id} {self.cost_pence}p {self.cost_source.value}>"
        )
