"""`cafeops.domain.composition`, the changeset section.

Split from one 2,270-line module on 2026-09-29 (ARCHITECTURE 8Y). Import the public
names from the package; this module is an implementation detail of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from cafeops.domain.composition.display import _size_word, gbp, qty_text, unit_label
from cafeops.domain.labour import (
    PrepTime,
    resolve_prep_time,
)
from cafeops.domain.types import (
    ComponentRole,
    ComponentSpec,
    MenuItemSpec,
    SizeCode,
    Unit,
    VariantOptionSpec,
)

# --------------------------------------------------------------------------
# Template changesets: the draft, the operations, and what they do. Pure.
# --------------------------------------------------------------------------
#
# The service loads the live template into a `TemplateDraft`, the operations transform
# it into another `TemplateDraft`, and BOTH sides are turned into `MenuItemSpec`s by
# the same function (`item_spec_from_draft`) and resolved by the same resolver. So the
# before side of a preview is built exactly the way the after side is, and what a
# reviewer approved is what the apply writes: the apply walks the same two drafts.


@dataclass(frozen=True, slots=True)
class DraftAxis:
    axis_id: int
    name: str
    role: ComponentRole


@dataclass(frozen=True, slots=True)
class DraftComponent:
    """One slot. `component_id` None = added by this changeset (key `n<i>`)."""

    key: str
    component_id: int | None
    role: ComponentRole
    ingredient_id: int | None
    qty_by_size: Mapping[str, Decimal]
    is_substitutable: bool
    is_required: bool

    def qty(self, size: SizeCode | None) -> Decimal | None:
        return self.qty_by_size.get(size.value if size else SizeCode.ONE.value)


@dataclass(frozen=True, slots=True)
class DraftOption:
    """One flavour. `qty_by_size` None = inherit the slot's quantity."""

    key: str
    option_id: int | None
    axis_id: int
    name: str
    ingredient_id: int | None
    qty_by_size: Mapping[str, Decimal] | None
    price_delta_pence: int
    season_id: int | None
    removed: bool = False

    def qty(self, size: SizeCode | None) -> Decimal | None:
        if self.qty_by_size is None:
            return None
        return self.qty_by_size.get(size.value if size else SizeCode.ONE.value)


@dataclass(frozen=True, slots=True)
class DraftItem:
    """One generated menu item. `menu_item_id` None = created by this changeset."""

    key: str
    menu_item_id: int | None
    name: str
    size_code: SizeCode | None
    option_keys: Mapping[int, str]
    price_pence: int
    active: bool
    prep_seconds: int | None = None
    prep_is_estimate: bool | None = None
    on_till: bool = True


@dataclass(frozen=True, slots=True)
class TemplateDraft:
    template_id: int
    name: str
    category: str | None
    sizes: tuple[SizeCode, ...]
    axes: tuple[DraftAxis, ...]
    components: tuple[DraftComponent, ...]
    options: tuple[DraftOption, ...]
    items: tuple[DraftItem, ...]
    prep_seconds_by_size: Mapping[str, int]
    prep_is_estimate: bool | None

    def option(self, key: str) -> DraftOption | None:
        return next((o for o in self.options if o.key == key), None)

    def axis(self, axis_id: int) -> DraftAxis | None:
        return next((a for a in self.axes if a.axis_id == axis_id), None)


@dataclass(frozen=True, slots=True)
class OpComponentQty:
    component_id: int
    qty_by_size: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class OpComponentSet:
    component_id: int
    role: ComponentRole | None = None
    set_ingredient: bool = False
    ingredient_id: int | None = None
    is_substitutable: bool | None = None
    is_required: bool | None = None


@dataclass(frozen=True, slots=True)
class OpComponentAdd:
    role: ComponentRole
    ingredient_id: int | None
    qty_by_size: Mapping[str, Decimal]
    is_substitutable: bool = False
    is_required: bool = True


@dataclass(frozen=True, slots=True)
class OpComponentRemove:
    component_id: int


