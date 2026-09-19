"""Prep-time estimates. Spec 4.2 and 5.6.

Prep time is NOT in the legacy workbook, exactly like shelf life (`seed/shelf_life.py`,
spec 15 q3). Without it `labour_cost`, `true_margin` and `margin_per_minute` are all
`None`, so spec 5.6's central finding -- that ranking the menu by margin-per-minute
disagrees with ranking it by margin % -- cannot be computed at all.

So these are seeded, and **every one of them is written as an ESTIMATE**
(`prep_seconds_is_estimate = True`), surfaced wherever the number is. They are
plausible barista times for a two-person counter, not measurements of anybody at
Sasha's Corner. The same treatment prices and shelf lives get, for the same reason
(invariant 8).

Two deliberate refusals:

1. **Nothing gets a made-up number just to fill the column.** An item no rule
   recognises stays `NULL`, and its labour figures stay `None` rather than becoming a
   flattering guess. The untimed list is a worklist, and `cafeops prep-times` prints
   it.
2. **Bundles and add-ons are left unset on purpose.** A "Lunch Deal" takes as long as
   the things inside it, and "Extra shot" is a modifier that happens to be sellable --
   its prep time belongs to the drink it is added to. Assigning either one a number
   would double-count staff time.

The one that matters most is the toasted range: a panini at ~3.5 minutes of counter
time against an espresso at ~50 seconds is what makes the two rankings disagree, and
the disagreement is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import DrinkTemplate, MenuItem, SizeCode

__all__ = [
    "PREP_RULES",
    "TEMPLATE_PREP_SECONDS",
    "PrepEstimate",
    "PrepTimeReport",
    "estimate_for",
    "seed_prep_times",
]


@dataclass(frozen=True, slots=True)
class PrepEstimate:
    """Seconds of counter time at size M, and why.

    `seconds is None` means "deliberately not timed", which is a decision and not a
    gap -- `reason` says which.
    """

    seconds: int | None
    rule: str
    reason: str


#: Per-size prep seconds for materialised templates, keyed by template name. A
#: template default covers every leaf it generates, which is the whole point of the
#: template model: adding a flavour must not mean typing a prep time again.
#:
#: A flavoured latte at M: grind, dose and tamp ~20s, extract ~28s, steam and texture
#: the milk ~30s, syrup, pour and lid ~7s. XL steams more milk; S steams less.
TEMPLATE_PREP_SECONDS: dict[str, dict[str, int]] = {
    "Flavoured Latte": {"S": 75, "M": 85, "XL": 100},
}

#: Menu items that override their template. A per-item override exists so one leaf
#: can differ from its pattern, and a seeded example proves the override path is
#: actually wired rather than merely present.
#:
#: The Pistachio XL is genuinely slower: the pistachio syrup is thick, needs stirring
#: through rather than a pump-and-pour, and the XL gets a pistachio dust finish.
ITEM_PREP_OVERRIDES: dict[tuple[str, str | None], int] = {
    ("Pistachio Latte", "XL"): 135,
}

#: Ordered. FIRST MATCH WINS, so the order is the rule set -- "Iced Banana Matcha"
#: must reach the matcha rule (whisking dominates) before the generic iced rule, and
#: every bundle and add-on must be excluded before anything can claim it.
PREP_RULES: tuple[tuple[tuple[str, ...], PrepEstimate], ...] = (
    # --- deliberately NOT timed ------------------------------------------------
    (
        ("deal",),
        PrepEstimate(
            None,
            "bundle",
            "a meal deal takes as long as the things inside it; timing the bundle as "
            "well would count the same staff minutes twice",
        ),
    ),
    (
        ("(add-on)", "add-on", "(+upcharge)", "extra shot"),
        PrepEstimate(
            None,
            "modifier sold as an item",
            "its prep time belongs to the drink it is added to, not to a line of its own",
        ),
    ),
    (
        ("gift set", "'card'"),
        PrepEstimate(
            None,
            "not made on the counter",
            "retail, not preparation -- already on the data-quality list (spec 6)",
        ),
    ),
    # --- the slow end: this is what makes the two rankings disagree -----------
    (
        ("panini", "toastie"),
        PrepEstimate(220, "toasted", "filled, pressed, cut and plated; the grill sets the pace"),
    ),
    (
        ("pancake",),
        PrepEstimate(180, "griddled", "cooked to order"),
    ),
    (
        ("bubble tea",),
        PrepEstimate(150, "bubble tea", "boba portioned, shaken, sealed"),
    ),
    (
        ("milkshake",),
        PrepEstimate(165, "blended", "blender, pour, and then wash the blender"),
    ),
    (
        ("croissant", "roll"),
        PrepEstimate(100, "filled and warmed", "filled, warmed through, plated"),
    ),
    # --- drinks ---------------------------------------------------------------
    (
        ("matcha",),
        PrepEstimate(105, "matcha", "sifted and whisked; the whisking does not scale down"),
    ),
    (
        ("hot chocolate", "chai", "babyccino", "raff"),
        PrepEstimate(80, "steamed milk drink", "powder or paste, steamed milk, finish"),
    ),
    (
        ("iced",),
        PrepEstimate(70, "iced", "no texturing, but ice, shake and a taller build"),
    ),
    (
        ("espresso", "americano", "macchiato", "cortado"),
        PrepEstimate(50, "espresso-based", "grind, tamp, extract, top up"),
    ),
    (
        ("latte", "cappuccino", "flat white", "mocha"),
        PrepEstimate(85, "textured milk", "extract and texture milk to a standard worth serving"),
    ),
    (
        ("lemonade", "tonic"),
        PrepEstimate(90, "built soft drink", "syrup, ice, top, garnish"),
    ),
    (
        ("tea", "earl grey", "english breakfast", "green", "mint", "honey", "jasmine"),
        PrepEstimate(45, "tea", "pot or cup, water, leave to brew while the next order starts"),
    ),
    # --- food off the shelf ---------------------------------------------------
    (
        (
            "muffin",
            "brownie",
            "cheesecake",
            "shortbread",
            "cookie",
            "wafer",
            "cake",
            "biscoff",
            "bar",
        ),
        PrepEstimate(30, "bakery, plated", "taken from the counter display and plated"),
    ),
    (
        ("crisps", "pringles", "pom-bear", "marshmallow"),
        PrepEstimate(10, "packaged", "handed over"),
    ),
    (
        ("cola", "fanta", "capri-sun", "tropicana", "juice", "water"),
        PrepEstimate(15, "bottle or can", "out of the fridge, opened if asked"),
    ),
)

#: Bigger drinks take marginally longer -- more milk to texture, a taller build --
#: but not proportionally: the grind, the extraction and the lid do not change. So a
#: small multiplier, applied to the rule's size-M figure and rounded to 5s. Rounding
#: to 5s is honest about the precision on offer: nobody can estimate a latte to the
#: second, and a table of 78s / 85s / 97s would imply somebody had.
SIZE_FACTOR: dict[str, Decimal] = {
    SizeCode.S.value: Decimal("0.90"),
    SizeCode.M.value: Decimal("1.00"),
    SizeCode.XL.value: Decimal("1.15"),
    SizeCode.ONE.value: Decimal("1.00"),
}

UNMATCHED = PrepEstimate(
    None,
    "no rule matched",
    "nobody has timed this and no rule recognises it; its labour figures stay UNKNOWN "
    "rather than becoming a guess",
)


def estimate_for(name: str, size_code: str | None) -> PrepEstimate:
    """The prep-time estimate for one item name, at one size.

    Matched on the NAME, not the category: 252 of the 314 seeded menu items have no
    category at all, and the ones that do carry the workbook's own spelling
    (`Spring saesonal drinks`). The names are the only reliable signal in the file.
    """
    lowered = name.casefold()
    for needles, estimate in PREP_RULES:
        if not any(needle in lowered for needle in needles):
            continue
        if estimate.seconds is None:
            return estimate
        factor = SIZE_FACTOR.get((size_code or SizeCode.ONE.value).upper(), Decimal("1.00"))
        scaled = (Decimal(estimate.seconds) * factor / 5).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ) * 5
        return PrepEstimate(int(scaled), estimate.rule, estimate.reason)
    return UNMATCHED


@dataclass
class PrepTimeReport:
    templates_set: int = 0
    items_estimated: int = 0
    items_overridden: int = 0
    items_left_unset: int = 0
    items_template_driven: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)
    unset_examples: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.templates_set} template(s) given per-size prep times",
            f"{self.items_estimated} menu item(s) estimated",
            f"{self.items_overridden} explicit override(s)",
            f"{self.items_template_driven} left to their template",
            f"{self.items_left_unset} left UNSET (labour stays None, not zero)",
        ]
        return "; ".join(parts)


def seed_prep_times(session: Session) -> PrepTimeReport:
    """Write prep-time ESTIMATES onto templates and menu items. Idempotent.

    Three passes, in this order because the later ones depend on the earlier:

    1. Templates get `prep_seconds_by_size`, which covers every leaf they generate.
    2. Template-driven items are left alone -- writing an item-level number for each
       leaf would defeat the template and make a per-size edit a 9-row job.
    3. Everything else gets a name-matched estimate, or stays NULL.

    Re-running does not overwrite a value already there: a human who has timed a
    panini must not have their measurement replaced by this file's guess on the next
    reseed.
    """
    report = PrepTimeReport()

    # --- 1. templates --------------------------------------------------------
    for template in session.scalars(select(DrinkTemplate)):
        wanted = TEMPLATE_PREP_SECONDS.get(template.name)
        if wanted is None or template.prep_seconds_by_size:
            continue
        template.prep_seconds_by_size = dict(wanted)
        template.prep_seconds_is_estimate = True
        report.templates_set += 1
    session.flush()

    # --- 2 and 3. menu items -------------------------------------------------
    for item in session.scalars(select(MenuItem).order_by(MenuItem.name, MenuItem.size_code)):
        size = item.size_code.value if item.size_code is not None else None

        override = ITEM_PREP_OVERRIDES.get((item.name, size))
        if override is not None:
            if item.prep_seconds is None:
                item.prep_seconds = override
                item.prep_seconds_is_estimate = True
                report.items_overridden += 1
            continue

        if item.template_id is not None:
            # The template answers for it. Nothing to write, and writing anything
            # here would silently win over the template for every future edit.
            report.items_template_driven += 1
            continue

        if item.prep_seconds is not None:
            continue

        estimate = estimate_for(item.name, size)
        if estimate.seconds is None:
            report.items_left_unset += 1
            if len(report.unset_examples) < 12:
                report.unset_examples.append(f"{item.name} ({estimate.rule})")
            continue
        item.prep_seconds = estimate.seconds
        item.prep_seconds_is_estimate = True
        report.items_estimated += 1
        report.by_rule[estimate.rule] = report.by_rule.get(estimate.rule, 0) + 1
    session.flush()

    if report.items_left_unset:
        report.warnings.append(
            f"{report.items_left_unset} menu item(s) have NO prep time, so their labour "
            "cost, true margin and margin-per-minute are None rather than zero. That is "
            "correct and it is also a worklist."
        )
    if report.items_estimated or report.templates_set:
        report.warnings.append(
            "Every prep time written here is an ESTIMATE, not a measurement. The "
            "margin-per-minute ranking is only as good as they are, and the ~15 items "
            "that actually sell are the ones worth timing with a stopwatch."
        )
    return report
