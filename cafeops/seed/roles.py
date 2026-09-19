"""Inferring a ComponentRole for a legacy ingredient.

The workbook has no role column -- roles are this system's idea. Pattern detection
(spec 6 pass 3) cannot group recipes without them: "62 flavoured lattes are one
template with the syrup swapped" is only visible once you know which line IS the
syrup.

Inference is by category first (the workbook's categories are clean and
consistent), then by name for the handful that categories do not separate.
Anything unresolved returns None and is reported rather than guessed -- a
mis-roled ingredient produces a wrong template, and a wrong template costs every
drink that uses it incorrectly.
"""

from __future__ import annotations

from cafeops.domain.types import ComponentRole

#: Category -> role. Covers the 13 categories in the legacy workbook.
ROLE_BY_CATEGORY: dict[str, ComponentRole] = {
    "Coffee": ComponentRole.COFFEE,
    "Dairy": ComponentRole.MILK,
    "Dairy alt": ComponentRole.MILK,
    "Syrup": ComponentRole.FLAVOUR,
    "Packaging": ComponentRole.PACKAGING,
    "Sundries": ComponentRole.SUNDRY,
    "Chocolate": ComponentRole.BASE,
    "Specialty": ComponentRole.BASE,
    "Tea": ComponentRole.BASE,
}

#: Categories whose members are finished goods, not components. An item built from
#: these is a one-off, not a template instance: a wholesale cake has no recipe
#: worth modelling.
STANDALONE_CATEGORIES: frozenset[str] = frozenset({"Bottled", "Food", "Cake", "Cake (CakeSmiths)"})

#: Name fragments that override the category. Lowercase, substring match.
ROLE_BY_NAME_FRAGMENT: tuple[tuple[str, ComponentRole], ...] = (
    ("napkin", ComponentRole.SUNDRY),
    ("straw", ComponentRole.SUNDRY),
    ("stirrer", ComponentRole.SUNDRY),
    ("sugar", ComponentRole.SUNDRY),
    ("cup", ComponentRole.PACKAGING),
    ("lid", ComponentRole.PACKAGING),
    ("sleeve", ComponentRole.PACKAGING),
    ("carrier", ComponentRole.PACKAGING),
    ("cream", ComponentRole.TOPPING),
    ("marshmallow", ComponentRole.TOPPING),
    ("sprinkle", ComponentRole.TOPPING),
    ("wafer", ComponentRole.TOPPING),
    ("topping", ComponentRole.TOPPING),
    ("pearl", ComponentRole.TOPPING),
    ("tapioca", ComponentRole.TOPPING),
    ("milk", ComponentRole.MILK),
    ("syrup", ComponentRole.FLAVOUR),
    ("sauce", ComponentRole.FLAVOUR),
    ("bean", ComponentRole.COFFEE),
    ("espresso", ComponentRole.COFFEE),
    ("matcha", ComponentRole.BASE),
    ("powder", ComponentRole.BASE),
    ("tea bag", ComponentRole.BASE),
)

#: Roles whose specific ingredient is determined by SIZE, not by the recipe's
#: identity. An 8oz cup and a 16oz cup are the same slot at different sizes, so
#: pattern detection must abstract them or every size becomes its own template.
SIZE_DETERMINED_ROLES: frozenset[ComponentRole] = frozenset({ComponentRole.PACKAGING})

#: Roles that vary across the members of a pattern and therefore become a
#: variant axis rather than a fixed component.
AXIS_CANDIDATE_ROLES: frozenset[ComponentRole] = frozenset(
    {ComponentRole.FLAVOUR, ComponentRole.TOPPING}
)


def infer_role(ingredient_name: str, category: str | None) -> ComponentRole | None:
    """Best-effort role for a legacy ingredient. None means "could not tell"."""
    name = (ingredient_name or "").strip().lower()

    # Name fragments win over category: the workbook files napkins and cups under
    # "Sundries" and "Packaging" respectively, but also lists a few packaging
    # items under other categories.
    for fragment, role in ROLE_BY_NAME_FRAGMENT:
        if fragment in name:
            return role

    cat = (category or "").strip()
    if cat in ROLE_BY_CATEGORY:
        return ROLE_BY_CATEGORY[cat]
    if cat in STANDALONE_CATEGORIES:
        return None
    return None


def is_standalone(category: str | None) -> bool:
    return (category or "").strip() in STANDALONE_CATEGORIES
