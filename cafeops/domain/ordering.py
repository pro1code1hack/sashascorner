"""Order sizing and the minimum-order top-up. Spec 5.4.

```
cover_days = lead_time_days + days_to_next_delivery_after(target) + safety_days
need   = sum(forecast over the cover window) - on_hand - qty on open POs
packs  = ceil(need / pack_size), clamped so on-hand lands in [min_qty, max_qty]
```

Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O, no `config` import.

## Where the cover window starts, and why it matters

The window starts on the **order date**, not on the delivery date. `lead_time_days`
is one of the three terms in `cover_days`, so the days between placing the order and
the goods arriving are days the current shelf has to cover -- if the window began at
the delivery date those days would be counted in `cover_days` and then not forecast,
which is how a two-day lead time turns into a two-day stockout.

`days_to_next_delivery_after(target)` is the gap to the *next* delivery slot after the
one being ordered for. For a supplier that delivers most days that gap is 1, which
silently assumes the order is repeated at every delivery opportunity. Order weekly
from a five-day-a-week supplier and this formula under-orders by design; the caller
is the only one who knows the real reordering cadence, so this module computes what
the spec says and `cafeops simulate` says so out loud.

## Empty `delivery_weekdays` means any day

`SupplierSpec.delivery_weekdays` is ISO (Mon=1..Sun=7) and an **empty tuple means
"any day"** -- Tesco, walk-in, lead time 0. That case short-circuits: the next
delivery after a day is simply the following day. There is no search loop to run and
nothing to divide by, so neither can spin or fault.

## Units

`need`, `on_hand`, `max_qty` and the forecast are all in the ingredient's stocking
unit; `pack_size` is in the supplier's pack unit, which is not always the same one.
`units.convert` does the conversion exactly and **raises across dimensions** -- a
litre is not a kilogram, and the answer to "1 L of milk vs a 1 kg pack" is an
exception, not a number.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from cafeops.domain.types import (
    CoverWindow,
    ForecastResult,
    OrderSuggestion,
    PackChoice,
    ParSpec,
    SuggestedLine,
    SupplierSpec,
    Tier,
    Unit,
)
from cafeops.domain.units import convert

__all__ = [
    "OrderCandidate",
    "OrderingError",
    "SizingOutcome",
    "SizingPlan",
    "build_suggestion",
    "cover_window",
    "next_delivery_after",
    "next_delivery_on_or_after",
    "pounds",
    "remaining_cover_days",
    "size_line",
    "target_delivery_date",
]

_ZERO = Decimal("0")
_DISPLAY = Decimal("0.001")
#: A full pass over the top-up pool that adds nothing ends the loop, so this is a
#: belt-and-braces stop rather than the real terminator.
_MAX_TOP_UP_PASSES = 50


class OrderingError(ValueError):
    """Supplier or par data that cannot produce an honest order size."""


def pounds(pence: int) -> str:
    """Integer pence -> "£12.34". Integer arithmetic only (invariant 8)."""
    sign = "-" if pence < 0 else ""
    whole, part = divmod(abs(pence), 100)
    return f"{sign}£{whole}.{part:02d}"


def _shown(qty: Decimal) -> str:
    """Three decimal places, trailing zeros trimmed, never in exponent notation.

    `Decimal.normalize()` renders 40 as "4E+1", which in a note about how much milk
    is in the fridge is worse than useless.
    """
    text = f"{qty.quantize(_DISPLAY, rounding=ROUND_HALF_UP):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _ceil_packs(qty: Decimal, pack_qty: Decimal) -> int:
    return int((qty / pack_qty).to_integral_value(rounding=ROUND_CEILING))


def _floor_packs(qty: Decimal, pack_qty: Decimal) -> int:
    return int((qty / pack_qty).to_integral_value(rounding=ROUND_FLOOR))


# ==========================================================================
# Delivery calendar
# ==========================================================================


def _validated_weekdays(delivery_weekdays: Sequence[int]) -> tuple[int, ...]:
    weekdays = tuple(sorted(set(delivery_weekdays)))
    bad = [d for d in weekdays if d < 1 or d > 7]
    if bad:
        raise OrderingError(
            f"delivery_weekdays must be ISO weekdays 1..7 (Mon..Sun), got {bad}; "
            "an EMPTY tuple is how 'any day' is expressed"
        )
    return weekdays


def next_delivery_on_or_after(day: date, delivery_weekdays: Sequence[int]) -> date:
    """The first delivery slot at or after `day`. Empty weekdays means `day` itself."""
    weekdays = _validated_weekdays(delivery_weekdays)
    if not weekdays:
        return day
    for offset in range(7):
        candidate = day + timedelta(days=offset)
        if candidate.isoweekday() in weekdays:
            return candidate
    # Unreachable: a non-empty set of valid ISO weekdays is hit within any 7 days.
    raise OrderingError(f"no delivery weekday among {weekdays} within 7 days of {day}")


def next_delivery_after(day: date, delivery_weekdays: Sequence[int]) -> date:
    """The first delivery slot strictly after `day`."""
    return next_delivery_on_or_after(day + timedelta(days=1), delivery_weekdays)


def target_delivery_date(*, order_date: date, supplier: SupplierSpec) -> date:
    """Earliest slot that respects the lead time. Walk-in suppliers deliver today."""
    if supplier.lead_time_days < 0:
        raise OrderingError(
            f"{supplier.name}: lead_time_days is {supplier.lead_time_days}; "
            "a negative lead time has no meaning"
        )
    earliest = order_date + timedelta(days=supplier.lead_time_days)
    return next_delivery_on_or_after(earliest, supplier.delivery_weekdays)


def cover_window(
    *,
    order_date: date,
    supplier: SupplierSpec,
    safety_days: Decimal,
    reorder_cadence_days: int | None = None,
) -> CoverWindow:
    """`lead_time_days + days_to_next_delivery_after(target) + safety_days`, as days.

    `safety_days` comes from the ingredient's `ParSpec`, so the window is per
    ingredient and not per supplier. A fractional total rounds **up** -- rounding a
    safety margin down is not a safety margin -- and the window is never shorter than
    one day, because an order that covers nothing is not an order.

    `reorder_cadence_days` resolves a real ambiguity in spec 5.4's middle term. Read
    literally, "days to the next delivery after the target" is the gap to the next
    slot the supplier *could* deliver on -- 1 day for Tesco, who delivers any day, and
    1 day for a Mon-Fri supplier ordered on a weekday. That reading quietly assumes
    the order is repeated at every delivery opportunity. Order once a week and the
    real gap is seven days, and the literal reading under-orders by a factor of
    seven. Only the caller knows the rhythm, so:

    - `None` (default) is the literal spec: the next slot the supplier offers.
    - an integer is the caller's actual reordering interval, and the gap becomes the
      distance to the first slot at or after `target + cadence`.

    Either way the number used ends up in `CoverWindow.days_until_next_delivery`, so
    the working shows which reading produced the order.
    """
    if safety_days < _ZERO:
        raise OrderingError(f"safety_days is {safety_days}; a negative buffer has no meaning")
    if reorder_cadence_days is not None and reorder_cadence_days < 1:
        raise OrderingError(
            f"reorder_cadence_days is {reorder_cadence_days}; pass None for the literal "
            "spec reading (next available slot) rather than zero"
        )

    target = target_delivery_date(order_date=order_date, supplier=supplier)
    if reorder_cadence_days is None:
        following = next_delivery_after(target, supplier.delivery_weekdays)
    else:
        following = next_delivery_on_or_after(
            target + timedelta(days=reorder_cadence_days), supplier.delivery_weekdays
        )
    gap_days = max((following - target).days, 1)
    total = Decimal(supplier.lead_time_days) + Decimal(gap_days) + safety_days
    length = max(int(total.to_integral_value(rounding=ROUND_CEILING)), 1)
    return CoverWindow(
        days=tuple(order_date + timedelta(days=offset) for offset in range(length)),
        lead_time_days=supplier.lead_time_days,
        days_until_next_delivery=gap_days,
        safety_days=safety_days,
    )


# ==========================================================================
# Sizing one line
# ==========================================================================


@dataclass(frozen=True, slots=True)
class OrderCandidate:
    """One ingredient, everything needed to size it, assembled by the service layer.

    Not in `domain/types.py` because it is this module's input shape rather than a
    shared contract. It carries the forecast and the cover window so the working can
    be shown -- `cafeops simulate` is the only proof this arithmetic has.
    """

    ingredient_id: int
    ingredient_name: str
    unit: Unit
    tier: Tier
    par: ParSpec
    pack: PackChoice
    on_hand_qty: Decimal
    on_open_pos_qty: Decimal
    cover: CoverWindow
    forecast: ForecastResult

    def pack_qty(self) -> Decimal:
        """Pack size in the ingredient's own stocking unit.

        Raises `units.IncompatibleUnitsError` when a pack is measured in a different
        dimension from the stock -- deliberately, rather than treating 1 L as 1 kg.
        """
        return convert(self.pack.pack_size, self.pack.pack_unit, self.unit)

    @property
    def forecast_qty(self) -> Decimal:
        return self.forecast.total

    @property
    def available_qty(self) -> Decimal:
        """On-hand plus everything already on an open PO."""
        return self.on_hand_qty + self.on_open_pos_qty

    @property
    def need_qty(self) -> Decimal:
        return self.forecast_qty - self.available_qty


@dataclass(frozen=True, slots=True)
class SizingOutcome:
    """What sizing decided about one candidate, and why.

    `line is None` means nothing was ordered; `note` then says which of the several
    good reasons applied. A note also appears alongside a line when a clamp moved the
    number, so no adjustment is ever silent.

    `note` is the full explanation and exists for `cafeops simulate`, which shows the
    working for every candidate. What reaches `OrderSuggestion.notes` -- and therefore
    the owner's Telegram message -- is curated by `build_suggestion`: forty lines of
    "nothing needed" is not information, whereas `below_par_floor` and `data_error`
    both are.
    """

    candidate: OrderCandidate
    line: SuggestedLine | None
    note: str | None = None
    #: Stock is under `min_qty` but nothing is forecast to move it. Reported, never
    #: ordered against -- see `size_line`.
    below_par_floor: bool = False
    #: Par or pack data that cannot produce an honest order size.
    data_error: str | None = None
    #: The clamp that reduced a real, forecast-driven need to nothing. `"max_qty"` here
    #: means the par ceiling, not the forecast, decided not to order.
    clamp_blocked: str | None = None


@dataclass(frozen=True, slots=True)
class SizingPlan:
    """The suggestion plus the working behind every candidate considered."""

    suggestion: OrderSuggestion
    outcomes: tuple[SizingOutcome, ...]

    @property
    def ordered(self) -> tuple[SizingOutcome, ...]:
        return tuple(o for o in self.outcomes if o.line is not None)


def forecast_text(candidate: OrderCandidate) -> str:
    """The forecast as a sentence, honouring invariant 7.

    A low-confidence forecast is described by its **reason instead of its figure**.
    That is the whole of invariant 7: "say so in place of the number, not beside it".
    Quoting `need` would leak the figure back out, since need is the forecast minus two
    numbers already on screen, so that is withheld with it.
    """
    if candidate.forecast.low_confidence:
        reasons = "; ".join(candidate.forecast.confidence_reasons) or "low confidence"
        return f"forecast withheld -- {reasons}"
    return (
        f"forecast {_shown(candidate.forecast_qty)} {candidate.unit.value} over "
        f"{candidate.cover.length} day(s), need {_shown(candidate.need_qty)}"
    )


def size_line(candidate: OrderCandidate) -> SizingOutcome:
    """`packs = ceil(need / pack_size)`, clamped into `[min_qty, max_qty]`.

    Never returns a line with zero or negative packs: "order nothing" is expressed by
    `line is None` with a reason, not by a line for nought.

    Both clamps only ever adjust a line the **forecast already asked for**. In
    particular, stock sitting under `min_qty` with nothing forecast to move it does
    *not* create an order: it sets `below_par_floor` and says so. The seeded database
    shows why -- 23 tier-B syrups sit under their par floor with zero measured
    consumption, and a `min_qty` clamp that created lines for them spent £207 of the
    owner's money on stock she never asked for and does not sell. Spec 5.4 forbids
    silently inflating an order, and a par floor breached by an ingredient that is not
    moving is a par level to fix, not a purchase to make.
    """
    name = candidate.ingredient_name
    par = candidate.par
    pack_qty = candidate.pack_qty()
    if pack_qty <= _ZERO:
        error = (
            f"{name}: pack size {_shown(candidate.pack.pack_size)} "
            f"{candidate.pack.pack_unit.value} is not positive, so no pack count can be "
            "computed; fix the supplier product"
        )
        return SizingOutcome(candidate, None, error, data_error=error)

    available = candidate.available_qty
    need = candidate.need_qty
    notes: list[str] = []
    data_error: str | None = None
    if par.min_qty > par.max_qty:
        data_error = (
            f"{name}: par min_qty {_shown(par.min_qty)} exceeds max_qty "
            f"{_shown(par.max_qty)}; max_qty wins so the order cannot run away, but the "
            "par level is wrong and needs fixing"
        )
        notes.append(data_error)

    packs = _ceil_packs(need, pack_qty) if need > _ZERO else 0
    forecast_driven = packs
    clamped: str | None = None
    below_par_floor = available < par.min_qty

    floor_packs = max(_ceil_packs(par.min_qty - available, pack_qty), 0)
    if packs >= 1 and floor_packs > packs:
        clamped = "min_qty"
        notes.append(
            f"{name}: raised from {packs} to {floor_packs} pack(s) so on-hand reaches "
            f"the par floor min_qty {_shown(par.min_qty)} {candidate.unit.value} "
            f"({forecast_text(candidate)})"
        )
        packs = floor_packs

    cap_packs = _floor_packs(par.max_qty - available, pack_qty)
    if cap_packs < packs:
        clamped = "max_qty"
        note = (
            f"{name}: cut from {packs} to {max(cap_packs, 0)} pack(s) by par ceiling "
            f"max_qty {_shown(par.max_qty)} {candidate.unit.value} "
            f"(on-hand {_shown(candidate.on_hand_qty)} + open POs "
            f"{_shown(candidate.on_open_pos_qty)}); the par level, not the forecast, "
            "is sizing this line"
        )
        packs = max(cap_packs, 0)
        capped_result = available + Decimal(packs) * pack_qty
        if not candidate.forecast.low_confidence and capped_result < candidate.forecast_qty:
            # max_qty below the window's demand is not a ceiling, it is a planned
            # stockout. Worth saying before she confirms it, not after she runs out.
            note += (
                f". WARNING: that leaves {_shown(capped_result)} against forecast demand "
                f"{_shown(candidate.forecast_qty)} over the next {candidate.cover.length} "
                f"day(s) -- max_qty is below one cover window of demand, so this par level "
                "guarantees a shortfall no matter what the forecast says"
            )
        notes.append(note)

    if packs <= 0:
        if below_par_floor and forecast_driven == 0:
            notes.append(
                f"{name}: on-hand {_shown(candidate.on_hand_qty)} "
                f"{candidate.unit.value} is under the par floor min_qty "
                f"{_shown(par.min_qty)}, but nothing is forecast to move it "
                f"({forecast_text(candidate)}). NOT ordered -- either the par floor is "
                "wrong or this ingredient is not selling. A human decides, not a clamp."
            )
        elif not notes:
            notes.append(
                f"{name}: nothing needed -- {forecast_text(candidate)} is already "
                f"covered by on-hand {_shown(candidate.on_hand_qty)} + open POs "
                f"{_shown(candidate.on_open_pos_qty)}"
            )
        return SizingOutcome(
            candidate,
            None,
            " | ".join(notes),
            below_par_floor=below_par_floor and forecast_driven == 0,
            data_error=data_error,
            clamp_blocked=clamped if forecast_driven > 0 else None,
        )

    if clamped is None and forecast_driven != packs:
        # Defensive: `clamped` is the only record that a number was adjusted, and a
        # silent adjustment is exactly what spec 5.4 forbids.
        raise OrderingError(f"{name}: pack count changed without a clamp being recorded")

    line = SuggestedLine(
        ingredient_id=candidate.ingredient_id,
        ingredient_name=name,
        pack=candidate.pack,
        packs=packs,
        need_qty=need,
        forecast_qty=candidate.forecast_qty,
        on_hand_qty=candidate.on_hand_qty,
        on_open_pos_qty=candidate.on_open_pos_qty,
        resulting_on_hand=available + Decimal(packs) * pack_qty,
        unit=candidate.unit,
        clamped=clamped,
        low_confidence=candidate.forecast.low_confidence,
        confidence_reasons=candidate.forecast.confidence_reasons,
    )
    return SizingOutcome(
        candidate,
        line,
        " | ".join(notes) if notes else None,
        data_error=data_error,
    )


# ==========================================================================
# Minimum-order top-up
# ==========================================================================


def remaining_cover_days(candidate: OrderCandidate) -> Decimal | None:
    """Days the current stock lasts at `base_daily`. `None` when nothing moves.

    `None` is the honest answer for an ingredient with no measured consumption: its
    cover is not "very long", it is unknown, and it must not be ranked as though a
    number had been computed for it.
    """
    base_daily = candidate.forecast.base_daily
    if base_daily <= _ZERO:
        return None
    return candidate.available_qty / base_daily


def _top_up_pool(
    candidates: Iterable[OrderCandidate], ordered_ids: frozenset[int]
) -> tuple[list[tuple[Decimal, OrderCandidate]], list[OrderCandidate]]:
    """Tier B candidates not already on the order, ranked by shortest remaining cover.

    Tier A is excluded because spec 5.4 says tier B; items already on the order are
    excluded because inflating a line the owner will read as "this is what I need"
    hides the top-up inside it. A top-up is always its own line.
    """
    eligible: list[tuple[Decimal, OrderCandidate]] = []
    no_velocity: list[OrderCandidate] = []
    for candidate in candidates:
        if candidate.tier is not Tier.B or candidate.ingredient_id in ordered_ids:
            continue
        cover = remaining_cover_days(candidate)
        if cover is None:
            no_velocity.append(candidate)
        else:
            eligible.append((cover, candidate))
    eligible.sort(key=lambda pair: (pair[0], pair[1].ingredient_name))
    return eligible, no_velocity


def _top_up(
    *,
    supplier: SupplierSpec,
    lines: Sequence[SuggestedLine],
    candidates: Sequence[OrderCandidate],
) -> tuple[tuple[SuggestedLine, ...], bool, tuple[str, ...]]:
    """Add tier B lines, shortest remaining cover first, until the minimum is met.

    Every added pack is stock the owner did not ask for, so: it is tier B (reviewed
    by a human anyway), it is something that demonstrably moves, it respects the same
    `max_qty` ceiling as a normal line, and it arrives as its own `is_top_up` line
    with the reason in `notes`. If the minimum still cannot be met the shortfall is
    reported rather than padded.
    """
    subtotal = sum(line.line_total_pence for line in lines)
    minimum = supplier.min_order_pence
    eligible, no_velocity = _top_up_pool(
        candidates, frozenset(line.ingredient_id for line in lines)
    )

    added: dict[int, tuple[OrderCandidate, int]] = {}
    total = subtotal
    for _ in range(_MAX_TOP_UP_PASSES):
        if total >= minimum:
            break
        progressed = False
        for _cover, candidate in eligible:
            if total >= minimum:
                break
            pack_qty = candidate.pack_qty()
            if pack_qty <= _ZERO:
                continue
            current = added[candidate.ingredient_id][1] if candidate.ingredient_id in added else 0
            cap = _floor_packs(candidate.par.max_qty - candidate.available_qty, pack_qty)
            if current + 1 > cap:
                continue
            added[candidate.ingredient_id] = (candidate, current + 1)
            total += candidate.pack.price_pence
            progressed = True
        if not progressed:
            break

    if not added:
        notes = [
            f"{supplier.name}: order total {pounds(subtotal)} is below the "
            f"{pounds(minimum)} minimum and no tier-B item could be added"
        ]
        notes.extend(_pool_notes(eligible, no_velocity))
        return tuple(lines), False, tuple(notes)

    top_up_lines: list[SuggestedLine] = []
    described: list[str] = []
    for candidate, packs in added.values():
        pack_qty = candidate.pack_qty()
        top_up_lines.append(
            SuggestedLine(
                ingredient_id=candidate.ingredient_id,
                ingredient_name=candidate.ingredient_name,
                pack=candidate.pack,
                packs=packs,
                need_qty=candidate.need_qty,
                forecast_qty=candidate.forecast_qty,
                on_hand_qty=candidate.on_hand_qty,
                on_open_pos_qty=candidate.on_open_pos_qty,
                resulting_on_hand=candidate.available_qty + Decimal(packs) * pack_qty,
                unit=candidate.unit,
                is_top_up=True,
                low_confidence=candidate.forecast.low_confidence,
                confidence_reasons=candidate.forecast.confidence_reasons,
            )
        )
        cover = remaining_cover_days(candidate)
        described.append(
            f"{candidate.ingredient_name} x{packs} "
            f"({_shown(cover) if cover is not None else '?'} days' cover left, "
            f"{pounds(packs * candidate.pack.price_pence)})"
        )

    notes = [
        f"{supplier.name}: MINIMUM-ORDER TOP-UP. The forecast asked for "
        f"{pounds(subtotal)}, below the {pounds(minimum)} minimum, so "
        f"{len(top_up_lines)} tier-B item(s) were added -- shortest remaining cover "
        f"first: {'; '.join(described)}. None of this was forecast as needed; it is "
        "stock bought early to reach the minimum, and it is the one part of this "
        "order that exists for the supplier's benefit rather than the cafe's.",
    ]
    if total < minimum:
        notes.append(
            f"{supplier.name}: still {pounds(minimum - total)} short of the "
            f"{pounds(minimum)} minimum after topping up -- every remaining tier-B item "
            "is already at its par max_qty. Do not pad this further; either the "
            "minimum, the par levels or the supplier needs a human decision."
        )
    notes.extend(_pool_notes(eligible, no_velocity))
    return (*lines, *top_up_lines), True, tuple(notes)


def _pool_notes(
    eligible: Sequence[tuple[Decimal, OrderCandidate]], no_velocity: Sequence[OrderCandidate]
) -> list[str]:
    notes: list[str] = []
    if no_velocity:
        names = ", ".join(sorted(c.ingredient_name for c in no_velocity))
        notes.append(
            f"{len(no_velocity)} tier-B item(s) were not used as top-up because nothing "
            f"has been recorded moving: {names}. Buying stock that does not sell to "
            "reach a supplier minimum is not a saving."
        )
    if not eligible:
        notes.append("no tier-B item with measured consumption was available to top up with.")
    return notes


# ==========================================================================
# The whole order
# ==========================================================================


def build_suggestion(
    *,
    supplier: SupplierSpec,
    target_delivery_date: date,
    cover_window: CoverWindow,
    candidates: Sequence[OrderCandidate],
) -> SizingPlan:
    """Size every candidate, then top up to the supplier minimum if one is set.

    The returned `OrderSuggestion` carries a single `cover_window`, but `safety_days`
    is per par level so the real window is per line. What is reported here is the
    **widest** of them; each candidate's own window is in `SizingPlan.outcomes`. That
    `SuggestedLine` has nowhere to record its own window is a gap in the
    integrator-owned contract, raised rather than worked around.
    """
    outcomes = [size_line(candidate) for candidate in candidates]
    lines = tuple(o.line for o in outcomes if o.line is not None)

    # Curated, not every outcome's note. A purchase order's notes are read by a person
    # deciding whether to press Confirm: a clamp that moved a number she is about to
    # approve belongs there, forty "nothing needed" lines do not.
    notes = [o.note for o in outcomes if o.line is not None and o.note is not None]
    notes.extend(o.data_error for o in outcomes if o.data_error is not None and o.line is None)
    # A clamp that suppressed a real need belongs in front of whoever confirms this
    # order: the absence of a line is the dangerous part, and an absent line cannot
    # carry its own explanation.
    notes.extend(
        o.note
        for o in outcomes
        if o.clamp_blocked is not None and o.line is None and o.note is not None
    )
    below_floor = [o.candidate.ingredient_name for o in outcomes if o.below_par_floor]
    if below_floor:
        notes.append(
            f"{len(below_floor)} item(s) are below their par floor min_qty with nothing "
            f"forecast to move them, and were NOT ordered: {', '.join(sorted(below_floor))}. "
            "Either the par floor or the tier is wrong. Ordering against a floor that "
            "nothing is consuming would be spending money on a data error."
        )

    topped_up = False
    if supplier.min_order_pence > 0:
        subtotal = sum(line.line_total_pence for line in lines)
        if lines and subtotal < supplier.min_order_pence:
            lines, topped_up, top_up_notes = _top_up(
                supplier=supplier, lines=lines, candidates=candidates
            )
            notes.extend(top_up_notes)
        elif not lines:
            notes.append(
                f"{supplier.name}: nothing is needed, so the {pounds(supplier.min_order_pence)} "
                "minimum does not apply -- a minimum is a condition on placing an order, "
                "not a reason to place one."
            )

    by_id = {line.ingredient_id: line for line in lines}
    final_outcomes = tuple(
        replace(outcome, line=by_id.get(outcome.candidate.ingredient_id, outcome.line))
        for outcome in outcomes
    )

    suggestion = OrderSuggestion(
        supplier=supplier,
        target_delivery_date=target_delivery_date,
        cover_window=cover_window,
        lines=lines,
        min_order_topped_up=topped_up,
        notes=tuple(notes),
    )
    return SizingPlan(suggestion=suggestion, outcomes=final_outcomes)
