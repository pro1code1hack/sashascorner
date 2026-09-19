"""Prep time turned into money, and the ranking that disagrees with margin %. Spec 5.6.

    labour_cost       = prep_seconds / 3600 * loaded_hourly_rate_pence   (GBP 14.50/hr)
    true_margin       = price - ingredient_cost - labour_cost
    margin_per_minute = (price - ingredient_cost) / (prep_seconds / 60)

`LabourCost` in `domain/types.py` already does that arithmetic for ONE item. This
module is the aggregation around it: resolving which prep time applies, rolling
labour up per template and per menu, and ranking the menu two ways so the
disagreement between them is visible.

**The disagreement is the finding, not a bug to reconcile.** Verified with the
figures from spec 5.6: a GBP 4.00 drink at 85% margin taking 3 minutes yields
113p/min, while one at 70% taking 40 seconds yields 420p/min. Margin % says the
first is better; margin-per-minute says the second earns nearly four times as much
of the only resource that is actually scarce at 11am. Both are true. This module
reports both orderings and names the items they disagree about most, and it
deliberately does not compute a blended score -- a single number would hide exactly
the tension the owner needs to see.

Three rules, all of them refusals:

1. **No prep time, no labour figure.** Every labour number is `None` when prep
   seconds or the loaded rate is unset. A labour cost derived from a guessed rate is
   a guess wearing a number's clothes, and it is indistinguishable from a real one
   once it is in a table.
2. **An unknown ingredient cost poisons the labour figures too.** `true_margin` and
   `margin_per_minute` both need it, so both stay `None` rather than reporting the
   price as if it were all contribution (invariant 8).
3. **Excluded items are named, never dropped silently.** An aggregate that quietly
   covers 40% of the menu is worse than no aggregate, because it looks complete.

Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O, no `config` import --
the loaded hourly rate arrives as an argument, because a domain module that reads
settings is a domain module that cannot be asked "and what if the rate were 15.50?".
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from cafeops.domain.types import LabourCost, SizeCode

__all__ = [
    "ItemLabour",
    "LabourRollup",
    "MarginRanking",
    "PrepTime",
    "PrepTimeSource",
    "RankedItem",
    "labour_for",
    "rank_menu",
    "resolve_prep_time",
    "rollup_labour",
]


class PrepTimeSource(enum.StrEnum):
    """Where an item's prep time came from.

    Provenance is not decoration here: a number set on the menu item and a per-size
    template default are edited in different screens, and `UNSET` is the reason a
    labour figure is missing. Without this the margin table can only say "no number"
    and not "nobody has timed a panini yet".

    `MENU_ITEM` covers both cases where the number lives on the item itself: a one-off
    with no template, and a template-driven leaf whose `prep_seconds` overrides its
    pattern. They are the same column and the same edit screen.
    """

    MENU_ITEM = "MENU_ITEM"
    TEMPLATE_SIZE = "TEMPLATE_SIZE"
    UNSET = "UNSET"


@dataclass(frozen=True, slots=True)
class PrepTime:
    """How long one item takes to make, and how much that claim is worth.

    `is_estimate` travels with the number for the same reason `PriceSource` does
    (invariant 8): every prep time in the system today is a plausible guess, and a
    margin-per-minute ranking built on guesses must say so on the screen that shows
    it, not in a commit message.
    """

    seconds: int | None
    source: PrepTimeSource
    is_estimate: bool

    @property
    def is_known(self) -> bool:
        return self.seconds is not None and self.seconds > 0

    @property
    def minutes(self) -> Decimal | None:
        if self.seconds is None:
            return None
        return Decimal(self.seconds) / Decimal(60)

    def describe(self) -> str:
        if self.seconds is None:
            return "not timed"
        flag = " (estimate)" if self.is_estimate else ""
        return f"{self.seconds}s{flag}"


#: The absence of a prep time, as a value. Used instead of `None` so callers always
#: get a `PrepTime` and the reason is readable rather than inferred.
UNTIMED = PrepTime(seconds=None, source=PrepTimeSource.UNSET, is_estimate=False)


def resolve_prep_time(
    *,
    template_prep_seconds_by_size: Mapping[str, int] | None,
    item_prep_seconds: int | None,
    size_code: SizeCode | None,
    item_is_estimate: bool | None = None,
    template_is_estimate: bool | None = None,
) -> PrepTime:
    """Which prep time applies to one menu item. The item overrides its template.

    Spec 4.2: `drink_template.prep_seconds_by_size` is the pattern's default and
    `menu_item.prep_seconds` overrides it. The override wins unconditionally -- an
    iced version of a hot drink genuinely takes a different time, and a template
    whose per-size defaults could silently beat an explicit per-item number would
    make the override field a lie.

    A non-positive prep time is treated as UNSET rather than as zero. Zero seconds
    would make `margin_per_minute` infinite, and "this takes no time" is not a claim
    anybody has made about anything on this menu.
    """
    if item_prep_seconds is not None and item_prep_seconds > 0:
        return PrepTime(
            seconds=item_prep_seconds,
            source=PrepTimeSource.MENU_ITEM,
            # An override with no flag of its own is assumed estimated: nothing in
            # this system has been stopwatched yet, and assuming "measured" would
            # promote a guess for free.
            is_estimate=True if item_is_estimate is None else item_is_estimate,
        )

    if template_prep_seconds_by_size:
        key = size_code.value if size_code is not None else SizeCode.ONE.value
        raw = template_prep_seconds_by_size.get(key)
        if raw is not None and int(raw) > 0:
            return PrepTime(
                seconds=int(raw),
                source=PrepTimeSource.TEMPLATE_SIZE,
                is_estimate=True if template_is_estimate is None else template_is_estimate,
            )

    return UNTIMED


def labour_for(
    *,
    menu_item_id: int,
    price_pence: int,
    ingredient_cost_pence: Decimal | None,
    prep: PrepTime,
    loaded_hourly_rate_pence: int | None,
) -> LabourCost:
    """One item's `LabourCost`, with the rate refused when it is not really set.

    A rate of zero or a negative rate is `None`, not 0p/hr. "Labour is free" is a
    claim no café can make, so a misconfigured rate must read as missing rather than
    as a flattering true margin.
    """
    rate = (
        loaded_hourly_rate_pence
        if loaded_hourly_rate_pence is not None and loaded_hourly_rate_pence > 0
        else None
    )
    return LabourCost(
        menu_item_id=menu_item_id,
        prep_seconds=prep.seconds if prep.is_known else None,
        loaded_hourly_rate_pence=rate,
        ingredient_cost_pence=ingredient_cost_pence,
        price_pence=price_pence,
    )


# ==========================================================================
# One item, ready to rank
# ==========================================================================


@dataclass(frozen=True, slots=True)
class ItemLabour:
    """A menu item's labour picture: the maths, the provenance and the volume.

    `LabourCost` deliberately knows nothing about names, sizes or how many sold --
    it is the arithmetic. Ranking and rolling up need all three, so they live here
    rather than being requested as fields on an integrator-owned type.
    """

    labour: LabourCost
    name: str
    size_code: SizeCode | None
    prep: PrepTime
    #: Net units sold over the rollup window. Weights every aggregate below.
    units_sold: Decimal = Decimal("0")
    template_id: int | None = None
    template_name: str | None = None

    @property
    def menu_item_id(self) -> int:
        return self.labour.menu_item_id

    @property
    def label(self) -> str:
        return f"{self.name} {self.size_code.value}" if self.size_code else self.name

    @property
    def price_pence(self) -> int:
        return self.labour.price_pence

    @property
    def ingredient_cost_pence(self) -> Decimal | None:
        return self.labour.ingredient_cost_pence

    @property
    def contribution_pence(self) -> Decimal | None:
        """Price less ingredients, before labour. The numerator of margin-per-minute."""
        cost = self.labour.ingredient_cost_pence
        if cost is None:
            return None
        return Decimal(self.price_pence) - cost

    @property
    def margin_pct(self) -> float | None:
        """Plain gross margin on ingredients only -- the number the menu is priced on."""
        contribution = self.contribution_pence
        if contribution is None or self.price_pence <= 0:
            return None
        return float(contribution / Decimal(self.price_pence) * 100)

    @property
    def true_margin_pct(self) -> float | None:
        """Margin after labour. Lower than `margin_pct`, always, and by a lot on slow drinks."""
        true_margin = self.labour.true_margin_pence
        if true_margin is None or self.price_pence <= 0:
            return None
        return float(true_margin / Decimal(self.price_pence) * 100)

    @property
    def margin_per_minute_pence(self) -> Decimal | None:
        return self.labour.margin_per_minute_pence

    @property
    def can_rank(self) -> bool:
        """Rankable only when BOTH orderings can place it.

        An item ranked on one axis and absent from the other would make the two
        tables incomparable, which is the whole point of printing them together.
        """
        return self.margin_pct is not None and self.margin_per_minute_pence is not None

    def exclusion_reason(self) -> str | None:
        """Why this item cannot be ranked, in words a person can act on."""
        if self.can_rank:
            return None
        reasons: list[str] = []
        if self.labour.ingredient_cost_pence is None:
            reasons.append("ingredient cost unknown")
        if not self.prep.is_known:
            reasons.append("prep time not set")
        if self.labour.loaded_hourly_rate_pence is None:
            reasons.append("loaded hourly rate not set")
        if self.price_pence <= 0:
            reasons.append("no sale price")
        return "; ".join(reasons) if reasons else "not rankable"


# ==========================================================================
# The two orderings, side by side
# ==========================================================================


@dataclass(frozen=True, slots=True)
class RankedItem:
    """One item's position in both orderings, and the gap between them."""

    item: ItemLabour
    #: 1 is the best. Both ranks are dense over the SAME rankable set.
    margin_rank: int
    margin_per_minute_rank: int

    @property
    def rank_delta(self) -> int:
        """Positive means margin-per-minute rates it HIGHER than margin % does.

        A large positive delta is a fast, thin-margin item the margin screen
        undersells. A large negative delta is a flattering high-margin item that
        eats the counter at the peak hour.
        """
        return self.margin_rank - self.margin_per_minute_rank

    @property
    def disagreement(self) -> int:
        return abs(self.rank_delta)


