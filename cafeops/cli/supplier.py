"""`cafeops supplier ...` and `cafeops shelf-life ...` -- record what a human confirmed.

These close the loop that `cafeops doctor` opens. The doctor has always been able to
say "six suppliers carry invented terms" and "113 shelf lives are ESTIMATE defaults";
until now its remedy was to edit a seed file in the repository, which is not something
the person who telephones the supplier can do. These commands are that person's path,
and running them is what makes the doctor go quiet.

Both write through `services/confirm_terms.py`, which is where the refusals live --
nothing here validates anything itself (spec 8: the bot, the API and the CLI are all
clients of the services layer).
"""

from __future__ import annotations

from datetime import time
from typing import Annotated

import typer
from rich.table import Table

from cafeops.cli._common import console
from cafeops.db.base import session_scope
from cafeops.domain.enums import PriceSource
from cafeops.services.confirm_terms import (
    ConfirmationRefused,
    SupplierTerms,
    confirm_shelf_life,
    confirm_supplier_terms,
    unconfirmed_shelf_lives,
)

supplier_app = typer.Typer(
    no_args_is_help=True,
    help="Supplier terms: see which are still invented, and record confirmed ones.",
)
shelf_life_app = typer.Typer(
    no_args_is_help=True,
    help="Shelf lives: see which are still guesses, and record confirmed ones.",
)

WEEKDAYS = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def _days(weekdays: list[int]) -> str:
    return "/".join(WEEKDAYS[d] for d in sorted(weekdays)) if weekdays else "(none)"


def _money(pence: int | None) -> str:
    return "--" if pence is None else f"GBP {pence / 100:.2f}"


@supplier_app.command("list")
def supplier_list() -> None:
    """Every supplier, and whether anybody has confirmed its terms."""
    from sqlalchemy import select

    from cafeops.db.models.supplier import Supplier

    with session_scope() as session:
        rows = list(session.scalars(select(Supplier).order_by(Supplier.name)))
        table = Table(title="supplier terms", title_justify="left")
        table.add_column("")
        table.add_column("supplier")
        table.add_column("lead", justify="right")
        table.add_column("delivers")
        table.add_column("cutoff")
        table.add_column("minimum", justify="right")
        table.add_column("fee", justify="right")
        table.add_column("free over", justify="right")
        invented = 0
        for s in rows:
            placeholder = s.terms_are_placeholders
            invented += 1 if placeholder else 0
            mark = "[yellow]INVENTED[/yellow]" if placeholder else "[green]confirmed[/green]"
            table.add_row(
                mark,
                s.name,
                f"{s.lead_time_days}d",
                _days(list(s.delivery_weekdays)),
                s.cutoff_time.strftime("%H:%M") if s.cutoff_time else "--",
                _money(s.min_order_pence),
                _money(s.delivery_fee_pence),
                _money(s.free_delivery_threshold_pence),
            )
        console.print(table)
        if invented:
            console.print(
                f"[yellow]{invented} supplier(s) have terms nobody has confirmed.[/yellow] "
                "Every cover window and every quantity on their orders rests on those "
                "guesses. Confirm one with:\n"
                "  cafeops supplier confirm 'Booker' --lead-days 2 --days 2,5 "
                "--min-order 5000 --fee 0"
            )
        else:
            console.print("[green]Every supplier's terms have been confirmed.[/green]")


@supplier_app.command("confirm")
def supplier_confirm(
    name: Annotated[str, typer.Argument(help="Supplier name, exactly as listed.")],
    lead_days: Annotated[int, typer.Option("--lead-days", help="Days from order to delivery.")],
    days: Annotated[
        str, typer.Option("--days", help="Delivery weekdays, ISO 1-7 comma separated, e.g. 2,5.")
    ],
    min_order: Annotated[
        int, typer.Option("--min-order", help="Minimum order in PENCE (0 for none).")
    ],
    fee: Annotated[int, typer.Option("--fee", help="Delivery fee in PENCE (0 for none).")],
    cutoff: Annotated[
        str | None, typer.Option("--cutoff", help="Order cutoff, HH:MM. Omit if none.")
    ] = None,
    free_over: Annotated[
        int | None,
        typer.Option("--free-over", help="Free delivery above this many PENCE. Omit if none."),
    ] = None,
) -> None:
    """Record terms confirmed WITH THE SUPPLIER, and stop flagging them as invented.

    All of them at once, on purpose: the cover window is computed from several of
    these together, so clearing the flag while one is still a guess would silence
    the warning on an order that is still partly fiction.
    """
    try:
        weekdays = tuple(int(d) for d in days.split(",") if d.strip())
    except ValueError:
        console.print(f"[red]--days must be numbers 1-7, comma separated. Got {days!r}.[/red]")
        raise typer.Exit(code=2) from None

    parsed_cutoff: time | None = None
    if cutoff:
        try:
            hh, mm = cutoff.split(":")
            parsed_cutoff = time(int(hh), int(mm))
        except ValueError:
            console.print(f"[red]--cutoff must be HH:MM. Got {cutoff!r}.[/red]")
            raise typer.Exit(code=2) from None

    with session_scope() as session:
        try:
            result = confirm_supplier_terms(
                session,
                name=name,
                terms=SupplierTerms(
                    lead_time_days=lead_days,
                    delivery_weekdays=weekdays,
                    min_order_pence=min_order,
                    delivery_fee_pence=fee,
                    cutoff_time=parsed_cutoff,
                    free_delivery_threshold_pence=free_over,
                ),
            )
        except ConfirmationRefused as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from None

        before, after = result.before, result.after
        console.print(f"[green]{result.name}: terms confirmed.[/green]")
        changes = [
            ("lead time", f"{before.lead_time_days}d", f"{after.lead_time_days}d"),
            (
                "delivers",
                _days(list(before.delivery_weekdays)),
                _days(list(after.delivery_weekdays)),
            ),
            ("minimum", _money(before.min_order_pence), _money(after.min_order_pence)),
            ("fee", _money(before.delivery_fee_pence), _money(after.delivery_fee_pence)),
            (
                "cutoff",
                before.cutoff_time.strftime("%H:%M") if before.cutoff_time else "--",
                after.cutoff_time.strftime("%H:%M") if after.cutoff_time else "--",
            ),
            (
                "free over",
                _money(before.free_delivery_threshold_pence),
                _money(after.free_delivery_threshold_pence),
            ),
        ]
        for label, was, now in changes:
            if was != now:
                console.print(f"  {label}: {was} -> [bold]{now}[/bold]")
        if result.was_placeholder:
            console.print("  orders from this supplier no longer carry the invented-terms warning.")
        console.print(
            "[dim]Quantities change from the next ordering run: the cover window is "
            "built from these.[/dim]"
        )


