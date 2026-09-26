"""Order sizing, the shelf-life cap and the two top-up decisions. Spec 5.4.

```
cover_days      = lead_time_days + days_to_next_delivery_after(target) + safety_days
effective_cover = min(cover_days,
                      shelf_life_days - transit_buffer_days,  if perishable
                      days_remaining_in_season,               if seasonal)
need   = sum(forecast over the EFFECTIVE cover) - on_hand - qty on open POs
packs  = ceil(need / pack_size), clamped so on-hand lands in [min_qty, max_qty]
```

Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O, no `config` import.

## The shelf-life cap is the point, not a refinement (invariant 4)

Whole milk has a 7-day life and a 2-day transit buffer, so five days of it will keep.
A Brakes order on a weekly cadence has a ten-day cover window. Without the cap the
forecast asks for ten days of milk, four to five days of which cannot be drunk before
it turns -- the system would buy the waste itself, with confidence, every week.

So the cap truncates the *forecast window*, not the pack count: `forecast_qty` is the
sum of the first `effective_cover_days` forecast points. That keeps the arithmetic
legible -- the same forecast, read over a shorter window -- and it is why both figures
are kept on `OrderCandidate`. When the cap bites, `cap_reason` is set and every
renderer must show it. Spec 5.4 is explicit about why: an under-order the user cannot
see the reason for is an under-order the user overrides, and the override recreates
exactly the waste the cap prevented.

A season caps the same way, and an ingredient whose season is *over* is not ordered at
all: `days_remaining` is then `None`, the effective cover is zero, and the line
disappears with a note saying so rather than silently shrinking.

## A cutoff missed by ten minutes costs a delivery cycle

`SupplierTerms.cutoff_time` is local. Order after it and the lead time starts
tomorrow: Booker's noon cutoff missed at 12:10 on Monday does not mean a late Tuesday
delivery, it means Thursday. That is one extra day the shelf has to cover, and it is
how a Tesco run happens. `cover_plan` reports the slip so the day is visible rather
than absorbed into a number.

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
from datetime import date, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from cafeops.domain.types import (
    CapKind,
    CoverWindow,
    ForecastResult,
    OrderNote,
    OrderNoteKind,
    OrderSuggestion,
    PackChoice,
    ParSpec,
    SeasonSpec,
    ShelfLifeSpec,
    SuggestedLine,
    SupplierSpec,
    SupplierTerms,
    Tier,
    Unit,
)
from cafeops.domain.units import convert

__all__ = [
    "DEFAULT_FREE_DELIVERY_TOP_UP_MULTIPLE",
    "CoverPlan",
    "OrderCandidate",
    "OrderingError",
    "SizingOutcome",
    "SizingPlan",
    "build_suggestion",
    "cover_plan",
    "cover_window",
    "cutoff_slip_days",
    "next_delivery_after",
    "next_delivery_on_or_after",
    "pounds",
    "remaining_cover_days",
    "size_line",
    "target_delivery_date",
    "terms_of",
]

_ZERO = Decimal("0")
_DISPLAY = Decimal("0.001")
#: A full pass over the top-up pool that adds nothing ends the loop, so this is a
#: belt-and-braces stop rather than the real terminator.
_MAX_TOP_UP_PASSES = 50

#: How far past a free-delivery threshold it is worth buying stock to save the fee.
#: An INTERPRETATION, stated out loud on every order it affects: spec 5.4 says to top
#: up below the threshold but not at what price, and "spend £30 on cups to save a
#: £5.95 fee" is not a saving, it is a fee paid in stock plus £24. The rule applied
#: here is that the shortfall must be worth at most three times the fee -- close
#: enough that the top-up is bringing forward a purchase already coming, rather than
#: inventing one. The owner can overrule it; she cannot fail to see it.
DEFAULT_FREE_DELIVERY_TOP_UP_MULTIPLE = Decimal("3")


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


def cutoff_slip_days(*, order_time: time | None = None, cutoff_time: time | None = None) -> int:
    """1 when the order is placed after the supplier's cutoff, else 0.

    The whole of spec 4.4's `cutoff_time`. Both arguments are LOCAL times, because a
    cutoff is a fact about the supplier's warehouse day and not about UTC. Either one
    missing means the question cannot be asked -- no cutoff recorded, or no time of day
    supplied by the caller -- and the honest answer is then 0 rather than a guess in
    either direction.

    Exactly ON the cutoff still makes it: a 12:00 cutoff accepts an order placed at
    12:00:00. Ten minutes later it does not, and that is the entire point of the
    function -- 12:10 does not mean a late delivery, it means the next cycle.
    """
    if order_time is None or cutoff_time is None:
        return 0
    return 1 if order_time > cutoff_time else 0


def target_delivery_date(
    *,
    order_date: date,
    supplier: SupplierSpec,
    order_time: time | None = None,
    cutoff_time: time | None = None,
) -> date:
    """Earliest slot that respects the lead time. Walk-in suppliers deliver today.

    A missed cutoff (`order_time` after `cutoff_time`) pushes the whole calculation to
    tomorrow: the lead time starts when the order is *accepted*, not when it is typed.
    """
    if supplier.lead_time_days < 0:
        raise OrderingError(
            f"{supplier.name}: lead_time_days is {supplier.lead_time_days}; "
            "a negative lead time has no meaning"
        )
    slip = cutoff_slip_days(order_time=order_time, cutoff_time=cutoff_time)
    earliest = order_date + timedelta(days=supplier.lead_time_days + slip)
    return next_delivery_on_or_after(earliest, supplier.delivery_weekdays)


@dataclass(frozen=True, slots=True)
class CoverPlan:
    """A cover window plus the calendar reasoning that produced it.

    `CoverWindow` is integrator-owned and has nowhere to record a missed cutoff, so
    that fact lives here instead of being absorbed into `lead_time_days` where nobody
    could see it. `window.lead_time_days` DOES include the slip, because the shelf
    genuinely has to cover the extra day -- `cutoff_missed` is how a renderer explains
    why the number is one higher than the supplier's stated lead time.
    """

    window: CoverWindow
    target_delivery_date: date
    cutoff_missed: bool = False
    slip_days: int = 0
    #: Coded, because these travel into `OrderSuggestion.notes` and a missed cutoff is
    #: the owner's own ten minutes -- the one note on an order she can act on tomorrow.
    notes: tuple[OrderNote, ...] = ()


def cover_plan(
    *,
    order_date: date,
    supplier: SupplierSpec,
    safety_days: Decimal,
    reorder_cadence_days: int | None = None,
    order_time: time | None = None,
    cutoff_time: time | None = None,
) -> CoverPlan:
    """`cover_window`, plus the target date and what a missed cutoff cost."""
    missed = bool(cutoff_slip_days(order_time=order_time, cutoff_time=cutoff_time))
    target = target_delivery_date(
        order_date=order_date,
        supplier=supplier,
        order_time=order_time,
        cutoff_time=cutoff_time,
    )
    on_time = target_delivery_date(order_date=order_date, supplier=supplier)
    slip = (target - on_time).days
    window = cover_window(
        order_date=order_date,
        supplier=supplier,
        safety_days=safety_days,
        reorder_cadence_days=reorder_cadence_days,
        order_time=order_time,
        cutoff_time=cutoff_time,
    )
    notes: list[OrderNote] = []
    if missed and slip > 0:
        notes.append(
            OrderNote(
                kind=OrderNoteKind.CUTOFF_MISSED,
                text=(
                    f"{supplier.name}: CUTOFF MISSED, and it cost {slip} day(s). Ordering at "
                    f"{order_time} is past the {cutoff_time} cutoff, so the lead time "
                    "starts tomorrow -- which lands past this supplier's next delivery "
                    f"day, moving delivery from {on_time} to {target}. The cover window is "
                    f"{slip} day(s) longer to pay for it, so every quantity on this order "
                    "is larger than it needed to be. Ten minutes earlier and this order "
                    "would have been on the earlier van; a run of these is what a Tesco "
                    "trip is made of."
                ),
            )
        )
    elif missed:
        notes.append(
            OrderNote(
                kind=OrderNoteKind.CUTOFF_MISSED,
                text=(
                    f"{supplier.name}: cutoff missed ({order_time} against a {cutoff_time} "
                    f"cutoff) but it cost nothing -- the next delivery day is {target} "
                    "either way, so the extra day is absorbed by the schedule rather than "
                    "by the shelf. Worth knowing, not worth ordering for."
                ),
            )
        )
    return CoverPlan(
        window=window,
        target_delivery_date=target,
        cutoff_missed=missed,
        slip_days=slip,
        notes=tuple(notes),
    )


def cover_window(
    *,
    order_date: date,
    supplier: SupplierSpec,
    safety_days: Decimal,
    reorder_cadence_days: int | None = None,
    order_time: time | None = None,
    cutoff_time: time | None = None,
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

    target = target_delivery_date(
        order_date=order_date,
        supplier=supplier,
        order_time=order_time,
        cutoff_time=cutoff_time,
    )
    # What the missed cutoff ACTUALLY cost, in delivery days rather than in calendar
    # days. A cutoff missed the night before a Mon/Wed/Fri supplier's Wednesday van
    # often costs nothing: the next slot absorbs the extra day. Charging the cover
    # window a day anyway would inflate every late-afternoon order in the week for a
    # delay that did not happen.
    on_time_target = target_delivery_date(order_date=order_date, supplier=supplier)
    slip = (target - on_time_target).days
    if reorder_cadence_days is None:
        following = next_delivery_after(target, supplier.delivery_weekdays)
    else:
        following = next_delivery_on_or_after(
            target + timedelta(days=reorder_cadence_days), supplier.delivery_weekdays
        )
    gap_days = max((following - target).days, 1)
    # The slip is added to the lead-time term rather than the gap: a missed cutoff
    # delays the ARRIVAL, which is exactly what lead time means. See `cover_plan` for
    # how it is reported -- absorbing it silently is what this comment exists to stop.
    effective_lead = supplier.lead_time_days + slip
    total = Decimal(effective_lead) + Decimal(gap_days) + safety_days
    length = max(int(total.to_integral_value(rounding=ROUND_CEILING)), 1)
    return CoverWindow(
        days=tuple(order_date + timedelta(days=offset) for offset in range(length)),
        lead_time_days=effective_lead,
        days_until_next_delivery=gap_days,
        safety_days=safety_days,
    )


def terms_of(supplier: SupplierSpec) -> SupplierTerms:
    """A `SupplierTerms` carrying only what a `SupplierSpec` knows.

    `SupplierSpec` predates spec 4.4's cutoff, delivery fee and free-delivery
    threshold, and both types are integrator-owned. Rather than have every caller
    write this conversion -- and get `terms_are_placeholders` wrong by defaulting it to
    False when it is unknown -- sizing accepts either and fills the gaps here. The
    fields that cannot be recovered are absent, not invented: no cutoff, no fee, no
    threshold.
    """
    return SupplierTerms(
        supplier_id=supplier.id,
        name=supplier.name,
        lead_time_days=supplier.lead_time_days,
        delivery_weekdays=supplier.delivery_weekdays,
        min_order_pence=supplier.min_order_pence,
        order_channel=supplier.order_channel,
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
    #: Spec 5.4's shelf-life cap and invariant 5's perishable test. `None` means the
    #: shelf life was never read -- which is NOT the same as "does not expire", and is
    #: treated as unknown: no cap, and barred from being a top-up.
    shelf_life: ShelfLifeSpec | None = None
    #: The season this ingredient belongs to, if any (spec 4.3).
    season: SeasonSpec | None = None
    #: Has this ingredient EVER been physically counted? False means `on_hand_qty` is a
    #: bare movement sum with no anchor (invariant 6: "not good enough to order
    #: against"). Such a candidate is never sized -- see `size_line` -- because a need
    #: computed from a number nobody has seen on a shelf is a guess wearing a quantity.
    #: Defaults True so a caller that has not been taught about counts keeps its old
    #: behaviour rather than silently ordering nothing.
    has_count_basis: bool = True

    def pack_qty(self) -> Decimal:
        """Pack size in the ingredient's own stocking unit.

        Raises `units.IncompatibleUnitsError` when a pack is measured in a different
        dimension from the stock -- deliberately, rather than treating 1 L as 1 kg.
        """
        return convert(self.pack.pack_size, self.pack.pack_unit, self.unit)

    # --- shelf life and season: the caps (spec 5.4, invariant 4) -------------

    @property
    def is_perishable(self) -> bool:
        """True only when a shelf life is KNOWN and finite (invariant 5)."""
        return self.shelf_life is not None and self.shelf_life.is_perishable

    @property
    def shelf_life_unknown(self) -> bool:
        """No shelf-life record at all -- neither a life nor a statement that it keeps.

        Reported separately from `is_perishable` (`ARCHITECTURE.md` 8F.1): a missing cap
        silently permits the waste the cap exists to prevent, so it must not be allowed
        to read as "does not expire".
        """
        return self.shelf_life is None

    @property
    def shelf_life_cap_days(self) -> int | None:
        """`shelf_life_days - transit_buffer_days`, or None when nothing perishes."""
        return None if self.shelf_life is None else self.shelf_life.usable_days

    @property
    def season_cap_days(self) -> int | None:
        """Days left in the season at the START of the cover window.

        `0` when the ingredient is seasonal and the season is NOT running: an
        out-of-season item is not ordered at all, and zero is how that is expressed so
        the same `min()` handles both cases.
        """
        if self.season is None:
            return None
        remaining = self.season.days_remaining(self.cover.days[0])
        return 0 if remaining is None else remaining

    @property
    def out_of_season(self) -> bool:
        return self.season is not None and not self.season.contains(self.cover.days[0])

    @property
    def effective_cover_days(self) -> int:
        """`min(cover_days, usable shelf life, days left in season)`. Spec 5.4."""
        caps = [self.cover.length]
        for cap in (self.shelf_life_cap_days, self.season_cap_days):
            if cap is not None:
                caps.append(cap)
        return max(min(caps), 0)

    @property
    def is_capped(self) -> bool:
        return self.effective_cover_days < self.cover.length

    @property
    def cap_reason(self) -> str | None:
        """The short sentence that goes on the line. None when nothing capped it.

        Deliberately short -- `po_line.cap_reason` is 80 characters, and this is the
        phrase a person reads next to a quantity they are about to confirm. The full
        arithmetic goes in `SizingOutcome.note`.
        """
        if not self.is_capped:
            return None
        days = self.effective_cover_days
        season_cap = self.season_cap_days
        shelf_cap = self.shelf_life_cap_days
        if self.season is not None and season_cap == days:
            if self.out_of_season:
                return f"not ordered -- {self.season.name} is out of season"
            return f"capped at {days} days -- {self.season.name} ends"
        if shelf_cap == days:
            return f"capped at {days} days -- {self.ingredient_name} shelf life"
        return f"capped at {days} days"

    @property
    def cap_kind(self) -> CapKind | None:
        """The same decision as `cap_reason`, structured.

        Derived from the SAME comparisons rather than parsed back out of the sentence,
        so a reword cannot change what code sees. `cap_reason` stays for a human
        reading a log; this is what a non-English surface branches on (invariant 4).
        """
        if not self.is_capped:
            return None
        days = self.effective_cover_days
        if self.season is not None and self.season_cap_days == days:
            return CapKind.OUT_OF_SEASON if self.out_of_season else CapKind.SEASON_END
        if self.shelf_life_cap_days == days:
            return CapKind.SHELF_LIFE
        return CapKind.OTHER

    # --- quantities ---------------------------------------------------------

    @property
    def full_forecast_qty(self) -> Decimal:
        """What the uncapped cover window forecast -- kept so the cap can be shown."""
        return self.forecast.total

    @property
    def forecast_qty(self) -> Decimal:
        """Forecast over the EFFECTIVE cover window.

        The forecast points are one per day of `cover.days`, in order, so truncating
        the window is a slice rather than a re-scaling: it is the same forecast read
        over fewer days, which is what makes "10 days asked for 55 L, 5 days asks for
        27 L" an arithmetic a person can check.
        """
        days = self.effective_cover_days
        if days >= len(self.forecast.points):
            return self.forecast.total
        return sum((p.qty for p in self.forecast.points[:days]), _ZERO)

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

    `line is None` means nothing was ordered; the notes then say which of the several
    good reasons applied. A note also appears alongside a line when a clamp moved the
    number, so no adjustment is ever silent.

    Each note carries an `OrderNoteKind`, derived from the same comparison that wrote the
    sentence. `note` -- the joined prose -- stays as a property for `cafeops simulate`,
    which shows the working for every candidate in English. What reaches
    `OrderSuggestion.notes` -- and therefore the owner's Telegram message -- is curated
    by `build_suggestion`: forty lines of "nothing needed" is not information, whereas
    `below_par_floor` and `data_error` both are.
    """

    candidate: OrderCandidate
    line: SuggestedLine | None
    coded_notes: tuple[OrderNote, ...] = ()
    #: Stock is under `min_qty` but nothing is forecast to move it. Reported, never
    #: ordered against -- see `size_line`.
    below_par_floor: bool = False
    #: Par or pack data that cannot produce an honest order size.
    data_error: str | None = None
    #: The clamp that reduced a real, forecast-driven need to nothing. `"max_qty"` here
    #: means the par ceiling, not the forecast, decided not to order.
    clamp_blocked: str | None = None
    #: The shelf-life or season cap shortened this line's window (spec 5.4).
    capped: bool = False
    #: Seasonal, and the season is not running. Nothing was ordered, on purpose.
    out_of_season: bool = False
    #: Never counted, so never ordered (stock-orders-suppliers spec C8). The screen lists
    #: these as "count these first", which is the action that unblocks them.
    no_count: bool = False

    @property
    def note(self) -> str | None:
        """Every sentence, joined. The working, for a back-office reader."""
        if not self.coded_notes:
            return None
        return " | ".join(note.text for note in self.coded_notes)


