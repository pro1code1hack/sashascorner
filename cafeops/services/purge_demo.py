"""Remove the synthetic data `cafeops seed --demo` wrote. `cafeops purge-demo`.

The owner asked for fake data to go. This is the proposal for how, written so the
owner can read the dry run and decide; nothing here runs by itself.

What counts as demo is established from the seeder's own markers, never guessed:

- `sale`: `lightspeed_receipt_id LIKE 'DEMO-R%' AND lightspeed_line_id LIKE 'DEMO-L%'`
  (seed/demo.py `_generate_sales`). A real Lightspeed id never starts `DEMO-`.
- `stock_movement` SALE rows with `ref_type = 'sale'` pointing at those sales
  (written by `expand_pending` from them).
- `stock_movement` DELIVERY rows with `ref_type = 'demo_restock'`
  (seed/demo.py `simulate_restocking`).
- `stock_count` rows with `counted_by = 'demo-seed'` (opening and periodic counts).
- `drift_observation` rows whose `stock_count_id` is one of those counts.
- `stock_batch` rows with no `po_line_id` and no `received_by`, received no later
  than the last demo event, and referenced by no movement that survives the purge.
  These were built by `rebuild_batches` replaying the demo ledger ("opening stock",
  "delivery", "count surplus").
- `stock_movement` EXPIRED rows with `ref_type = 'stock_batch'` on those batches
  (derived by the expiry sweep).

Optionally (`include_bot_preview=True`), the rows `cafeops bot-preview` wrote while
the bot was being verified: actor `telegram:sasha`, before BOT_PREVIEW_BEFORE. The
bot has never had a token (DECISIONS 18), so nothing under that actor before that
date can be a real person's action.

Invariant 12 (append-only ledger) is suspended here on purpose, like
`rebuild_batches(purge=True)`: these rows record nothing that happened. The purge
refuses when anything real depends on them -- a non-demo movement drawing from a
demo batch, a loyalty stamp pointing at a demo sale -- because deleting then would
rewrite a real history. Left alone and only reported: the Flavoured Latte template
and its menu items, the three alt-milk modifiers, seasons, par levels and the
alternate supplier products (configuration the owner may want to keep or change,
not fabricated history).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session

from cafeops.db.base import Base
from cafeops.db.models import (
    ChecklistResponse,
    DriftObservation,
    DrinkTemplate,
    Expense,
    MenuItem,
    MovementType,
    POLine,
    PurchaseOrder,
    Sale,
    Season,
    StockBatch,
    StockCount,
    StockMovement,
    SupplierProduct,
    TescoRouting,
)
from cafeops.db.models.loyalty import LoyaltyReward

DEMO_RECEIPT = "DEMO-R%"
DEMO_LINE = "DEMO-L%"
DEMO_COUNTER = "demo-seed"
DEMO_RESTOCK = "demo_restock"
BOT_PREVIEW_ACTOR = "telegram:sasha"
#: The bot preview ran on 2026-09-23. Anything under that actor after this instant
#: could be a real person once the bot has a token, so it is never touched.
BOT_PREVIEW_BEFORE = datetime(2026, 9, 24, tzinfo=UTC)


@dataclass
class PurgePlan:
    counts: dict[str, int] = field(default_factory=dict)
    refusals: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    committed: bool = False

    def lines(self) -> list[str]:
        out = ["Rows identified as demo (would be deleted):"]
        width = max((len(k) for k in self.counts), default=0)
        for key, n in self.counts.items():
            out.append(f"  {key.ljust(width)}  {n:>6}")
        out.append(f"  {'total'.ljust(width)}  {sum(self.counts.values()):>6}")
        if self.kept:
            out.append("Left alone (configuration, the owner's call):")
            out.extend(f"  - {k}" for k in self.kept)
        if self.refusals:
            out.append("REFUSED -- real data depends on demo rows:")
            out.extend(f"  ! {r}" for r in self.refusals)
        return out


def _ids(session: Session, stmt: Select[tuple[int]]) -> list[int]:
    return [int(i) for i in session.scalars(stmt)]


def _chunks(ids: list[int], size: int = 500) -> list[list[int]]:
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def _delete(session: Session, model: type[Base], ids: list[int]) -> None:
    col = model.__table__.c.id
    for chunk in _chunks(ids):
        session.execute(delete(model).where(col.in_(chunk)))


def purge_demo(
    session: Session, *, commit: bool = False, include_bot_preview: bool = False
) -> PurgePlan:
    """Plan (and with commit=True, perform) the demo purge. Returns what it found."""
    plan = PurgePlan()

    sale_ids = _ids(
        session,
        select(Sale.id).where(
            Sale.lightspeed_receipt_id.like(DEMO_RECEIPT), Sale.lightspeed_line_id.like(DEMO_LINE)
        ),
    )
    sale_set = set(sale_ids)
    sale_mv = [
        mid
        for mid, ref in session.execute(
            select(StockMovement.id, StockMovement.ref_id).where(
                StockMovement.type == MovementType.SALE, StockMovement.ref_type == "sale"
            )
        )
        if ref in sale_set
    ]
    restock_mv = _ids(
        session,
        select(StockMovement.id).where(
            StockMovement.type == MovementType.DELIVERY, StockMovement.ref_type == DEMO_RESTOCK
        ),
    )
    count_ids = _ids(session, select(StockCount.id).where(StockCount.counted_by == DEMO_COUNTER))

    # Last instant anything demo happened: no demo batch can be received after it.
    ends = [
        session.scalar(
            select(func.max(Sale.sold_at)).where(Sale.lightspeed_receipt_id.like(DEMO_RECEIPT))
        ),
        session.scalar(
            select(func.max(StockCount.counted_at)).where(StockCount.counted_by == DEMO_COUNTER)
        ),
        session.scalar(
            select(func.max(StockMovement.occurred_at)).where(
                StockMovement.ref_type == DEMO_RESTOCK
            )
        ),
    ]
    known = [e for e in ends if e is not None]
    demo_end = max(known) if known else None

    batch_ids: list[int] = []
    if demo_end is not None:
        batch_ids = _ids(
            session,
            select(StockBatch.id).where(
                StockBatch.po_line_id.is_(None),
                StockBatch.received_by.is_(None),
                StockBatch.received_at <= demo_end,
            ),
        )
    batch_set = set(batch_ids)
    expired_mv = [
        mid
        for mid, bid in session.execute(
            select(StockMovement.id, StockMovement.batch_id).where(
                StockMovement.type == MovementType.EXPIRED,
                StockMovement.ref_type == "stock_batch",
            )
        )
        if bid in batch_set
    ]
    drift_ids: list[int] = []
    for chunk in _chunks(count_ids):
        drift_ids += _ids(
            session,
            select(DriftObservation.id).where(DriftObservation.stock_count_id.in_(chunk)),
        )

    po_ids: list[int] = []
    line_ids: list[int] = []
    bot_batch: list[int] = []
    bot_mv: list[int] = []
    bot_counts: list[int] = []
    bot_drift: list[int] = []
    bot_check: list[int] = []
    if include_bot_preview:
        po_ids = _ids(
            session,
            select(PurchaseOrder.id).where(
                or_(
                    PurchaseOrder.confirmed_by == BOT_PREVIEW_ACTOR,
                    PurchaseOrder.sent_by == BOT_PREVIEW_ACTOR,
                ),
                PurchaseOrder.created_at < BOT_PREVIEW_BEFORE,
            ),
        )
        line_ids = _ids(session, select(POLine.id).where(POLine.po_id.in_(po_ids or [-1])))
        bot_batch = _ids(
            session, select(StockBatch.id).where(StockBatch.po_line_id.in_(line_ids or [-1]))
        )
        bot_mv = _ids(
            session,
            select(StockMovement.id).where(
                StockMovement.ref_type == "po_line", StockMovement.ref_id.in_(line_ids or [-1])
            ),
        )
        bot_counts = _ids(
            session,
            select(StockCount.id).where(
                StockCount.counted_by == BOT_PREVIEW_ACTOR,
                StockCount.counted_at < BOT_PREVIEW_BEFORE,
            ),
        )
        bot_drift = _ids(
            session,
            select(DriftObservation.id).where(
                DriftObservation.stock_count_id.in_(bot_counts or [-1])
            ),
        )
        bot_check = _ids(
            session,
            select(ChecklistResponse.id).where(
                ChecklistResponse.responded_by == BOT_PREVIEW_ACTOR,
                ChecklistResponse.responded_at < BOT_PREVIEW_BEFORE,
            ),
        )

    doomed_mv = set(sale_mv) | set(restock_mv) | set(expired_mv) | set(bot_mv)
    doomed_batches = batch_set | set(bot_batch)

    # --- refusals: real rows that lean on demo rows -----------------------------
    survivors = session.execute(
        select(StockMovement.id, StockMovement.batch_id, StockMovement.type).where(
            StockMovement.batch_id.is_not(None)
        )
    ).all()
    leaning = [m for m in survivors if m[1] in doomed_batches and m[0] not in doomed_mv]
    if leaning:
        plan.refusals.append(
            f"{len(leaning)} non-demo stock movement(s) draw from demo batches "
            f"(e.g. movement {leaning[0][0]}, {leaning[0][2].value}); rebuild batches first"
        )
    stamped = 0
    for chunk in _chunks(sale_ids):
        stamped += int(
            session.scalar(
                select(func.count(LoyaltyReward.id)).where(LoyaltyReward.sale_id.in_(chunk))
            )
            or 0
        )
    if stamped:
        plan.refusals.append(f"{stamped} loyalty reward(s) point at demo sales")
    if po_ids:
        tied = int(
            session.scalar(
                select(func.count(Expense.id)).where(Expense.purchase_order_id.in_(po_ids))
            )
            or 0
        ) + int(
            session.scalar(
                select(func.count(TescoRouting.id)).where(
                    TescoRouting.po_line_id.in_(line_ids or [-1])
                )
            )
            or 0
        )
        if tied:
            plan.refusals.append(f"{tied} expense/Tesco-routing row(s) point at bot-preview orders")

    plan.counts = {
        "sale (DEMO-R receipts)": len(sale_ids),
        "stock_movement SALE (from demo sales)": len(sale_mv),
        "stock_movement DELIVERY (demo_restock)": len(restock_mv),
        "stock_movement EXPIRED (demo batches)": len(expired_mv),
        "stock_count (demo-seed)": len(count_ids),
        "drift_observation (on demo counts)": len(drift_ids),
        "stock_batch (rebuilt from demo ledger)": len(batch_ids),
    }
    if include_bot_preview:
        plan.counts.update(
            {
                "purchase_order (bot preview)": len(po_ids),
                "po_line (bot preview)": len(line_ids),
                "stock_batch (bot preview receipt)": len(bot_batch),
                "stock_movement DELIVERY (bot preview)": len(bot_mv),
                "stock_count (bot preview)": len(bot_counts),
                "drift_observation (bot preview)": len(bot_drift),
                "checklist_response (bot preview)": len(bot_check),
            }
        )

    template = session.scalar(select(DrinkTemplate).where(DrinkTemplate.name == "Flavoured Latte"))
    if template is not None:
        n = session.scalar(
            select(func.count(MenuItem.id)).where(MenuItem.template_id == template.id)
        )
        plan.kept.append(
            f"template 'Flavoured Latte' (id {template.id}) and its {n} menu items "
            "(Vanilla/Caramel Latte were created by the demo; Pistachio Latte is legacy)"
        )
    seasons = session.scalar(
        select(func.count(Season.id)).where(Season.note == "seeded demo season")
    )
    if seasons:
        plan.kept.append(f"{seasons} season(s) noted 'seeded demo season'")
    alts = session.scalar(
        select(func.count(SupplierProduct.id)).where(SupplierProduct.sku.like("ALT-%"))
    )
    if alts:
        plan.kept.append(f"{alts} alternate supplier product(s) with invented prices (sku ALT-*)")
    plan.kept.append(
        "par levels sized from demo throughput, alt-milk modifiers, the eight suppliers"
    )

    if not commit or plan.refusals:
        session.rollback()
        return plan

    # --- delete, children first --------------------------------------------------
    try:
        _delete(session, DriftObservation, drift_ids + bot_drift)
        _delete(session, StockMovement, sorted(doomed_mv))
        _delete(session, StockCount, count_ids + bot_counts)
        _delete(session, StockBatch, sorted(doomed_batches))
        _delete(session, ChecklistResponse, bot_check)
        _delete(session, POLine, line_ids)
        _delete(session, PurchaseOrder, po_ids)
        _delete(session, Sale, sale_ids)
        session.commit()
    except Exception:
        session.rollback()
        raise
    plan.committed = True
    return plan
