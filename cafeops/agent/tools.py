"""The allowlist, in full. Three jobs, and nothing else is registered.

Spec 9 gives the agent exactly three jobs and this module is the whole surface:

* **READ** -- drift with its expiry attribution, expiry write-offs, channel
  performance. Every number in these results is computed by deterministic Python
  before the model ever sees it, and `ToolResult.figures` records what was computed
  so the runner can check the model's prose against it. "Reads computed numbers,
  never computes them" is therefore *checked*, not requested.
* **BROWSER** -- a read-only report-download plan, and a supplier basket staged
  through the existing `OrderChannelAdapter`. There is deliberately no second route
  to a checkout: the basket tool calls `prepare()` and never `dispatch()`, and it is
  declared `stops_at_human` so the log records AWAITING_HUMAN.
* **PROPOSE** -- a `waste_factor` adjustment, a template grouping, a channel import.
  Each returns an `AgentProposal`. None of them applies anything.

Two things are absent on purpose and their absence is the feature: there is no tool
that writes a movement, an order or a recipe, and no tool that computes a stock
figure, a par level, a cover window or an order quantity. Deterministic stays
deterministic (spec 9's first line).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from cafeops.agent.policies import (
    ToolContext,
    ToolKind,
    ToolResult,
    ToolSpec,
    register,
)
from cafeops.config import settings
from cafeops.db.models import Ingredient, StockBatch, StockMovement
from cafeops.domain.stock import drift_attribution
from cafeops.domain.types import AgentProposal, MovementType, SalesChannelName
from cafeops.integrations.channels.analytics import (
    channel_performance,
    format_pct,
    format_x,
    rank_well_convert_badly,
)

DEFAULT_LOOKBACK_DAYS = 60


def _money(pence: Decimal | int | None) -> str:
    if pence is None:
        return "unknown"
    return f"GBP {Decimal(pence) / 100:.2f}"


def _window(args: dict[str, Any], *, default_days: int) -> tuple[date, date]:
    until = (
        date.fromisoformat(args["until"])
        if args.get("until")
        else datetime.now(UTC).date()  # a reporting window, not an instant
    )
    since = (
        date.fromisoformat(args["since"])
        if args.get("since")
        else until - timedelta(days=default_days - 1)
    )
    return since, until


# ==========================================================================
# READ -- drift, and whether the fix is the recipe or the ordering
# ==========================================================================


def _drift_facts(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Drift per ingredient, split into measurement error and expiry write-off.

    All arithmetic here: `drift_attribution` is `domain/stock.py`, the sums are
    repository reads. The model receives the result and writes a sentence.
    """
    limit = int(args.get("limit", 6))
    name = args.get("ingredient")

    if name:
        subject = ctx.reads.ingredients.get_by_name(str(name))
        subjects = [subject] if subject is not None else []
        if not subjects:
            return ToolResult(text=f"No ingredient named {name!r} is tracked.")
    else:
        subjects = ctx.reads.ingredients.list_tracked()

    figures: list[str] = []
    # Ranked so an over-ordering case cannot be pushed off the list by a bigger
    # percentage that is merely a measurement gap. Found by running it: whole milk's
    # LATEST drift is -3.70%, so ranking on |drift| alone hid the one ingredient with
    # repeated expiry write-offs behind six cups and lids with no waste at all -- and
    # the over-ordering case is the actionable one.
    rows: list[tuple[int, float, str]] = []
    window_start = datetime.now(UTC) - timedelta(days=DEFAULT_LOOKBACK_DAYS)

    for subject in subjects:
        history = ctx.reads.drift.history(subject.id, limit=2)
        if not history:
            continue
        latest = history[0]
        previous_at = history[1].observed_at if len(history) > 1 else None
        # Spec 5.2's attribution is count-to-count: that is the window the drift figure
        # itself covers, so it is the only window over which the split means anything.
        expired = ctx.reads.stock.expired_qty_between(
            subject.id, after=previous_at, until=latest.observed_at
        )
        # And separately, the whole lookback window, because a pattern of write-offs is
        # the over-ordering evidence even when the newest count happens to be clean.
        window_expired = ctx.reads.stock.expired_qty_between(
            subject.id, after=window_start, until=datetime.now(UTC)
        )
        gap = latest.theoretical_qty - latest.counted_qty
        measurement, expiry = drift_attribution(total_gap=gap, expired_qty=expired)
        share = 0 if gap == 0 else int(abs(expiry) * 100 / abs(gap))
        unit = subject.unit.value
        if share >= 50:
            verdict = "expiry write-offs explain most of this gap, so the fix is to order less"
        elif window_expired > 0:
            verdict = (
                f"this count's gap is measurement, but {window_expired:.3f} {unit} has "
                f"expired over the last {DEFAULT_LOOKBACK_DAYS} days, which is an "
                "ordering problem on its own"
            )
        else:
            verdict = (
                "no write-offs, so the gap is measurement: the fix is the recipe or the "
                "waste factor"
            )
        rows.append(
            (
                1 if window_expired > 0 else 0,
                abs(latest.drift_pct),
                f"{subject.name} (tier {subject.tier.value}): drift {latest.drift_pct:+.2f}%; "
                f"theoretical {latest.theoretical_qty:.3f} {unit} against counted "
                f"{latest.counted_qty:.3f} {unit}; gap {abs(gap):.3f} {unit} = "
                f"{measurement:.3f} measurement + {expiry:.3f} expiry ({share}% expiry "
                f"in this count's window); expired over {DEFAULT_LOOKBACK_DAYS} days "
                f"{window_expired:.3f} {unit}; waste_factor at count "
                f"{latest.waste_factor_at_count:.3f}. {verdict}.",
            )
        )
        figures += [
            f"{latest.drift_pct:+.2f}%",
            f"{abs(latest.drift_pct):.2f}%",
            f"{latest.theoretical_qty:.3f}",
            f"{latest.counted_qty:.3f}",
            f"{abs(gap):.3f}",
            f"{measurement:.3f}",
            f"{expiry:.3f}",
            f"{share}%",
            f"{window_expired:.3f}",
            f"{latest.waste_factor_at_count:.3f}",
        ]

    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    lines = [text for _, _, text in rows[:limit]]
    if not lines:
        return ToolResult(text="No drift observations exist yet. Run `cafeops drift --backfill`.")
    header = (
        f"Drift, {len(lines)} of {len(rows)} tracked ingredient(s) with a drift history, "
        "newest observation each, ingredients with expiry write-offs first. "
        "drift_pct = (theoretical - counted) / max(counted, epsilon) * 100."
    )
    return ToolResult(
        text=header + "\n" + "\n".join(f"- {line}" for line in lines),
        figures=tuple(dict.fromkeys(figures)),
    )


