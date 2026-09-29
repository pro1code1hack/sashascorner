"""receive / expiry-sweep / open-batch -- the batch lifecycle (spec 4.1)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated

import typer
from rich.table import Table

from cafeops.cli._common import console, parse_as_of, resolve_ingredient
from cafeops.config import settings
from cafeops.db.base import session_scope
from cafeops.domain.units import format_qty


def _parse_expiry(raw: str | None) -> datetime | None:
    """A date read off a carton. End of that local day, because a best-before date
    means "good through this day", not "good until midnight at its start"."""
    if raw is None:
        return None
    try:
        parsed = date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise typer.BadParameter(f"{raw!r}: expected YYYY-MM-DD (the date on the carton)") from exc
    return datetime.combine(parsed, time.max, tzinfo=settings.tz).astimezone(UTC)


def receive(
    po_line: Annotated[
        int | None, typer.Option("--po-line", help="po_line.id being received.")
    ] = None,
    ingredient: Annotated[
        str | None,
        typer.Option(
            "--ingredient",
            "-i",
            help="Ad-hoc delivery with no order behind it (a Tesco run): ingredient name or id.",
        ),
    ] = None,
    packs: Annotated[
        int | None, typer.Option("--packs", help="Packs that arrived. --po-line only.")
    ] = None,
    qty: Annotated[
        str | None, typer.Option("--qty", help="Quantity in the ingredient's stocking unit.")
    ] = None,
    expires: Annotated[
        str | None,
        typer.Option(
            "--expires",
            help="YYYY-MM-DD from the carton. Omitted means assumed, and the receipt says so.",
        ),
    ] = None,
    at: Annotated[
        str, typer.Option("--at", help="'now', 'today', or YYYY-MM-DD. When it arrived.")
    ] = "now",
    by: Annotated[str, typer.Option("--by", help="Who took the delivery. Recorded.")] = "cli",
    price_pence: Annotated[
        str | None,
        typer.Option("--price-pence", help="Ad-hoc only: what it cost per stocking unit."),
    ] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Receive a delivery: create a batch with its expiry and a linked DELIVERY movement."""
    from cafeops.services.receive_delivery import ReceiveRefused, receive_adhoc, receive_po_line

    if (po_line is None) == (ingredient is None):
        raise typer.BadParameter(
            "give exactly one of --po-line (a confirmed order line) or --ingredient "
            "(an ad-hoc delivery with no order behind it)"
        )
    parsed_qty = None if qty is None else Decimal(qty)
    received_at = parse_as_of(at)
    expires_at = _parse_expiry(expires)

    with session_scope() as session:
        try:
            if po_line is not None:
                receipt = receive_po_line(
                    session,
                    po_line_id=po_line,
                    received_packs=packs,
                    received_qty=parsed_qty,
                    expires_at=expires_at,
                    received_at=received_at,
                    received_by=by,
                    note=note,
                )
            else:
                assert ingredient is not None
                found = resolve_ingredient(session, ingredient)
                if parsed_qty is None:
                    raise typer.BadParameter("--qty is required for an ad-hoc delivery")
                receipt = receive_adhoc(
                    session,
                    ingredient_id=found.id,
                    qty=parsed_qty,
                    expires_at=expires_at,
                    received_at=received_at,
                    received_by=by,
                    unit_cost_pence=None if price_pence is None else Decimal(price_pence),
                    note=note,
                )
        except ReceiveRefused as exc:
            console.print(f"[red]REFUSED[/red] {exc}")
            raise typer.Exit(code=1) from exc

        table = Table(
            title=f"{receipt.ingredient_name} -- delivery received",
            title_style="bold",
            show_header=False,
        )
        table.add_column("key")
        table.add_column("value")
        table.add_row("batch", str(receipt.batch_id))
        table.add_row("quantity", format_qty(receipt.qty, receipt.unit))
        table.add_row(
            "received", f"{receipt.received_at.astimezone(settings.tz):%Y-%m-%d %H:%M %Z}"
        )
        if receipt.expires_at is None:
            table.add_row("expires", "[dim]never (this ingredient does not spoil)[/dim]")
        else:
            style = "yellow" if receipt.expiry_was_assumed else "green"
            table.add_row(
                "expires",
                f"[{style}]{receipt.expires_at.astimezone(settings.tz):%Y-%m-%d}"
                f" ({receipt.shelf_life_days_left}d)"
                + (" -- ASSUMED, not read off the carton" if receipt.expiry_was_assumed else "")
                + f"[/{style}]",
            )
        table.add_row("unit cost", f"{receipt.unit_cost_pence:.4f}p per {receipt.unit.value}")
        table.add_row("batch value", f"GBP {receipt.value_pence / 100:.2f}")
        table.add_row("DELIVERY movements", str(receipt.movements_written))
        if receipt.po_line_id is not None:
            table.add_row("po_line", str(receipt.po_line_id))
        if receipt.order_completed:
            table.add_row("order", "[green]every line received -> RECEIVED[/green]")
        console.print(table)
        for warning in receipt.warnings:
            console.print(f"[yellow]warning[/yellow] {warning}")
        console.print(
            "[dim]The batch is sealed: opened_at is NULL, so the effective expiry is the "
            "unopened one. The first FIFO draw opens it and the open-life clock starts "
            "then (services/receive_delivery.py).[/dim]"
        )