@dataclass(frozen=True, slots=True)
class MarginRanking:
    """The menu ranked twice, and what the two rankings disagree about.

    No blended score is offered. Averaging margin % with margin-per-minute would
    produce a number that answers neither question the owner is asking -- "am I
    pricing this right" and "should I still be selling this when there is a queue" --
    and it would hide the disagreement that spec 5.6 calls the finding.
    """

    by_margin_pct: tuple[ItemLabour, ...]
    by_margin_per_minute: tuple[ItemLabour, ...]
    ranked: tuple[RankedItem, ...]
    #: (item, reason) for everything that could not be ranked. Named, never dropped.
    excluded: tuple[tuple[ItemLabour, str], ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def rankable_count(self) -> int:
        return len(self.ranked)

    @property
    def orderings_agree(self) -> bool:
        """True only if the two tables are in identical order.

        Expected to be False. If it is ever True on a real menu, either every item
        takes the same time to make or the prep times are not real.
        """
        return tuple(i.menu_item_id for i in self.by_margin_pct) == tuple(
            i.menu_item_id for i in self.by_margin_per_minute
        )

    @property
    def biggest_disagreements(self) -> tuple[RankedItem, ...]:
        """Ranked items ordered by how far the two views move them, worst first."""
        return tuple(sorted(self.ranked, key=lambda r: (-r.disagreement, r.margin_rank)))

    def by_id(self, menu_item_id: int) -> RankedItem | None:
        for entry in self.ranked:
            if entry.item.menu_item_id == menu_item_id:
                return entry
        return None


def rank_menu(items: Sequence[ItemLabour]) -> MarginRanking:
    """Rank the menu by margin % and by margin-per-minute, over the same item set.

    Items that cannot be placed on both axes are excluded from BOTH and named in
    `excluded`. Ranking an item on one axis only would let a reader compare two
    tables that are not about the same menu.
    """
    rankable = [item for item in items if item.can_rank]
    excluded = tuple(
        (item, item.exclusion_reason() or "not rankable") for item in items if not item.can_rank
    )

    warnings: list[str] = []
    if excluded:
        warnings.append(
            f"{len(excluded)} of {len(items)} item(s) cannot be ranked and are listed "
            "separately rather than sorted to the bottom as zeroes (invariant 8)"
        )
    estimated = [item for item in rankable if item.prep.is_estimate]
    if estimated:
        warnings.append(
            f"{len(estimated)} of {len(rankable)} ranked item(s) use an ESTIMATED prep "
            "time, so the margin-per-minute ordering is only as good as those estimates"
        )

    # Tie-break by label so the ordering is stable across runs -- a ranking that
    # reshuffles equal items between two invocations is unreadable as a report.
    by_margin = tuple(
        sorted(rankable, key=lambda i: (-(i.margin_pct or 0.0), i.label, i.menu_item_id))
    )
    by_minute = tuple(
        sorted(
            rankable,
            key=lambda i: (-(i.margin_per_minute_pence or Decimal(0)), i.label, i.menu_item_id),
        )
    )

    margin_rank = {item.menu_item_id: n for n, item in enumerate(by_margin, start=1)}
    minute_rank = {item.menu_item_id: n for n, item in enumerate(by_minute, start=1)}
    ranked = tuple(
        RankedItem(
            item=item,
            margin_rank=margin_rank[item.menu_item_id],
            margin_per_minute_rank=minute_rank[item.menu_item_id],
        )
        for item in by_margin
    )

    if ranked and all(entry.rank_delta == 0 for entry in ranked):
        warnings.append(
            "the two orderings agree exactly, which on a real menu means every ranked "
            "item has the same prep time -- check the prep times before trusting this"
        )

    return MarginRanking(
        by_margin_pct=by_margin,
        by_margin_per_minute=by_minute,
        ranked=ranked,
        excluded=excluded,
        warnings=tuple(warnings),
    )


# ==========================================================================
# Labour rolled up: per template, and per menu
# ==========================================================================


@dataclass(frozen=True, slots=True)
class LabourRollup:
    """Labour for a group of items, weighted by what actually sold.

    Every total is `None` when nothing in the group could be included, rather than
    zero: "this template cost no labour" and "we cannot say what this template cost"
    are different claims, and only one of them is ever true here.

    Totals are over `units_sold`, so this is money and staff time actually spent in
    the window -- not a per-item figure multiplied by a guess at volume.
    """

    label: str
    template_id: int | None = None
    items_total: int = 0
    items_included: int = 0
    #: Sum over items of prep_seconds * units_sold.
    prep_seconds_total: Decimal | None = None
    labour_cost_pence_total: Decimal | None = None
    ingredient_cost_pence_total: Decimal | None = None
    revenue_pence_total: Decimal | None = None
    units_total: Decimal = Decimal("0")
    #: (label, reason) for each item left out of the totals.
    excluded: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def staff_hours(self) -> Decimal | None:
        if self.prep_seconds_total is None:
            return None
        return self.prep_seconds_total / Decimal(3600)

    @property
    def contribution_pence_total(self) -> Decimal | None:
        """Revenue less ingredients. What margin-per-minute is measuring."""
        if self.revenue_pence_total is None or self.ingredient_cost_pence_total is None:
            return None
        return self.revenue_pence_total - self.ingredient_cost_pence_total

    @property
    def true_margin_pence_total(self) -> Decimal | None:
        contribution = self.contribution_pence_total
        if contribution is None or self.labour_cost_pence_total is None:
            return None
        return contribution - self.labour_cost_pence_total

    @property
    def margin_per_minute_pence(self) -> Decimal | None:
        """Group contribution per staff minute spent. Comparable across templates."""
        contribution = self.contribution_pence_total
        if contribution is None or not self.prep_seconds_total:
            return None
        minutes = self.prep_seconds_total / Decimal(60)
        if minutes <= 0:
            return None
        return contribution / minutes

    @property
    def labour_share_of_revenue_pct(self) -> float | None:
        if (
            self.labour_cost_pence_total is None
            or self.revenue_pence_total is None
            or self.revenue_pence_total <= 0
        ):
            return None
        return float(self.labour_cost_pence_total / self.revenue_pence_total * 100)

    @property
    def is_complete(self) -> bool:
        """False when anything was left out. A partial total must say so."""
        return not self.excluded

    def summary(self) -> str:
        if self.items_included == 0:
            return (
                f"{self.label}: no item has both a cost and a prep time, so no labour "
                f"figure is reported ({self.items_total} item(s) considered)"
            )
        hours = self.staff_hours
        parts = [
            f"{self.label}: {self.items_included} of {self.items_total} item(s)",
            f"{self.units_total} unit(s) sold",
        ]
        if hours is not None:
            parts.append(f"{hours:.2f} staff hours")
        if self.labour_cost_pence_total is not None:
            parts.append(f"labour GBP {self.labour_cost_pence_total / 100:.2f}")
        per_minute = self.margin_per_minute_pence
        if per_minute is not None:
            parts.append(f"{per_minute:.1f}p contribution per staff minute")
        if self.excluded:
            parts.append(f"{len(self.excluded)} item(s) EXCLUDED (see below)")
        return "; ".join(parts)


def rollup_labour(
    items: Sequence[ItemLabour],
    *,
    label: str,
    template_id: int | None = None,
) -> LabourRollup:
    """Weighted labour totals for a group of items -- one template, or the whole menu.

    An item is included only when it has a labour cost AND an ingredient cost. Half
    an item's economics summed into a total would make the total wrong in a direction
    nobody could detect, so it is excluded and named instead (invariant 8).

    An included item with zero sales contributes zero to the totals, which is
    correct: it cost no staff time because nobody ordered it. It still counts as
    included, because its absence from the totals is a fact about volume rather than
    about missing data.
    """
    included: list[ItemLabour] = []
    excluded: list[tuple[str, str]] = []
    for item in items:
        labour_cost = item.labour.labour_cost_pence
        if labour_cost is None or item.labour.ingredient_cost_pence is None:
            excluded.append((item.label, item.exclusion_reason() or "labour not computable"))
            continue
        included.append(item)

    warnings: list[str] = []
    if excluded:
        warnings.append(
            f"{len(excluded)} of {len(items)} item(s) are EXCLUDED from every total "
            "below -- they are not counted as zero, so these figures cover only the "
            f"{len(included)} item(s) that have both a cost and a prep time"
        )
    if included and any(item.prep.is_estimate for item in included):
        estimated = sum(1 for item in included if item.prep.is_estimate)
        warnings.append(
            f"{estimated} of {len(included)} included item(s) use an ESTIMATED prep time"
        )

    if not included:
        return LabourRollup(
            label=label,
            template_id=template_id,
            items_total=len(items),
            items_included=0,
            excluded=tuple(excluded),
            warnings=tuple(warnings),
        )

    prep_seconds = Decimal("0")
    labour_total = Decimal("0")
    ingredient_total = Decimal("0")
    revenue_total = Decimal("0")
    units_total = Decimal("0")
    for item in included:
        units = item.units_sold
        seconds = item.labour.prep_seconds
        labour_cost = item.labour.labour_cost_pence
        ingredient_cost = item.labour.ingredient_cost_pence
        # Narrowing for mypy; the filter above already guarantees all three.
        if seconds is None or labour_cost is None or ingredient_cost is None:  # pragma: no cover
            continue
        prep_seconds += Decimal(seconds) * units
        labour_total += labour_cost * units
        ingredient_total += ingredient_cost * units
        revenue_total += Decimal(item.price_pence) * units
        units_total += units

    return LabourRollup(
        label=label,
        template_id=template_id,
        items_total=len(items),
        items_included=len(included),
        prep_seconds_total=prep_seconds,
        labour_cost_pence_total=labour_total,
        ingredient_cost_pence_total=ingredient_total,
        revenue_pence_total=revenue_total,
        units_total=units_total,
        excluded=tuple(excluded),
        warnings=tuple(warnings),
    )