@dataclass(frozen=True, slots=True)
class SizingPlan:
    """The suggestion plus the working behind every candidate considered."""

    suggestion: OrderSuggestion
    outcomes: tuple[SizingOutcome, ...]
    #: Notes the caller supplied rather than sizing deriving -- a missed cutoff, a
    #: what-if override, ingredients skipped for want of a par level. Kept separately so
    #: a re-size (the sourcing pass moving a line to another supplier) can carry them
    #: forward: they are facts about the RUN, and a rebuilt suggestion cannot rediscover
    #: them. Losing them is how a cover window silently grows a day with no explanation.
    extra_notes: tuple[OrderNote, ...] = ()

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
        f"{candidate.effective_cover_days} day(s), need {_shown(candidate.need_qty)}"
    )


def cap_note(candidate: OrderCandidate) -> str | None:
    """The full arithmetic behind a shelf-life or season cap. Spec 5.4.

    `SuggestedLine.cap_reason` is the phrase; this is the working. Both exist because
    the short phrase fits next to a quantity and the working answers the question the
    phrase provokes -- "by how much, and what was it before?" -- which is the question
    that decides whether the owner overrides the cap.
    """
    if not candidate.is_capped:
        return None
    name = candidate.ingredient_name
    unit = candidate.unit.value
    days = candidate.effective_cover_days
    full = candidate.cover.length
    if candidate.out_of_season and candidate.season is not None:
        return (
            f"{name}: NOT ORDERED -- {candidate.season.name} is not running on "
            f"{candidate.cover.days[0]} (spec 4.3). A seasonal line ordered out of "
            "season is stock bought for a drink that is not on the menu."
        )
    shelf = candidate.shelf_life
    if candidate.season is not None and candidate.season_cap_days == days:
        head = (
            f"{name}: SEASON CAP. {candidate.season.name} has {days} day(s) left, "
            f"against a {full}-day cover window"
        )
    elif shelf is not None and shelf.shelf_life_days is not None:
        head = (
            f"{name}: SHELF-LIFE CAP (invariant 4). {shelf.shelf_life_days}-day life "
            f"less a {shelf.transit_buffer_days}-day transit buffer leaves {days} "
            f"usable day(s), against a {full}-day cover window"
        )
    else:
        head = f"{name}: cover window cut from {full} to {days} day(s)"
    if candidate.forecast.low_confidence:
        # Invariant 7: no figures for a withheld forecast, not even the one the cap
        # removed -- the difference of two withheld numbers is still the number.
        return (
            f"{head}. The order is sized on the shorter window, and the forecast figures "
            "are withheld (low confidence)."
        )
    removed = candidate.full_forecast_qty - candidate.forecast_qty
    return (
        f"{head}. Forecast over {full} days was {_shown(candidate.full_forecast_qty)} "
        f"{unit}; over {days} usable day(s) it is {_shown(candidate.forecast_qty)} {unit}, "
        f"so {_shown(removed)} {unit} was deliberately NOT ordered. This under-order is "
        "on purpose: the difference would have spoiled before it could be used. Raising "
        "it recreates exactly the waste the cap prevents -- order again sooner instead."
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

    The **shelf-life and season caps** (spec 5.4, invariant 4) are applied before any of
    that, by `OrderCandidate.forecast_qty` reading the forecast over the effective cover
    window instead of the full one. `cap_reason` then travels on the line to
    `po_line.cap_reason` and into the Telegram message, because a deliberate under-order
    the user cannot see the reason for is one they will override.
    """
    name = candidate.ingredient_name
    if not candidate.has_count_basis:
        # Spec C8 / invariant 6. The on-hand behind this candidate is a movement sum with
        # no physical count under it, so `need = forecast - on_hand` would be a guess
        # dressed as a quantity. Ordering stops here, with the one action that fixes it.
        return SizingOutcome(
            candidate,
            None,
            (
                OrderNote(
                    kind=OrderNoteKind.NOT_COUNTED,
                    text=(
                        f"{name}: NOT ORDERED -- it has never been counted, so the stock "
                        "figure is a ledger sum with nothing under it (invariant 6). Count "
                        "it once and it is sized from the next run."
                    ),
                ),
            ),
            no_count=True,
        )
    par = candidate.par
    pack_qty = candidate.pack_qty()
    if pack_qty <= _ZERO:
        error = (
            f"{name}: pack size {_shown(candidate.pack.pack_size)} "
            f"{candidate.pack.pack_unit.value} is not positive, so no pack count can be "
            "computed; fix the supplier product"
        )
        return SizingOutcome(
            candidate,
            None,
            (OrderNote(kind=OrderNoteKind.PACK_DATA_ERROR, text=error),),
            data_error=error,
        )

    available = candidate.available_qty
    need = candidate.need_qty
    notes: list[OrderNote] = []
    data_error: str | None = None
    capped = candidate.is_capped
    capped_note = cap_note(candidate)
    if capped_note is not None:
        notes.append(
            OrderNote(
                kind=(
                    OrderNoteKind.OUT_OF_SEASON_NOT_ORDERED
                    if candidate.out_of_season
                    else OrderNoteKind.CAP_LINE
                ),
                text=capped_note,
            )
        )
    if par.min_qty > par.max_qty:
        data_error = (
            f"{name}: par min_qty {_shown(par.min_qty)} exceeds max_qty "
            f"{_shown(par.max_qty)}; max_qty wins so the order cannot run away, but the "
            "par level is wrong and needs fixing"
        )
        notes.append(OrderNote(kind=OrderNoteKind.PAR_DATA_ERROR, text=data_error))

    packs = _ceil_packs(need, pack_qty) if need > _ZERO else 0
    forecast_driven = packs
    clamped: str | None = None
    below_par_floor = available < par.min_qty

    floor_packs = max(_ceil_packs(par.min_qty - available, pack_qty), 0)
    if packs >= 1 and floor_packs > packs:
        if capped:
            # INVARIANT 4 outranks the par floor. The floor says "keep this much on the
            # shelf"; the cap says "this much is all that will keep". Obeying the floor
            # here would buy the spoilage the cap exists to prevent, so the floor loses
            # and says why -- a floor above the usable window is a par level to fix.
            notes.append(
                OrderNote(
                    kind=OrderNoteKind.PAR_FLOOR_BELOW_CAP,
                    text=(
                        f"{name}: par floor min_qty {_shown(par.min_qty)} "
                        f"{candidate.unit.value} would need {floor_packs} pack(s), but only "
                        f"{packs} fit inside the {candidate.effective_cover_days}-day usable "
                        "window. NOT raised -- invariant 4 outranks a par floor, and a floor "
                        "above what will keep is a par level to fix, not stock to buy."
                    ),
                )
            )
        else:
            clamped = "min_qty"
            notes.append(
                OrderNote(
                    kind=OrderNoteKind.PAR_FLOOR_RAISED,
                    text=(
                        f"{name}: raised from {packs} to {floor_packs} pack(s) so on-hand "
                        f"reaches the par floor min_qty {_shown(par.min_qty)} "
                        f"{candidate.unit.value} ({forecast_text(candidate)})"
                    ),
                )
            )
            packs = floor_packs

    cap_packs = _floor_packs(par.max_qty - available, pack_qty)
    if cap_packs < packs:
        clamped = "max_qty"
        note_kind = OrderNoteKind.PAR_CEILING_CUT
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
            # A different KIND from a plain ceiling cut, because the answer is different:
            # one is "the par level sized this", the other is "fix the par level".
            note_kind = OrderNoteKind.PAR_CEILING_BELOW_DEMAND
            note += (
                f". WARNING: that leaves {_shown(capped_result)} against forecast demand "
                f"{_shown(candidate.forecast_qty)} over the next "
                f"{candidate.effective_cover_days} day(s) -- max_qty is below one cover "
                "window of demand, so this par level guarantees a shortfall no matter "
                "what the forecast says"
            )
        notes.append(OrderNote(kind=note_kind, text=note))

    if packs <= 0:
        if candidate.out_of_season:
            # The cap note already says it. Adding "nothing needed" on top would read
            # as though the forecast decided this, when the calendar did.
            pass
        elif below_par_floor and forecast_driven == 0:
            notes.append(
                OrderNote(
                    kind=OrderNoteKind.BELOW_PAR_FLOOR_NOT_ORDERED,
                    text=(
                        f"{name}: on-hand {_shown(candidate.on_hand_qty)} "
                        f"{candidate.unit.value} is under the par floor min_qty "
                        f"{_shown(par.min_qty)}, but nothing is forecast to move it "
                        f"({forecast_text(candidate)}). NOT ordered -- either the par floor "
                        "is wrong or this ingredient is not selling. A human decides, not a "
                        "clamp."
                    ),
                )
            )
        elif clamped is None:
            notes.append(
                OrderNote(
                    kind=OrderNoteKind.NOTHING_NEEDED,
                    text=(
                        f"{name}: nothing needed -- {forecast_text(candidate)} is already "
                        f"covered by on-hand {_shown(candidate.on_hand_qty)} + open POs "
                        f"{_shown(candidate.on_open_pos_qty)}"
                    ),
                )
            )
        return SizingOutcome(
            candidate,
            None,
            tuple(notes),
            below_par_floor=below_par_floor and forecast_driven == 0,
            data_error=data_error,
            clamp_blocked=clamped if forecast_driven > 0 else None,
            capped=capped,
            out_of_season=candidate.out_of_season,
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
        confidence=candidate.forecast.confidence,
        cover_days=candidate.effective_cover_days,
        cap_reason=candidate.cap_reason,
        cap_kind=candidate.cap_kind,
        below_par_floor=below_par_floor,
    )
    return SizingOutcome(
        candidate,
        line,
        tuple(notes),
        data_error=data_error,
        capped=capped,
    )


# ==========================================================================
# The two top-up decisions: a minimum, and a price break (spec 5.4)
# ==========================================================================
#
# `min_order_pence` and `free_delivery_threshold_pence` are NOT the same decision and
# are handled separately here:
#
# * A **minimum** is a condition on ordering at all. Below it the supplier will not
#   ship, so the choice is "add stock or get nothing" -- and getting nothing is a
#   stockout in whatever the order was for.
# * A **threshold** is a price break. Below it the order still ships, it just costs
#   the delivery fee. So the choice is "add stock or pay the fee", and adding £30 of
#   stock to save £5.95 is not a saving.
#
# Both may only use **non-perishable** items (invariant 5). Buying milk to clear a
# minimum is buying waste, and doing it to save a delivery fee is buying waste to save
# £5.95. Every top-up line says which of the two decisions produced it.


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


@dataclass(frozen=True, slots=True)
class TopUpPool:
    """Who may be used to top up an order, and who was refused for what reason."""

    eligible: tuple[tuple[Decimal, OrderCandidate], ...] = ()
    no_velocity: tuple[OrderCandidate, ...] = ()
    #: INVARIANT 5. Perishable, so never a top-up however short its cover.
    perishable: tuple[OrderCandidate, ...] = ()
    #: No shelf-life record at all. Excluded for the same reason, one step weaker:
    #: unknown is not "keeps forever" (`ARCHITECTURE.md` 8F.1).
    shelf_life_unknown: tuple[OrderCandidate, ...] = ()


def top_up_pool(candidates: Iterable[OrderCandidate], ordered_ids: frozenset[int]) -> TopUpPool:
    """Non-perishable tier B candidates not on the order, shortest remaining cover first.

    Four exclusions, in order of how much they matter:

    1. **Perishables (invariant 5).** The test is `ShelfLifeSpec.is_perishable`. This is
       not a heuristic and not overridable: a perishable top-up is buying stock whose
       reason for existing is a supplier's minimum rather than a customer's order, and
       it will be thrown away.
    2. **Unknown shelf life.** No record is not the same fact as "does not expire", and
       the safe reading of an unknown is that it might perish.
    3. **Tier A**, because spec 5.4 says tier B -- tier A is the auto-ordered core and
       its quantities should come from the forecast, not from a supplier's minimum.
    4. **Items already on the order**, because inflating a line the owner reads as
       "this is what I need" hides the top-up inside it. A top-up is always its own line.
    """
    eligible: list[tuple[Decimal, OrderCandidate]] = []
    no_velocity: list[OrderCandidate] = []
    perishable: list[OrderCandidate] = []
    unknown: list[OrderCandidate] = []
    for candidate in candidates:
        if candidate.tier is not Tier.B or candidate.ingredient_id in ordered_ids:
            continue
        if not candidate.has_count_basis:
            # Its cover is computed from an unanchored figure, so "shortest cover first"
            # would rank it on a guess. Same rule as `size_line`: count it first.
            continue
        if candidate.is_perishable:
            perishable.append(candidate)
            continue
        if candidate.shelf_life_unknown:
            unknown.append(candidate)
            continue
        cover = remaining_cover_days(candidate)
        if cover is None:
            no_velocity.append(candidate)
        else:
            eligible.append((cover, candidate))
    eligible.sort(key=lambda pair: (pair[0], pair[1].ingredient_name))
    return TopUpPool(
        eligible=tuple(eligible),
        no_velocity=tuple(no_velocity),
        perishable=tuple(perishable),
        shelf_life_unknown=tuple(unknown),
    )


def _top_up(
    *,
    terms: SupplierTerms,
    lines: Sequence[SuggestedLine],
    candidates: Sequence[OrderCandidate],
    target_pence: int,
    objective: str,
    rationale: str,
    free_delivery: bool,
) -> tuple[tuple[SuggestedLine, ...], bool, tuple[OrderNote, ...]]:
    """Add non-perishable tier B lines, shortest cover first, until `target_pence`.

    Every added pack is stock the owner did not ask for, so the constraints are tight:
    tier B only (a human reviews it anyway), non-perishable only (invariant 5), only
    items that demonstrably move, never past the item's own `max_qty`, always as its own
    `is_top_up` line, always with the reason -- and with WHICH target it was chasing --
    in the notes. If the target still cannot be met, the shortfall is reported rather
    than padded.

    Packs are added **one per item per pass**, walking the ranking from the shortest
    remaining cover downward and stopping the instant the target is met. The strict
    alternative -- fill the shortest-cover item to its `max_qty` before looking at the
    next -- follows spec 5.4's wording more literally but concentrates the whole
    shortfall on one product. One pass at a time still serves the shortest-cover item
    first, and spreads the rest. This is an interpretation, and it is the kind of choice
    the owner should get to overrule.
    """
    subtotal = sum(line.line_total_pence for line in lines)
    pool = top_up_pool(candidates, frozenset(line.ingredient_id for line in lines))

    added: dict[int, tuple[OrderCandidate, int]] = {}
    total = subtotal
    for _ in range(_MAX_TOP_UP_PASSES):
        if total >= target_pence:
            break
        progressed = False
        for _cover, candidate in pool.eligible:
            if total >= target_pence:
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
            OrderNote(
                kind=OrderNoteKind.TOP_UP_IMPOSSIBLE,
                text=(
                    f"{terms.name}: order total {pounds(subtotal)} is below the "
                    f"{pounds(target_pence)} {objective} and no eligible item could be "
                    f"added. {rationale}"
                ),
            )
        ]
        notes.extend(_pool_notes(pool))
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
                confidence=candidate.forecast.confidence,
                cover_days=candidate.effective_cover_days,
                cap_reason=f"top-up to reach the {objective}",
                # From the CALLER's decision, not from the objective's wording. The
                # previous `"free delivery" in objective` test read the code back out of
                # the sentence it had just written, which is the whole failure this
                # change exists to end -- rewording `objective` silently reclassified
                # every top-up line as a minimum top-up.
                cap_kind=(
                    CapKind.TOP_UP_FREE_DELIVERY if free_delivery else CapKind.TOP_UP_MINIMUM
                ),
            )
        )
        cover = remaining_cover_days(candidate)
        described.append(
            f"{candidate.ingredient_name} x{packs} "
            f"({_shown(cover) if cover is not None else '?'} days' cover left, "
            f"{pounds(packs * candidate.pack.price_pence)})"
        )

    notes = [
        OrderNote(
            kind=OrderNoteKind.TOP_UP_APPLIED,
            text=(
                f"{terms.name}: TOP-UP TO REACH THE {objective.upper()}. The forecast asked "
                f"for {pounds(subtotal)}, below {pounds(target_pence)}, so "
                f"{len(top_up_lines)} non-perishable tier-B item(s) were added -- shortest "
                f"remaining cover first: {'; '.join(described)}. {rationale} None of this "
                "was forecast as needed; it is stock bought early, and it is the one part "
                "of this order that exists for the supplier's benefit rather than the "
                "cafe's."
            ),
        ),
    ]
    if total < target_pence:
        notes.append(
            OrderNote(
                kind=OrderNoteKind.TOP_UP_SHORT_OF_TARGET,
                text=(
                    f"{terms.name}: still {pounds(target_pence - total)} short of the "
                    f"{pounds(target_pence)} {objective} after topping up -- every eligible "
                    "item is already at its par max_qty. Do not pad this further; either "
                    "the target, the par levels or the supplier needs a human decision."
                ),
            )
        )
    notes.extend(_pool_notes(pool))
    return (*lines, *top_up_lines), True, tuple(notes)


def _pool_notes(pool: TopUpPool) -> list[OrderNote]:
    notes: list[OrderNote] = []
    if pool.perishable:
        names = ", ".join(sorted(c.ingredient_name for c in pool.perishable))
        notes.append(
            OrderNote(
                kind=OrderNoteKind.TOP_UP_EXCLUDED_PERISHABLE,
                text=(
                    f"INVARIANT 5: {len(pool.perishable)} tier-B item(s) were NOT used as "
                    f"top-up because they are perishable: {names}. Buying something that "
                    "spoils in order to clear a supplier's minimum or save a delivery fee is "
                    "buying waste, so these are excluded however short their cover is."
                ),
            )
        )
    if pool.shelf_life_unknown:
        names = ", ".join(sorted(c.ingredient_name for c in pool.shelf_life_unknown))
        notes.append(
            OrderNote(
                kind=OrderNoteKind.TOP_UP_EXCLUDED_SHELF_LIFE_UNKNOWN,
                text=(
                    f"{len(pool.shelf_life_unknown)} tier-B item(s) were not used as top-up "
                    f"because no shelf life is recorded for them: {names}. An unknown shelf "
                    "life is not 'does not expire', and the top-up is not the place to find "
                    "out which."
                ),
            )
        )
    if pool.no_velocity:
        names = ", ".join(sorted(c.ingredient_name for c in pool.no_velocity))
        notes.append(
            OrderNote(
                kind=OrderNoteKind.TOP_UP_EXCLUDED_NO_VELOCITY,
                text=(
                    f"{len(pool.no_velocity)} tier-B item(s) were not used as top-up because "
                    f"nothing has been recorded moving: {names}. Buying stock that does not "
                    "sell to reach a supplier minimum is not a saving."
                ),
            )
        )
    if not pool.eligible:
        notes.append(
            OrderNote(
                kind=OrderNoteKind.TOP_UP_POOL_EMPTY,
                text=(
                    "no non-perishable tier-B item with measured consumption was available "
                    "to top up with."
                ),
            )
        )
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
    terms: SupplierTerms | None = None,
    free_delivery_top_up_multiple: Decimal = DEFAULT_FREE_DELIVERY_TOP_UP_MULTIPLE,
    extra_notes: Sequence[OrderNote] = (),
) -> SizingPlan:
    """Size every candidate, then consider the two top-ups. Spec 5.4.

    `terms` carries spec 4.4's delivery fee, free-delivery threshold and
    `terms_are_placeholders`, none of which fit on the integrator-owned `SupplierSpec`.
    Omit it and only the minimum is considered -- `terms_of(supplier)` is what fills in,
    and it invents nothing.

    The returned `OrderSuggestion` carries a single `cover_window`, but `safety_days` is
    per par level and the shelf-life cap is per ingredient, so the real window is per
    line. What is reported here is the **widest** of them; each line carries its own
    `cover_days` and each candidate's full window is in `SizingPlan.outcomes`.
    """
    resolved = terms or terms_of(supplier)
    outcomes = [size_line(candidate) for candidate in candidates]
    lines = tuple(o.line for o in outcomes if o.line is not None)

    # Curated, not every outcome's note. A purchase order's notes are read by a person
    # deciding whether to press Confirm: a clamp that moved a number she is about to
    # approve belongs there, forty "nothing needed" lines do not.
    notes: list[OrderNote] = [
        note for o in outcomes if o.line is not None for note in o.coded_notes
    ]
    notes.extend(
        note
        for o in outcomes
        if o.data_error is not None and o.line is None
        for note in o.coded_notes
        if note.kind in (OrderNoteKind.PAR_DATA_ERROR, OrderNoteKind.PACK_DATA_ERROR)
    )
    # A clamp or a cap that suppressed a real need belongs in front of whoever confirms
    # this order: the absence of a line is the dangerous part, and an absent line cannot
    # carry its own explanation.
    notes.extend(
        note
        for o in outcomes
        if (o.clamp_blocked is not None or o.out_of_season) and o.line is None
        for note in o.coded_notes
    )
    below_floor = [o.candidate.ingredient_name for o in outcomes if o.below_par_floor]
    if below_floor:
        notes.append(
            OrderNote(
                kind=OrderNoteKind.BELOW_PAR_FLOOR_SUMMARY,
                text=(
                    f"{len(below_floor)} item(s) are below their par floor min_qty with "
                    "nothing forecast to move them, and were NOT ordered: "
                    f"{', '.join(sorted(below_floor))}. Either the par floor or the tier is "
                    "wrong. Ordering against a floor that nothing is consuming would be "
                    "spending money on a data error."
                ),
            )
        )
    uncounted = sorted(o.candidate.ingredient_name for o in outcomes if o.no_count)
    if uncounted:
        notes.append(
            OrderNote(
                kind=OrderNoteKind.NOT_COUNTED,
                text=(
                    f"{len(uncounted)} item(s) have never been counted and were NOT ordered: "
                    f"{', '.join(uncounted)}. Their stock figure has no physical count "
                    "under it (invariant 6); one count each makes them orderable."
                ),
            )
        )
    capped = [o for o in outcomes if o.capped and o.line is not None]
    if capped:
        notes.append(
            OrderNote(
                kind=OrderNoteKind.CAP_SUMMARY,
                text=(
                    f"SHELF LIFE / SEASON CAP (invariant 4): {len(capped)} line(s) are "
                    "deliberately SMALLER than the forecast asked for, because the rest "
                    "would spoil or fall outside the season before it could be used: "
                    + "; ".join(
                        f"{o.line.ingredient_name} -- {o.line.cap_reason}"
                        for o in capped
                        if o.line is not None and o.line.cap_reason is not None
                    )
                    + ". Ordering more often is the fix, not ordering more."
                ),
            )
        )

    topped_up = False
    if lines:
        subtotal = sum(line.line_total_pence for line in lines)
        if resolved.min_order_pence > 0 and subtotal < resolved.min_order_pence:
            lines, topped_up, top_up_notes = _top_up(
                terms=resolved,
                lines=lines,
                candidates=candidates,
                target_pence=resolved.min_order_pence,
                objective="minimum order",
                rationale=(
                    "A MINIMUM is a condition on ordering at all: below it this supplier "
                    "ships nothing, so the alternative to topping up is not a smaller "
                    "order, it is no order and a stockout."
                ),
                free_delivery=False,
            )
            notes.extend(top_up_notes)
        lines, fee_topped_up, fee_notes = _consider_free_delivery(
            terms=resolved,
            lines=lines,
            candidates=candidates,
            multiple=free_delivery_top_up_multiple,
        )
        topped_up = topped_up or fee_topped_up
        notes.extend(fee_notes)
    elif resolved.min_order_pence > 0:
        notes.append(
            OrderNote(
                kind=OrderNoteKind.MINIMUM_NOT_APPLICABLE,
                text=(
                    f"{resolved.name}: nothing is needed, so the "
                    f"{pounds(resolved.min_order_pence)} minimum does not apply -- a minimum "
                    "is a condition on placing an order, not a reason to place one."
                ),
            )
        )

    if resolved.terms_are_placeholders:
        notes.append(
            OrderNote(
                kind=OrderNoteKind.PLACEHOLDER_TERMS,
                text=(
                    f"{resolved.name}: THESE TERMS ARE INVENTED PLACEHOLDERS. Lead time "
                    f"{resolved.lead_time_days}d, delivery days, cutoff "
                    f"{resolved.cutoff_time or 'unknown'}, minimum "
                    f"{pounds(resolved.min_order_pence)} and the free-delivery threshold "
                    "were never confirmed with this supplier (ARCHITECTURE.md 8F.4). The "
                    "cover window above -- and therefore every quantity on this order -- is "
                    "only as good as they are. Confirm them before trusting the numbers."
                ),
            )
        )
    notes.extend(extra_notes)

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
        coded_notes=tuple(notes),
    )
    return SizingPlan(
        suggestion=suggestion, outcomes=final_outcomes, extra_notes=tuple(extra_notes)
    )


def _consider_free_delivery(
    *,
    terms: SupplierTerms,
    lines: tuple[SuggestedLine, ...],
    candidates: Sequence[OrderCandidate],
    multiple: Decimal,
) -> tuple[tuple[SuggestedLine, ...], bool, tuple[OrderNote, ...]]:
    """The price-break decision, kept apart from the minimum on purpose (spec 5.4).

    Three outcomes, all of them spoken aloud:

    * Already above the threshold, or no threshold / no fee -> nothing to decide.
    * Close enough that the shortfall is worth at most `multiple` x the fee -> top up,
      and say that the FEE, not a minimum, drove it.
    * Further away than that -> pay the fee, and say what topping up would have cost.
      Spending £30 to save £5.95 is not a saving, and an order that silently did it
      would look like a forecast when it was a fee dressed up as one.
    """
    if terms.free_delivery_threshold_pence is None or terms.delivery_fee_pence <= 0:
        return lines, False, ()
    threshold = terms.free_delivery_threshold_pence
    subtotal = sum(line.line_total_pence for line in lines)
    if subtotal >= threshold:
        return (
            lines,
            False,
            (
                OrderNote(
                    kind=OrderNoteKind.FREE_DELIVERY_CLEARED,
                    text=(
                        f"{terms.name}: {pounds(subtotal)} clears the {pounds(threshold)} "
                        f"free-delivery threshold, so the "
                        f"{pounds(terms.delivery_fee_pence)} delivery fee is waived. "
                        "Nothing was added to achieve that."
                    ),
                ),
            ),
        )
    shortfall = threshold - subtotal
    budget = int(
        (Decimal(terms.delivery_fee_pence) * multiple).to_integral_value(rounding=ROUND_FLOOR)
    )
    if shortfall > budget:
        return (
            lines,
            False,
            (
                OrderNote(
                    kind=OrderNoteKind.FREE_DELIVERY_FEE_PAID,
                    text=(
                        f"{terms.name}: PAYING THE {pounds(terms.delivery_fee_pence)} "
                        f"DELIVERY FEE. The order is {pounds(subtotal)} and free delivery "
                        f"starts at {pounds(threshold)}, so clearing it would mean buying "
                        f"{pounds(shortfall)} of stock nobody asked for to save "
                        f"{pounds(terms.delivery_fee_pence)}. That is not a saving, so the "
                        "fee is paid. (A free-delivery threshold is a price break, not a "
                        "minimum -- this order ships either way.)"
                    ),
                ),
            ),
        )
    lines, added, notes = _top_up(
        terms=terms,
        lines=lines,
        candidates=candidates,
        target_pence=threshold,
        objective="free-delivery threshold",
        rationale=(
            f"This is the FEE decision, not the minimum: the order ships either way, and "
            f"topping up {pounds(shortfall)} to save the "
            f"{pounds(terms.delivery_fee_pence)} delivery fee is only worth it because "
            f"the shortfall is within {multiple}x the fee -- an interpretation of spec "
            "5.4, which says to top up below the threshold but not at what price."
        ),
        free_delivery=True,
    )
    return lines, added, notes
