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

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    DrinkTemplate,
    Ingredient,
    ManualRecipeLine,
    MenuItem,
    Modifier,
    ModifierVersion,
    Season,
    TemplateComponent,
    VariantAxis,
    VariantOption,
)
from cafeops.domain.composition import OptionSeason
from cafeops.domain.labour import UNTIMED, PrepTime, resolve_prep_time
from cafeops.domain.types import (
    ComponentRole,
    ComponentSpec,
    MenuItemSpec,
    ModifierSpec,
    SeasonSpec,
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


#: The composition tables that carry effective dating. Spelled out rather than as a
#: structural protocol: mypy will not match a class object against a protocol whose
#: members are SQLAlchemy `Mapped` descriptors, and this is the whole list anyway.
type _EffectiveDated = (
    type[TemplateComponent] | type[VariantOption] | type[ManualRecipeLine] | type[ModifierVersion]
)


class SqlCompositionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- effective-dated predicates ---------------------------------------

    @staticmethod
    def _live(
        model: _EffectiveDated, at: datetime
    ) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
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
        option_names = self._option_names(template_ids)

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
                options = tuple(self._selected_options(item, options_by_axis, option_names))

            out[item.id] = MenuItemSpec(
                menu_item_id=item.id,
                name=item.name,
                size_code=item.size_code,
                template_id=item.template_id,
                price_pence=item.price_pence,
                manual_recipe=item.manual_recipe,
                season_id=item.season_id,
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

    def _option_names(self, template_ids: Sequence[int]) -> dict[int, tuple[int, str]]:
        """option_id -> (axis_id, name) for EVERY version, open or closed.

        A flavour edit closes its `variant_option` row and opens a successor with a new
        id (invariant 3), and the recipes screen re-points the menu items at the
        successor. A sale dated before the edit must still find the version that was in
        force then, so a selection is followed along its lineage: same axis, same name
        (`uq_variant_option_axis_name` makes that the version key). A rename renames
        every version of the lineage, so the key survives it.
        """
        if not template_ids:
            return {}
        rows = self.session.execute(
            select(VariantOption.id, VariantOption.axis_id, VariantOption.name)
            .join(VariantAxis, VariantAxis.id == VariantOption.axis_id)
            .where(VariantAxis.template_id.in_(list(template_ids)))
        ).all()
        return {int(option_id): (int(axis_id), str(name)) for option_id, axis_id, name in rows}

    def _selected_options(
        self,
        item: MenuItem,
        options_by_axis: dict[int, list[tuple[VariantOption, VariantAxis]]],
        option_names: dict[int, tuple[int, str]] | None = None,
    ) -> list[VariantOptionSpec]:
        chosen: list[VariantOptionSpec] = []
        # selected_options keys are stringified ints: JSON object keys always are.
        for axis_key, option_id in (item.selected_options or {}).items():
            try:
                axis_id = int(axis_key)
            except (TypeError, ValueError):
                continue
            live = options_by_axis.get(axis_id, [])
            wanted: int | None = int(option_id)
            if not any(option.id == wanted for option, _axis in live):
                # Not in force at this date: follow the lineage to the version that is.
                lineage = (option_names or {}).get(int(option_id))
                wanted = next(
                    (
                        option.id
                        for option, _axis in live
                        if lineage is not None
                        and option.axis_id == lineage[0]
                        and option.name == lineage[1]
                    ),
                    None,
                )
            for option, axis in live:
                if option.id != wanted:
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
                        # Rides on the spec so no caller can lose it by forgetting an
                        # argument. The richer season lookups below still exist for the
                        # menu layer, which needs names and remaining days, not just an id.
                        season_id=option.season_id,
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

    def modifiers(
        self, modifier_ids: Sequence[int], at: datetime | None = None
    ) -> list[ModifierSpec]:
        """The modifiers as they behaved at `at` (spec C-6: `modifier_version`).

        A modifier's behaviour is effective-dated: an Oat milk swap that becomes an ADD
        next month must not re-resolve last month's sales. With `at`, each modifier is
        read from the `modifier_version` in force then; the `modifier` row's own
        columns are only a cache of the OPEN version and are used when `at` is None
        (today) or when no version covers `at` (a sale older than the version history,
        whose backfill starts at the earliest sale -- so in practice never).
        """
        if not modifier_ids:
            return []
        ids = list(modifier_ids)
        rows = list(self.session.scalars(select(Modifier).where(Modifier.id.in_(ids))))
        versions: dict[int, ModifierVersion] = {}
        if at is not None:
            for version in self.session.scalars(
                select(ModifierVersion)
                .where(ModifierVersion.modifier_id.in_(ids), *self._live(ModifierVersion, at))
                .order_by(ModifierVersion.effective_from)
            ):
                versions[version.modifier_id] = version
        out: list[ModifierSpec] = []
        for m in rows:
            v = versions.get(m.id)
            out.append(
                ModifierSpec(
                    modifier_id=m.id,
                    name=m.name,
                    action=v.action if v is not None else m.action,
                    target_role=v.target_role if v is not None else m.target_role,
                    ingredient_id=v.ingredient_id if v is not None else m.ingredient_id,
                    qty_delta=v.qty_delta if v is not None else m.qty_delta,
                    qty_multiplier=v.qty_multiplier if v is not None else m.qty_multiplier,
                    price_pence=v.price_pence if v is not None else m.price_pence,
                )
            )
        return out

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

    # -- seasons on the composition side (spec 4.3) ------------------------
    #
    # `SqlSeasonRepository` owns the ordering-side answers ("is this ingredient
    # seasonal", "what is running today"). These three are the COMPOSITION-side
    # answers -- "which seasons do THIS menu item's selected options belong to" --
    # and they live here because they are joins across `menu_item.selected_options`,
    # `variant_option` and `variant_axis`, which is this module's subject.

    def _seasons_by_id(self) -> dict[int, SeasonSpec]:
        return {
            row.id: SeasonSpec(
                season_id=row.id,
                name=row.name,
                starts_on=row.starts_on,
                ends_on=row.ends_on,
                is_recurring_annually=row.is_recurring_annually,
            )
            for row in self.session.scalars(select(Season))
        }

    def item_seasons(self, menu_item_ids: Sequence[int]) -> dict[int, SeasonSpec]:
        """menu_item_id -> its OWN season, for the items that have one."""
        if not menu_item_ids:
            return {}
        seasons = self._seasons_by_id()
        rows = self.session.execute(
            select(MenuItem.id, MenuItem.season_id).where(
                MenuItem.id.in_(list(menu_item_ids)), MenuItem.season_id.is_not(None)
            )
        ).all()
        return {
            item_id: seasons[season_id]
            for item_id, season_id in rows
            if season_id is not None and season_id in seasons
        }

    def option_season_details(self, menu_item_ids: Sequence[int]) -> dict[int, list[OptionSeason]]:
        """menu_item_id -> the seasonal options it actually SELECTS, with their names.

        Only the selected options. A template may carry a seasonal option this leaf
        does not use, and warning about it would be noise -- which is how real
        warnings come to be ignored (ARCHITECTURE 7.5).

        The names travel with the seasons because "Pistachio Latte is unavailable" is
        not actionable, whereas "option 'Pistachio' is 'Spring seasonal drinks' only
        (01 Mar to 31 May, recurring)" is.
        """
        if not menu_item_ids:
            return {}
        seasons = self._seasons_by_id()
        items = list(
            self.session.scalars(
                select(MenuItem).where(
                    MenuItem.id.in_(list(menu_item_ids)), MenuItem.template_id.is_not(None)
                )
            )
        )
        if not items:
            return {}

        template_ids = sorted({i.template_id for i in items if i.template_id is not None})
        rows = self.session.execute(
            select(VariantOption.id, VariantOption.name, VariantOption.season_id)
            .join(VariantAxis, VariantAxis.id == VariantOption.axis_id)
            .where(
                VariantAxis.template_id.in_(template_ids),
                VariantOption.season_id.is_not(None),
            )
        ).all()
        seasonal = {
            option_id: (name, season_id)
            for option_id, name, season_id in rows
            if season_id is not None and season_id in seasons
        }
        if not seasonal:
            return {}

        out: dict[int, list[OptionSeason]] = {}
        for item in items:
            chosen: list[OptionSeason] = []
            for raw_option_id in (item.selected_options or {}).values():
                found = seasonal.get(int(raw_option_id))
                if found is None:
                    continue
                name, season_id = found
                chosen.append(
                    OptionSeason(option_id=int(raw_option_id), name=name, season=seasons[season_id])
                )
            if chosen:
                out[item.id] = chosen
        return out

    def option_seasons_for_items(
        self, menu_item_ids: Sequence[int]
    ) -> dict[int, dict[int, SeasonSpec]]:
        """The same mapping in the shape `resolve_recipe` takes: option_id -> season."""
        return {
            item_id: {option.option_id: option.season for option in options}
            for item_id, options in self.option_season_details(menu_item_ids).items()
        }

    # -- prep time (spec 4.2, 5.6) -----------------------------------------

    def prep_times(self, menu_item_ids: Sequence[int]) -> dict[int, PrepTime]:
        """menu_item_id -> the prep time that applies, with its provenance.

        Two sources and one precedence rule, both decided in `domain/labour.py`:
        `menu_item.prep_seconds` overrides `drink_template.prep_seconds_by_size` at the
        item's size. This method only supplies the raw values -- which one wins, and
        what an absent or non-positive value means, is domain logic and stays there.

        Every id asked for appears in the result, `UNTIMED` when nothing is recorded.
        A caller iterating its own id list must not have to distinguish "not in the
        dict" from "no prep time", because those would be the same fact wearing two
        shapes.
        """
        if not menu_item_ids:
            return {}
        rows = self.session.execute(
            select(
                MenuItem.id,
                MenuItem.size_code,
                MenuItem.prep_seconds,
                MenuItem.prep_seconds_is_estimate,
                DrinkTemplate.prep_seconds_by_size,
                DrinkTemplate.prep_seconds_is_estimate,
            )
            .outerjoin(DrinkTemplate, DrinkTemplate.id == MenuItem.template_id)
            .where(MenuItem.id.in_(list(menu_item_ids)))
        ).all()
        out: dict[int, PrepTime] = {int(i): UNTIMED for i in menu_item_ids}
        for item_id, size_code, item_seconds, item_est, template_map, template_est in rows:
            out[item_id] = resolve_prep_time(
                template_prep_seconds_by_size=template_map,
                item_prep_seconds=item_seconds,
                size_code=size_code,
                item_is_estimate=item_est,
                template_is_estimate=template_est,
            )
        return out

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