@shelf_life_app.command("list")
def shelf_life_list(
    limit: Annotated[int, typer.Option("--limit", help="How many to show.")] = 20,
) -> None:
    """Shelf lives still on a seeded guess, busiest first.

    Ordered by how much stock actually moves, because confirming the shelf life of
    something nobody buys changes nothing. Shelf life CAPS order size, so a wrong
    one either wastes stock or causes a stockout.
    """
    with session_scope() as session:
        rows = unconfirmed_shelf_lives(session, limit=limit)
        if not rows:
            console.print("[green]Every shelf life has been confirmed.[/green]")
            return
        table = Table(title="shelf lives nobody has confirmed", title_justify="left")
        table.add_column("ingredient")
        table.add_column("storage")
        table.add_column("guessed", justify="right")
        table.add_column("transit", justify="right")
        table.add_column("usable", justify="right")
        table.add_column("movements", justify="right")
        for r in rows:
            table.add_row(
                r.name,
                r.storage.lower(),
                f"{r.shelf_life_days}d" if r.shelf_life_days is not None else "--",
                f"{r.transit_buffer_days}d",
                f"{r.usable_days}d" if r.usable_days is not None else "--",
                str(r.movement_count),
            )
        console.print(table)
        console.print(
            "[yellow]'usable' is what actually caps an order[/yellow] "
            "(shelf life less the transit buffer, invariant 4). Confirm one with:\n"
            "  cafeops shelf-life set 'Whole milk' --days 7 --open-days 3 --source supplier"
        )


@shelf_life_app.command("set")
def shelf_life_set(
    name: Annotated[str, typer.Argument(help="Ingredient name, exactly as listed.")],
    days: Annotated[int, typer.Option("--days", help="Unopened shelf life in days.")],
    open_days: Annotated[
        int | None, typer.Option("--open-days", help="Days once opened. Omit to leave as is.")
    ] = None,
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Where it came from: 'supplier' (they told you) or 'packaging' (you read it).",
        ),
    ] = "supplier",
) -> None:
    """Record a shelf life somebody checked, and stop calling it an estimate."""
    mapping = {
        "supplier": PriceSource.SUPPLIER_FEED,
        "packaging": PriceSource.INVOICE,
        "invoice": PriceSource.INVOICE,
    }
    chosen = mapping.get(source.lower())
    if chosen is None:
        console.print(f"[red]--source must be 'supplier' or 'packaging'. Got {source!r}.[/red]")
        raise typer.Exit(code=2)

    with session_scope() as session:
        try:
            r = confirm_shelf_life(
                session,
                name=name,
                shelf_life_days=days,
                open_life_days=open_days,
                source=chosen,
            )
        except ConfirmationRefused as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from None

        was = f"{r.shelf_life_days_before}d" if r.shelf_life_days_before is not None else "unset"
        console.print(
            f"[green]{r.name}: shelf life {was} -> {r.shelf_life_days_after}d"
            f"[/green] (source {r.source_before.value if r.source_before else 'none'}"
            f" -> {r.source_after.value})"
        )
        if r.open_life_days_after is not None:
            console.print(f"  once opened: {r.open_life_days_after}d")
        console.print(
            f"  usable on arrival: [bold]{r.usable_days_after}d[/bold] "
            f"({r.shelf_life_days_after} less a {r.transit_buffer_days}-day transit buffer)"
        )
        moved = r.usable_days_changed_by
        if moved:
            direction = "more" if moved > 0 else "less"
            console.print(
                f"  [yellow]the order cap moves {abs(moved)} day(s) {direction}[/yellow] -- "
                "a single order may now cover a different span of trade (invariant 4)."
            )
        else:
            console.print("  the order cap is unchanged; only the source is now confirmed.")


__all__ = ["shelf_life_app", "supplier_app"]
