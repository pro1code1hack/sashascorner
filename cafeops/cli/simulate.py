"""simulate -- forecast and ordering (spec 5.3, 5.4)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

import typer
from rich.table import Table

from cafeops.cli._common import console
from cafeops.config import settings
from cafeops.db.base import session_scope
from cafeops.domain.types import Tier
from cafeops.domain.units import format_qty

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from cafeops.domain.ordering import SizingOutcome
    from cafeops.domain.types import SupplierSpec, SupplierTerms, Unit
    from cafeops.services.build_order import SplitResult

SIM_CAVEATS = (
    "Every order below is a DRAFT for human confirmation (invariant 1), so "
    "par_level.auto_order_enabled does not gate it unless --require-auto-order is "
    "passed. That flag is the unattended path, where invariant 2 does gate it.",
    "This replay reads only what was knowable on each order date: on-hand at local "
    "midnight that morning, history to the previous day. No hindsight.",
    "Shelf lives are ESTIMATE defaults, not measurements (ARCHITECTURE.md 8F.1). They "
    "CAP order quantities (invariant 4), so a wrong one either wastes stock or causes a "
    "stockout. The ~15 perishables that actually move are worth confirming first.",
)


def _sim_terms_caveat(terms: Sequence[SupplierTerms]) -> str:
    """The placeholder caveat, built from the column rather than a hard-coded list."""
    invented = [t.name for t in terms if t.terms_are_placeholders]
    real = [t.name for t in terms if not t.terms_are_placeholders]
    return (
        f"{len(invented)} of {len(terms)} suppliers' terms are INVENTED PLACEHOLDERS: "
        f"{', '.join(invented)}. Lead time, delivery weekdays, cutoff, minimum order and "
        "free-delivery threshold were never confirmed with any of them "
        "(ARCHITECTURE.md 8F.4). Every cover window below is only as good as they are, "
        "and nothing in the forecaster or the sizing has been tuned against them. Only "
        f"{' and '.join(real)} have terms anybody has checked."
    )


def _sim_history_range(session: Session) -> tuple[date, date] | None:
    """First and last LOCAL day with a SALE movement."""
    from sqlalchemy import func, select

    from cafeops.db.models import MovementType, StockMovement

    row = session.execute(
        select(
            func.min(StockMovement.occurred_at),
            func.max(StockMovement.occurred_at),
        ).where(StockMovement.type == MovementType.SALE)
    ).first()
    if row is None or row[0] is None:
        return None
    tz = settings.tz
    return row[0].astimezone(tz).date(), row[1].astimezone(tz).date()


def _sim_order_dates(start: date, end: date, weekday: int) -> list[date]:
    """Every `weekday` in (start, end]. One order run per week, per supplier."""
    first = start + timedelta(days=1)
    offset = (weekday - first.isoweekday()) % 7
    day = first + timedelta(days=offset)
    dates: list[date] = []
    while day <= end:
        dates.append(day)
        day += timedelta(days=7)
    return dates


def _sim_delivery_text(spec: SupplierSpec) -> str:
    from cafeops.domain.forecast import WEEKDAY_NAMES

    if not spec.delivery_weekdays:
        return "any day (walk-in)"
    return "/".join(WEEKDAY_NAMES[d] for d in spec.delivery_weekdays)


def _sim_qty(qty: Decimal, unit: Unit) -> str:
    return format_qty(qty, unit)


def _sim_print_line(outcome: SizingOutcome) -> None:
    """The working for one ordered line. Invariant 7 withholds the low-confidence figure."""
    from cafeops.domain.ordering import pounds

    line = outcome.line
    # Only `plan.ordered` reaches here, and `ordered` is exactly the outcomes with a line.
    assert line is not None
    candidate = outcome.candidate
    cover = candidate.cover
    unit = candidate.unit
    tag = "[magenta]TOP-UP[/magenta] " if line.is_top_up else ""
    console.print(
        f"    {tag}[bold]{line.ingredient_name}[/bold] [{candidate.tier.value}]"
        f"  pack {_sim_qty(line.pack.pack_size, line.pack.pack_unit)} @ "
        f"{pounds(line.pack.price_pence)}"
    )
    console.print(
        f"      cover {cover.length}d {cover.days[0]}..{cover.days[-1]}"
        f"  = lead {cover.lead_time_days} + gap {cover.days_until_next_delivery}"
        f" + safety {cover.safety_days}"
    )
    if candidate.is_capped:
        # SPEC 5.4 / INVARIANT 4: the deliberate under-order has to be visible, and
        # visible NEXT TO the quantity rather than in a note further down the page.
        shelf = candidate.shelf_life
        detail = ""
        if shelf is not None and shelf.shelf_life_days is not None:
            detail = (
                f"  (shelf life {shelf.shelf_life_days}d - transit {shelf.transit_buffer_days}d)"
            )
        console.print(
            f"      [yellow]CAPPED: effective cover {candidate.effective_cover_days}d of "
            f"{cover.length}d -- {line.cap_reason}[/yellow]{detail}"
        )
    if line.low_confidence:
        # INVARIANT 7: the reason goes in place of the number, not beside it.
        console.print("      [yellow]forecast WITHHELD -- low confidence:[/yellow]")
        for reason in line.confidence_reasons:
            console.print(f"        [yellow]{reason}[/yellow]")
        console.print(
            f"      on-hand {_sim_qty(line.on_hand_qty, unit)}"
            f"   open POs {_sim_qty(line.on_open_pos_qty, unit)}"
            f"   -> [bold]{line.packs} pack(s)[/bold] {pounds(line.line_total_pence)}"
        )
    else:
        window = (
            f"{candidate.effective_cover_days}d of {cover.length}d"
            if candidate.is_capped
            else f"{cover.length}d"
        )
        console.print(
            f"      forecast {_sim_qty(line.forecast_qty, unit)} over {window}"
            + (
                f" (uncapped {_sim_qty(candidate.full_forecast_qty, unit)})"
                if candidate.is_capped
                else ""
            )
            + f"  - on-hand {_sim_qty(line.on_hand_qty, unit)}"
            f"  - open POs {_sim_qty(line.on_open_pos_qty, unit)}"
            f"  = need {_sim_qty(line.need_qty, unit)}"
        )
        console.print(
            f"      base_daily {_sim_qty(candidate.forecast.base_daily, unit)}/day"
            f"   packs = ceil({_sim_qty(line.need_qty, unit)} / "
            f"{_sim_qty(candidate.pack_qty(), unit)}) = [bold]{line.packs}[/bold]"
            f"   {pounds(line.line_total_pence)}"
        )
    console.print(
        f"      resulting on-hand {_sim_qty(line.resulting_on_hand, unit)}"
        f"   par [{_sim_qty(candidate.par.min_qty, unit)} .. "
        f"{_sim_qty(candidate.par.max_qty, unit)}]"
        + (f"   [red]CLAMPED by {line.clamped}[/red]" if line.clamped else "")
    )
    if outcome.note:
        console.print(f"      [dim]{outcome.note}[/dim]")


def _sim_print_sourcing(result: SplitResult, *, verbose: bool) -> None:
    """Every sourcing decision, and what each one cost or saved. Spec 4.4.

    All of them, not only the switches. A decision to keep the incumbent because the
    alternate is DEARER is the case that proves the comparison is running at all, and
    the two deliberately worse seeded alternates (Monolith beans, Amazon cups) exist to
    be shown losing. Only ingredients with a real alternate reach `choices`, so this
    stays a handful of lines rather than a wall.
    """
    from cafeops.domain.ordering import pounds

    choices = result.split.choices
    if not choices:
        return
    switched = [c for c in choices if not c.chosen.is_preferred]
    rejected = [c for c in choices if c.cheaper_rejected is not None]
    console.print(
        f"\n[bold]sourcing[/bold]  {len(choices)} ingredient(s) with a real choice: "
        f"{len(switched)} switched, {len(rejected)} kept a dearer supplier on purpose"
    )
    for choice in choices:
        if not choice.alternatives and not verbose:
            continue
        marker = "[green]SWITCHED[/green]" if not choice.chosen.is_preferred else "KEPT"
        console.print(f"  {marker} {choice.reason}")
        if choice.cheaper_rejected is not None and choice.forgone_saving_pence is not None:
            console.print(
                f"    [yellow]forgone saving {pounds(int(choice.forgone_saving_pence))}"
                f"[/yellow] -- a decision, not an oversight (spec 4.4)"
            )


def _sim_print_emergency(
    session: Session, result: SplitResult, *, order_date: date, commit: bool
) -> None:
    """The Tesco run: what could not wait, and the premium it cost. Spec 4.4."""
    from datetime import UTC, datetime
    from datetime import time as dtime
    from decimal import ROUND_HALF_UP

    from cafeops.domain.ordering import pounds
    from cafeops.domain.sourcing import emergency_premium_pence
    from cafeops.services.build_order import record_emergency_lines

    def _pence(value: Decimal) -> int:
        # ROUND_HALF_UP, not int() truncation: `record_emergency_lines` rounds the same
        # way before writing `tesco_routing.premium_pence`, and a live figure that
        # truncates while the logged one rounds would show two different premiums for
        # the same routing a moment apart (invariant 8 -- the numbers must agree).
        return int(value.to_integral_value(rounding=ROUND_HALF_UP))

    plan = result.emergency
    if not plan.lines and not plan.notes:
        return
    if plan.lines:
        premium = plan.total_premium_pence
        console.print(
            f"\n[bold red]Tesco emergency routing[/bold red]  {len(plan.lines)} line(s)"
            + (
                f", retail premium {pounds(_pence(premium))} over the scheduled suppliers"
                if premium is not None
                else ", premium not computable -- a unit price is missing"
            )
        )
        for line in plan.lines:
            line_premium = emergency_premium_pence(line)
            console.print(
                f"    [bold]{line.ingredient_name}[/bold] {_sim_qty(line.qty, line.unit)}"
                + (
                    f"  retail {line.retail_unit_price_pence:.2f}p/{line.unit.value}"
                    if line.retail_unit_price_pence is not None
                    else "  retail price unknown"
                )
                + (
                    f"  vs preferred {line.preferred_unit_price_pence:.2f}p/{line.unit.value}"
                    if line.preferred_unit_price_pence is not None
                    else ""
                )
                + (
                    f"  = [red]premium {pounds(_pence(line_premium))}[/red]"
                    if line_premium is not None
                    else ""
                )
            )
            console.print(f"      [dim]{line.reason}[/dim]")
    for note in plan.notes:
        console.print(f"  [yellow]note[/yellow] {note}")
    if commit and plan.lines:
        at = datetime.combine(order_date, dtime(9, 0), tzinfo=settings.tz).astimezone(UTC)
        written = record_emergency_lines(session, result, at=at)
        console.print(
            f"  [green]logged {len(written)} routing(s) to tesco_routing[/green] -- the "
            "accumulated log is the argument for fixing the ordering cadence, so it is "
            "data rather than a note (spec 4.4)"
        )


def simulate(
    supplier: Annotated[
        str | None, typer.Option("--supplier", help="Only this supplier (name, case-insensitive).")
    ] = None,
    weeks: Annotated[
        int | None, typer.Option("--weeks", help="Only the last N weekly order runs.")
    ] = None,
    order_weekday: Annotated[
        int, typer.Option("--order-weekday", help="ISO weekday to place orders on (1=Mon).")
    ] = 1,
    cadence_days: Annotated[
        int,
        typer.Option(
            "--cadence-days",
            help=(
                "Reordering interval for the cover window's middle term. 0 = the literal "
                "spec reading (the supplier's next available slot), which assumes "
                "reordering at every opportunity."
            ),
        ),
    ] = 7,
    min_order_pence: Annotated[
        int | None,
        typer.Option(
            "--min-order-pence",
            help=(
                "WHAT-IF: override every supplier's minimum order. Six of the eight stored "
                "minimums are invented placeholders, so asking 'what if it were really X' "
                "is the only honest way to exercise the top-up."
            ),
        ),
    ] = None,
    order_time: Annotated[
        str | None,
        typer.Option(
            "--order-time",
            help=(
                "Local time of day the order is placed, HH:MM. Past a supplier's cutoff "
                "the lead time starts tomorrow and the cover window grows a day -- which "
                "is how a Tesco run happens. Omitted, no cutoff is applied."
            ),
        ),
    ] = None,
    free_delivery_multiple: Annotated[
        float,
        typer.Option(
            "--free-delivery-multiple",
            help=(
                "How far below a free-delivery threshold it is worth buying stock to save "
                "the fee, as a multiple of the fee. An INTERPRETATION of spec 5.4, which "
                "says to top up below the threshold but not at what price. 0 never tops up "
                "for a fee."
            ),
        ),
    ] = 3.0,
    require_auto_order: Annotated[
        bool,
        typer.Option(
            "--require-auto-order",
            help=(
                "Only order ingredients that have EARNED auto-ordering through drift "
                "history (invariant 2). This is the unattended path a scheduled job takes."
            ),
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Show the working for candidates that were NOT ordered."),
    ] = False,
    commit: Annotated[
        bool,
        typer.Option("--commit", help="Write each week's suggestion as a DRAFT purchase order."),
    ] = False,
) -> None:
    """Replay the seeded days and show what would have been ordered each week.

    There is no test suite, so this output is the proof of the arithmetic: every
    ordered line shows its cover window, forecast total, on-hand, open-PO quantity,
    need, pack division, resulting on-hand, and any clamp or top-up.
    """
    from datetime import time as dtime

    from sqlalchemy import select

    from cafeops.db.models import Ingredient, ParLevel
    from cafeops.db.repositories.sourcing import SqlSourcingRepository
    from cafeops.db.repositories.stock import SqlStockRepository
    from cafeops.db.repositories.supplier import SqlSupplierRepository
    from cafeops.domain.forecast import WEEKDAY_NAMES, daily_series, dow_factors
    from cafeops.domain.ordering import pounds
    from cafeops.services.build_order import ForecastKnobs, build_split, create_draft_po

    if order_weekday < 1 or order_weekday > 7:
        raise typer.BadParameter(f"--order-weekday must be 1..7 (Mon..Sun), got {order_weekday}")
    cadence: int | None = None if cadence_days == 0 else cadence_days
    if cadence is not None and cadence < 1:
        raise typer.BadParameter("--cadence-days must be 0 (spec literal) or a positive integer")
    placed_at: dtime | None = None
    if order_time is not None:
        try:
            hours, _, minutes = order_time.partition(":")
            placed_at = dtime(int(hours), int(minutes or 0))
        except ValueError as exc:
            raise typer.BadParameter(f"--order-time must be HH:MM, got {order_time!r}") from exc

    knobs = ForecastKnobs.from_settings()

    with session_scope() as session:
        span = _sim_history_range(session)
        if span is None:
            console.print(
                "[yellow]No SALE movements in the ledger. Run `cafeops seed --demo` "
                "and `cafeops expand` first.[/yellow]"
            )
            return
        start, end = span
        terms_by_id = SqlSourcingRepository(session).terms_by_id()
        suppliers = SqlSupplierRepository(session).list_all()
        if supplier:
            wanted = supplier.strip().lower()
            suppliers = [s for s in suppliers if s.name.lower() == wanted]
            if not suppliers:
                raise typer.BadParameter(f"{supplier!r}: no such supplier")
        # The minimum override is applied inside sizing (`build_split`), not to the
        # supplier list: the top-up is part of sizing, and re-labelling the header while
        # the lines came from the stored minimum would show an order nobody built.

        order_dates = _sim_order_dates(start, end, order_weekday)
        if weeks is not None:
            order_dates = order_dates[-weeks:]

        console.rule("[bold]cafeops simulate[/bold]  spec 5.3 forecast + 5.4 order sizing")
        console.print(
            f"history      {start} .. {end}  ({(end - start).days + 1} local days)\n"
            f"order runs   {len(order_dates)} x {WEEKDAY_NAMES[order_weekday]}"
            f"  ({', '.join(str(d) for d in order_dates)})\n"
            f"forecast     alpha {knobs.ewma_alpha}, EWMA window {knobs.ewma_window_days}d, "
            f"dow window {knobs.dow_window_weeks}w, clamp "
            f"[{knobs.dow_factor_min}, {knobs.dow_factor_max}], "
            f"min history {knobs.min_history_days}d\n"
            f"cover gap    "
            + (
                f"{cadence}-day reordering cadence"
                if cadence is not None
                else "literal spec: the supplier's next available delivery slot"
            )
        )
        if min_order_pence is not None:
            console.print(
                f"[magenta]WHAT-IF: every supplier minimum overridden to "
                f"{pounds(min_order_pence)}[/magenta]"
            )
        console.print(f"[yellow]CAVEAT[/yellow] {_sim_terms_caveat(list(terms_by_id.values()))}")
        for caveat in SIM_CAVEATS:
            console.print(f"[yellow]CAVEAT[/yellow] {caveat}")
        if placed_at is not None:
            console.print(
                f"[magenta]orders placed at {placed_at} local: any supplier whose cutoff "
                "is earlier loses a delivery cycle, and its cover window grows to pay for "
                "it.[/magenta]"
            )
        earned = list(
            session.scalars(
                select(Ingredient.name)
                .join(ParLevel, ParLevel.ingredient_id == Ingredient.id)
                .where(ParLevel.auto_order_enabled.is_(True))
                .order_by(Ingredient.name)
            )
        )
        console.print(
            f"auto-order    {len(earned)} ingredient(s) have earned it"
            + (f": {', '.join(earned)}" if earned else " (the drift gate has not run yet)")
        )
        if require_auto_order:
            console.print(
                "[magenta]--require-auto-order: only those ingredients are considered "
                "(invariant 2). Everything else is left for a human.[/magenta]"
            )
        if commit:
            console.print(
                "[red]--commit: each week's suggestion is written as a DRAFT purchase "
                "order. Later weeks then see those drafts as open-PO quantity, which is "
                "correct but makes the run non-idempotent.[/red]"
            )

        drafts: list[tuple[date, str, int]] = []
        for order_date in order_dates:
            console.rule(f"[bold]{order_date} {WEEKDAY_NAMES[order_date.isoweekday()]}[/bold]")
            result = build_split(
                session,
                order_date=order_date,
                knobs=knobs,
                reorder_cadence_days=cadence,
                require_auto_order=require_auto_order,
                supplier_ids=[s.id for s in suppliers] if supplier else None,
                min_order_pence=min_order_pence,
                order_time=placed_at,
                # Through `str` so no float reaches a money calculation (invariant 11).
                free_delivery_top_up_multiple=Decimal(str(free_delivery_multiple)),
            )
            for plan in result.plans:
                suggestion = plan.suggestion
                spec = suggestion.supplier
                terms = terms_by_id.get(spec.id)
                window = suggestion.cover_window
                placeholder = (
                    " [yellow](terms are PLACEHOLDERS)[/yellow]"
                    if (terms is not None and terms.terms_are_placeholders)
                    else ""
                )
                fee_text = ""
                if terms is not None and terms.delivery_fee_pence > 0:
                    fee_text = f"  delivery {pounds(terms.delivery_fee_pence)}"
                    if terms.free_delivery_threshold_pence is not None:
                        fee_text += f" (free over {pounds(terms.free_delivery_threshold_pence)})"
                cutoff_text = (
                    f"  cutoff {terms.cutoff_time}"
                    if terms is not None and terms.cutoff_time is not None
                    else ""
                )
                console.print(
                    f"\n[bold cyan]{spec.name}[/bold cyan]{placeholder}"
                    f"  lead {spec.lead_time_days}d"
                    f"  delivers {_sim_delivery_text(spec)}"
                    f"  min order {pounds(spec.min_order_pence)}"
                    f"  channel {spec.order_channel.value}"
                    f"{cutoff_text}{fee_text}"
                )
                console.print(
                    f"  target delivery {suggestion.target_delivery_date}"
                    f"  |  widest cover {window.length}d "
                    f"{window.days[0]}..{window.days[-1]}"
                    f"  = lead {window.lead_time_days} + gap "
                    f"{window.days_until_next_delivery} + safety {window.safety_days}"
                )

                if cadence is None and window.days_until_next_delivery == 1:
                    console.print(
                        "  [yellow]note: the gap term came out at 1 day, so this window "
                        "assumes reordering tomorrow. For a weekly rhythm pass "
                        "--cadence-days 7 -- otherwise every order here is a seventh of "
                        "what a week needs.[/yellow]"
                    )

                ordered = plan.ordered
                if ordered:
                    status = "met" if suggestion.meets_minimum else "[red]NOT MET[/red]"
                    console.print(
                        f"  [green]{len(ordered)} line(s), {pounds(suggestion.total_pence)}"
                        f"[/green]  minimum {pounds(spec.min_order_pence)} {status}"
                        + (
                            "  [magenta]TOPPED UP[/magenta]"
                            if suggestion.min_order_topped_up
                            else ""
                        )
                    )
                    for outcome in ordered:
                        _sim_print_line(outcome)
                else:
                    console.print("  [dim]nothing to order[/dim]")

                # Not-ordered candidates, loudest first. A par ceiling that suppressed a
                # real need is a finding; forty "already covered" lines are noise, and
                # the below-par-floor list is already aggregated into the order notes.
                not_ordered = [o for o in plan.outcomes if o.line is None]
                for outcome in not_ordered:
                    if outcome.data_error or outcome.clamp_blocked or verbose:
                        console.print(f"  [dim]- {outcome.note}[/dim]")
                floors = sum(1 for o in not_ordered if o.below_par_floor)
                quiet = sum(
                    1
                    for o in not_ordered
                    if not (o.data_error or o.clamp_blocked or o.below_par_floor)
                )
                if floors and not verbose:
                    console.print(
                        f"  [dim]- {floors} candidate(s) under their par floor with nothing "
                        f"forecast to move them; named in the note below[/dim]"
                    )
                if quiet:
                    console.print(
                        f"  [dim]- {quiet} further candidate(s) already covered "
                        f"(--verbose to see them)[/dim]"
                    )
                for note in suggestion.notes:
                    console.print(f"  [yellow]note[/yellow] {note}")

                if commit and suggestion.lines:
                    po_id = create_draft_po(session, plan)
                    if po_id is not None:
                        drafts.append((order_date, spec.name, po_id))
                        console.print(f"  [green]wrote DRAFT purchase order {po_id}[/green]")

            _sim_print_sourcing(result, verbose=verbose)
            _sim_print_emergency(session, result, order_date=order_date, commit=commit)
            for note in result.split.notes:
                console.print(f"[yellow]run note[/yellow] {note}")

        # The weekday shape the forecaster actually found, for one busy ingredient.
        console.rule("[bold]day-of-week factors discovered[/bold]")
        stock_repo = SqlStockRepository(session)
        reference = session.scalar(
            select(Ingredient).where(Ingredient.name == "Whole milk")
        ) or session.scalar(select(Ingredient).where(Ingredient.tier == Tier.A))
        if reference is not None:
            history = stock_repo.daily_consumption(
                reference.id,
                since=end - timedelta(days=knobs.dow_window_weeks * 7 - 1),
                until=end,
            )
            series = daily_series(
                history, start=end - timedelta(days=knobs.dow_window_weeks * 7 - 1), end=end
            )
            factors, notes = dow_factors(
                series,
                factor_min=knobs.dow_factor_min,
                factor_max=knobs.dow_factor_max,
            )
            table = Table(title=f"{reference.name}, {len(series)} day(s) to {end}")
            table.add_column("Weekday")
            table.add_column("Days", justify="right")
            table.add_column("Mean", justify="right")
            table.add_column("Factor", justify="right")
            per_day: dict[int, list[Decimal]] = {}
            for day, qty in series:
                per_day.setdefault(day.isoweekday(), []).append(qty)
            for weekday, label in WEEKDAY_NAMES.items():
                values = per_day.get(weekday, [])
                mean = sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")
                table.add_row(
                    label,
                    str(len(values)),
                    format_qty(mean, reference.unit),
                    f"{factors[weekday]:.3f}",
                )
            console.print(table)
            for note in notes:
                console.print(f"  [yellow]{note}[/yellow]")
            console.print(
                "[dim]Zero-sales days count as zeros; days before the first observation "
                "do not. A declared closed day is excluded from both the history and the "
                "forecast -- see domain/forecast.py.[/dim]"
            )

        if drafts:
            console.print()
            for order_date, name, po_id in drafts:
                console.print(f"DRAFT {po_id}  {order_date}  {name}")
        console.print(
            "\n[dim]Every order above is a DRAFT. CONFIRMED and SENT need a recorded "
            "human and are refused by ck_po_confirmed_requires_human otherwise "
            "(invariant 1).[/dim]"
        )


def register(app: typer.Typer) -> None:
    app.command()(simulate)
