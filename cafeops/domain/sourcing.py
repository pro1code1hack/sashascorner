"""Which supplier to buy each ingredient from, and when to give up and walk to Tesco.

Spec 4.4 and 5.5. Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O, no
`config` import -- every threshold arrives as an argument.

## The rule, and why it is stated as two conditions rather than one

> Prefer the preferred product unless an alternate is materially cheaper per unit AND
> the switch does not push another supplier's order below its minimum.

Both halves earn their place, and the seeded data has a case for each:

* **Materially cheaper.** A penny a litre is not a reason to move a line to another
  supplier, another delivery day and another invoice to check. Tesco's 2 L milk at
  82.5p/L against Brakes' 3.4 L at 69.7p/L is not cheaper at all -- the alternate must
  be able to lose, and two of the six seeded alternates (Monolith beans, Amazon cups)
  exist precisely so that path is exercised rather than assumed.
* **Without breaking another minimum.** Moving £12 of oat milk off a £58 Booker order
  saves 60p a litre and leaves Booker £45.50 against a £50 minimum -- so the whole
  Booker order fails to ship, and the 60p buys a stockout of everything else on it.
  The saving is real and the cost of taking it is elsewhere, which is exactly the kind
  of trade-off spec 4.4 says to **surface, never resolve silently**.

A third condition is local to this café rather than in the spec: an alternate whose
pack is **larger than the usable shelf life** is rejected however cheap it is. A 3 kg
bag at a better price per kilo that goes stale in the bin is not a saving, and it is the
same arithmetic as invariant 4 one step earlier in the process.

When a cheaper option is rejected, `SourcingChoice.cheaper_rejected` and
`forgone_saving_pence` carry what it would have saved. That number is the point: it is
what makes "confirm the real terms with Booker" an argument with a figure attached
rather than an opinion.

## Tesco is a symptom, not a supplier

`route_to_retail` only ever produces lines for what **cannot wait** for a scheduled
delivery, and every one carries the retail premium over what the proper supplier would
have charged. Spec 4.4: the accumulated log is the argument for fixing the ordering
cadence, so it is data, not a note. One emergency is a bad week; a pattern is a broken
cadence, and the premium is the number that makes the case.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from cafeops.domain.types import (
    EmergencyLine,
    SourcingChoice,
    SourcingOption,
    SupplierTerms,
    Unit,
)
from cafeops.domain.units import IncompatibleUnitsError, convert

__all__ = [
    "DEFAULT_POLICY",
    "EmergencyPlan",
    "EmergencyRequest",
    "SourcingError",
    "SourcingPolicy",
    "SourcingRequest",
    "choose_source",
    "choose_sources",
    "cost_to_cover",
    "emergency_premium_pence",
    "pack_qty_in",
    "route_to_retail",
    "unit_price_in",
]

_ZERO = Decimal("0")
_DISPLAY = Decimal("0.001")
_PENCE = Decimal("0.01")


class SourcingError(ValueError):
    """Sourcing data that cannot produce an honest choice."""


def _shown(qty: Decimal) -> str:
    text = f"{qty.quantize(_DISPLAY, rounding=ROUND_HALF_UP):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _pounds(pence: Decimal | int) -> str:
    """Integer pence -> "£12.34". Money never becomes a float (invariant 11)."""
    value = pence if isinstance(pence, int) else int(pence.to_integral_value())
    sign = "-" if value < 0 else ""
    whole, part = divmod(abs(value), 100)
    return f"{sign}£{whole}.{part:02d}"


def _per_unit(price: Decimal, unit: Unit) -> str:
    return f"{price.quantize(_PENCE, rounding=ROUND_HALF_UP)}p/{unit.value}"


# ==========================================================================
# Comparing two ways to buy the same thing
# ==========================================================================


def pack_qty_in(option: SourcingOption, unit: Unit) -> Decimal:
    """The option's pack size in the ingredient's stocking unit.

    Raises `units.IncompatibleUnitsError` across dimensions rather than treating a
    kilogram as a litre. Callers that would rather skip such an option than fail catch
    it; `choose_source` does exactly that and says so in the reason.
    """
    return convert(option.pack_size, option.pack_unit, unit)


def unit_price_in(option: SourcingOption, unit: Unit) -> Decimal | None:
    """Price per stocking unit, or None when the pack size makes that meaningless.

    `SourcingOption.unit_price_pence` divides by the pack size in the SUPPLIER's unit,
    which compares a 500-pack of cups against a 100-pack correctly but a 3.4 L bottle
    against a 2000 ml one wrongly. Normalising to the ingredient's own unit is the only
    comparison that means anything.
    """
    qty = pack_qty_in(option, unit)
    if qty <= _ZERO:
        return None
    return Decimal(option.price_pence) / qty


def cost_to_cover(option: SourcingOption, need_qty: Decimal, unit: Unit) -> tuple[int, int]:
    """`(packs, pence)` actually required to cover `need_qty`, honouring `moq_packs`.

    The honest comparison between two suppliers is not the unit price, it is what the
    till will ring up: a cheaper unit price behind a 10-pack minimum order is dearer
    than a costlier one you can buy singly. Packs are rounded UP, because half a pack
    cannot be bought.
    """
    pack_qty = pack_qty_in(option, unit)
    if pack_qty <= _ZERO:
        raise SourcingError(
            f"supplier_product {option.supplier_product_id}: pack size "
            f"{option.pack_size} {option.pack_unit.value} is not positive"
        )
    wanted = max(need_qty, _ZERO)
    packs = int((wanted / pack_qty).to_integral_value(rounding=ROUND_CEILING))
    packs = max(packs, option.moq_packs if wanted > _ZERO else 0)
    return packs, packs * option.price_pence


@dataclass(frozen=True, slots=True)
class SourcingPolicy:
    """When an alternate is *materially* cheaper. Spec 4.4's word, made a number.

    Both tests must pass. A 40% saving on 30p is 12p and not worth a second invoice;
    £8 off a £400 line is 2% and might be a rounding difference in a pack size. The
    defaults are a starting point, not a measurement, and they are arguments precisely
    so the owner can move them.
    """

    #: Minimum saving per stocking unit, as a percentage of the preferred unit price.
    material_saving_pct: Decimal = Decimal("10")
    #: Minimum saving in pence on the quantity actually being bought.
    material_saving_pence: int = 100


DEFAULT_POLICY = SourcingPolicy()


@dataclass(frozen=True, slots=True)
class SourcingRequest:
    """One ingredient, every way to buy it, and what the order needs.

    `need_qty` is what sizing decided to buy, in `unit`. It is optional because a
    sourcing question can also be asked before sizing ("who should we buy beans
    from?"), but without it the comparison is per-unit only and `moq_packs` cannot be
    taken into account -- so the reason says so rather than quietly ignoring it.
    """

    ingredient_id: int
    ingredient_name: str
    unit: Unit
    options: tuple[SourcingOption, ...]
    need_qty: Decimal | None = None
    #: Forecast consumption per day, used with `usable_days` to reject a pack that
    #: cannot be used before it spoils.
    daily_rate: Decimal | None = None
    #: `ShelfLifeSpec.usable_days`. None means nothing perishes here.
    usable_days: int | None = None

    @property
    def usable_qty(self) -> Decimal | None:
        """How much of this can be used before it spoils. None when unconstrained."""
        if self.usable_days is None or self.daily_rate is None or self.daily_rate <= _ZERO:
            return None
        return self.daily_rate * Decimal(self.usable_days)


def _preferred_option(options: Sequence[SourcingOption], unit: Unit) -> SourcingOption:
    """The incumbent: `is_preferred` if anything claims it, else the cheapest per unit.

    Ties break on `supplier_product_id` so the same data always produces the same
    choice. An ordering system that changes its mind between runs on identical inputs
    cannot be audited.
    """
    flagged = [o for o in options if o.is_preferred]
    pool = flagged or list(options)

    def key(option: SourcingOption) -> tuple[Decimal, int]:
        try:
            price = unit_price_in(option, unit)
        except IncompatibleUnitsError:
            price = None
        # A pack that cannot be compared sorts last rather than winning by accident.
        return (price if price is not None else Decimal("999999999"), option.supplier_product_id)

    return sorted(pool, key=key)[0]


def choose_source(
    request: SourcingRequest,
    *,
    policy: SourcingPolicy = DEFAULT_POLICY,
    terms: Mapping[int, SupplierTerms] | None = None,
    supplier_baskets: Mapping[int, int] | None = None,
) -> SourcingChoice:
    """Pick one option for one ingredient, and record what picking it cost. Spec 4.4.

    `supplier_baskets` maps supplier id to the pence already in that supplier's order
    for this run, and `terms` to that supplier's minimum. Together they answer the
    second half of spec 4.4's rule: would moving this line off its usual supplier drop
    that order below the minimum it has to clear to ship at all? If so the alternate is
    refused and the forgone saving is reported -- the trade-off is surfaced, and the
    person who can renegotiate a minimum is the one who gets to decide.
    """
    if not request.options:
        raise SourcingError(
            f"{request.ingredient_name}: no supplier sells this, so there is nothing to "
            "source. An ingredient with no supplier product cannot be ordered at all, "
            "which is a data gap rather than a sourcing decision."
        )
    terms = terms or {}
    baskets = supplier_baskets or {}
    unit = request.unit
    preferred = _preferred_option(request.options, unit)
    others = [o for o in request.options if o.supplier_product_id != preferred.supplier_product_id]
    if not others:
        return SourcingChoice(
            ingredient_id=request.ingredient_id,
            chosen=preferred,
            reason=(
                f"{request.ingredient_name}: single source -- "
                f"{_supplier_label(preferred, terms)} is the only supplier on file, so "
                "there is no decision to make and no alternate to compare against."
            ),
        )

    preferred_unit_price = unit_price_in(preferred, unit)
    ranked: list[tuple[Decimal, SourcingOption]] = []
    skipped: list[str] = []
    for option in others:
        try:
            price = unit_price_in(option, unit)
        except IncompatibleUnitsError:
            skipped.append(
                f"{_supplier_label(option, terms)} sells it by the "
                f"{option.pack_unit.value}, which cannot be converted to "
                f"{unit.value} without a density -- skipped rather than guessed"
            )
            continue
        if price is None:
            skipped.append(
                f"{_supplier_label(option, terms)} has a pack size of "
                f"{_shown(option.pack_size)}, so it has no unit price -- skipped"
            )
            continue
        ranked.append((price, option))
    ranked.sort(key=lambda pair: (pair[0], pair[1].supplier_product_id))

    alternatives = tuple(option for _price, option in ranked)
    reasons: list[str] = []
    cheaper_rejected: SourcingOption | None = None
    forgone: Decimal | None = None

    for price, option in ranked:
        if preferred_unit_price is None or price >= preferred_unit_price:
            break  # ranked cheapest first, so nothing after this is cheaper either
        saving_per_unit = preferred_unit_price - price
        saving_pct = saving_per_unit / preferred_unit_price * Decimal("100")
        saving_total = _total_saving(request, preferred, option, saving_per_unit)
        blocker = _switch_blocker(
            request=request,
            preferred=preferred,
            option=option,
            terms=terms,
            baskets=baskets,
        )
        material = saving_pct >= policy.material_saving_pct and saving_total >= Decimal(
            policy.material_saving_pence
        )
        if blocker is not None:
            reasons.append(
                f"{_supplier_label(option, terms)} is cheaper at "
                f"{_per_unit(price, unit)} against {_per_unit(preferred_unit_price, unit)} "
                f"({_pounds(saving_total)} on this line), but it was NOT taken: {blocker}"
            )
            if cheaper_rejected is None:
                cheaper_rejected, forgone = option, saving_total
            continue
        if not material:
            # Name the test that failed. "Under the 10% and £1 bar" reads as nonsense
            # next to a 24% saving, and a reason the reader has to decode is a reason
            # they will overrule.
            failed = (
                f"the saving is {_pounds(saving_total)} on this line, under the "
                f"{_pounds(policy.material_saving_pence)} floor"
                if saving_total < Decimal(policy.material_saving_pence)
                else (
                    f"the gap is {saving_pct.quantize(_PENCE)}% per {unit.value}, under the "
                    f"{policy.material_saving_pct}% floor"
                )
            )
            reasons.append(
                f"{_supplier_label(option, terms)} is cheaper at "
                f"{_per_unit(price, unit)} against {_per_unit(preferred_unit_price, unit)} "
                f"({saving_pct.quantize(_PENCE)}%, {_pounds(saving_total)} on this line), "
                f"but NOT materially cheaper: {failed}. The preferred supplier keeps the "
                "line rather than splitting the order, a second delivery and a second "
                "invoice to check for small change"
            )
            if cheaper_rejected is None:
                cheaper_rejected, forgone = option, saving_total
            continue
        reason = (
            f"{request.ingredient_name}: SWITCHED to {_supplier_label(option, terms)} at "
            f"{_per_unit(price, unit)}, against {_supplier_label(preferred, terms)} at "
            f"{_per_unit(preferred_unit_price, unit)} -- "
            f"{saving_pct.quantize(_PENCE)}% cheaper per {unit.value}, "
            f"{_pounds(saving_total)} on this line. Materially cheaper and no other "
            "supplier's minimum is broken by moving it."
        )
        chosen_terms = terms.get(option.supplier_id)
        if chosen_terms is not None and chosen_terms.terms_are_placeholders:
            reason += (
                f" CAVEAT: {chosen_terms.name}'s lead time, delivery days and minimum are "
                "INVENTED PLACEHOLDERS, so this switch is cheaper on a price we know and "
                "a schedule we do not."
            )
        if skipped:
            reason += " Not compared: " + "; ".join(skipped) + "."
        return SourcingChoice(
            ingredient_id=request.ingredient_id,
            chosen=option,
            alternatives=alternatives,
            reason=reason,
        )

    dearer = [
        f"{_supplier_label(option, terms)} at {_per_unit(price, unit)}"
        for price, option in ranked
        if preferred_unit_price is not None and price >= preferred_unit_price
    ]
    head = (
        f"{request.ingredient_name}: KEPT {_supplier_label(preferred, terms)} at "
        + (
            _per_unit(preferred_unit_price, unit)
            if preferred_unit_price is not None
            else "an uncomparable pack size"
        )
        + "."
    )
    if dearer:
        reasons.append("dearer alternates: " + ", ".join(dearer))
    if skipped:
        reasons.append("not compared: " + "; ".join(skipped))
    return SourcingChoice(
        ingredient_id=request.ingredient_id,
        chosen=preferred,
        alternatives=alternatives,
        reason=head + " " + ". ".join(reasons) if reasons else head,
        cheaper_rejected=cheaper_rejected,
        forgone_saving_pence=forgone,
    )


def _supplier_label(option: SourcingOption, terms: Mapping[int, SupplierTerms]) -> str:
    term = terms.get(option.supplier_id)
    name = term.name if term is not None else f"supplier {option.supplier_id}"
    return f"{name} ({_shown(option.pack_size)} {option.pack_unit.value})"


def _total_saving(
    request: SourcingRequest,
    preferred: SourcingOption,
    option: SourcingOption,
    saving_per_unit: Decimal,
) -> Decimal:
    """What the switch saves on the quantity actually being bought.

    With a `need_qty` this is the difference between two till receipts, `moq_packs` and
    pack rounding included -- which is the only figure worth putting in front of the
    owner. Without one it falls back to the per-unit saving on a single pack, and the
    caller's reason says the comparison was per-unit.
    """
    if request.need_qty is None or request.need_qty <= _ZERO:
        return saving_per_unit * pack_qty_in(preferred, request.unit)
    _packs_pref, cost_pref = cost_to_cover(preferred, request.need_qty, request.unit)
    _packs_alt, cost_alt = cost_to_cover(option, request.need_qty, request.unit)
    return Decimal(cost_pref - cost_alt)


def _switch_blocker(
    *,
    request: SourcingRequest,
    preferred: SourcingOption,
    option: SourcingOption,
    terms: Mapping[int, SupplierTerms],
    baskets: Mapping[int, int],
) -> str | None:
    """Why this cheaper option must not be taken, or None when it may be.

    Two blockers, in the order they matter. Both are stated as sentences because they
    are what the owner reads instead of a silently different supplier.
    """
    # 1. Shelf life. Spec 5.4 one step earlier: a cheaper pack that cannot be used
    #    before it spoils is dearer, and the waste is invisible in the unit price.
    usable = request.usable_qty
    if usable is not None:
        pack_qty = pack_qty_in(option, request.unit)
        if pack_qty > usable:
            return (
                f"its {_shown(pack_qty)} {request.unit.value} pack is bigger than the "
                f"{_shown(usable)} {request.unit.value} that will be used inside the "
                f"{request.usable_days}-day usable shelf life, so the cheaper unit price "
                "would be paid on stock that goes in the bin (invariant 4)"
            )
    # 2. Another supplier's minimum. Moving the line takes money out of that supplier's
    #    order, and an order below the minimum does not ship at all.
    losing = terms.get(preferred.supplier_id)
    if losing is None or losing.min_order_pence <= 0:
        return None
    basket = baskets.get(preferred.supplier_id)
    if basket is None or basket < losing.min_order_pence:
        # That order already fails its minimum, so this line is not what breaks it.
        return None
    if request.need_qty is None or request.need_qty <= _ZERO:
        return None
    _packs, line_cost = cost_to_cover(preferred, request.need_qty, request.unit)
    remaining = basket - line_cost
    if remaining >= losing.min_order_pence:
        return None
    return (
        f"moving it would leave {losing.name}'s order at {_pounds(remaining)} against its "
        f"{_pounds(losing.min_order_pence)} minimum, so that whole order would not ship. "
        f"The saving is real and the cost of taking it lands on everything else "
        f"{losing.name} supplies -- spec 4.4 says surface this, not resolve it: either "
        f"accept the {_pounds(losing.min_order_pence - remaining)} shortfall on "
        f"{losing.name}, move more lines at once, or renegotiate the minimum"
    )


def choose_sources(
    requests: Iterable[SourcingRequest],
    *,
    policy: SourcingPolicy = DEFAULT_POLICY,
    terms: Mapping[int, SupplierTerms] | None = None,
    supplier_baskets: Mapping[int, int] | None = None,
) -> tuple[SourcingChoice, ...]:
    """`choose_source` over many ingredients, in a stable order.

    Each ingredient is decided against the **same** starting baskets rather than
    against baskets updated as the loop goes: a first-come-first-served sweep makes the
    answer depend on ingredient id, and a choice that changes because of the order the
    loop happened to run in is not a decision anybody can audit. The consequence is
    that two simultaneous switches could in principle break a minimum neither breaks
    alone -- so the per-supplier totals are re-checked after sizing, and a group short
    of its minimum is reported there (spec 5.5).
    """
    return tuple(
        choose_source(request, policy=policy, terms=terms, supplier_baskets=supplier_baskets)
        for request in sorted(requests, key=lambda r: (r.ingredient_name, r.ingredient_id))
    )


# ==========================================================================
# Tesco: what cannot wait, and what the hurry cost
# ==========================================================================


@dataclass(frozen=True, slots=True)
class EmergencyRequest:
    """One ingredient that may not last until the next scheduled delivery."""

    ingredient_id: int
    ingredient_name: str
    unit: Unit
    available_qty: Decimal
    #: Forecast consumption per day. Zero or less means nothing is moving, and
    #: something that is not moving cannot run out.
    daily_rate: Decimal
    #: Days from now until the proper supplier can deliver.
    days_until_delivery: int
    #: What the proper supplier charges, for the premium calculation.
    preferred: SourcingOption | None = None
    #: The retail option actually being bought today.
    retail: SourcingOption | None = None
    low_confidence: bool = False
    supplier_name: str | None = None

    @property
    def days_of_cover(self) -> Decimal | None:
        if self.daily_rate <= _ZERO:
            return None
        return self.available_qty / self.daily_rate


def emergency_premium_pence(line: EmergencyLine) -> Decimal | None:
    """`line.premium_pence`, floored at zero. The number spec 4.4's log actually wants.

    `EmergencyLine.premium_pence` (`domain/types.py`, integrator-owned) is a plain
    `(retail - preferred) * qty` -- honest, but it goes negative whenever the retail
    alternate happens to be unit-CHEAPER than the scheduled supplier. Oat milk is
    exactly this case in the seeded data: Tesco at 190p/L against Booker's 250p/L. That
    is real information, but it is not a premium, and letting it through would let a
    cheap emergency quietly cancel out an expensive one in the total -- the opposite of
    invariant 8's "never understate the figure this table exists to make". `route_to_retail`
    adds a note for the case instead: it is a sourcing finding (make Tesco the
    preferred supplier for this line), not a cost to log. Every reader of a premium --
    the plan total, the database write, the CLI -- goes through this floor so none of
    them can show a negative number by forgetting to apply it.
    """
    premium = line.premium_pence
    if premium is None:
        return None
    return premium if premium >= _ZERO else _ZERO


@dataclass(frozen=True, slots=True)
class EmergencyPlan:
    """What must be bought at retail today, and what that decision cost.

    `notes` is not decoration: it carries the cases deliberately NOT routed, which is
    the half of this report that stops it becoming a licence to shop at Tesco.
    """

    lines: tuple[EmergencyLine, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def total_premium_pence(self) -> Decimal | None:
        """Sum of the premiums, or None when any line cannot price its own premium.

        None rather than a partial sum: a premium total missing two of five lines
        understates the argument it exists to make (invariant 8's reasoning). Each
        line's own premium is floored at zero first (`emergency_premium_pence`) so a
        retail alternate that happened to be cheaper cannot drag the total down.
        """
        if not self.lines:
            return _ZERO
        total = _ZERO
        for line in self.lines:
            premium = emergency_premium_pence(line)
            if premium is None:
                return None
            total += premium
        return total


def route_to_retail(
    requests: Iterable[EmergencyRequest],
    *,
    retail_supplier_name: str = "Tesco",
) -> EmergencyPlan:
    """Route to retail only what cannot wait for a scheduled delivery. Spec 4.4, 5.5.

    An ingredient qualifies when its stock runs out **before** the proper supplier can
    deliver. The quantity bought is the bridge -- what is needed to reach that delivery,
    rounded up to whole retail packs -- and never a full cover window: the scheduled
    order is still coming, and buying a week of milk at retail on top of it is two
    mistakes rather than one.

    Two cases are refused on purpose and reported instead:

    * **Nothing moving.** A daily rate of zero cannot produce a stockout, so a
      zero-velocity item is never an emergency however low its stock.
    * **A low-confidence forecast.** Invariant 7: the run-out date would be a guess
      shown as a number, and "walk to Tesco" is not a decision to take on one. It is
      reported for a human instead.
    """
    lines: list[EmergencyLine] = []
    notes: list[str] = []
    for request in sorted(requests, key=lambda r: (r.ingredient_name, r.ingredient_id)):
        cover = request.days_of_cover
        if cover is None:
            continue
        if cover >= Decimal(request.days_until_delivery):
            continue
        if request.low_confidence:
            notes.append(
                f"{request.ingredient_name}: stock may not last the "
                f"{request.days_until_delivery} day(s) to the next delivery, but the "
                "forecast behind that is low-confidence, so no retail run is proposed "
                "(invariant 7 -- the run-out date would be a guess wearing a number's "
                "clothes). A human should look at this one."
            )
            continue
        if request.retail is None:
            notes.append(
                f"{request.ingredient_name}: runs out in {_shown(cover)} day(s), before "
                f"the next delivery in {request.days_until_delivery}, and {retail_supplier_name} "
                "does not stock it either. Nothing can bridge this gap -- it is a stockout "
                "unless the proper supplier can be hurried."
            )
            continue
        shortfall = (
            request.daily_rate * Decimal(request.days_until_delivery) - request.available_qty
        )
        pack_qty = pack_qty_in(request.retail, request.unit)
        if pack_qty <= _ZERO:
            notes.append(
                f"{request.ingredient_name}: the {retail_supplier_name} pack size is not "
                "positive, so no quantity can be computed; fix the supplier product"
            )
            continue
        packs = int((shortfall / pack_qty).to_integral_value(rounding=ROUND_CEILING))
        packs = max(packs, 1)
        qty = Decimal(packs) * pack_qty
        retail_price = unit_price_in(request.retail, request.unit)
        preferred_price = (
            unit_price_in(request.preferred, request.unit)
            if request.preferred is not None
            else None
        )
        waiting_for = request.supplier_name or "the scheduled supplier"
        if (
            retail_price is not None
            and preferred_price is not None
            and retail_price < preferred_price
        ):
            # Still routed -- the shelf runs out either way -- but this is not the cost
            # spec 4.4's log is arguing about. Said here so it is not lost, and floored
            # to zero everywhere a premium is read (`emergency_premium_pence`).
            notes.append(
                f"{request.ingredient_name}: {retail_supplier_name} at "
                f"{_per_unit(retail_price, request.unit)} is actually CHEAPER than {waiting_for} "
                f"at {_per_unit(preferred_price, request.unit)} on this line -- routed for the "
                "shortfall as normal, but that is a SOURCING FINDING (spec 4.4's "
                "multi-supplier choice), not an emergency premium, and is logged at "
                "£0.00 rather than as a negative number."
            )
        lines.append(
            EmergencyLine(
                ingredient_id=request.ingredient_id,
                ingredient_name=request.ingredient_name,
                qty=qty,
                unit=request.unit,
                reason=(
                    f"{_shown(request.available_qty)} {request.unit.value} on hand is "
                    f"{_shown(cover)} day(s) of trade at {_shown(request.daily_rate)}/day, "
                    f"but {waiting_for} cannot deliver for {request.days_until_delivery} "
                    f"day(s). {packs} x {_shown(request.retail.pack_size)} "
                    f"{request.retail.pack_unit.value} from {retail_supplier_name} bridges "
                    f"the {_shown(shortfall)} {request.unit.value} gap. This is a symptom "
                    "of the ordering cadence, not a supplier choice."
                ),
                retail_unit_price_pence=retail_price,
                preferred_unit_price_pence=preferred_price,
            )
        )
    if lines:
        notes.append(
            f"{len(lines)} line(s) routed to {retail_supplier_name} at retail. Every one is "
            "a premium paid for being late rather than a purchase anybody chose, and every "
            "one is logged (spec 4.4) -- one is a bad week, a pattern is a broken ordering "
            "cadence, and the accumulated premium is the argument for fixing it."
        )
    return EmergencyPlan(lines=tuple(lines), notes=tuple(notes))