def _expiry_facts(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Every EXPIRED write-off in the window, with what it cost.

    This is the honest waste figure the batch rebuild produced (ARCHITECTURE.md
    8F.2). Money comes from the batch's own `unit_cost_pence`, so it is what was
    actually paid rather than a current price applied retrospectively.
    """
    since, until = _window(args, default_days=DEFAULT_LOOKBACK_DAYS)
    start = datetime.combine(since, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(until, datetime.max.time(), tzinfo=UTC)

    stmt = (
        select(
            Ingredient.name,
            Ingredient.unit,
            StockMovement.qty,
            StockMovement.occurred_at,
            StockBatch.unit_cost_pence,
        )
        .join(Ingredient, Ingredient.id == StockMovement.ingredient_id)
        .outerjoin(
            StockBatch,
            (StockBatch.id == StockMovement.ref_id) & (StockMovement.ref_type == "stock_batch"),
        )
        .where(
            StockMovement.type == MovementType.EXPIRED,
            StockMovement.occurred_at >= start,
            StockMovement.occurred_at <= end,
        )
        .order_by(StockMovement.occurred_at)
    )

    per_ingredient: dict[str, tuple[str, Decimal, Decimal | None, int]] = {}
    total_pence: Decimal | None = Decimal("0")
    unpriced = 0
    count = 0
    for name, unit, qty, _at, unit_cost in ctx.reads.query(stmt):
        count += 1
        magnitude = -qty if qty < 0 else qty
        loss = None if unit_cost is None else magnitude * unit_cost
        if loss is None:
            unpriced += 1
        elif total_pence is not None:
            total_pence += loss
        prior = per_ingredient.get(name)
        if prior is None:
            per_ingredient[name] = (unit.value, magnitude, loss, 1)
        else:
            prior_unit, prior_qty, prior_loss, prior_n = prior
            merged_loss = None if (prior_loss is None or loss is None) else prior_loss + loss
            per_ingredient[name] = (prior_unit, prior_qty + magnitude, merged_loss, prior_n + 1)

    if not count:
        return ToolResult(text=f"No expiry write-offs between {since} and {until}.")

    # Invariant 8: an unpriced batch makes the TOTAL unknown rather than understated.
    stated_total = None if unpriced else total_pence
    lines = []
    figures: list[str] = []
    ranked = sorted(
        per_ingredient.items(),
        key=lambda kv: kv[1][2] if kv[1][2] is not None else Decimal("-1"),
        reverse=True,
    )
    sold = _sale_counts(ctx, start, end)
    for name, (unit, qty, loss, n) in ranked:
        share = (
            f", {int(loss * 100 / stated_total)}% of the total"
            if loss is not None and stated_total not in (None, Decimal("0"))
            else ""
        )
        # "Bought and thrown away without ever being sold" is the sharpest version of
        # an over-ordering finding, and it is a read of the existing ledger rather than
        # anything inferred.
        movement = sold.get(name, 0)
        traded = (
            f"; it depleted through sales {movement} time(s) in the same window"
            if movement
            else "; it NEVER depleted through a sale in the same window -- bought and thrown away"
        )
        lines.append(
            f"- {name}: {n} write-off(s), {qty:.3f} {unit} thrown away, "
            f"{_money(loss)}{share}{traded}"
        )
        figures += [f"{qty:.3f}", _money(loss)]
        if loss is not None and stated_total:
            figures.append(f"{int(loss * 100 / stated_total)}%")

    total_text = _money(stated_total)
    if unpriced:
        total_text = (
            f"unknown -- {unpriced} of {count} write-offs came from a batch with no "
            "recorded unit cost, so the total is not stated rather than understated"
        )
    figures.append(total_text)
    return ToolResult(
        text=(
            f"Expiry write-offs {since}..{until}: {count} across "
            f"{len(per_ingredient)} ingredient(s), total {total_text}.\n" + "\n".join(lines)
        ),
        figures=tuple(dict.fromkeys(figures)),
    )


def _sale_counts(ctx: ToolContext, start: datetime, end: datetime) -> dict[str, int]:
    """SALE movements per ingredient name over the window. A read, not a derivation."""
    from sqlalchemy import func

    stmt = (
        select(Ingredient.name, func.count(StockMovement.id))
        .join(Ingredient, Ingredient.id == StockMovement.ingredient_id)
        .where(
            StockMovement.type == MovementType.SALE,
            StockMovement.occurred_at >= start,
            StockMovement.occurred_at <= end,
        )
        .group_by(Ingredient.name)
    )
    return {name: int(n) for name, n in ctx.reads.query(stmt)}


def _channel_facts(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """ROAS, contribution after commission AND ad spend, and the conversion outliers."""
    since, until = _window(args, default_days=14)
    channels = ctx.reads.channels.channels_present(since=since, until=until)
    if not channels:
        return ToolResult(text=f"No channel metrics between {since} and {until}.")

    lines: list[str] = []
    figures: list[str] = []
    for channel in channels:
        days = ctx.reads.channels.day_figures(since=since, until=until, channel=channel)
        perf = channel_performance(days, channel=channel, since=since, until=until)
        lines.append(
            f"- {channel.value} over {perf.days} day(s): gross {_money(perf.gross_pence)}, "
            f"commission {_money(perf.commission_pence)}, ad spend "
            f"{_money(perf.ad_spend_pence)}, contribution after both "
            f"{_money(perf.net_pence)}, ROAS {format_x(perf.roas_bp)}, conversion "
            f"{format_pct(perf.conversion_bp)}"
        )
        figures += [
            _money(perf.gross_pence),
            _money(perf.commission_pence),
            _money(perf.ad_spend_pence),
            _money(perf.net_pence),
            format_x(perf.roas_bp),
            format_pct(perf.conversion_bp),
        ]
        for caveat in perf.caveats():
            lines.append(f"    caveat: {caveat}")

        items = ctx.reads.channels.item_figures(since=since, until=until, channel=channel)
        finding = rank_well_convert_badly(items, channel=channel)
        for gap in finding.gaps:
            lines.append(f"    ranks well, converts badly: {gap.sentence()}")
            figures += [
                f"#{gap.best_rank}",
                str(gap.views),
                str(gap.orders),
                format_pct(gap.conversion_bp),
                format_pct(gap.benchmark_bp),
                _money(gap.lost_revenue_pence),
            ]
        if not finding.gaps:
            lines.append("    nothing flagged: placement and conversion agree.")

    return ToolResult(
        text=f"Channel performance {since}..{until}:\n" + "\n".join(lines),
        figures=tuple(dict.fromkeys(figures)),
    )


# ==========================================================================
# BROWSER -- data or a staged basket. Never a submission.
# ==========================================================================


def _channel_download_plan(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """The read-only instruction script for pulling a portal's own CSV export."""
    from cafeops.integrations.channels.browser_source import BrowserAgentChannelSource

    since, until = _window(args, default_days=14)
    raw = str(args.get("channel", "")).strip().upper().replace("-", "_").replace(" ", "_")
    try:
        channel = SalesChannelName(raw)
    except ValueError:
        return ToolResult(
            text=(
                f"{raw!r} is not a channel this system knows. "
                + ", ".join(c.value for c in SalesChannelName)
            )
        )
    plan = BrowserAgentChannelSource().plan_for(channel=channel, since=since, until=until)
    return ToolResult(
        text=(
            plan.script()
            + "\n\nThis plan downloads the portal's own export and nothing else. It does "
            "not read figures off the screen, does not change any setting, and does not "
            "touch an ad budget. The downloaded file goes through the same parser and "
            "the same refusals as a hand export."
        ),
        extra={"target_url": plan.target_url, "read_only": plan.read_only},
    )


def _stage_supplier_basket(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Fill a supplier basket and stop, reusing the existing BROWSER_AGENT adapter.

    Deliberately thin. `integrations/suppliers/` already implements the one path to a
    basket that halts before checkout with `requires_human_completion=True`; a second
    path here would be a second thing to get wrong. This tool calls `prepare()` and
    nothing else -- `dispatch()` is not imported, let alone called -- and is declared
    `stops_at_human`, so the outcome logged is AWAITING_HUMAN, never OK.
    """
    from cafeops.db.models import POStatus, PurchaseOrder

    # Importing `channels` is what populates the adapter registry in `base`. Found by
    # running it: without this the tool raised "no adapter registered for channel
    # PORTAL" -- and `integrations/suppliers/__init__.py` is empty, so nothing else
    # imports it for us.
    from cafeops.integrations.suppliers import channels as _channels  # noqa: F401
    from cafeops.integrations.suppliers.base import OrderItem, adapter_for

    po_id = int(args["po_id"])
    order = ctx.reads.query(
        select(PurchaseOrder).where(PurchaseOrder.id == po_id)
    ).scalar_one_or_none()
    if order is None:
        return ToolResult(
            text=f"No purchase order {po_id}.",
            awaiting_human=f"purchase order {po_id} does not exist; nothing was staged",
        )
    if order.status is not POStatus.DRAFT:
        return ToolResult(
            text=(
                f"Purchase order {po_id} is {order.status.value}, not DRAFT. The agent "
                "stages drafts only."
            ),
            awaiting_human=f"purchase order {po_id} is already {order.status.value}",
        )

    items = tuple(
        OrderItem(
            ingredient_name=row.ingredient_name,
            sku=row.sku or "",
            packs=row.packs,
            pack_size=row.pack_size,
            unit_price_pence=row.unit_price_pence,
        )
        for row in _po_lines(ctx, po_id)
    )
    adapter = adapter_for(order.supplier.order_channel)
    prepared = adapter.prepare(po_id, order.supplier, items)
    text = adapter.describe(prepared) + "\n\n" + prepared.instructions
    if settings.browser_worker_enabled:
        # The handler holds a read-only repository bundle and no Session (policies.py
        # device 2), so the queue INSERT is left to the services layer: the tool
        # describes the basket and names the two places a person queues the job.
        # docs/agents/BROWSER-ORDERING.md 4.1.
        text += (
            f"\n\nThe browser worker is on. Queue it with `cafeops portal stage {po_id}` or "
            "the order page's Stage basket: a browser job fills the supplier's basket "
            "and stops there, with a SUPPLIER_BASKET proposal on the Agents page."
        )
    return ToolResult(
        text=text,
        figures=(_money(prepared.total_pence),),
        awaiting_human=(
            f"basket for purchase order {po_id} ({prepared.supplier_name}, "
            f"{_money(prepared.total_pence)}) is staged and STOPS HERE. A person places "
            "the order. Invariant 1: nothing is ordered without human confirmation."
        ),
        extra={"po_id": po_id, "total_pence": prepared.total_pence},
    )


def _po_lines(ctx: ToolContext, po_id: int) -> list[Any]:
    from cafeops.db.models import POLine, SupplierProduct

    # `final_packs` is what a human settled on; `suggested_packs` is what the
    # forecast asked for. The basket must reflect the human's number.
    stmt = (
        select(
            Ingredient.name.label("ingredient_name"),
            SupplierProduct.sku.label("sku"),
            POLine.final_packs.label("packs"),
            SupplierProduct.pack_size.label("pack_size"),
            POLine.unit_price_pence.label("unit_price_pence"),
            SupplierProduct.product_url.label("product_url"),
        )
        .join(Ingredient, Ingredient.id == POLine.ingredient_id)
        .join(SupplierProduct, SupplierProduct.id == POLine.supplier_product_id)
        .where(POLine.po_id == po_id)
    )
    return list(ctx.reads.query(stmt))


# ==========================================================================
# PROPOSE -- a change a human confirms. Nothing here applies anything.
# ==========================================================================


def _propose_waste_factor(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Propose a `waste_factor` change. Spec 9 job 3; applied by `cafeops drift`."""
    name = str(args["ingredient"])
    subject = ctx.reads.ingredients.get_by_name(name)
    if subject is None:
        return ToolResult(
            text=f"No ingredient named {name!r}.",
            proposal=AgentProposal(
                kind="waste_factor",
                subject_ref=name,
                summary=f"no ingredient named {name!r}; nothing to propose",
                confidence="none",
            ),
        )
    proposed = Decimal(str(args["waste_factor"]))
    reason = str(args.get("reason", "")).strip() or "no reason given"
    return ToolResult(
        text=(
            f"Proposed (not applied): {subject.name} waste_factor "
            f"{subject.waste_factor:.3f} -> {proposed:.3f}. Reason: {reason}. "
            "A human confirms this with `cafeops drift --apply-waste`."
        ),
        figures=(f"{subject.waste_factor:.3f}", f"{proposed:.3f}"),
        proposal=AgentProposal(
            kind="waste_factor",
            subject_ref=f"ingredient:{subject.id}",
            summary=(
                f"{subject.name}: waste_factor {subject.waste_factor:.3f} -> "
                f"{proposed:.3f} -- {reason}"
            ),
            payload={
                "ingredient_id": subject.id,
                "current": str(subject.waste_factor),
                "proposed": str(proposed),
                "reason": reason,
            },
            confidence=str(args.get("confidence", "medium")),
        ),
    )


def _propose_template_grouping(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Propose that a set of menu items share one template. Confirmed in the UI."""
    items = [str(i) for i in (args.get("item_names") or [])]
    template = str(args.get("template_name", "")).strip()
    if not items or not template:
        return ToolResult(
            text="A grouping proposal needs a template name and at least one item.",
            proposal=AgentProposal(
                kind="template_grouping",
                subject_ref=template or "?",
                summary="incomplete proposal; nothing to review",
                confidence="none",
            ),
        )
    return ToolResult(
        text=(
            f"Proposed (not applied): group {len(items)} item(s) under a "
            f"{template!r} template -- {', '.join(items[:8])}"
            f"{' ...' if len(items) > 8 else ''}. "
            "Materialising it is `cafeops materialise-template`, which a human runs."
        ),
        proposal=AgentProposal(
            kind="template_grouping",
            subject_ref=f"template:{template}",
            summary=f"{template}: {len(items)} item(s) proposed -- "
            + str(args.get("reason", "")).strip(),
            payload={"template_name": template, "item_names": items},
            confidence=str(args.get("confidence", "low")),
        ),
    )


def _propose_channel_import(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Propose importing a portal export, having parsed it without writing it.

    The parse is real -- the file goes through `CsvChannelSource`, so a file that
    cannot be mapped is refused here rather than proposed. What the human confirms is
    the write, with the row counts already known.
    """
    from pathlib import Path

    from cafeops.integrations.channels.base import UnmappableReportError
    from cafeops.integrations.channels.csv_source import CsvChannelSource

    path = Path(str(args["path"])).expanduser()
    try:
        parsed = CsvChannelSource(files=[path]).parse_file(path)
    except UnmappableReportError as exc:
        return ToolResult(
            text="REFUSED, not proposed -- this file could not be mapped:\n" + exc.report(),
            proposal=AgentProposal(
                kind="channel_import",
                subject_ref=str(path),
                summary=f"{path.name} is not a mappable platform export; refused",
                payload={"error": str(exc)},
                confidence="none",
            ),
        )
    return ToolResult(
        text=(
            f"Proposed (not applied): import {path.name} as "
            f"{parsed.schema.label} -- {len(parsed.day_rows)} day row(s), "
            f"{len(parsed.item_rows)} item row(s), {len(parsed.rejected)} rejected. "
            "A human commits it with `cafeops channels import --commit`."
        ),
        proposal=AgentProposal(
            kind="channel_import",
            subject_ref=str(path),
            summary=(
                f"{path.name}: {parsed.schema.platform.value} {parsed.schema.kind} export, "
                f"{len(parsed.day_rows)} day + {len(parsed.item_rows)} item row(s)"
            ),
            payload={
                "path": str(path),
                "platform": parsed.schema.platform.value,
                "kind": parsed.schema.kind,
                "day_rows": len(parsed.day_rows),
                "item_rows": len(parsed.item_rows),
                "rejected": len(parsed.rejected),
            },
            confidence="high" if not parsed.rejected else "medium",
        ),
    )


# ==========================================================================
# Registration. This list IS the allowlist.
# ==========================================================================

_DATE_WINDOW = {
    "since": {"type": "string", "description": "YYYY-MM-DD. Optional."},
    "until": {"type": "string", "description": "YYYY-MM-DD. Optional."},
}

register(
    ToolSpec(
        name="read_drift_report",
        kind=ToolKind.READ,
        description=(
            "Drift per tracked ingredient with its expiry attribution already computed: "
            "theoretical against counted, the gap split into measurement error and "
            "expiry write-off, and which of the two opposite fixes applies. Use this to "
            "explain a drift number. Do not do arithmetic on the result."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ingredient": {"type": "string", "description": "One ingredient, or omit for all."},
                "limit": {"type": "integer", "description": "How many to return. Default 6."},
            },
        },
        handler=_drift_facts,
    )
)

register(
    ToolSpec(
        name="read_expiry_writeoffs",
        kind=ToolKind.READ,
        description=(
            "Every EXPIRED write-off in a window, per ingredient, with what it cost at "
            "the price actually paid for that batch, and each one's share of the total. "
            "The total is stated as unknown if any batch had no recorded cost."
        ),
        input_schema={"type": "object", "properties": dict(_DATE_WINDOW)},
        handler=_expiry_facts,
    )
)

register(
    ToolSpec(
        name="read_channel_performance",
        kind=ToolKind.READ,
        description=(
            "Deliveroo / Just Eat: gross, commission, ad spend, contribution after both, "
            "ROAS, conversion, and any item that ranks well but converts badly. All "
            "computed; quote the figures, do not derive new ones."
        ),
        input_schema={"type": "object", "properties": dict(_DATE_WINDOW)},
        handler=_channel_facts,
    )
)

register(
    ToolSpec(
        name="browser_plan_channel_report",
        kind=ToolKind.BROWSER,
        description=(
            "The read-only instruction script for downloading a delivery platform's own "
            "CSV export. Produces a plan; it does not open a browser, log in, or change "
            "anything."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": [c.value for c in SalesChannelName]},
                **_DATE_WINDOW,
            },
            "required": ["channel"],
        },
        handler=_channel_download_plan,
    )
)

register(
    ToolSpec(
        name="browser_stage_supplier_basket",
        kind=ToolKind.BROWSER,
        description=(
            "Describe and queue the filling of a supplier's web basket for a DRAFT "
            "purchase order, STOPPING before checkout. With the browser worker enabled "
            "the basket is staged by a queued browser job (`cafeops portal stage`); "
            "otherwise the existing BROWSER_AGENT channel plan is described. This spends "
            "money if completed, so it always ends with a person pressing the last "
            "button; the agent can never place the order."
        ),
        input_schema={
            "type": "object",
            "properties": {"po_id": {"type": "integer"}},
            "required": ["po_id"],
        },
        handler=_stage_supplier_basket,
        stops_at_human=True,
    )
)

register(
    ToolSpec(
        name="propose_waste_factor",
        kind=ToolKind.PROPOSE,
        description=(
            "Propose a new waste_factor for one ingredient. A proposal only -- a human "
            "applies it with `cafeops drift --apply-waste`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ingredient": {"type": "string"},
                "waste_factor": {"type": "string", "description": "Decimal as a string."},
                "reason": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["ingredient", "waste_factor", "reason"],
        },
        handler=_propose_waste_factor,
    )
)

register(
    ToolSpec(
        name="propose_template_grouping",
        kind=ToolKind.PROPOSE,
        description=(
            "Propose that a set of menu items share one composition template. A "
            "proposal only -- materialising it is a human decision."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "template_name": {"type": "string"},
                "item_names": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["template_name", "item_names"],
        },
        handler=_propose_template_grouping,
    )
)

register(
    ToolSpec(
        name="propose_channel_import",
        kind=ToolKind.PROPOSE,
        description=(
            "Parse a delivery-platform CSV export and propose importing it. Refuses a "
            "file whose columns cannot be mapped rather than proposing a guess. Nothing "
            "is written."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=_propose_channel_import,
    )
)
