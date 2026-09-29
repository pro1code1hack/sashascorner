"""Seeding and imports: the legacy workbook, the finance workbook, the menu boards,
reference data, the demo scenario and its purge."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.table import Table

from cafeops.cli._common import console, unit_of_work
from cafeops.config import settings
from cafeops.db.base import session_scope

# --------------------------------------------------------------------------
# import-legacy
# --------------------------------------------------------------------------

FORCE_LEGACY_HELP = (
    "Import into a database that already has ingredients (refused otherwise). Existing "
    "ingredients are only filled where blank -- unit, tier, a tuned waste factor, a "
    "confirmed shelf life and invoice prices are kept -- and a changed price or recipe "
    "quantity closes the open row and opens a new one from today."
)


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
    force_legacy: Annotated[bool, typer.Option("--force-legacy", help=FORCE_LEGACY_HELP)] = False,
) -> None:
    """Import the legacy workbook and propose composition templates (spec 6)."""
    from cafeops.seed.legacy import LegacyImportRefused, import_legacy
    from cafeops.seed.report import render

    path = _workbook_path(workbook)
    try:
        # A dry run is rolled back at the end of the block: nothing is persisted. The
        # proposals exist to be read and argued with, not trusted (spec 6 pass 3).
        with unit_of_work(commit=not dry_run) as session:
            report = import_legacy(session, path, dry_run=dry_run, force=force_legacy)
            render(report, console)
    except LegacyImportRefused as exc:
        console.print(f"[red]Refused:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc
    if dry_run:
        console.print(
            "\n[yellow]DRY RUN: rolled back. Re-run with --commit to write "
            "ingredients, prices and staged recipes.[/yellow]"
        )
    else:
        console.print("\n[green]committed[/green]")


# --------------------------------------------------------------------------
# seed
# --------------------------------------------------------------------------


def import_finance_cmd(
    workbook: Annotated[
        Path | None,
        typer.Option(
            "--workbook", help="Finance workbook (.xlsx). Default: sashas_corner_finance.xlsx."
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--commit", help="Report what would change, or write it."),
    ] = True,
) -> None:
    """Import Daily Sales, Expenses and Director Account from the finance workbook.

    Idempotent: a second --commit changes nothing. Rows edited in the app are left alone.
    Just Eat money in the workbook's cash column becomes Just Eat takings (DECISIONS 4).
    """
    from cafeops.config import REPO_ROOT
    from cafeops.seed.finance_import import check_against_workbook, import_finance, report_lines

    path = (workbook or REPO_ROOT / "sashas_corner_finance.xlsx").expanduser()
    if not path.exists():
        raise typer.BadParameter(f"workbook not found: {path}")
    with unit_of_work(commit=not dry_run) as session:
        report = import_finance(session, path)
        for line in report_lines(report):
            console.print(line, markup=False, highlight=False)
        for line in check_against_workbook(session, path):
            console.print(line, markup=False, highlight=False)
    if dry_run:
        console.print("\n[yellow]DRY RUN: rolled back. Re-run with --commit to write.[/yellow]")
    else:
        console.print("\n[green]committed[/green]")
    # Outside the transaction: the rows that were not refused are committed first,
    # exactly as before, and only then does the exit code report the refusals.
    if report.refused:
        raise typer.Exit(code=2)


def menu_import_board_cmd(
    board: Annotated[
        Path | None,
        typer.Option(
            "--board",
            help="Menu boards file. Default: site/backend/config/menu_board.toml.",
        ),
    ] = None,
    prices: Annotated[
        str | None,
        typer.Option(
            "--prices",
            help="Owner's call on conflicting prices: 'board' (board wins) or 'keep'.",
        ),
    ] = None,
    commit: Annotated[
        bool, typer.Option("--commit/--dry-run", help="Write through the menu services.")
    ] = False,
) -> None:
    """Bring the ops menu up to the published TV menu boards (categories, products, prices).

    Dry run lists new categories, category assignments, new products, every price
    conflict (board vs current, with the current price's source), sizes ops lacks,
    and ops products not on the boards. Writes only with --commit, only through
    services.menu_catalog, and never decides a price conflict by itself.
    """
    from cafeops.config import REPO_ROOT
    from cafeops.seed.menu_board import apply, plan, report_lines

    path = (board or REPO_ROOT / "site" / "backend" / "config" / "menu_board.toml").expanduser()
    if not path.exists():
        raise typer.BadParameter(f"board file not found: {path}")
    price_call: Literal["board", "keep"] | None
    if prices is None:
        price_call = None
    elif prices == "board":
        price_call = "board"
    elif prices == "keep":
        price_call = "keep"
    else:
        raise typer.BadParameter("--prices must be 'board' or 'keep'")
    if not commit:
        with unit_of_work(commit=False) as session:
            lines = report_lines(plan(session, path))
        for line in lines:
            console.print(line, markup=False, highlight=False)
        console.print("\n[yellow]DRY RUN: nothing written. Re-run with --commit.[/yellow]")
        return
    try:
        with unit_of_work(commit=True) as session:
            done = apply(session, path, prices=price_call)
    except (RuntimeError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    for line in done:
        console.print(line, markup=False, highlight=False)
    console.print(f"\n[green]done: {len(done)} changes[/green]")


def seed_reference_cmd(
    commit: Annotated[
        bool, typer.Option("--commit/--dry-run", help="Write through the services.")
    ] = False,
    only: Annotated[
        str | None,
        typer.Option(
            "--only",
            help="Comma-separated sections: ingredients, prices, menu, recipes, photos, "
            "ingredient-photos; stock-photos runs only when named here.",
        ),
    ] = None,
    directory: Annotated[
        Path | None,
        typer.Option("--dir", help="Folder with the CSVs. Default: cafeops/seed/reference."),
    ] = None,
    show: Annotated[int, typer.Option("--show", help="Detailed changes printed per section.")] = 15,
) -> None:
    """Load researched reference data (cafeops/seed/reference/README.md).

    Validates every file first and lists every error. Then, per section, one
    transaction: insert / update / skip counts with the reasons for skips. Never
    overwrites what the owner entered (INVOICE or SUPPLIER_FEED prices, a confirmed
    shelf life, sell prices, existing recipes, photos). Re-running a commit is a no-op.
    """
    from sqlalchemy import select

    from cafeops.db.models import Ingredient
    from cafeops.seed.reference_csv import (
        DEFAULT_DIR,
        OPT_IN,
        SECTIONS,
        board_described,
        load,
        site_library_add,
    )
    from cafeops.services import reference_seed as rs

    root = (directory or DEFAULT_DIR).expanduser().resolve()
    if not root.is_dir():
        raise typer.BadParameter(f"not a folder: {root}")
    chosen = list(SECTIONS)
    if only:
        chosen = [s.strip() for s in only.split(",") if s.strip()]
        unknown = [s for s in chosen if s not in (*SECTIONS, *OPT_IN)]
        if unknown:
            raise typer.BadParameter(
                f"unknown section(s) {', '.join(unknown)}; use {', '.join((*SECTIONS, *OPT_IN))}"
            )

    with unit_of_work(commit=False) as session:  # reads only
        bundle = load(root, session)
        existing = set(session.scalars(select(Ingredient.name)))
        described = board_described(session)
    console.print(f"reference folder: {root}", markup=False, highlight=False, emoji=False)
    for w in bundle.warnings[:20]:
        console.print(f"[yellow]warning[/yellow] {w}", highlight=False)
    if len(bundle.warnings) > 20:
        console.print(f"[yellow]... and {len(bundle.warnings) - 20} more warnings[/yellow]")
    if bundle.errors:
        console.print(f"\n[red]{len(bundle.errors)} error(s); nothing was written:[/red]")
        for e in bundle.errors:
            console.print(f"  {e}", markup=False, highlight=False, emoji=False)
        raise typer.Exit(code=2)
    planned = bundle.planned_new(existing)

    def show_report(report: rs.SectionReport) -> None:
        state = "committed" if report.committed else "dry run"
        table = Table(title=f"{report.section} ({state})", title_justify="left")
        for col in ("insert", "update", "skip"):
            table.add_column(col, justify="right")
        table.add_row(
            str(report.count("insert")),
            str(report.count("update")),
            str(report.count("skip")),
        )
        console.print(table)
        for reason, n in report.skip_reasons():
            console.print(f"  skip {n:>4}  {reason}", markup=False, highlight=False, emoji=False)
        writes = [c for c in report.changes if c.action != "skip"]
        for c in writes[:show]:
            mark = "+" if c.action == "insert" else "~"
            console.print(
                f"  {mark} {c.key}: {c.detail}", markup=False, highlight=False, emoji=False
            )
        if len(writes) > show:
            console.print(f"  ... {len(writes) - show} more (--show N)")
        for note in report.notes:
            console.print(f"  {note}", markup=False, highlight=False, emoji=False)

    def run_library(jobs: list[rs.LibraryJob]) -> None:
        for job in jobs:
            try:
                out = site_library_add(job.file, job.alt)
            except (RuntimeError, OSError) as exc:
                console.print(f"  [red]library:[/red] {exc}", highlight=False)
                continue
            console.print(f"  library: {out}", markup=False, highlight=False, emoji=False)

    photo_names: frozenset[str] = frozenset()
    order = [*SECTIONS, *OPT_IN]
    for section in [s for s in order if s in chosen]:
        if section not in bundle.present:
            console.print(f"\n{section}: no file in the folder, skipped", highlight=False)
            continue
        console.print()
        library_jobs: list[rs.LibraryJob] = []
        # One transaction per section. The services flush; this block commits (or, on
        # a dry run, rolls back whatever the section staged).
        with unit_of_work(commit=commit) as session:
            if section == "ingredients":
                report = rs.seed_ingredients(session, bundle.ingredients, commit=commit)
            elif section == "prices":
                report = rs.seed_prices(session, bundle.products, commit=commit, planned=planned)
            elif section == "menu":
                report = rs.seed_menu(
                    session, bundle.menu, commit=commit, board_described=described
                )
            elif section == "recipes":
                report = rs.seed_recipes(session, bundle.recipes, commit=commit, planned=planned)
            elif section == "ingredient-photos":
                report = rs.seed_ingredient_photos(
                    session, bundle.ingredient_photos, commit=commit, planned=planned
                )
            else:
                fallback = section == "stock-photos"
                report, library_jobs, names = rs.seed_menu_photos(
                    session,
                    bundle.stock_photos if fallback else bundle.photos,
                    commit=commit,
                    section=section,
                    fallback=fallback,
                    skip_names=photo_names if fallback and not commit else frozenset(),
                )
                photo_names = names
        show_report(report)
        if commit:
            run_library(library_jobs)
    if not commit:
        console.print("\n[yellow]DRY RUN: nothing written. Re-run with --commit.[/yellow]")


def purge_demo_cmd(
    commit: Annotated[
        bool, typer.Option("--commit/--dry-run", help="Delete for real. Default: dry run.")
    ] = False,
    bot_preview: Annotated[
        bool,
        typer.Option(
            "--bot-preview",
            help="Also remove what `cafeops bot-preview` wrote (actor telegram:sasha, 23 Sep).",
        ),
    ] = False,
) -> None:
    """Remove what `seed --demo` fabricated: DEMO- sales, their ledger, counts, batches.

    Identifies rows only by the seeder's own markers (see services/purge_demo.py),
    refuses if anything real depends on them, and never touches real imports,
    menu, prices or configuration. Take a backup first:
    sqlite3 cafeops.db ".backup backups/cafeops-before-purge.db"
    """
    from cafeops.services.purge_demo import purge_demo

    # purge_demo flushes its deletes; this transaction owns the commit.
    with unit_of_work(commit=commit) as session:
        plan = purge_demo(session, commit=commit, include_bot_preview=bot_preview)
    for line in plan.lines():
        console.print(line, markup=False, highlight=False)
    if plan.refusals:
        raise typer.Exit(code=2)
    if plan.committed:
        console.print(
            "\n[green]deleted.[/green] Run `cafeops drift --backfill` and "
            "`cafeops cost-rollup` next."
        )
    else:
        console.print("\n[yellow]DRY RUN: nothing deleted. Re-run with --commit.[/yellow]")


#: `seed --demo` fabricates 60 days of sales, deliveries and counts. On the café's
#: real database that is indistinguishable from trade in every screen, so it runs
#: only when asked twice: the flag AND this environment variable.
DEMO_ENV = "CAFEOPS_ALLOW_DEMO_SEED"


def _refuse_demo_unless_dev(command: str) -> None:
    import os

    if os.environ.get(DEMO_ENV) != "1":
        console.print(
            f"[red]{command} writes fake sales, stock and counts.[/red] It is for a "
            f"development database only. Set {DEMO_ENV}=1 to run it on purpose."
        )
        raise typer.Exit(code=2)
    from sqlalchemy import func, select

    from cafeops.db.models import Sale

    with session_scope() as session:
        real = session.scalar(
            select(func.count(Sale.id)).where(~Sale.lightspeed_receipt_id.like("DEMO-%"))
        )
    if real:
        console.print(
            f"[red]Refused: this database holds {real} real sale line(s).[/red] "
            "Demo data would mix with them."
        )
        raise typer.Exit(code=2)


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


def seed(
    demo: Annotated[
        bool, typer.Option("--demo", help="Generate the latte template and 60 days of sales.")
    ] = False,
    workbook: Annotated[
        Path | None, typer.Option("--workbook", help="Legacy workbook to import first.")
    ] = None,
    days: Annotated[int, typer.Option(help="Days of synthetic sales for --demo.")] = 60,
    force_legacy: Annotated[bool, typer.Option("--force-legacy", help=FORCE_LEGACY_HELP)] = False,
) -> None:
    """Seed a working database. With --demo, a full scenario per spec 14."""
    from cafeops.seed.demo import seed_demo

    if demo:
        _refuse_demo_unless_dev("seed --demo")

    if workbook is not None or settings.finance_workbook_path is not None:
        from cafeops.seed.legacy import LegacyImportRefused, import_legacy

        path = _workbook_path(workbook)
        try:
            with session_scope() as session:
                console.print(f"[bold]Importing[/bold] {path.name}")
                report = import_legacy(session, path, dry_run=False, force=force_legacy)
                console.print(f"  {report.summary()}")
                for warning in report.warnings:
                    console.print(f"  [yellow]warning[/yellow] {warning}")
        except LegacyImportRefused as exc:
            console.print(f"[red]Refused:[/red] {exc}", highlight=False)
            raise typer.Exit(code=2) from exc

    # Prep times are seeded whether or not --demo runs: without them every labour
    # figure, true margin and margin-per-minute in the system is None, and spec 5.6's
    # finding cannot be computed at all. Every value is written as an ESTIMATE.
    from cafeops.seed.prep_times import seed_prep_times

    if not demo:
        with session_scope() as session:
            console.print("[bold]Estimating[/bold] prep times")
            prep = seed_prep_times(session)
            console.print(f"  {prep.summary()}")
        console.print("[green]done[/green] (pass --demo for the full scenario)")
        return

    with session_scope() as session:
        console.print("\n[bold]Building[/bold] demo composition and sales")
        demo_report = seed_demo(session, days=days)

    from cafeops.services.expand_recipes import expand_pending

    with session_scope() as session:
        console.print("[bold]Expanding[/bold] sales into stock movements")
        # allocate_batches=False: the seed expands 60 days of sales BEFORE the
        # deliveries that supplied them exist, then builds batches by replaying the
        # finished ledger (ARCHITECTURE.md 8F.2). Allocating here would report every
        # movement as a shortfall against batches that have not been created yet.
        expansion = expand_pending(session, allocate_batches=False)
        console.print(f"  {expansion.summary()}")
        for warning in expansion.warnings[:5]:
            console.print(f"  [yellow]warning[/yellow] {warning}")

    # Restocking reads the SALE movements, so it has to follow expansion.
    from cafeops.seed.demo import simulate_restocking, size_par_levels

    with session_scope() as session:
        console.print("[bold]Simulating[/bold] deliveries and physical counts")
        deliveries, counts = simulate_restocking(session)
        demo_report.deliveries, demo_report.counts = deliveries, counts

    # Par levels must be sized from OBSERVED throughput, which only exists once
    # sales have been expanded. A pack-multiple ceiling put 8 of 17 moving
    # ingredients below one cover window of demand, so the clamp -- not the
    # forecast -- was sizing the orders.
    # Batches are built by replaying the finished ledger chronologically: a batch
    # cannot be allocated before it is received, and deliveries interleave with 60
    # days of sales. See services/rebuild_batches for why a replay rather than
    # allocating during expansion.
    from cafeops.services.rebuild_batches import rebuild_batches

    with session_scope() as session:
        console.print("[bold]Rebuilding[/bold] batches from the ledger (FIFO + expiry)")
        rebuild = rebuild_batches(session)
        console.print(f"  {rebuild.summary()}")
        demo_report.batches = rebuild.batches_created

    with session_scope() as session:
        console.print("[bold]Sizing[/bold] par levels from observed consumption")
        resized, skipped = size_par_levels(session)
        console.print(
            f"  {resized} re-sized from throughput; {skipped} left at the seeded "
            "pack multiple (no measured consumption)"
        )

    # After the template exists, so its per-size defaults can be written to it.
    with session_scope() as session:
        console.print("[bold]Estimating[/bold] prep times (all ESTIMATE, spec 5.6)")
        prep = seed_prep_times(session)
        console.print(f"  {prep.summary()}")
        for warning in prep.warnings:
            console.print(f"  [yellow]warning[/yellow] {warning}")

    # Labour lands in menu_item_cost, so the cache has to be built after prep times.
    from cafeops.jobs.cost_rollup import rollup_all

    with session_scope() as session:
        console.print("[bold]Costing[/bold] the menu (cost + labour)")
        rollup = rollup_all(session)
        console.print(f"  {rollup.summary()}")

    console.print()
    for line in demo_report.lines():
        console.print(f"  {line}")
    console.print("\n[green]done[/green]")


def register(app: typer.Typer) -> None:
    app.command(name="import-legacy")(import_legacy_cmd)
    app.command(name="import-finance")(import_finance_cmd)
    app.command(name="menu-import-board")(menu_import_board_cmd)
    app.command(name="seed-reference")(seed_reference_cmd)
    app.command(name="purge-demo")(purge_demo_cmd)
    app.command()(seed)
