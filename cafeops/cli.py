"""Back-office CLI. The owner never sees this -- her only interface is Telegram."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cafeops.config import settings
from cafeops.db.base import SessionFactory, session_scope
from cafeops.domain.types import Tier
from cafeops.domain.units import format_qty

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Cafe Ops -- inventory and auto-ordering for Sasha's Corner.",
)
# Wide enough that ingredient names and quantities are never truncated.
console = Console(width=120)


def _parse_as_of(raw: str) -> datetime:
    """`today` | `now` | YYYY-MM-DD -> a UTC instant.

    A bare date means end-of-day local time, because "stock as of today" means
    "after today's trade", not "at midnight before it".
    """
    tz = settings.tz
    lowered = raw.strip().lower()
    if lowered == "now":
        return datetime.now(UTC)
    if lowered == "today":
        return datetime.now(tz).replace(hour=23, minute=59, second=59).astimezone(UTC)
    if lowered == "yesterday":
        local = datetime.now(tz) - timedelta(days=1)
        return local.replace(hour=23, minute=59, second=59).astimezone(UTC)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(
            f"{raw!r}: expected 'today', 'yesterday', 'now', or YYYY-MM-DD"
        ) from exc
    return datetime.combine(parsed, time.max, tzinfo=tz).astimezone(UTC)


# --------------------------------------------------------------------------
# import-legacy
# --------------------------------------------------------------------------


@app.command(name="import-legacy")
def import_legacy_cmd(
    workbook: Annotated[
        Path | None, typer.Option("--workbook", help="Legacy finance workbook (.xlsx).")
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--commit",
            help="Print the proposed templates without writing anything.",
        ),
    ] = True,
) -> None:
    """Import the legacy workbook and propose composition templates (spec 6)."""
    from cafeops.seed.legacy import import_legacy
    from cafeops.seed.report import render

    path = _workbook_path(workbook)
    session = SessionFactory()
    try:
        report = import_legacy(session, path, dry_run=dry_run)
        render(report, console)
        if dry_run:
            # Nothing is persisted. The proposals exist to be read and argued
            # with, not to be trusted (spec 6 pass 3).
            session.rollback()
            console.print(
                "\n[yellow]DRY RUN: rolled back. Re-run with --commit to write "
                "ingredients, prices and staged recipes.[/yellow]"
            )
        else:
            session.commit()
            console.print("\n[green]committed[/green]")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------
# seed
# --------------------------------------------------------------------------


def _workbook_path(workbook: Path | None) -> Path:
    path = workbook or settings.finance_workbook_path
    if path is None:
        raise typer.BadParameter(
            "no workbook given; pass --workbook or set CAFEOPS_FINANCE_WORKBOOK_PATH"
        )
    path = Path(path).expanduser()
    if not path.exists():
        raise typer.BadParameter(f"workbook not found: {path}")
    return path


@app.command()
def seed(
    demo: Annotated[
        bool, typer.Option("--demo", help="Generate the latte template and 60 days of sales.")
    ] = False,
    workbook: Annotated[
        Path | None, typer.Option("--workbook", help="Legacy workbook to import first.")
    ] = None,
    days: Annotated[int, typer.Option(help="Days of synthetic sales for --demo.")] = 60,
) -> None:
    """Seed a working database. With --demo, a full scenario per spec 14."""
    from cafeops.seed.demo import seed_demo

    if workbook is not None or settings.finance_workbook_path is not None:
        from cafeops.seed.legacy import import_legacy

        path = _workbook_path(workbook)
        with session_scope() as session:
            console.print(f"[bold]Importing[/bold] {path.name}")
            report = import_legacy(session, path, dry_run=False)
            console.print(f"  {report.summary()}")
            for warning in report.warnings:
                console.print(f"  [yellow]warning[/yellow] {warning}")

    if not demo:
        console.print("[green]done[/green] (pass --demo for the full scenario)")
        return

    with session_scope() as session:
        console.print("\n[bold]Building[/bold] demo composition and sales")
        demo_report = seed_demo(session, days=days)

    from cafeops.services.expand_recipes import expand_pending

    with session_scope() as session:
        console.print("[bold]Expanding[/bold] sales into stock movements")
        expansion = expand_pending(session)
        console.print(f"  {expansion.summary()}")
        for warning in expansion.warnings[:5]:
            console.print(f"  [yellow]warning[/yellow] {warning}")

    # Restocking reads the SALE movements, so it has to follow expansion.
    from cafeops.seed.demo import simulate_restocking

    with session_scope() as session:
        console.print("[bold]Simulating[/bold] deliveries and physical counts")
        deliveries, counts = simulate_restocking(session)
        demo_report.deliveries, demo_report.counts = deliveries, counts

    console.print()
    for line in demo_report.lines():
        console.print(f"  {line}")
    console.print("\n[green]done[/green]")


# --------------------------------------------------------------------------
# stock
# --------------------------------------------------------------------------


@app.command()
def stock(
    as_of: Annotated[
        str, typer.Option("--as-of", help="'today', 'yesterday', 'now', or YYYY-MM-DD.")
    ] = "today",
    tier: Annotated[str | None, typer.Option("--tier", help="Filter to a tier: A, B or C.")] = None,
    all_ingredients: Annotated[
        bool, typer.Option("--all", help="Include untracked (tier C) ingredients.")
    ] = False,
) -> None:
    """Print THEORETICAL on-hand per ingredient."""
    from cafeops.services.read_stock import read_on_hand

    at = _parse_as_of(as_of)
    tiers = None
    if tier:
        try:
            tiers = (Tier(tier.strip().upper()),)
        except ValueError as exc:
            raise typer.BadParameter(f"{tier!r}: expected A, B or C") from exc

    with session_scope() as session:
        readings = read_on_hand(session, as_of=at, tiers=tiers, include_untracked=all_ingredients)

    if not readings:
        console.print("[yellow]No tracked ingredients. Run `cafeops seed` first.[/yellow]")
        return

    local = at.astimezone(settings.tz)
    table = Table(
        title=(f"THEORETICAL on-hand as of {local:%Y-%m-%d %H:%M %Z}  (not a physical count)"),
        title_style="bold",
    )
    table.add_column("Tier", justify="center")
    table.add_column("Ingredient", no_wrap=True)
    table.add_column("Theoretical", justify="right", no_wrap=True)
    table.add_column("Last count", justify="right")
    table.add_column("Counted at")
    table.add_column("Mv", justify="right")
    table.add_column("Basis")

    unanchored = 0
    negative = 0
    for reading in readings:
        on_hand = reading.on_hand
        ingredient = reading.ingredient
        qty_text = format_qty(on_hand.qty, ingredient.unit)
        if on_hand.qty < 0:
            negative += 1
            qty_text = f"[red]{qty_text}[/red]"
        if reading.is_anchored:
            basis = "counted + ledger"
            count_text = format_qty(on_hand.basis_count_qty or Decimal("0"), ingredient.unit)
            counted_at = (
                on_hand.basis_counted_at.astimezone(settings.tz).strftime("%Y-%m-%d")
                if on_hand.basis_counted_at
                else "-"
            )
        else:
            unanchored += 1
            basis = "[yellow]ledger only - NO COUNT[/yellow]"
            count_text, counted_at = "-", "-"

        table.add_row(
            ingredient.tier.value,
            ingredient.name,
            qty_text,
            count_text,
            counted_at,
            str(on_hand.movement_count),
            basis,
        )

    console.print(table)
    console.print(
        "[dim]Every figure above is THEORETICAL: the last physical count plus the "
        "signed ledger since. Physical counts are the source of truth.[/dim]"
    )
    if unanchored:
        console.print(
            f"[yellow]{unanchored} ingredient(s) have no physical count -- those "
            "figures are a bare movement sum and should not be trusted for ordering."
            "[/yellow]"
        )
    if negative:
        console.print(
            f"[red]{negative} ingredient(s) are negative -- the ledger has consumed "
            "more than the last count recorded. Count them.[/red]"
        )


# --------------------------------------------------------------------------
# expand / recalc
# --------------------------------------------------------------------------


@app.command()
def expand(
    limit: Annotated[int | None, typer.Option(help="Max sale lines to expand.")] = None,
) -> None:
    """Expand un-expanded sales into SALE stock movements."""
    from cafeops.services.expand_recipes import expand_pending

    with session_scope() as session:
        report = expand_pending(session, limit=limit)
    console.print(report.summary())


@app.command()
def ingredients(
    tier: Annotated[str | None, typer.Option("--tier", help="Filter to A, B or C.")] = None,
) -> None:
    """List ingredients with tier, unit, waste factor and supplier."""
    from sqlalchemy import select

    from cafeops.db.models import Ingredient, Supplier, SupplierProduct

    with session_scope() as session:
        stmt = select(Ingredient).order_by(Ingredient.tier, Ingredient.name)
        if tier:
            stmt = stmt.where(Ingredient.tier == Tier(tier.strip().upper()))
        rows = list(session.scalars(stmt))
        suppliers: dict[int, str] = {}
        for ingredient in rows:
            name = session.scalar(
                select(Supplier.name)
                .join(SupplierProduct, SupplierProduct.supplier_id == Supplier.id)
                .where(SupplierProduct.ingredient_id == ingredient.id)
                .limit(1)
            )
            suppliers[ingredient.id] = name or "-"

        table = Table(title=f"{len(rows)} ingredients", title_style="bold")
        table.add_column("Tier", justify="center")
        table.add_column("Ingredient")
        table.add_column("Unit")
        table.add_column("Waste", justify="right")
        table.add_column("Tracked", justify="center")
        table.add_column("Supplier")
        for ingredient in rows:
            table.add_row(
                ingredient.tier.value,
                ingredient.name,
                ingredient.unit.value,
                f"{ingredient.waste_factor:.2f}",
                "yes" if ingredient.tracking_enabled else "-",
                suppliers[ingredient.id],
            )
    console.print(table)


@app.command()
def info() -> None:
    """Show configuration and database state. Says what is NOT wired yet."""
    from sqlalchemy import func, select

    from cafeops.db.models import (
        Ingredient,
        MenuItem,
        RecipeLine,
        Sale,
        StockMovement,
        Supplier,
    )

    with session_scope() as session:
        counts = {
            "ingredients": session.scalar(select(func.count(Ingredient.id))),
            "menu items": session.scalar(select(func.count(MenuItem.id))),
            "recipe lines": session.scalar(select(func.count(RecipeLine.id))),
            "suppliers": session.scalar(select(func.count(Supplier.id))),
            "sales": session.scalar(select(func.count(Sale.id))),
            "stock movements": session.scalar(select(func.count(StockMovement.id))),
        }

    table = Table(title="cafeops", title_style="bold", show_header=False)
    table.add_column("key")
    table.add_column("value")
    table.add_row("database", settings.database_url)
    table.add_row("timezone", settings.local_timezone)
    table.add_row(
        "Lightspeed",
        "configured" if settings.lightspeed_configured else "[yellow]fixtures only[/yellow]",
    )
    table.add_row(
        "Telegram",
        "configured" if settings.telegram_bot_token else "[yellow]not configured[/yellow]",
    )
    for key, value in counts.items():
        table.add_row(key, str(value))
    console.print(table)
    console.print(
        "[dim]Phase 1 not yet built: Lightspeed sync, drift/tier gating, forecasting, "
        "order building, Telegram bot, scheduled jobs.[/dim]"
    )


if __name__ == "__main__":
    app()