@dataclass(frozen=True, slots=True)
class OpPrepSet:
    prep_seconds_by_size: Mapping[str, int]
    is_estimate: bool


@dataclass(frozen=True, slots=True)
class OpBasePrice:
    base_price_pence_by_size: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class OpOptionSet:
    option_id: int
    name: str | None = None
    set_ingredient: bool = False
    ingredient_id: int | None = None
    set_qty: bool = False
    qty_by_size: Mapping[str, Decimal] | None = None
    price_delta_pence: int | None = None
    set_season: bool = False
    season_id: int | None = None


@dataclass(frozen=True, slots=True)
class OpOptionAdd:
    axis_id: int
    name: str
    ingredient_id: int | None
    qty_by_size: Mapping[str, Decimal] | None
    price_delta_pence: int
    season_id: int | None


@dataclass(frozen=True, slots=True)
class OpOptionActive:
    option_id: int
    active: bool


@dataclass(frozen=True, slots=True)
class OpOptionRemove:
    option_id: int


@dataclass(frozen=True, slots=True)
class OpTemplateRename:
    name: str


TemplateOp = (
    OpComponentQty
    | OpComponentSet
    | OpComponentAdd
    | OpComponentRemove
    | OpPrepSet
    | OpBasePrice
    | OpOptionSet
    | OpOptionAdd
    | OpOptionActive
    | OpOptionRemove
    | OpTemplateRename
)


