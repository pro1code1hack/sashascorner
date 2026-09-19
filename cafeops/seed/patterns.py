"""Pattern detection. Spec 6 pass 3.

This module PROPOSES templates. It never writes them. A human confirms each
proposal in the import-review screen (spec 8.6) and only then is composition
written. Auto-generating templates from dirty data and treating them as truth is
how you get a system confidently costing drinks wrong.

The method:

1. Collapse each base item (name without size) to a structural SIGNATURE: which
   roles it uses, and which specific ingredient fills each role -- except for
   roles that are size-determined (packaging) or expected to vary (flavour),
   which are abstracted to the role alone.
2. Group base items by signature. A group of two or more is a candidate template.
3. Within a group, a role whose ingredient differs between members becomes a
   VARIANT AXIS. A role whose ingredient is shared becomes a fixed COMPONENT.
4. Report CONFLICTS: members that disagree on the quantity of a shared component
   at the same size. These are exactly the rows a human must adjudicate, and
   silently averaging them would bake a wrong recipe into 62 drinks.

Pure: takes staged rows in, returns proposals out. No DB writes, no I/O.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal

from cafeops.domain.types import ComponentRole
from cafeops.seed.roles import AXIS_CANDIDATE_ROLES, SIZE_DETERMINED_ROLES

__all__ = [
    "AxisProposal",
    "ComponentProposal",
    "Conflict",
    "StagedLine",
    "TemplateProposal",
    "propose_templates",
]


@dataclass(frozen=True, slots=True)
class StagedLine:
    """One legacy recipe line, already role-tagged."""

    recipe_no: int
    item_name: str
    size_code: str | None
    category: str | None
    sell_price_pence: int
    ingredient_name: str
    qty: Decimal
    role: ComponentRole | None


@dataclass(frozen=True, slots=True)
class ComponentProposal:
    role: ComponentRole
    ingredient_name: str | None  # None when filled by an axis
    #: size code -> quantity, as a string (exact Decimal round-trip)
    qty_by_size: dict[str, str]

    @property
    def is_axis_filled(self) -> bool:
        return self.ingredient_name is None


@dataclass(frozen=True, slots=True)
class AxisProposal:
    name: str
    role: ComponentRole
    #: option name -> ingredient name
    options: dict[str, str]
    qty_by_size: dict[str, str] = field(default_factory=dict)

    @property
    def option_count(self) -> int:
        return len(self.options)


@dataclass(frozen=True, slots=True)
class Conflict:
    role: ComponentRole
    ingredient_name: str
    size_code: str | None
    #: item name -> the quantity it uses
    quantities: dict[str, str]

    def describe(self) -> str:
        n = len(self.quantities)
        size = self.size_code or "-"
        return (
            f"{n} items differ in {self.ingredient_name} qty at size {size} "
            f"({', '.join(sorted(set(self.quantities.values())))})"
        )


@dataclass(frozen=True, slots=True)
class TemplateProposal:
    name: str
    category: str | None
    base_item_names: tuple[str, ...]
    menu_item_count: int
    sizes: tuple[str, ...]
    components: tuple[ComponentProposal, ...]
    axes: tuple[AxisProposal, ...]
    conflicts: tuple[Conflict, ...] = ()
    signature: str = ""

    @property
    def is_singleton(self) -> bool:
        """One base item -- not a pattern. Becomes a manual_recipe item."""
        return len(self.base_item_names) == 1


# --------------------------------------------------------------------------


def _signature(lines: list[StagedLine]) -> str:
    """Structural fingerprint of one base item.

    Size-determined roles (packaging) and axis-candidate roles (flavour, topping)
    contribute their ROLE only. Everything else contributes role + ingredient, so
    a latte and a mocha do not collapse into one template just because both have
    milk and a cup.
    """
    parts: set[str] = set()
    for line in lines:
        if line.role is None:
            parts.add(f"?:{line.ingredient_name}")
        elif line.role in SIZE_DETERMINED_ROLES or line.role in AXIS_CANDIDATE_ROLES:
            parts.add(f"{line.role.value}:*")
        else:
            parts.add(f"{line.role.value}:{line.ingredient_name}")
    return "|".join(sorted(parts))


def _mode(values: list[str | None]) -> str | None:
    counts: dict[str | None, int] = defaultdict(int)
    for v in values:
        counts[v] += 1
    return max(counts, key=lambda k: (counts[k], str(k))) if counts else None


#: Words that never make a useful template name on their own.
_NAME_STOPWORDS = frozenset({"deal", "set", "card", "one", "s", "m", "xl"})


def _template_name(category: str | None, base_names: list[str], *, has_axis: bool) -> str:
    """A readable name for a proposed template.

    Uses the MOST COMMON trailing word of the member names rather than requiring
    unanimity -- a latte group contains "Pistachio Latte" and "Caramel Latte" but
    also the odd "Latte Macchiato", and demanding one shared tail produced
    "Unnamed pattern" for the largest group in the menu.
    """
    if len(base_names) == 1:
        return base_names[0].strip()

    tails: dict[str, int] = defaultdict(int)
    for name in base_names:
        tokens = [t for t in name.strip().split() if t]
        if not tokens:
            continue
        tail = tokens[-1].title()
        if tail.lower() in _NAME_STOPWORDS and len(tokens) > 1:
            tail = tokens[-2].title()
        tails[tail] += 1

    if tails:
        best = max(tails, key=lambda k: (tails[k], k))
        # Only call it "Flavoured X" when a majority actually share the tail;
        # otherwise the name would assert a pattern the names do not support.
        if tails[best] * 2 >= len(base_names):
            return f"Flavoured {best}" if has_axis else f"{best} range"
        # No majority: the group is genuinely heterogeneous. Say so in the name
        # rather than hiding it behind "Unnamed pattern" -- a reviewer needs to
        # know this one wants splitting, and the conflict list will agree.
        return f"Mixed {best} group"
    if category:
        return category.strip().title()
    return "Unnamed pattern"


def _defining_ingredient(components: tuple[ComponentProposal, ...]) -> str | None:
    """The component that most distinguishes one template from a sibling.

    BASE first (matcha powder vs butterfly pea flowers), then COFFEE, then MILK.
    """
    for role in (ComponentRole.BASE, ComponentRole.COFFEE, ComponentRole.MILK):
        for component in components:
            if component.role is role and component.ingredient_name:
                return component.ingredient_name
    return None


def propose_templates(lines: list[StagedLine]) -> list[TemplateProposal]:
    """Group staged legacy recipes into proposed templates.

    Returns proposals sorted by menu_item_count descending, so the biggest wins
    ("Flavoured Latte -- 62 items") are the first thing a reviewer sees.
    """
    # base item name -> size code -> lines
    by_item: dict[str, dict[str | None, list[StagedLine]]] = defaultdict(lambda: defaultdict(list))
    for line in lines:
        by_item[line.item_name][line.size_code].append(line)

    # Signature is computed from any one size's structure -- ingredient identity
    # does not change across sizes, only quantity does.
    sig_by_item: dict[str, str] = {}
    for item_name, sizes in by_item.items():
        first = sizes[sorted(sizes, key=lambda s: (s is None, s or ""))[0]]
        sig_by_item[item_name] = _signature(first)

    groups: dict[str, list[str]] = defaultdict(list)
    for item_name, sig in sig_by_item.items():
        groups[sig].append(item_name)

    proposals: list[TemplateProposal] = []
    for sig, item_names in groups.items():
        proposals.append(_build_proposal(sig, sorted(item_names), by_item))

    proposals.sort(key=lambda p: (-p.menu_item_count, p.name))
    return _disambiguate(proposals)


def _disambiguate(proposals: list[TemplateProposal]) -> list[TemplateProposal]:
    """Make names unique.

    DrinkTemplate.name is unique in the schema, and two matcha groups legitimately
    both want to be called "Flavoured Matcha". Qualify collisions by their
    defining ingredient rather than by a bare counter, so the reviewer can tell
    which is which.
    """
    counts: dict[str, int] = defaultdict(int)
    for p in proposals:
        counts[p.name] += 1

    seen: dict[str, int] = defaultdict(int)
    out: list[TemplateProposal] = []
    for p in proposals:
        if counts[p.name] == 1:
            out.append(p)
            continue
        qualifier = _defining_ingredient(p.components)
        seen[p.name] += 1
        name = f"{p.name} ({qualifier})" if qualifier else f"{p.name} #{seen[p.name]}"
        out.append(replace(p, name=name))
    return out


def _build_proposal(
    signature: str,
    item_names: list[str],
    by_item: dict[str, dict[str | None, list[StagedLine]]],
) -> TemplateProposal:
    all_sizes: set[str] = set()
    categories: list[str | None] = []
    menu_item_count = 0

    # (role, ingredient) -> size -> {item: qty}
    fixed: dict[tuple[ComponentRole, str], dict[str, dict[str, str]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    # role -> {option ingredient name -> option label}
    axis_members: dict[ComponentRole, dict[str, str]] = defaultdict(dict)
    # role -> size -> qty seen (for the axis slot's default)
    axis_qty: dict[ComponentRole, dict[str, str]] = defaultdict(dict)
    unresolved: set[str] = set()

    for item_name in item_names:
        for size_code, lines in by_item[item_name].items():
            menu_item_count += 1
            size_key = size_code or "ONE"
            all_sizes.add(size_key)
            for line in lines:
                categories.append(line.category)
                if line.role is None:
                    unresolved.add(line.ingredient_name)
                    continue
                qty_text = format(line.qty, "f")
                if line.role in AXIS_CANDIDATE_ROLES:
                    axis_members[line.role][line.ingredient_name] = _option_label(
                        line.ingredient_name
                    )
                    axis_qty[line.role][size_key] = qty_text
                else:
                    fixed[(line.role, line.ingredient_name)][size_key][item_name] = qty_text

    # A fixed slot that only some members use is really a varying slot. Treat a
    # role as an axis when its ingredient differs across members.
    by_role: dict[ComponentRole, set[str]] = defaultdict(set)
    for role, ingredient in fixed:
        by_role[role].add(ingredient)

    components: list[ComponentProposal] = []
    axes: list[AxisProposal] = []
    conflicts: list[Conflict] = []

    for role, ingredients in sorted(by_role.items(), key=lambda kv: kv[0].value):
        if len(ingredients) > 1 and role not in SIZE_DETERMINED_ROLES:
            # Varies across members -> an axis.
            options = {_option_label(i): i for i in sorted(ingredients)}
            merged: dict[str, str] = {}
            for ingredient in ingredients:
                for size_key, per_item in fixed[(role, ingredient)].items():
                    merged.setdefault(size_key, next(iter(per_item.values())))
            axes.append(
                AxisProposal(name=_axis_name(role), role=role, options=options, qty_by_size=merged)
            )
            continue

        for ingredient in sorted(ingredients):
            per_size = fixed[(role, ingredient)]
            qty_by_size: dict[str, str] = {}
            for size_key, per_item in sorted(per_size.items()):
                distinct = sorted(set(per_item.values()))
                qty_by_size[size_key] = distinct[0]
                if len(distinct) > 1:
                    conflicts.append(
                        Conflict(
                            role=role,
                            ingredient_name=ingredient,
                            size_code=size_key,
                            quantities=dict(per_item),
                        )
                    )
            components.append(
                ComponentProposal(role=role, ingredient_name=ingredient, qty_by_size=qty_by_size)
            )

    for role, options in sorted(axis_members.items(), key=lambda kv: kv[0].value):
        axes.append(
            AxisProposal(
                name=_axis_name(role),
                role=role,
                options={label: ing for ing, label in options.items()},
                qty_by_size=dict(axis_qty[role]),
            )
        )
        components.append(
            ComponentProposal(role=role, ingredient_name=None, qty_by_size=dict(axis_qty[role]))
        )

    category = _mode(categories)
    proposal = TemplateProposal(
        name=_template_name(category, item_names, has_axis=bool(axes)),
        category=category,
        base_item_names=tuple(item_names),
        menu_item_count=menu_item_count,
        sizes=tuple(sorted(all_sizes, key=_size_sort_key)),
        components=tuple(components),
        axes=tuple(axes),
        conflicts=tuple(conflicts),
        signature=signature,
    )
    return proposal


_SIZE_ORDER = {"S": 0, "M": 1, "XL": 2, "ONE": 3}


def _size_sort_key(size: str) -> tuple[int, str]:
    return (_SIZE_ORDER.get(size, 9), size)


def _option_label(ingredient_name: str) -> str:
    """ "Pistachio syrup (Monin)" -> "Pistachio"."""
    name = ingredient_name
    for suffix in (" syrup (Monin)", " syrup", " sauce", " powder", " (barista)"):
        if name.lower().endswith(suffix.lower()):
            name = name[: -len(suffix)]
            break
    return name.strip().title() or ingredient_name


def _axis_name(role: ComponentRole) -> str:
    return {ComponentRole.FLAVOUR: "Flavour", ComponentRole.TOPPING: "Topping"}.get(
        role, role.value.title()
    )
