"""bot and jobs (agent E): drive the bot handlers locally, list or fire jobs, run the bot."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from cafeops.cli._common import console
from cafeops.db.base import session_scope

# The --run help is generated from the scheduler's JOBS table, so a job registered there
# (the loyalty ones were missing from a hand-written list) is always offered here too.
from cafeops.jobs.scheduler import JOBS as _SCHEDULED_JOBS

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


def bot_preview_cmd(
    flow: Annotated[
        str,
        typer.Argument(
            help=(
                "Which flow to drive: start, digest, orders, count, checklist, delivery, "
                "sale, cash, export, import, stranger, or all."
            )
        ),
    ] = "digest",
    supplier: Annotated[
        str | None,
        typer.Option("--supplier", help="orders: only the card for this supplier."),
    ] = None,
    answer: Annotated[
        str, typer.Option("--answer", help="count: the quantity to type for the first item.")
    ] = "12",
    packs: Annotated[
        str,
        typer.Option(
            "--packs",
            help=(
                "delivery: how many packs arrived. checklist: how many packs to request "
                "after 'running low' -- 0 presses 'do not order' instead."
            ),
        ),
    ] = "2",
    expiry: Annotated[
        str | None,
        typer.Option(
            "--expiry",
            help=(
                "delivery: the date on the pack, DD.MM. Pass --no-expiry to press "
                "'no date on the pack' instead and get an ASSUMED expiry."
            ),
        ),
    ] = "30.09",
    no_expiry: Annotated[
        bool, typer.Option("--no-expiry", help="delivery: there is no date on the pack.")
    ] = False,
    full_count: Annotated[
        bool, typer.Option("--full", help="count: the weekly full walk instead of tier A.")
    ] = False,
    ok: Annotated[
        bool, typer.Option("--ok", help="checklist: answer 'enough' instead of 'running low'.")
    ] = False,
    adjust: Annotated[
        bool, typer.Option("--adjust/--no-adjust", help="orders: press +1 on the first line.")
    ] = True,
    confirm: Annotated[
        bool, typer.Option("--confirm/--no-confirm", help="orders: press Confirm.")
    ] = True,
    buttons: Annotated[
        bool, typer.Option("--buttons/--no-buttons", help="Show the inline keyboards.")
    ] = True,
    commit: Annotated[
        bool,
        typer.Option(
            "--commit/--dry-run",
            help="Keep what the flows write. Default rolls it back.",
        ),
    ] = False,
) -> None:
    """Drive the REAL bot handlers locally and print the Russian they produce.

    No Telegram and no token: the dispatcher, routers, filters, FSM and keyboards are
    the real ones, and only the HTTP session is replaced by one that records outgoing
    calls. Nothing can reach a real cafe. See `cafeops/bot/preview.py`.

    **It is the database that needs the care, not the wire.** These are the real
    handlers, so `delivery` really receives a delivery, `orders` really confirms a
    purchase order and `count` really writes a count. "Nothing is sent" was only ever
    about Telegram, and reading it as "nothing happens" left a preview run's batches
    and movements sitting in a live ledger.

    So this now defaults to **--dry-run**, like every other writing command here
    (`import-legacy`, `channels import`, `materialise-template`): the flows run
    against a real session inside a transaction that is rolled back at the end, so
    what you read is exactly what would have been written. Pass `--commit` to keep it.
    """
    import asyncio

    from cafeops.bot.preview import (
        FLOWS,
        Preview,
        flow_checklist,
        flow_count,
        flow_delivery,
        flow_orders,
        render,
    )

    wanted = flow.strip().lower()
    names = list(FLOWS) if wanted == "all" else [wanted]
    unknown = [name for name in names if name not in FLOWS]
    if unknown:
        raise typer.BadParameter(f"unknown flow(s) {unknown}; choose from {sorted(FLOWS)} or 'all'")

    async def drive(name: str, factory: sessionmaker[Session] | None) -> str:
        preview = Preview(factory=factory)
        try:
            # The four flows that take options are called by name: `FLOWS` is a plain
            # dict, so going through it loses each flow's own keyword signature.
            if name == "orders":
                sent = await flow_orders(preview, supplier=supplier, adjust=adjust, confirm=confirm)
            elif name == "count":
                sent = await flow_count(preview, express=not full_count, answer=answer)
            elif name == "checklist":
                # `--packs 0` presses BTN_CHECKLIST_NO_ORDER instead: marking an item low
                # without naming a quantity is a real answer, and the flow has to be able
                # to show it.
                sent = await flow_checklist(
                    preview, low=not ok, packs=None if packs.strip() in ("", "0") else packs
                )
            elif name == "delivery":
                sent = await flow_delivery(
                    preview, packs=packs, expiry=None if no_expiry else expiry
                )
            else:
                sent = await FLOWS[name](preview)
            return render(sent, show_buttons=buttons)
        finally:
            await preview.close()

    from contextlib import ExitStack

    from sqlalchemy.orm import sessionmaker

    from cafeops.db.base import get_engine

    with ExitStack() as stack:
        factory: sessionmaker[Session] | None = None
        if not commit:
            # A real session on a real connection, inside a transaction nobody
            # commits: the handlers' own `session.commit()` calls flush without
            # committing the outer transaction, and the rollback at the end undoes
            # all of it.
            #
            # `rollback_only`, NOT `create_savepoint`. Measured on this stack:
            # create_savepoint LEAKS -- pysqlite's legacy implicit-transaction
            # handling turns the RELEASE of a savepoint into a real commit, so the
            # outer rollback has nothing left to undo and the "dry run" writes for
            # real. rollback_only needs no savepoints and was verified to roll back.
            connection = stack.enter_context(get_engine().connect())
            transaction = connection.begin()
            stack.callback(transaction.rollback)
            factory = sessionmaker(
                bind=connection, join_transaction_mode="rollback_only", expire_on_commit=False
            )

        mode = (
            "[yellow]--commit: what these flows write is KEPT[/yellow]"
            if commit
            else "dry run: nothing is sent, and what the flows write is rolled back"
        )
        for name in names:
            console.rule(f"[bold]cafeops bot-preview {name}[/bold]  ({mode})")
            text = asyncio.run(drive(name, factory))
            if not text.strip():
                console.print("[yellow](the bot answered nothing)[/yellow]")
            else:
                # `out`, not `print`: the bot's text is Telegram HTML, never Rich markup.
                console.out(text, highlight=False)
            console.print()


def jobs_cmd(
    run: Annotated[
        str | None,
        typer.Option(
            "--run",
            help="Fire one job by hand: " + ", ".join(_SCHEDULED_JOBS) + ".",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "pre_delivery_orders: ignore the idempotency guard and write a second "
                "draft for a delivery date that already has one."
            ),
        ),
    ] = False,
    order_date: Annotated[
        str | None,
        typer.Option("--order-date", help="pre_delivery_orders: replay a past day, YYYY-MM-DD."),
    ] = None,
) -> None:
    """List the schedule, or fire one job by hand.

    With no options this prints every trigger and the idempotency key that makes a late run
    safe. Nothing is scheduled by this command -- `cafeops.jobs.scheduler:main` is the
    long-running unit.
    """
    import asyncio
    from datetime import date as _date

    from cafeops.jobs.scheduler import JOBS, describe_schedule
    from cafeops.logging_setup import configure_logging

    # Jobs report through the logger, because in production they run headless under
    # systemd. Firing one by hand has to show that same output rather than nothing.
    configure_logging()

    if run is None:
        console.out(describe_schedule(), highlight=False)
        return
    name = run.strip()
    if name not in JOBS:
        raise typer.BadParameter(f"unknown job {name!r}; choose from {sorted(JOBS)}")

    if name == "pre_delivery_orders":
        from cafeops.jobs.pre_delivery_orders import run_pre_delivery_orders

        when = _date.fromisoformat(order_date) if order_date else None
        with session_scope() as session:
            report = run_pre_delivery_orders(session, order_date=when, force=force)
        console.out(report.summary(), highlight=False)
        for outcome in report.outcomes:
            if outcome.skipped and outcome.due:
                console.print(f"  [yellow]{outcome.supplier_name}: {outcome.skipped}[/yellow]")
        for warning in report.warnings:
            console.print(f"  [red]{warning}[/red]")
        return

    async def fire() -> None:
        # JOBS values are typed as returning an Awaitable, and `asyncio.run` wants a
        # coroutine, so the await happens inside one.
        await JOBS[name]()

    asyncio.run(fire())
    console.print(f"[green]{name} finished. See the log lines above.[/green]")


def bot_run_cmd() -> None:
    """Start the Telegram bot. Refuses without a token and an owner chat id."""
    from cafeops.bot.app import BotNotConfigured
    from cafeops.bot.app import main as bot_main

    try:
        bot_main()
    except BotNotConfigured as exc:
        # A clean sentence and a distinct exit code, not a traceback. 78 is sysexits
        # EX_CONFIG: "not configured" is a stable state, not a crash, and it must be
        # distinguishable from one. Exiting 1 here made both supervisors restart the bot
        # every few seconds forever, which looks exactly like a crash loop and buries any
        # real error under two log lines a second. systemd stops on 78 via
        # RestartPreventExitStatus; compose bounds it with `restart: on-failure:3`.
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=78) from None


def register(app: typer.Typer) -> None:
    app.command(name="bot-preview")(bot_preview_cmd)
    app.command(name="jobs")(jobs_cmd)
    app.command(name="bot-run")(bot_run_cmd)