@dataclass(frozen=True, slots=True)
class ChangesetContext:
    """What the operations need to know about the world outside the template."""

    ingredient_names: Mapping[int, str]
    ingredient_units: Mapping[int, Unit]
    retired_ingredient_ids: frozenset[int] = frozenset()
    season_names: Mapping[int, str] = field(default_factory=dict)
    #: Casefolded names of every OTHER template (template.name is unique).
    other_template_names: frozenset[str] = frozenset()
    #: (casefolded name, size key) of menu items NOT on this template.
    taken_item_names: frozenset[tuple[str, str]] = frozenset()
    #: How a new flavour's items are named: "{flavour} Latte". Derived by the service
    #: from the template's existing items, so a new flavour reads like its siblings.
    item_name_pattern: str = "{flavour}"
    #: SUBSTITUTE modifiers by the role they swap, for the "swaps now refused" warning.
    substitute_modifiers_by_role: Mapping[ComponentRole, tuple[str, ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class Refusal:
    op_index: int
    message: str


@dataclass(frozen=True, slots=True)
class ChangesetOutcome:
    before: TemplateDraft
    after: TemplateDraft
    diff: tuple[str, ...]
    refusals: tuple[Refusal, ...]
    warnings: tuple[str, ...]
    pos_actions: tuple[str, ...]


def base_prices(draft: TemplateDraft) -> dict[str, tuple[int | None, bool]]:
    """size -> (base price, items disagree).

    There is no template base price in the schema: each generated item has its own
    `price_pence` and each flavour a `price_delta_pence`. The base is derived as the
    most common `price - delta` at each size (ties to the lower); `True` says the items
    disagree, which the editor must show rather than hide.
    """
    out: dict[str, tuple[int | None, bool]] = {}
    for size in draft.sizes:
        values: list[int] = []
        for item in draft.items:
            if item.size_code != size:
                continue
            delta = sum(
                option.price_delta_pence
                for key in item.option_keys.values()
                if (option := draft.option(key)) is not None
            )
            values.append(item.price_pence - delta)
        if not values:
            out[size.value] = (None, False)
            continue
        counts: dict[int, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out[size.value] = (best, len(counts) > 1)
    return out


def item_spec_from_draft(draft: TemplateDraft, item: DraftItem) -> MenuItemSpec:
    """The resolver's input for one item, from a draft. Both preview sides use this."""
    components = tuple(
        ComponentSpec(
            component_id=c.component_id if c.component_id is not None else -(index + 1),
            role=c.role,
            ingredient_id=c.ingredient_id,
            qty=c.qty(item.size_code),
            is_substitutable=c.is_substitutable,
            is_required=c.is_required,
        )
        for index, c in enumerate(draft.components)
    )
    options: list[VariantOptionSpec] = []
    for index, (axis_id, key) in enumerate(sorted(item.option_keys.items())):
        option = draft.option(key)
        axis = draft.axis(axis_id)
        if option is None or option.removed or axis is None:
            continue
        options.append(
            VariantOptionSpec(
                option_id=option.option_id if option.option_id is not None else -(index + 1000),
                axis_id=axis_id,
                name=option.name,
                role=axis.role,
                ingredient_id=option.ingredient_id,
                qty=option.qty(item.size_code),
                price_delta_pence=option.price_delta_pence,
                season_id=option.season_id,
            )
        )
    return MenuItemSpec(
        menu_item_id=item.menu_item_id if item.menu_item_id is not None else -1,
        name=item.name,
        size_code=item.size_code,
        template_id=draft.template_id,
        price_pence=item.price_pence,
        manual_recipe=False,
        components=components,
        options=tuple(options),
    )


def item_prep(draft: TemplateDraft, item: DraftItem) -> PrepTime:
    return resolve_prep_time(
        template_prep_seconds_by_size=draft.prep_seconds_by_size,
        item_prep_seconds=item.prep_seconds,
        size_code=item.size_code,
        item_is_estimate=item.prep_is_estimate,
        template_is_estimate=draft.prep_is_estimate,
    )


class _Edit:
    """Mutable working copy used while applying operations. Never escapes."""

    def __init__(self, draft: TemplateDraft) -> None:
        self.name = draft.name
        self.components = list(draft.components)
        self.options = list(draft.options)
        self.items = list(draft.items)
        self.prep = dict(draft.prep_seconds_by_size)
        self.prep_est = draft.prep_is_estimate
        self.new_counter = 0

    def next_key(self, prefix: str) -> str:
        self.new_counter += 1
        return f"n{prefix}{self.new_counter}"

    def freeze(self, draft: TemplateDraft) -> TemplateDraft:
        return TemplateDraft(
            template_id=draft.template_id,
            name=self.name,
            category=draft.category,
            sizes=draft.sizes,
            axes=draft.axes,
            components=tuple(self.components),
            options=tuple(self.options),
            items=tuple(self.items),
            prep_seconds_by_size=dict(self.prep),
            prep_is_estimate=self.prep_est,
        )

    def component_index(self, component_id: int) -> int | None:
        return next(
            (i for i, c in enumerate(self.components) if c.component_id == component_id), None
        )

    def option_index(self, option_id: int) -> int | None:
        return next(
            (i for i, o in enumerate(self.options) if o.option_id == option_id and not o.removed),
            None,
        )


class _ChangesetApplier:
    """Applies one changeset to a working copy, one method per operation kind.

    Refusals are collected, never raised: a refused operation is skipped and reported
    with its index so the preview can show every problem at once. Everything in here
    is pure: the only state is the working copy and the four report lists.
    """

    def __init__(self, draft: TemplateDraft, ctx: ChangesetContext) -> None:
        self.draft = draft
        self.ctx = ctx
        self.work = _Edit(draft)
        self.diff: list[str] = []
        self.refusals: list[Refusal] = []
        self.warnings: list[str] = []
        self.pos: list[str] = []
        self.sizes = {s.value for s in draft.sizes}

    # -- shared vocabulary ---------------------------------------------------

    def refuse(self, index: int, reason: str) -> None:
        self.refusals.append(Refusal(index, reason))

    def ing_name(self, ingredient_id: int | None) -> str:
        if ingredient_id is None:
            return "nothing"
        return self.ctx.ingredient_names.get(ingredient_id, f"ingredient #{ingredient_id}")

    def comp_label(self, c: DraftComponent) -> str:
        if c.ingredient_id is not None:
            return self.ing_name(c.ingredient_id)
        return c.role.value.lower()

    def check_ingredient(self, index: int, ingredient_id: int | None) -> bool:
        if ingredient_id is None:
            return True
        if ingredient_id not in self.ctx.ingredient_names:
            self.refuse(index, f"ingredient #{ingredient_id} does not exist")
            return False
        if ingredient_id in self.ctx.retired_ingredient_ids:
            self.refuse(
                index, f"{self.ing_name(ingredient_id)} is retired and cannot go into a recipe"
            )
            return False
        return True

    def check_sizes(self, index: int, keys: Sequence[str]) -> bool:
        bad = [k for k in keys if k not in self.sizes]
        if bad:
            self.refuse(
                index,
                f"size(s) {', '.join(bad)} are not sizes of this recipe "
                f"({', '.join(s.value for s in self.draft.sizes)})",
            )
            return False
        return True

    def item_name(self, flavour: str) -> str:
        return self.ctx.item_name_pattern.replace("{flavour}", flavour).strip()

    def component_at(self, index: int, component_id: int) -> int | None:
        at = self.work.component_index(component_id)
        if at is None:
            self.refuse(index, f"component {component_id} is not in this recipe")
        return at

    def option_at(self, index: int, option_id: int) -> int | None:
        at = self.work.option_index(option_id)
        if at is None:
            self.refuse(index, f"flavour {option_id} is not in this recipe")
        return at

    def reprice_items_with_option(self, option_key: str, change: int) -> None:
        for i, item in enumerate(self.work.items):
            if option_key in item.option_keys.values():
                self.work.items[i] = replace_item(item, price_pence=item.price_pence + change)

    # -- the operations ------------------------------------------------------

    def apply(self, index: int, op: TemplateOp) -> None:
        match op:
            case OpTemplateRename():
                self.rename(index, op)
            case OpComponentQty():
                self.component_qty(index, op)
            case OpComponentSet():
                self.component_set(index, op)
            case OpComponentAdd():
                self.component_add(index, op)
            case OpComponentRemove():
                self.component_remove(index, op)
            case OpPrepSet():
                self.prep_set(index, op)
            case OpBasePrice():
                self.base_price(index, op)
            case OpOptionSet():
                self.option_set(index, op)
            case OpOptionAdd():
                self.option_add(index, op)
            case OpOptionActive():
                self.option_active(index, op)
            case OpOptionRemove():
                self.option_remove(index, op)

    def rename(self, index: int, op: OpTemplateRename) -> None:
        name = op.name.strip()
        if not name:
            self.refuse(index, "a recipe needs a name")
        elif name.casefold() in self.ctx.other_template_names:
            self.refuse(index, f"another recipe is already called {name!r}")
        elif name != self.work.name:
            self.work.name = name
            self.diff.append(f"Renamed to {name}")

    def component_qty(self, index: int, op: OpComponentQty) -> None:
        at = self.component_at(index, op.component_id)
        if at is None or not self.check_sizes(index, list(op.qty_by_size)):
            return
        old = self.work.components[at]
        merged = dict(old.qty_by_size)
        for size_key, qty in op.qty_by_size.items():
            if qty < 0:
                self.refuse(index, f"a quantity cannot be negative ({qty})")
                break
            before = old.qty_by_size.get(size_key)
            if before is not None and before == qty:
                continue
            merged[size_key] = qty
            unit = unit_label(self.ctx.ingredient_units.get(old.ingredient_id or -1))
            self.diff.append(
                f"{self.comp_label(old)} {size_key}: "
                f"{qty_text(before) if before is not None else 'none'} → {qty_text(qty)}"
                + (f" {unit}" if unit else "")
            )
        self.work.components[at] = replace_component(old, qty_by_size=merged)

    def component_set(self, index: int, op: OpComponentSet) -> None:
        at = self.component_at(index, op.component_id)
        if at is None:
            return
        old = self.work.components[at]
        new = old
        if op.set_ingredient and op.ingredient_id != old.ingredient_id:
            if not self.check_ingredient(index, op.ingredient_id):
                return
            if op.ingredient_id is None and not any(
                a.role is (op.role or old.role) for a in self.draft.axes
            ):
                self.refuse(
                    index,
                    f"the {old.role.value.lower()} slot needs an ingredient: only a slot a "
                    "flavour fills can be left empty",
                )
                return
            old_unit = self.ctx.ingredient_units.get(old.ingredient_id or -1)
            new_unit = self.ctx.ingredient_units.get(op.ingredient_id or -1)
            if old_unit is not None and new_unit is not None and old_unit is not new_unit:
                self.warnings.append(
                    f"{self.ing_name(old.ingredient_id)} is measured in {unit_label(old_unit)} "
                    f"and {self.ing_name(op.ingredient_id)} in {unit_label(new_unit)}: the "
                    "quantities are kept as numbers, so check them before applying"
                )
            new = replace_component(new, ingredient_id=op.ingredient_id)
            self.diff.append(
                f"{self.ing_name(old.ingredient_id)} → {self.ing_name(op.ingredient_id)}"
            )
        if op.role is not None and op.role is not old.role:
            new = replace_component(new, role=op.role)
            self.diff.append(
                f"{self.comp_label(old)}: {old.role.value.lower()} → {op.role.value.lower()}"
            )
        if op.is_substitutable is not None and op.is_substitutable != old.is_substitutable:
            new = replace_component(new, is_substitutable=op.is_substitutable)
            self.diff.append(
                f"{self.comp_label(new)} can now be swapped"
                if op.is_substitutable
                else f"{self.comp_label(new)} can no longer be swapped"
            )
            swaps = self.ctx.substitute_modifiers_by_role.get(new.role, ())
            if not op.is_substitutable and swaps:
                self.warnings.append(
                    f"{', '.join(swaps)} swap the {new.role.value.lower()} slot: after this, "
                    "a sale of this recipe with one of those swaps is refused at stock "
                    "expansion rather than guessed at"
                )
        if op.is_required is not None and op.is_required != old.is_required:
            new = replace_component(new, is_required=op.is_required)
            self.diff.append(
                f"{self.comp_label(new)} is now required"
                if op.is_required
                else f"{self.comp_label(new)} is now optional"
            )
        self.work.components[at] = new

    def component_add(self, index: int, op: OpComponentAdd) -> None:
        if not self.check_ingredient(index, op.ingredient_id):
            return
        if op.ingredient_id is None and not any(a.role is op.role for a in self.draft.axes):
            self.refuse(index, "pick an ingredient: only a slot a flavour fills can be empty")
            return
        if not self.check_sizes(index, list(op.qty_by_size)):
            return
        if any(q < 0 for q in op.qty_by_size.values()):
            self.refuse(index, "a quantity cannot be negative")
            return
        added = DraftComponent(
            key=self.work.next_key("c"),
            component_id=None,
            role=op.role,
            ingredient_id=op.ingredient_id,
            qty_by_size=dict(op.qty_by_size),
            is_substitutable=op.is_substitutable,
            is_required=op.is_required,
        )
        self.work.components.append(added)
        self.diff.append(f"Added {self.comp_label(added)}")

    def component_remove(self, index: int, op: OpComponentRemove) -> None:
        at = self.component_at(index, op.component_id)
        if at is None:
            return
        removed = self.work.components.pop(at)
        self.diff.append(f"Removed {self.comp_label(removed)}")

    def prep_set(self, index: int, op: OpPrepSet) -> None:
        if not self.check_sizes(index, list(op.prep_seconds_by_size)):
            return
        if any(s <= 0 for s in op.prep_seconds_by_size.values()):
            self.refuse(index, "a prep time must be more than 0 seconds; clear it instead")
            return
        work = self.work
        for size_key, seconds in sorted(op.prep_seconds_by_size.items()):
            old_seconds = work.prep.get(size_key)
            if old_seconds != seconds:
                self.diff.append(
                    f"Prep {size_key}: "
                    f"{old_seconds if old_seconds is not None else '—'}s → {seconds}s"
                )
                work.prep[size_key] = seconds
        if op.is_estimate != bool(work.prep_est) or work.prep_est is None:
            if work.prep_est is not None or not op.is_estimate:
                self.diff.append(
                    "Prep times marked as estimates"
                    if op.is_estimate
                    else "Prep times marked as timed"
                )
            work.prep_est = op.is_estimate

    def base_price(self, index: int, op: OpBasePrice) -> None:
        if not self.check_sizes(index, list(op.base_price_pence_by_size)):
            return
        if any(p < 0 for p in op.base_price_pence_by_size.values()):
            self.refuse(index, "a price cannot be negative")
            return
        work = self.work
        current = base_prices(work.freeze(self.draft))
        for size_key, new_base in sorted(op.base_price_pence_by_size.items()):
            old_base, _disagree = current.get(size_key, (None, False))
            changed_any = False
            for i, item in enumerate(work.items):
                if (item.size_code.value if item.size_code else "ONE") != size_key:
                    continue
                delta = sum(
                    o.price_delta_pence
                    for k in item.option_keys.values()
                    if (o := next((x for x in work.options if x.key == k), None)) is not None
                )
                new_price = new_base + delta
                if new_price != item.price_pence:
                    work.items[i] = replace_item(item, price_pence=new_price)
                    changed_any = True
            if changed_any or old_base != new_base:
                self.diff.append(
                    f"Price {size_key}: "
                    f"{gbp(old_base) if old_base is not None else '—'} → {gbp(new_base)}"
                )

    def option_set(self, index: int, op: OpOptionSet) -> None:
        at = self.option_at(index, op.option_id)
        if at is None:
            return
        work = self.work
        old_option = work.options[at]
        option = old_option
        if op.name is not None and op.name.strip() and op.name.strip() != old_option.name:
            name = op.name.strip()
            if any(
                o.name.casefold() == name.casefold()
                and o.axis_id == old_option.axis_id
                and not o.removed
                and o.key != old_option.key
                for o in work.options
            ):
                self.refuse(index, f"there is already a flavour called {name!r}")
                return
            self.diff.append(f"{old_option.name} renamed {name}")
            option = replace_option(option, name=name)
        if op.set_ingredient and op.ingredient_id != old_option.ingredient_id:
            if not self.check_ingredient(index, op.ingredient_id):
                return
            self.diff.append(
                f"{option.name}: {self.ing_name(old_option.ingredient_id)} → "
                f"{self.ing_name(op.ingredient_id)}"
            )
            option = replace_option(option, ingredient_id=op.ingredient_id)
        if op.set_qty:
            if op.qty_by_size is not None and not self.check_sizes(index, list(op.qty_by_size)):
                return
            old_q = dict(old_option.qty_by_size or {})
            new_q = dict(op.qty_by_size or {})
            if old_q != new_q:
                for size in self.draft.sizes:
                    a, b = old_q.get(size.value), new_q.get(size.value)
                    if a != b:
                        self.diff.append(
                            f"{option.name} {size.value}: "
                            f"{qty_text(a) if a is not None else 'recipe'} → "
                            f"{qty_text(b) if b is not None else 'recipe'}"
                        )
                option = replace_option(option, qty_by_size=new_q or None, clear_qty=not new_q)
        if (
            op.price_delta_pence is not None
            and op.price_delta_pence != old_option.price_delta_pence
        ):
            change = op.price_delta_pence - old_option.price_delta_pence
            self.diff.append(
                f"{option.name} extra {gbp(old_option.price_delta_pence)} → "
                f"{gbp(op.price_delta_pence)}"
            )
            option = replace_option(option, price_delta_pence=op.price_delta_pence)
            self.reprice_items_with_option(old_option.key, change)
        if op.set_season and op.season_id != old_option.season_id:
            if op.season_id is not None and op.season_id not in self.ctx.season_names:
                self.refuse(index, f"season {op.season_id} does not exist")
                return
            label = (
                self.ctx.season_names.get(op.season_id, "all year") if op.season_id else "all year"
            )
            self.diff.append(f"{option.name} season → {label}")
            option = replace_option(
                option, season_id=op.season_id, clear_season=op.season_id is None
            )
        work.options[at] = option

    def option_add(self, index: int, op: OpOptionAdd) -> None:
        draft, work = self.draft, self.work
        axis = draft.axis(op.axis_id)
        name = op.name.strip()
        if axis is None:
            self.refuse(index, f"axis {op.axis_id} is not in this recipe")
            return
        if not name:
            self.refuse(index, "a flavour needs a name")
            return
        if any(
            o.axis_id == op.axis_id and o.name.casefold() == name.casefold() and not o.removed
            for o in work.options
        ):
            self.refuse(index, f"there is already a flavour called {name!r}")
            return
        if not self.check_ingredient(index, op.ingredient_id):
            return
        if op.qty_by_size is not None and not self.check_sizes(index, list(op.qty_by_size)):
            return
        if op.season_id is not None and op.season_id not in self.ctx.season_names:
            self.refuse(index, f"season {op.season_id} does not exist")
            return
        new_name = self.item_name(name)
        taken = [
            s.value
            for s in draft.sizes
            if (new_name.casefold(), s.value) in self.ctx.taken_item_names
            or any(
                it.name.casefold() == new_name.casefold() and it.size_code is s for it in work.items
            )
        ]
        if taken:
            self.refuse(
                index,
                f"a menu item called {new_name!r} already exists ({', '.join(taken)}). "
                "Moving an existing item onto this recipe would change what its past "
                "sales are deemed to have used, so it is not done here -- name the "
                "flavour differently, or confirm that item's recipe separately",
            )
            return
        key = work.next_key("o")
        work.options.append(
            DraftOption(
                key=key,
                option_id=None,
                axis_id=op.axis_id,
                name=name,
                ingredient_id=op.ingredient_id,
                qty_by_size=dict(op.qty_by_size) if op.qty_by_size else None,
                price_delta_pence=op.price_delta_pence,
                season_id=op.season_id,
            )
        )
        base = base_prices(work.freeze(draft))
        for size in draft.sizes:
            base_price, _ = base.get(size.value, (None, False))
            work.items.append(
                DraftItem(
                    key=work.next_key("i"),
                    menu_item_id=None,
                    name=new_name,
                    size_code=size,
                    option_keys={op.axis_id: key},
                    price_pence=(base_price or 0) + op.price_delta_pence,
                    active=True,
                    on_till=False,
                )
            )
        self.diff.append(f"New flavour: {name}")
        self.pos.append(
            f"Add {new_name} ({', '.join(_size_word(s) for s in draft.sizes)}) in Lightspeed "
            "too, or their sales will not be counted."
        )

    def option_active(self, index: int, op: OpOptionActive) -> None:
        at = self.option_at(index, op.option_id)
        if at is None:
            return
        work = self.work
        option = work.options[at]
        touched = False
        for i, item in enumerate(work.items):
            if option.key in item.option_keys.values() and item.active != op.active:
                work.items[i] = replace_item(item, active=op.active)
                touched = True
        if touched:
            self.diff.append(
                f"{option.name} back on the menu" if op.active else f"{option.name} off the menu"
            )

    def option_remove(self, index: int, op: OpOptionRemove) -> None:
        at = self.option_at(index, op.option_id)
        if at is None:
            return
        work = self.work
        option = work.options[at]
        work.options[at] = replace_option(option, removed=True)
        for i, item in enumerate(work.items):
            if option.key in item.option_keys.values() and item.active:
                work.items[i] = replace_item(item, active=False)
        self.diff.append(f"Removed flavour: {option.name}")

    # -- the result ----------------------------------------------------------

    def outcome(self) -> ChangesetOutcome:
        draft = self.draft
        after = self.work.freeze(draft)

        # Warnings about the state the changeset leaves, not about any one operation.
        flavour_axes = {a.axis_id for a in draft.axes}
        missing = [
            o.name
            for o in after.options
            if not o.removed
            and o.axis_id in flavour_axes
            and o.ingredient_id is None
            and any(it.active and o.key in it.option_keys.values() for it in after.items)
        ]
        if missing:
            self.warnings.append(
                f"{len(missing)} flavour{'s have' if len(missing) != 1 else ' has'} no flavour "
                f"ingredient, so {'their' if len(missing) != 1 else 'its'} cost is too low: "
                + ", ".join(missing)
            )
        if any(it.active and it.price_pence <= 0 for it in after.items):
            self.warnings.append("A size has no price.")
        if after.prep_is_estimate is not False and after.prep_seconds_by_size:
            self.warnings.append("Prep times are estimates, so staff-time figures are too.")

        return ChangesetOutcome(
            before=draft,
            after=after,
            diff=tuple(self.diff),
            refusals=tuple(self.refusals),
            warnings=tuple(dict.fromkeys(self.warnings)),
            pos_actions=tuple(self.pos),
        )


def apply_template_ops(
    draft: TemplateDraft, ops: Sequence[TemplateOp], ctx: ChangesetContext
) -> ChangesetOutcome:
    """Apply a changeset to a draft. Pure: refusals are collected, never raised.

    A refused operation is skipped and reported with its index, so the preview can
    show every problem at once; the apply service refuses the whole changeset if any
    operation was refused (all or nothing).
    """
    applier = _ChangesetApplier(draft, ctx)
    for index, op in enumerate(ops):
        applier.apply(index, op)
    return applier.outcome()


def replace_component(
    c: DraftComponent,
    *,
    role: ComponentRole | None = None,
    ingredient_id: int | object | None = ...,
    qty_by_size: Mapping[str, Decimal] | None = None,
    is_substitutable: bool | None = None,
    is_required: bool | None = None,
) -> DraftComponent:
    return DraftComponent(
        key=c.key,
        component_id=c.component_id,
        role=role if role is not None else c.role,
        ingredient_id=(c.ingredient_id if ingredient_id is ... else _opt_int(ingredient_id)),
        qty_by_size=qty_by_size if qty_by_size is not None else c.qty_by_size,
        is_substitutable=c.is_substitutable if is_substitutable is None else is_substitutable,
        is_required=c.is_required if is_required is None else is_required,
    )


def replace_option(
    o: DraftOption,
    *,
    name: str | None = None,
    ingredient_id: int | object | None = ...,
    qty_by_size: Mapping[str, Decimal] | None = None,
    clear_qty: bool = False,
    price_delta_pence: int | None = None,
    season_id: int | None = None,
    clear_season: bool = False,
    removed: bool | None = None,
) -> DraftOption:
    return DraftOption(
        key=o.key,
        option_id=o.option_id,
        axis_id=o.axis_id,
        name=name if name is not None else o.name,
        ingredient_id=o.ingredient_id if ingredient_id is ... else _opt_int(ingredient_id),
        qty_by_size=None
        if clear_qty
        else (qty_by_size if qty_by_size is not None else o.qty_by_size),
        price_delta_pence=o.price_delta_pence if price_delta_pence is None else price_delta_pence,
        season_id=None if clear_season else (season_id if season_id is not None else o.season_id),
        removed=o.removed if removed is None else removed,
    )


def replace_item(
    item: DraftItem, *, price_pence: int | None = None, active: bool | None = None
) -> DraftItem:
    return DraftItem(
        key=item.key,
        menu_item_id=item.menu_item_id,
        name=item.name,
        size_code=item.size_code,
        option_keys=item.option_keys,
        price_pence=item.price_pence if price_pence is None else price_pence,
        active=item.active if active is None else active,
        prep_seconds=item.prep_seconds,
        prep_is_estimate=item.prep_is_estimate,
        on_till=item.on_till,
    )


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"expected an int id or None, got {value!r}")