def expiry_sweep_cmd(
    at: Annotated[
        str, typer.Option("--at", help="'now', 'today', or YYYY-MM-DD. Sweep as of this instant.")
    ] = "now",
    ingredient: Annotated[
        str | None, typer.Option("--ingredient", "-i", help="One ingredient only.")
    ] = None,
    short_dated_days: Annotated[
        int, typer.Option("--short-dated-days", help="Also report stock expiring within N days.")
    ] = 3,
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--commit", help="Show the write-offs without booking them.")
    ] = False,
) -> None:
    """Write off batches that reached their expiry with stock left, and say what it cost."""
    from cafeops.jobs.expiry_sweep import sweep_expiry

    swept_at = parse_as_of(at)
    with session_scope() as session:
        target = None if ingredient is None else resolve_ingredient(session, ingredient).id
        report = sweep_expiry(
            session,
            at=swept_at,
            ingredient_id=target,
            short_dated_days=short_dated_days,
            dry_run=dry_run,
        )

        console.print(
            f"[bold]expiry sweep[/bold] as of "
            f"{report.swept_at.astimezone(settings.tz):%Y-%m-%d %H:%M %Z}: {report.summary()}"
        )
        if report.write_offs:
            table = Table(title="EXPIRED -- written off", title_style="bold")
            table.add_column("Ingredient", no_wrap=True)
            table.add_column("Batch", justify="right")
            table.add_column("Qty", justify="right", no_wrap=True)
            table.add_column("Expired", no_wrap=True)
            table.add_column("Days ago", justify="right")
            table.add_column("Value lost", justify="right")
            table.add_column("Why")
            for write_off in report.write_offs:
                why = "open life" if write_off.expired_after_opening else "carton date"
                if write_off.expiry_was_assumed:
                    why += " [yellow](ASSUMED)[/yellow]"
                table.add_row(
                    write_off.ingredient_name,
                    str(write_off.batch_id),
                    format_qty(write_off.qty, write_off.unit),
                    f"{write_off.expired_at.astimezone(settings.tz):%Y-%m-%d}",
                    str(write_off.days_overdue),
                    (
                        "[dim]unpriced[/dim]"
                        if write_off.loss_pence is None
                        else f"[red]GBP {write_off.loss_pence / 100:.2f}[/red]"
                    ),
                    why,
                )
            console.print(table)
            console.print(
                f"[red]total written off: GBP {report.total_loss_pence / 100:.2f}[/red]"
                "  -- this is real money, and it is the number that says the cover window "
                "is too long, not that the recipe is wrong."
            )
        if report.short_dated:
            console.print(
                f"\n[yellow]{len(report.short_dated)} batch(es) expiring within "
                f"{short_dated_days} days -- still sellable:[/yellow]"
            )
            for soon in report.short_dated[:12]:
                console.print(f"  {soon.line()}")
        for warning in report.warnings:
            console.print(f"[yellow]warning[/yellow] {warning}")
        console.print(
            "[dim]Idempotent: stock_batch.expired_at and an existing EXPIRED movement both "
            "guard the write-off, so a second run books nothing twice.[/dim]"
        )


def open_batch_cmd(
    batch: Annotated[int, typer.Option("--batch", help="stock_batch.id that was opened.")],
    at: Annotated[str, typer.Option("--at", help="'now', 'today', or YYYY-MM-DD.")] = "now",
) -> None:
    """Record that a pack was opened, shortening its expiry to its open life.

    Normally unnecessary: the first FIFO draw stamps `opened_at` on its own. Use this
    for a carton opened for prep rather than a sale, or one opened out of FIFO order.
    """
    from cafeops.services.receive_delivery import open_batch

    with session_scope() as session:
        changed, message = open_batch(session, batch_id=batch, at=parse_as_of(at))
    console.print(f"[{'green' if changed else 'yellow'}]{message}[/]")


def register(app: typer.Typer) -> None:
    app.command()(receive)
    app.command(name="expiry-sweep")(expiry_sweep_cmd)
    app.command(name="open-batch")(open_batch_cmd)
