"""What a staged basket becomes: the snapshot's warnings, the proposal, the audit row.

docs/agents/BROWSER-ORDERING.md §4 step 6 and §10. This module is the part of a
STAGE_BASKET job that does not need a browser, split out of `job.py` so the tier 0
path (a cart link built by the adapter, opened in the owner's own browser) can write
the very same rows from `services/browser_jobs.py` without importing Playwright or
the model loop. Everything here is deterministic text and INSERTs: no model, no page.

The audit rows are written on the caller's session (see `job.py`'s module docstring
for why not the narration agent's audit engine): one `agent_action_log` row per job
and, when a basket was staged, one `agent_proposal` of kind SUPPLIER_BASKET whose
accept is "Open basket". Invariant 10 holds by construction: nothing here imports a
stock, order or composition service.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from cafeops.db.models import BrowserJob, Supplier
from cafeops.db.repositories.agent_log import SqlAgentLogRepository
from cafeops.domain.types import AgentProposal, AgentToolOutcome
from cafeops.integrations.suppliers.portals.base import BasketLine, BasketSnapshot
from cafeops.services.agent_proposals import insert_proposal

AGENT_NAME = "basket_stager"
TOOL_NAME = "browser_stage_supplier_basket"
#: A price seen this far from the order's expectation is worth a warning.
PRICE_TOLERANCE = 0.05

#: The tier ladder, best first (§10). `BasketLine.added_by` names the half that put
#: a line in: "cart_link" | "quick_order" | "script" | "model"; the last two are the
#: browser tier.
TIERS: tuple[str, ...] = ("cart_link", "quick_order", "browser")
_ADDED_BY_TIER: dict[str, str] = {
    "cart_link": "cart_link",
    "quick_order": "quick_order",
    "script": "browser",
    "model": "browser",
}
_IN_BASKET = ("added", "already")

#: The warning every cart-link snapshot carries: the link puts the items in, the
#: basket page shows the money.
CART_LINK_WARNING = "Prices and stock are not checked by a cart link; the basket page shows them"


def tier_of(line: BasketLine) -> str | None:
    """Which tier a line's `added_by` belongs to, or None when nothing added it."""
    return _ADDED_BY_TIER.get(line.added_by or "")


def highest_tier(lines: list[BasketLine] | tuple[BasketLine, ...]) -> str | None:
    """The best tier that put at least one line in the basket, or None."""
    reached = {tier_of(line) for line in lines if line.status in _IN_BASKET}
    for tier in TIERS:
        if tier in reached:
            return tier
    return None


def lines_from_params(params: dict[str, Any]) -> list[BasketLine]:
    """The order lines as `enqueue_stage_basket` snapshotted them in `job.params`."""
    lines: list[BasketLine] = []
    for raw in params.get("lines") or []:
        lines.append(
            BasketLine(
                po_line_id=int(raw.get("po_line_id") or 0),
                ingredient_name=str(raw.get("ingredient_name") or ""),
                sku=str(raw.get("sku") or ""),
                product_url=(str(raw["product_url"]) if raw.get("product_url") else None),
                packs_wanted=int(raw.get("packs") or 0),
                unit_price_expected_pence=int(raw.get("unit_price_pence") or 0),
            )
        )
    return lines


def int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pounds(pence: int | None) -> str:
    return "unread" if pence is None else f"£{pence / 100:,.2f}"


def off_by(seen: int, expected: int) -> bool:
    return abs(seen - expected) / expected > PRICE_TOLERANCE


def warnings_for(
    lines: list[BasketLine],
    *,
    subtotal_seen: int | None,
    total_expected: int,
    unexpected: list[str],
    budget_note: str | None,
    basket_read: bool,
) -> list[str]:
    out: list[str] = []
    incomplete = [line for line in lines if line.status not in _IN_BASKET]
    for line in incomplete:
        out.append(
            f"{line.ingredient_name}: {line.status.replace('_', ' ')}"
            + (f" — {line.note}" if line.note else "")
        )
    for line in lines:
        seen, expected = line.unit_price_seen_pence, line.unit_price_expected_pence
        if seen is not None and expected > 0 and off_by(seen, expected):
            out.append(
                f"{line.ingredient_name}: price seen {pounds(seen)} vs {pounds(expected)} "
                "on the order"
            )
        if (
            line.status in _IN_BASKET
            and line.packs_in_basket is not None
            and line.packs_in_basket != line.packs_wanted
        ):
            out.append(
                f"{line.ingredient_name}: basket holds {line.packs_in_basket} pack(s), order wants "
                f"{line.packs_wanted}"
            )
    if not basket_read:
        out.append("the basket page could not be read; check it before paying")
    elif subtotal_seen is None:
        out.append("basket subtotal not read")
    elif total_expected > 0 and off_by(subtotal_seen, total_expected):
        out.append(
            f"basket subtotal {pounds(subtotal_seen)} differs from the order's "
            f"{pounds(total_expected)}"
        )
    if unexpected:
        out.append(
            f"{len(unexpected)} line(s) in the basket the order did not ask for: "
            + "; ".join(unexpected[:6])
        )
    if budget_note:
        out.append(f"stopped early: {budget_note}")
    return out


