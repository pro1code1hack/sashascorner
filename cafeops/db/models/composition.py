"""Composition: templates, size profiles, variant axes, modifiers.

The core of the build. Spec 2's conclusion drives all of it:

    318 rows is not 318 recipes. It is roughly 20 patterns, multiplied out.

62 flavoured lattes are ONE template with a syrup axis and per-size quantities.
The legacy workbook flattened that into ~1,600 hand-maintained lines, which is why
changing the milk quantity in a latte was a day's work.

EFFECTIVE DATING IS MANDATORY (spec 4.3, invariant 3). Every component and option
carries effective_from / effective_to. An edit CLOSES the old row and OPENS a new
one; it never updates in place. Recomputing March's consumption with today's
recipe produces wrong history and destroys the drift metric, which is the one
number the whole product's trustworthiness rests on.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime, enum_col
from cafeops.db.models.enums import ComponentRole, ModifierAction, SizeCode

if TYPE_CHECKING:
    from cafeops.db.models.ingredient import Ingredient


class DrinkTemplate(Base):
    """A pattern: "Flavoured Latte", "Hot Chocolate", "Bubble Tea"."""

    __tablename__ = "drink_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Spec 4.2: prep seconds per size, e.g. {"S": 45, "M": 50, "XL": 60}. Earns its
    # place three ways -- true margin including labour, throughput at the peak hour,
    # and finding items that are margin-positive but time-negative during a rush.
    # A menu_item may override it.
    prep_seconds_by_size: Mapped[dict[str, int]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    # Three states, and all three are needed. NULL: no prep time recorded at all.
    # True: an estimate, the same treatment prices and shelf lives get (invariant 8) --
    # every prep time in the system today is a plausible guess, and a
    # margin-per-minute ranking built on guesses must say so on the screen that shows
    # it. False: somebody stood at the machine with a stopwatch.
    prep_seconds_is_estimate: Mapped[bool | None] = mapped_column(Boolean)

    sizes: Mapped[list[SizeProfile]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="SizeProfile.sort_order",
    )
    components: Mapped[list[TemplateComponent]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )
    axes: Mapped[list[VariantAxis]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<DrinkTemplate {self.id} {self.name!r}>"


class SizeProfile(Base):
    """A size belonging to ONE template.

    Spec 4.2: sizes belong to a template because XL latte and XL milkshake are
    unrelated things that happen to share a letter.
    """

    __tablename__ = "size_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("drink_template.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[SizeCode] = mapped_column(enum_col(SizeCode), nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    template: Mapped[DrinkTemplate] = relationship(back_populates="sizes")

    __table_args__ = (UniqueConstraint("template_id", "code", name="uq_size_profile_tpl_code"),)

    def __repr__(self) -> str:
        return f"<SizeProfile tpl={self.template_id} {self.code.value}>"


class TemplateComponent(Base):
    """One slot in a template.

    `ingredient_id` is nullable: null means the slot is filled by a variant axis
    (the FLAVOUR slot of a flavoured latte has no fixed syrup).

    `qty_by_size` maps size code -> quantity as a STRING, e.g.
    {"S": "0.12", "M": "0.18", "XL": "0.25"}. Strings, not floats: JSON has only
    doubles, and 0.1 + 0.2 in a recipe editor is unacceptable (spec 8). Parsed to
    Decimal on read.
    """

    __tablename__ = "template_component"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("drink_template.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[ComponentRole] = mapped_column(enum_col(ComponentRole), nullable=False)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))
    qty_by_size: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    # Can a modifier swap it? A SUBSTITUTE against a slot with this False is an
    # ERROR, not a silent no-op (spec 4.3 rule 4).
    is_substitutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    effective_from: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Set when an edit supersedes this row, so the audit trail is walkable.
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("template_component.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(String(400))

    template: Mapped[DrinkTemplate] = relationship(back_populates="components")
    ingredient: Mapped[Ingredient | None] = relationship()

    __table_args__ = (
        Index("ix_template_component_tpl_from", "template_id", "effective_from"),
        Index("ix_template_component_role", "template_id", "role"),
    )

    def __repr__(self) -> str:
        return (
            f"<TemplateComponent {self.id} tpl={self.template_id} "
            f"{self.role.value} ing={self.ingredient_id}>"
        )


class VariantAxis(Base):
    """A dimension of choice within a template: "Flavour", "Temperature"."""

    __tablename__ = "variant_axis"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("drink_template.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[ComponentRole] = mapped_column(enum_col(ComponentRole), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    template: Mapped[DrinkTemplate] = relationship(back_populates="axes")
    options: Mapped[list[VariantOption]] = relationship(
        back_populates="axis", cascade="all, delete-orphan", order_by="VariantOption.name"
    )

    __table_args__ = (UniqueConstraint("template_id", "name", name="uq_variant_axis_tpl_name"),)

    def __repr__(self) -> str:
        return f"<VariantAxis {self.id} tpl={self.template_id} {self.name!r}>"


class VariantOption(Base):
    """One choice on an axis: "Pistachio" -> Pistachio syrup (Monin).

    Adding a flavour is ONE row here and creates three sellable items. That is the
    whole point of the model.
    """

    __tablename__ = "variant_option"

    id: Mapped[int] = mapped_column(primary_key=True)
    axis_id: Mapped[int] = mapped_column(
        ForeignKey("variant_axis.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))
    # Overrides the slot's qty_by_size when present. Null means inherit the slot.
    qty_by_size: Mapped[dict[str, str] | None] = mapped_column(JSON)
    price_delta_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Spec 4.3: Pistachio is spring-only. A season on the option, not on 21 derived
    # menu items, so adding a seasonal flavour stays one row.
    season_id: Mapped[int | None] = mapped_column(ForeignKey("season.id"))

    effective_from: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(UTCDateTime)

    axis: Mapped[VariantAxis] = relationship(back_populates="options")
    ingredient: Mapped[Ingredient | None] = relationship()

    __table_args__ = (
        UniqueConstraint("axis_id", "name", "effective_from", name="uq_variant_option_axis_name"),
        Index("ix_variant_option_axis_from", "axis_id", "effective_from"),
    )

    def __repr__(self) -> str:
        return f"<VariantOption {self.id} {self.name!r} ing={self.ingredient_id}>"


class Modifier(Base):
    """A POS modifier: "Oat milk", "Extra shot", "Extra syrup pump".

    Targets a ROLE, not an ingredient, which is what lets one "Oat milk" modifier
    work across every template that has a MILK slot.

    Applied in the order SUBSTITUTE -> SCALE -> ADD (spec 4.3 rule 3).
    """

    __tablename__ = "modifier"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    lightspeed_modifier_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    action: Mapped[ModifierAction] = mapped_column(enum_col(ModifierAction), nullable=False)
    target_role: Mapped[ComponentRole] = mapped_column(enum_col(ComponentRole), nullable=False)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))
    # ADD reads qty_delta; SCALE reads qty_multiplier; SUBSTITUTE reads neither
    # and carries the replaced slot's quantity across.
    qty_delta: Mapped[Decimal | None] = mapped_column(Qty())
    qty_multiplier: Mapped[Decimal | None] = mapped_column(Qty())
    price_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    ingredient: Mapped[Ingredient | None] = relationship()

    def __repr__(self) -> str:
        return f"<Modifier {self.id} {self.name!r} {self.action.value}/{self.target_role.value}>"


class LegacyStagedRecipe(Base):
    """Staging for spec 6 pass 2: the flat legacy recipes, ported verbatim.

    No interpretation happens here. Pass 3 groups these by ingredient-role
    signature to PROPOSE templates, and a human confirms before anything is
    written to the composition tables. Auto-generating templates from dirty data
    and treating them as truth is how you get a system confidently costing
    drinks wrong.

    Kept after import rather than dropped: it is the only record of what the
    workbook actually said, and the import-review screen compares proposals
    against it.
    """

    __tablename__ = "legacy_staged_recipe"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    size_code: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(80))
    sell_price_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingredient_name: Mapped[str] = mapped_column(String(160), nullable=False)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))
    qty: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    raw_unit: Mapped[str | None] = mapped_column(String(40))
    role: Mapped[ComponentRole | None] = mapped_column(enum_col(ComponentRole))
    notes: Mapped[str | None] = mapped_column(String(400))
    # Anything the importer could not make sense of, surfaced on the
    # data-quality screen rather than silently fixed.
    data_quality_flag: Mapped[str | None] = mapped_column(String(200))

    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_staged_recipe_no", "recipe_no"),
        Index("ix_staged_item", "item_name", "size_code"),
    )

    def __repr__(self) -> str:
        return f"<LegacyStagedRecipe #{self.recipe_no} {self.item_name!r} {self.ingredient_name!r}>"
