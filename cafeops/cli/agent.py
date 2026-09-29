"""`cafeops agent ...` -- the allowlist, the narration, the log, and the refusals.

`prove-boundary` exists because with no test suite the only honest way to show the
boundary holds is to attack it and print what happened, with the `agent_action_log`
rows underneath. It is a shipped command rather than a throwaway script for the same
reason the seeded scenario is a CLI feature (ARCHITECTURE.md 9): it can be re-run
after any change, by anyone.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.table import Table

from cafeops.cli._common import console
from cafeops.config import settings
from cafeops.db.base import session_scope
from cafeops.db.repositories.agent_log import SqlAgentLogRepository, render_rows
from cafeops.domain.types import AgentToolOutcome

app = typer.Typer(
    no_args_is_help=True,
    help="The bounded agent (spec 9): browser automation, narration, proposals. "
    "Never writes stock, orders or composition.",
)


@app.command()
def tools() -> None:
    """The allowlist, in full. Anything absent from this list is refused."""
    from cafeops.agent.policies import FORBIDDEN_TABLES, ToolKind
    from cafeops.agent.runner import registered_tools

    table = Table(title="Agent tool allowlist (spec 9)", title_style="bold", width=118)
    table.add_column("Tool", no_wrap=True)
    table.add_column("Kind", justify="center")
    table.add_column("Stops at a human", justify="center")
    table.add_column("What it does")
    for spec in registered_tools():
        style = {
            ToolKind.READ: "cyan",
            ToolKind.PROPOSE: "yellow",
            ToolKind.BROWSER: "magenta",
        }[spec.kind]
        table.add_row(
            spec.name,
            f"[{style}]{spec.kind.value}[/{style}]",
            "[red]yes[/red]" if spec.stops_at_human else "",
            spec.description.split(". ")[0] + ".",
        )
    console.print(table)
    console.print(
        f"[dim]READ tools are handed a read-only repository bundle and no Session. "
        f"PROPOSE tools return an AgentProposal a human confirms. BROWSER tools produce "
        f"data or a staged basket, never a submission. {len(FORBIDDEN_TABLES)} tables are "
        "named unwritable, and the agent's database connection refuses any statement that "
        "is not a read -- raw SQL included.[/dim]"
    )
    key_state = "set" if settings.anthropic_api_key else "NOT set (template narration)"
    console.print(f"[dim]model: {settings.agent_model} | ANTHROPIC_API_KEY: {key_state}[/dim]")


@app.command(name="prove-boundary")
def prove_boundary() -> None:
    """Attack the boundary and print what happened, with the log rows as evidence.

    Five attacks, each a thing a model could plausibly try:
      1. call a tool that would write the stock ledger
      2. call a tool that would create a purchase order
      3. call a tool that would edit a recipe
      4. call the existing order-dispatch path by name
      5. run a raw INSERT/UPDATE/DELETE through the agent's own connection
    """
    from sqlalchemy import text

    from cafeops.agent.policies import WriteAttemptBlocked
    from cafeops.agent.runner import AgentRun

    forbidden_tools: list[tuple[str, dict[str, Any]]] = [
        ("write_stock_movement", {"ingredient": "Whole milk", "qty": "-10"}),
        ("create_purchase_order", {"supplier": "Tesco", "total_pence": 4210}),
        ("edit_recipe", {"item": "Caramel Latte", "ingredient": "Whole milk", "qty": "0.30"}),
        ("send_order", {"po_id": 1}),
        ("set_auto_order", {"ingredient": "16oz paper cup", "enabled": True}),
    ]
    raw_statements = [
        "INSERT INTO stock_movement (ingredient_id, type, qty, occurred_at) "
        "VALUES (1, 'ADJUSTMENT', 1, '2026-09-19T00:00:00Z')",
        "UPDATE ingredient SET waste_factor = 0 WHERE id = 1",
        "DELETE FROM drift_observation WHERE drift_pct > 15",
        "UPDATE purchase_order SET status = 'SENT' WHERE id = 1",
    ]

    with AgentRun(purpose="prove the write boundary holds") as run:
        console.print(f"[bold]run_id[/bold] {run.run_id}\n")

        console.print("[bold]1-5. Tools that are not on the allowlist[/bold]")
        for name, args in forbidden_tools:
            record = run.call(name, args)
            marker = "[green]REFUSED[/green]" if record.refused else "[red]NOT REFUSED[/red]"
            console.print(f"  {marker} {name}({args})")
            console.print(f"          {record.refusal_reason}")

        console.print("\n[bold]6. Raw SQL through the agent's own connection[/bold]")
        for statement in raw_statements:
            try:
                run._read_session.execute(text(statement))
            except WriteAttemptBlocked as exc:
                run.log.log(
                    run_id=run.run_id,
                    tool_name="raw_sql",
                    inputs={"statement": statement},
                    outcome=AgentToolOutcome.REFUSED,
                    refusal_reason=str(exc),
                    purpose=run.purpose,
                    model=run.model,
                )
                run._audit_session.commit()
                console.print(f"  [green]BLOCKED[/green] {statement[:72]}...")
            else:
                console.print(f"  [red]NOT BLOCKED[/red] {statement}")
            finally:
                run._read_session.rollback()

        console.print("\n[bold]7. A read still works, so the guard is not just 'off'[/bold]")
        rows = run.call("read_expiry_writeoffs", {})
        first = (rows.output or "").splitlines()[0] if rows.output else "(nothing)"
        console.print(f"  [green]{rows.outcome.value}[/green] read_expiry_writeoffs -> {first}")

        run_id = run.run_id

    console.print(f"\n[bold]agent_action_log rows for run {run_id}[/bold]")
    with session_scope() as session:
        repo = SqlAgentLogRepository(session)
        logged = repo.rows(run_id=run_id, limit=100)
        console.print(render_rows(list(reversed(logged))))
        counts: dict[str, int] = {}
        for row in logged:
            counts[row.outcome.value] = counts.get(row.outcome.value, 0) + 1
        console.print(
            "\n"
            + ", ".join(f"{name}={n}" for name, n in sorted(counts.items()))
            + f"  ({len(logged)} rows, every action logged)"
        )


@app.command()
def narrate(
    subject: Annotated[str, typer.Argument(help="drift | channels")] = "drift",
    model: Annotated[str | None, typer.Option("--model", help="Override the model id.")] = None,
    facts: Annotated[
        bool, typer.Option("--facts/--no-facts", help="Also print the computed facts.")
    ] = False,
) -> None:
    """Explain computed numbers in a sentence somebody acts on (spec 9 job 2)."""
    from cafeops.agent.runner import narrate_channels, narrate_drift

    chosen = subject.strip().lower()
    if chosen == "drift":
        result = narrate_drift(model=model)
    elif chosen in {"channel", "channels"}:
        result = narrate_channels(model=model)
    else:
        raise typer.BadParameter(f"{subject!r}: expected 'drift' or 'channels'")

    console.print(f"[bold]run[/bold] {result.run_id}  [bold]model[/bold] {result.model}")
    if facts:
        for call in result.calls:
            console.print(f"\n[dim]--- {call.tool_name} ({call.outcome.value})[/dim]")
            console.print(call.output or call.refusal_reason or "")
    console.print()
    console.print(result.text or "[yellow](no narration produced)[/yellow]")
    if result.note:
        console.print(f"\n[yellow]note[/yellow] {result.note}")
    if result.unsupported_figures:
        console.print(
            "\n[red]DO NOT TRUST THIS NARRATION.[/red] It contains figure(s) no tool "
            "computed: " + ", ".join(result.unsupported_figures) + ". Logged as FAILED."
        )
    else:
        console.print(
            "\n[dim]Every figure above was traced back to a tool result. The agent read "
            "computed numbers; it did not compute them (spec 9).[/dim]"
        )


@app.command(name="stage-basket")
def stage_basket(
    po_id: Annotated[int, typer.Argument(help="A DRAFT purchase order id.")],
) -> None:
    """Fill a supplier basket for a draft order and stop. Spends money only via a human."""
    from cafeops.agent.runner import AgentRun

    with AgentRun(purpose=f"stage a supplier basket for PO {po_id}") as run:
        record = run.call("browser_stage_supplier_basket", {"po_id": po_id})
        console.print(f"[bold]run[/bold] {run.run_id}  outcome [bold]{record.outcome.value}[/bold]")
        console.print(record.output or record.refusal_reason or "")
    console.print(
        "\n[dim]The adapter is the one in integrations/suppliers/; its dispatch() is not "
        "reachable from here. Invariant 1: a person presses the last button.[/dim]"
    )


@app.command(name="log")
def log_cmd(
    run: Annotated[str | None, typer.Option("--run", help="One run_id.")] = None,
    refusals: Annotated[
        bool, typer.Option("--refusals", help="Only the REFUSED rows -- the interesting ones.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit")] = 30,
) -> None:
    """Read `agent_action_log`."""
    with session_scope() as session:
        repo = SqlAgentLogRepository(session)
        rows = repo.rows(
            run_id=run,
            outcome=AgentToolOutcome.REFUSED if refusals else None,
            limit=limit,
        )
        if not rows:
            console.print("[yellow]no rows[/yellow]")
            return
        table = Table(title="agent_action_log", title_style="bold", width=118)
        table.add_column("id", justify="right")
        table.add_column("when")
        table.add_column("run", no_wrap=True)
        table.add_column("tool", no_wrap=True)
        table.add_column("outcome")
        table.add_column("model", no_wrap=True)
        for row in rows:
            style = {
                "OK": "green",
                "REFUSED": "red",
                "FAILED": "red",
                "AWAITING_HUMAN": "yellow",
            }.get(row.outcome.value, "")
            table.add_row(
                str(row.id),
                f"{row.occurred_at.astimezone(settings.tz):%Y-%m-%d %H:%M:%S}",
                row.run_id,
                row.tool_name,
                f"[{style}]{row.outcome.value}[/{style}]",
                row.model or "-",
            )
        console.print(table)
        counts = repo.outcome_counts()
        console.print(
            "  ".join(
                f"{k.value}={v}" for k, v in sorted(counts.items(), key=lambda kv: kv[0].value)
            )
        )
        if refusals:
            console.print()
            console.print(render_rows(rows))