def confidence(snapshot: BasketSnapshot) -> str:
    if not snapshot.complete:
        return "low"
    if snapshot.subtotal_seen_pence is None:
        return "medium"
    expected = snapshot.total_expected_pence
    if expected > 0 and abs(snapshot.subtotal_seen_pence - expected) / expected <= PRICE_TOLERANCE:
        return "high"
    return "low"


def summary(snapshot: BasketSnapshot, supplier: Supplier, *, tier: str | None = None) -> str:
    """The proposal body: what is in, what to watch, what a person does next."""
    added = [line for line in snapshot.lines if line.status in _IN_BASKET]
    if tier == "cart_link":
        first = (
            f"{supplier.name}: a link that fills the basket with {len(added)} of "
            f"{len(snapshot.lines)} order lines in your own browser (order expects "
            f"{pounds(snapshot.total_expected_pence)}). Nothing was automated: the link "
            "carries the products and quantities, and the basket page shows the prices."
        )
        third = (
            "Staged, not sent. Open the link, check the prices and quantities there, pay on "
            "the supplier's site, then press Mark sent on the order."
        )
    else:
        first = (
            f"{supplier.name}: {len(added)} of {len(snapshot.lines)} order lines are in the "
            f"basket (subtotal seen {pounds(snapshot.subtotal_seen_pence)}, order expects "
            f"{pounds(snapshot.total_expected_pence)})."
        )
        third = (
            "Staged, not sent. Open the basket, check it, pay on the supplier's site, then "
            "press Mark sent on the order."
        )
    second = (
        "Warnings: " + " | ".join(snapshot.warnings)
        if snapshot.warnings
        else "No warnings: every line is in at the wanted quantity."
    )
    return f"{first}\n{second}\n{third}"


def write_audit(
    session: Session,
    job: BrowserJob,
    *,
    supplier: Supplier,
    snapshot: BasketSnapshot | None,
    outcome: AgentToolOutcome,
    summary_text: str,
    refusal_reason: str | None = None,
    tier: str | None = None,
) -> None:
    """One `agent_action_log` row per job and, when a basket was staged, one proposal."""
    run_id = job.run_id or uuid.uuid4().hex[:16]
    job.run_id = run_id
    repo = SqlAgentLogRepository(session)
    log_id = repo.log(
        run_id=run_id,
        tool_name=TOOL_NAME,
        inputs={"po_id": job.purchase_order_id, "job_id": job.id, "supplier": supplier.name},
        outcome=outcome,
        output=summary_text,
        purpose=f"stage a supplier basket for PO {job.purchase_order_id}",
        refusal_reason=refusal_reason,
        proposal_ref=f"supplier_basket:po:{job.purchase_order_id}" if snapshot else None,
        model=job.model,
        agent=AGENT_NAME,
    )
    if snapshot is None:
        return
    added = sum(1 for line in snapshot.lines if line.status in _IN_BASKET)
    if tier == "cart_link":
        title = f"{supplier.name} basket link ready: {added} of {len(snapshot.lines)} lines"
    else:
        title = (
            f"{supplier.name} basket staged: {added} of {len(snapshot.lines)} lines, "
            f"{pounds(snapshot.subtotal_seen_pence)} seen"
        )
    figures = [pounds(snapshot.total_expected_pence)]
    if snapshot.subtotal_seen_pence is not None:
        figures.append(pounds(snapshot.subtotal_seen_pence))
    payload: dict[str, object] = dict(snapshot.as_dict())
    payload["basket_url"] = snapshot.basket_url
    payload["current"] = {"po_status": (job.params or {}).get("po_status")}
    payload["job_id"] = job.id
    payload["tier"] = tier
    proposal = AgentProposal(
        kind="supplier_basket",
        subject_ref=f"po:{job.purchase_order_id}",
        summary=title,
        payload=payload,
        confidence=confidence(snapshot),
    )
    job.proposal_id = insert_proposal(
        session,
        run_id=run_id,
        log_id=log_id,
        agent=AGENT_NAME,
        proposal=proposal,
        figures=figures,
        title=title[:80],
        body=summary_text,
    )


__all__ = [
    "AGENT_NAME",
    "CART_LINK_WARNING",
    "PRICE_TOLERANCE",
    "TIERS",
    "TOOL_NAME",
    "confidence",
    "highest_tier",
    "int_or_none",
    "lines_from_params",
    "off_by",
    "pounds",
    "summary",
    "tier_of",
    "warnings_for",
    "write_audit",
]
