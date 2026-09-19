"""Composition queries. This is where effective dating lives.

`resolve_recipe` is pure and date-agnostic; it receives a MenuItemSpec that already
contains only what was in force at the resolve date. That narrowing happens here,
in SQL. Invariant 3 is therefore enforced by a query rather than by everyone
remembering to filter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    DrinkTemplate,
    Ingredient,
    ManualRecipeLine,
    MenuItem,
    Modifier,
    TemplateComponent,
    VariantAxis,
    VariantOption,
)
from cafeops.domain.types import (
    ComponentRole,
    ComponentSpec,
    MenuItemSpec,
    ModifierSpec,
    SizeCode,
    Unit,
    VariantOptionSpec,
)


@dataclass(frozen=True, slots=True)
class LiveComponent:
    """One template slot as the editor sees it: which ingredient, how much per size.

    A read model, not an ORM row -- see `live_components`.
    """

    component_id: int
    template_id: int
    role: ComponentRole
    ingredient_id: int | None
    ingredient_name: str | None
    unit: Unit | None
    qty_by_size: dict[str, str]
    is_substitutable: bool
    is_required: bool
    effective_from: datetime


def _qty_for_size(qty_by_size: dict[str, str] | None, size: SizeCode | None) -> Decimal | None:
    """Read a per-size quantity out of JSON.

    Quantities are stored as STRINGS and parsed to Decimal here. JSON has only
    doubles, and a recipe editor that loses 0.1 + 0.2 is unacceptable (spec 8).
    """
    if not qty_by_size:
        return None
    key = size.value if size else "ONE"
    raw = qty_by_size.get(key)
    if raw is None:
        return None
    return Decimal(str(raw))


class SqlCompositionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- effective-dated predicates ---------------------------------------

    @staticmethod
    def _live(model: type, at: datetime):  # type: ignore[no-untyped-def]
        return (
            model.effective_from <= at,
            or_(model.effective_to.is_(None), model.effective_to > at),
        )

    # -- item specs -------------------------------------------------------

    def item_spec(self, menu_item_id: int, at: datetime) -> MenuItemSpec | None:
        specs = self.item_specs([menu_item_id], at)
        return specs.get(menu_item_id)

    def item_specs(self, menu_item_ids: Sequence[int], at: datetime) -> dict[int, MenuItemSpec]:
        if not menu_item_ids:
            return {}
        items = list(
            self.session.scalars(select(MenuItem).where(MenuItem.id.in_(list(menu_item_ids))))
        )
        if not items:
            return {}

        template_ids = sorted({i.template_id for i in items if i.template_id is not None})
        components_by_template = self._components(template_ids, at)
        options_by_axis = self._options(template_ids, at)

        manual_ids = [i.id for i in items if i.manual_recipe]
        manual_by_item = self._manual_lines(manual_ids, at)

        out: dict[int, MenuItemSpec] = {}
        for item in items:
            components: tuple[ComponentSpec, ...] = ()
            options: tuple[VariantOptionSpec, ...] = ()

            if item.template_id is not None:
                components = tuple(
                    ComponentSpec(
                        component_id=c.id,
                        role=c.role,
                        ingredient_id=c.ingredient_id,
                        qty=_qty_for_size(c.qty_by_size, item.size_code),
                        is_substitutable=c.is_substitutable,
                        is_required=c.is_required,
                    )
                    for c in components_by_template.get(item.template_id, [])
                )
                options = tuple(self._selected_options(item, options_by_axis))

            out[item.id] = MenuItemSpec(
                menu_item_id=item.id,
                name=item.name,
                size_code=item.size_code,
                template_id=item.template_id,
                price_pence=item.price_pence,
                manual_recipe=item.manual_recipe,
                components=components,
                options=options,
                manual_lines=tuple(manual_by_item.get(item.id, [])),
            )
        return out

    def _components(
        self, template_ids: Sequence[int], at: datetime
    ) -> dict[int, list[TemplateComponent]]:
        if not template_ids:
            return {}
        rows = self.session.scalars(
            select(TemplateComponent)
            .where(
                TemplateComponent.template_id.in_(list(template_ids)),
                *self._live(TemplateComponent, at),
            )
            .order_by(TemplateComponent.template_id, TemplateComponent.id)
        )
        out: dict[int, list[TemplateComponent]] = {}
        for row in rows:
            out.setdefault(row.template_id, []).append(row)
        return out

    def _options(
        self, template_ids: Sequence[int], at: datetime
    ) -> dict[int, list[tuple[VariantOption, VariantAxis]]]:
        if not template_ids:
            return {}
        rows = self.session.execute(
            select(VariantOption, VariantAxis)
            .join(VariantAxis, VariantAxis.id == VariantOption.axis_id)
            .where(
                VariantAxis.template_id.in_(list(template_ids)),
                *self._live(VariantOption, at),
            )
        ).all()
        out: dict[int, list[tuple[VariantOption, VariantAxis]]] = {}
        for option, axis in rows:
            out.setdefault(axis.id, []).append((option, axis))
        return out

    def _selected_options(
        self,
        item: MenuItem,
        options_by_axis: dict[int, list[tuple[VariantOption, VariantAxis]]],
    ) -> list[VariantOptionSpec]:
        chosen: list[VariantOptionSpec] = []
        # selected_options keys are stringified ints: JSON object keys always are.
        for axis_key, option_id in (item.selected_options or {}).items():
            try:
                axis_id = int(axis_key)
            except (TypeError, ValueError):
                continue
            for option, axis in options_by_axis.get(axis_id, []):
                if option.id != option_id:
                    continue
                chosen.append(
                    VariantOptionSpec(
                        option_id=option.id,
                        axis_id=axis.id,
                        name=option.name,
                        role=axis.role,
                        ingredient_id=option.ingredient_id,
                        qty=_qty_for_size(option.qty_by_size, item.size_code),
                        price_delta_pence=option.price_delta_pence,
                    )
                )
        return chosen

    def _manual_lines(
        self, menu_item_ids: Sequence[int], at: datetime
    ) -> dict[int, list[tuple[int, Decimal]]]:
        if not menu_item_ids:
            return {}
        rows = self.session.execute(
            select(
                ManualRecipeLine.menu_item_id, ManualRecipeLine.ingredient_id, ManualRecipeLine.qty
            )
            .where(
                ManualRecipeLine.menu_item_id.in_(list(menu_item_ids)),
                *self._live(ManualRecipeLine, at),
            )
            .order_by(ManualRecipeLine.menu_item_id, ManualRecipeLine.id)
        ).all()
        out: dict[int, list[tuple[int, Decimal]]] = {}
        for item_id, ingredient_id, qty in rows:
            out.setdefault(item_id, []).append((ingredient_id, qty))
        return out

    # -- modifiers --------------------------------------------------------

    def modifiers(self, modifier_ids: Sequence[int]) -> list[ModifierSpec]:
        if not modifier_ids:
            return []
        rows = self.session.scalars(select(Modifier).where(Modifier.id.in_(list(modifier_ids))))
        return [
            ModifierSpec(
                modifier_id=m.id,
                name=m.name,
                action=m.action,
                target_role=m.target_role,
                ingredient_id=m.ingredient_id,
                qty_delta=m.qty_delta,
                qty_multiplier=m.qty_multiplier,
                price_pence=m.price_pence,
            )
            for m in rows
        ]

    # -- reverse lookups for the cost cascade ------------------------------

    def menu_item_ids_for_template(self, template_id: int) -> list[int]:
        return list(
            self.session.scalars(select(MenuItem.id).where(MenuItem.template_id == template_id))
        )

    def template_ids_using_ingredient(self, ingredient_id: int) -> list[int]:
        via_component = select(TemplateComponent.template_id).where(
            TemplateComponent.ingredient_id == ingredient_id
        )
        via_option = (
            select(VariantAxis.template_id)
            .join(VariantOption, VariantOption.axis_id == VariantAxis.id)
            .where(VariantOption.ingredient_id == ingredient_id)
        )
        ids = set(self.session.scalars(via_component)) | set(self.session.scalars(via_option))
        return sorted(ids)

    def close_and_open_component(
        self, component_id: int, *, qty_by_size: dict[str, str], effective_from: datetime
    ) -> int:
        """Effective-dated edit: close the old row, open a new one.

        INVARIANT 3, in code: never an in-place update. The old row keeps its
        quantities so a sale dated before this edit still resolves the way it did
        when it happened.
        """
        old = self.session.get(TemplateComponent, component_id)
        if old is None:
            raise LookupError(f"template_component {component_id} not found")
        if old.effective_to is not None:
            raise ValueError(
                f"template_component {component_id} is already closed at {old.effective_to}; "
                "edit the live row instead"
            )
        new = TemplateComponent(
            template_id=old.template_id,
            role=old.role,
            ingredient_id=old.ingredient_id,
            qty_by_size=qty_by_size,
            is_substitutable=old.is_substitutable,
            is_required=old.is_required,
            effective_from=effective_from,
            effective_to=None,
            note=old.note,
        )
        self.session.add(new)
        self.session.flush()
        old.effective_to = effective_from
        old.superseded_by_id = new.id
        return new.id

    # -- reverse lookups added for the cost cascade (Agent B) ---------------

    def menu_item_ids_using_ingredient_manually(self, ingredient_id: int) -> list[int]:
        """Manual-recipe items whose recipe names this ingredient.

        Deliberately NOT effective-dated: this drives a recost, and an item whose
        recipe used the ingredient last month still has a cached cost that was
        computed from its price. Narrowing to today would leave that row stale.
        """
        return sorted(
            set(
                self.session.scalars(
                    select(ManualRecipeLine.menu_item_id).where(
                        ManualRecipeLine.ingredient_id == ingredient_id
                    )
                )
            )
        )

    def menu_item_ids_for_component(self, component_id: int) -> list[int]:
        """Every item that resolves through the template this component belongs to."""
        component = self.session.get(TemplateComponent, component_id)
        if component is None:
            raise LookupError(f"template_component {component_id} not found")
        return self.menu_item_ids_for_template(component.template_id)

    def all_menu_item_ids(self, *, active_only: bool = True) -> list[int]:
        stmt = select(MenuItem.id).order_by(MenuItem.id)
        if active_only:
            stmt = stmt.where(MenuItem.active.is_(True))
        return list(self.session.scalars(stmt))

    def template_id_by_name(self, name: str) -> int | None:
        return self.session.scalar(select(DrinkTemplate.id).where(DrinkTemplate.name == name))

    def live_component(self, component_id: int) -> LiveComponent | None:
        """One component row, open or closed, as a read model.

        Returns the row itself rather than filtering by date: the editor works on a
        component BY ID, and `close_and_open_component` is what refuses to touch a
        row that is already closed.
        """
        row = self.session.execute(
            select(TemplateComponent, Ingredient.name, Ingredient.unit)
            .outerjoin(Ingredient, Ingredient.id == TemplateComponent.ingredient_id)
            .where(TemplateComponent.id == component_id)
        ).first()
        if row is None:
            return None
        component, name, unit = row
        return LiveComponent(
            component_id=component.id,
            template_id=component.template_id,
            role=component.role,
            ingredient_id=component.ingredient_id,
            ingredient_name=name,
            unit=unit,
            qty_by_size=dict(component.qty_by_size or {}),
            is_substitutable=component.is_substitutable,
            is_required=component.is_required,
            effective_from=component.effective_from,
        )

    def live_components(self, template_id: int, at: datetime) -> list[LiveComponent]:
        """The editable component rows of one template, as of `at`.

        Returned as dataclasses rather than ORM rows: the composition editor and the
        CLI need a component's id, role, ingredient name and per-size quantities,
        and handing out live ORM objects invites an in-place update -- which is
        exactly what invariant 3 forbids.
        """
        rows = self.session.execute(
            select(TemplateComponent, Ingredient.name, Ingredient.unit)
            .outerjoin(Ingredient, Ingredient.id == TemplateComponent.ingredient_id)
            .where(
                TemplateComponent.template_id == template_id,
                *self._live(TemplateComponent, at),
            )
            .order_by(TemplateComponent.role, TemplateComponent.id)
        ).all()
        return [
            LiveComponent(
                component_id=component.id,
                template_id=component.template_id,
                role=component.role,
                ingredient_id=component.ingredient_id,
                ingredient_name=name,
                unit=unit,
                qty_by_size=dict(component.qty_by_size or {}),
                is_substitutable=component.is_substitutable,
                is_required=component.is_required,
                effective_from=component.effective_from,
            )
            for component, name, unit in rows
        ]
