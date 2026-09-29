"""Placing an order and moving it through its life (CONTRACT §3.1, §3.2, §3.5-3.8).

`place_order` is the only writer of a `shop_order`; `transition` the only writer of a
status. Both are called by the public API (the customer), the admin API (staff) and
the scheduler (expiry), and none of those three knows the rules -- they are here:

- **A collected order is a sale** (§3.1). `COLLECTED` writes one `sale` row per line
  (`web:<code>` / `web:<code>:<n>`, `channel=WEB`, `source=ONLINE`,
  `recorded_by="online shop"`, `sold_at=collected_at`, `applied_modifiers` = the
  options' modifier ids, `expanded_at=NULL` so the nightly expansion depletes stock
  like any other sale). `sale_receipt_id` on the order is the guard against a second
  write. Nothing here touches `stock_movement` (invariant 12).
- **The free drink is redeemed at COLLECTED**, not at placement (§3.5): the reward row
  gets `redeemed_at`, `redeemed_menu_item_id` and `sale_id` -- pointing at the web sale,
  not a second £0 loyalty sale, or the drink would deplete stock twice. Between
  placement and collection the reward is only *held* (a live order references it), so
  cancelling releases it by doing nothing. If the reward was used at the till in the
  meantime, the discount is withdrawn and the order says so in an event -- the customer
  pays at the counter, and the counter sees the corrected total.
- **Stamps at COLLECTED** through `services/loyalty/stamping.apply_units`, one per
  eligible unit up to the programme's `max_stamps_per_scan` (a free drink earns none),
  points on the total for a POINTS card.
- **Cancel or reject after COLLECTED is refused** (§3.2). Cancelling a PAID order does
  not touch the payment: an event says "refund needed" and a person refunds in Stripe.
- **Every change is an event row and bumps `updated_at`** (§3.8).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    DiningOption,
    LoyaltyCard,
    LoyaltyReward,
    OrderStatus,
    PaymentStatus,
    ProgramKind,
    Sale,
    SaleChannel,
    SaleSource,
    ShopOrder,
    ShopOrderEvent,
    ShopOrderLine,
    ShopPaymentMethod,
    ShopSettings,
)
from cafeops.domain.loyalty import (
    item_matches,
    normalise_email,
    normalise_phone,
    points_for_spend,
)
from cafeops.domain.shop import (
    CUSTOMER_CANCELLABLE,
    can_transition,
    choose_reward_line,
    display_code,
    new_code,
    normalise_code,
)
from cafeops.services.loyalty.common import (
    audit,
    enqueue_wallet_update,
    refresh_reward_available,
    touch,
)
from cafeops.services.loyalty.programs import item_facts, program_rule
from cafeops.services.loyalty.stamping import apply_units
from cafeops.services.record_sale import line_gross_pence
from cafeops.services.shop.catalog import load_settings
from cafeops.services.shop.errors import ShopError
from cafeops.services.shop.pricing import LineIn, Quote, quote
from cafeops.services.shop.slots import resolve_requested

__all__ = [
    "ONLINE_RECORDED_BY",
    "PENDING_PAYMENT_TTL",
    "PlaceRequest",
    "add_staff_note",
    "customer_cancel",
    "expire_pending_payments",
    "load_order",
    "load_order_by_code",
    "mark_paid",
    "place_order",
    "queue_customer_notice",
    "record_event",
    "transition",
    "web_receipt_id",
]

ONLINE_RECORDED_BY = "online shop"
#: A Stripe Checkout the customer walked away from (§3.6).
PENDING_PAYMENT_TTL = timedelta(minutes=30)

_STATUS_EVENT = {
    OrderStatus.NEW: "placed",
    OrderStatus.ACCEPTED: "accepted",
    OrderStatus.PREPARING: "preparing",
    OrderStatus.READY: "ready",
    OrderStatus.COLLECTED: "collected",
    OrderStatus.CANCELLED: "cancelled",
    OrderStatus.REJECTED: "rejected",
}


@dataclass(frozen=True, slots=True)
class PlaceRequest:
    dining: str
    asap: bool
    requested_at: datetime | None
    lines: tuple[LineIn, ...]
    reward: bool
    customer_name: str
    customer_phone: str | None
    customer_email: str | None
    note: str | None
    allergy_ack: bool
    #: "counter" | "online".
    payment: str
    expected_total_pence: int
    sms_opt_in: bool = False
    #: Eat-in only; ignored (not stored) for takeaway.
    table: str | None = None
    client_ip: str | None = None
    user_agent: str | None = None


def web_receipt_id(code: str) -> str:
    return f"web:{code}"


def record_event(
    session: Session,
    order: ShopOrder,
    kind: str,
    detail: str | None,
    *,
    actor: str,
    at: datetime | None = None,
) -> ShopOrderEvent:
    at = at or datetime.now(UTC)
    event = ShopOrderEvent(
        order_id=order.id,
        at=at,
        kind=kind[:40],
        detail=(detail or "")[:400] or None,
        actor=actor[:80],
    )
    session.add(event)
    order.updated_at = at
    return event


# --------------------------------------------------------------------------
# lookups
# --------------------------------------------------------------------------


def load_order(session: Session, order_id: int) -> ShopOrder:
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise ShopError(404, "unknown_order", "There is no order with that number.")
    return order


def load_order_by_code(session: Session, raw_code: str, token: str | None) -> ShopOrder:
    """The customer's own order: right code AND right token, or the same 404 for both."""
    code = normalise_code(raw_code)
    order = session.scalar(select(ShopOrder).where(ShopOrder.code == code)) if code else None
    if order is None or not token or not secrets.compare_digest(order.access_token, token):
        raise ShopError(404, "unknown_order", "There is no order with that code on this device.")
    return order


# --------------------------------------------------------------------------
# placing
# --------------------------------------------------------------------------


def _clean_contact(
    req: PlaceRequest, card: LoyaltyCard | None
) -> tuple[str, str | None, str | None]:
    name = " ".join((req.customer_name or "").split())[:80]
    phone = email = None
    if req.customer_phone and req.customer_phone.strip():
        phone = normalise_phone(req.customer_phone)
        if phone is None:
            raise ShopError(422, "bad_phone", "That phone number does not look right.")
    if req.customer_email and req.customer_email.strip():
        email = normalise_email(req.customer_email)
        if email is None:
            raise ShopError(422, "bad_email", "That email address does not look right.")
    if card is not None and card.voided_at is None:
        member = card.member
        name = name or member.first_name
        if phone is None and email is None:
            phone, email = member.phone, member.email
    if not name:
        raise ShopError(422, "name_required", "Tell us the name to call out at the counter.")
    if phone is None and email is None:
        raise ShopError(
            422, "contact_required", "Add a phone number or an email so we can reach you."
        )
    return name, phone, email


def _unique_code(session: Session) -> str:
    for _ in range(20):
        code = new_code()
        if session.scalar(select(ShopOrder.id).where(ShopOrder.code == code)) is None:
            return code
    raise ShopError(503, "code_exhausted", "Could not allot an order code. Try again.")


def place_order(
    session: Session,
    req: PlaceRequest,
    *,
    card: LoyaltyCard | None,
    now: datetime | None = None,
) -> tuple[ShopOrder, Quote]:
    """Write the order (§3.3-3.6). Returns it with the quote it was priced from."""
    now = now or datetime.now(UTC)
    shop = load_settings(session)
    if not shop.enabled:
        raise ShopError(403, "shop_closed", shop.closed_message)
    if not req.allergy_ack:
        raise ShopError(422, "allergy_ack_required", "Please read the allergy notice first.")
    try:
        dining = DiningOption(req.dining.upper())
    except ValueError as exc:
        raise ShopError(422, "bad_dining", "Choose takeaway or eat in.") from exc
    if dining is DiningOption.TAKEAWAY and not shop.takeaway_enabled:
        raise ShopError(422, "bad_dining", "Takeaway is not offered at the moment.")
    if dining is DiningOption.EAT_IN and not shop.eat_in_enabled:
        raise ShopError(422, "bad_dining", "Eat in is not offered at the moment.")
    if req.payment == "counter":
        if not shop.pay_at_counter:
            raise ShopError(422, "bad_payment", "Paying at the counter is not offered.")
        method = ShopPaymentMethod.COUNTER
    elif req.payment == "online":
        from cafeops.services.shop.payments import online_payment_offered

        if not online_payment_offered(shop):
            raise ShopError(422, "bad_payment", "Paying online is not available right now.")
        method = ShopPaymentMethod.ONLINE
    else:
        raise ShopError(422, "bad_payment", "Choose how you will pay: counter or online.")

    live_card = card if card is not None and card.voided_at is None else None
    priced = quote(session, lines=req.lines, reward=req.reward, card=live_card, now=now)
    if not priced.ok:
        first = next((p for ln in priced.lines for p in ln.problems), None) or (
            priced.problems[0] if priced.problems else "The basket needs a look."
        )
        raise ShopError(422, "basket_problem", first, extra={"quote": _quote_payload(priced)})
    if priced.total_pence != req.expected_total_pence:
        raise ShopError(
            409,
            "price_changed",
            f"Prices changed while you were ordering: the total is now "
            f"£{priced.total_pence / 100:.2f}.",
            extra={"quote": _quote_payload(priced)},
        )

    requested_at = resolve_requested(
        session, shop, asap=req.asap, requested_at=req.requested_at, now=now
    )
    name, phone, email = _clean_contact(req, live_card)

    status = OrderStatus.PENDING_PAYMENT if method is ShopPaymentMethod.ONLINE else OrderStatus.NEW
    order = ShopOrder(
        code=_unique_code(session),
        status=status,
        dining=dining,
        asap=req.asap,
        requested_at=requested_at,
        placed_at=now,
        customer_name=name,
        customer_phone=phone,
        customer_email=email,
        member_id=live_card.member_id if live_card else None,
        card_id=live_card.id if live_card else None,
        note=(req.note or "").strip()[:300] or None,
        table=(
            " ".join((req.table or "").split())[:20] or None
            if dining is DiningOption.EAT_IN
            else None
        ),
        allergy_ack=True,
        subtotal_pence=priced.subtotal_pence,
        discount_pence=priced.discount_pence,
        total_pence=priced.total_pence,
        reward_id=priced.reward.reward_id if priced.reward.applied else None,
        payment_method=method,
        payment_status=PaymentStatus.UNPAID,
        sms_opt_in=bool(req.sms_opt_in and phone),
        access_token=secrets.token_urlsafe(32),
        client_ip_hash=(
            hashlib.sha256(req.client_ip.encode("utf-8")).hexdigest() if req.client_ip else None
        ),
        user_agent=(req.user_agent or "")[:200] or None,
        updated_at=now,
    )
    session.add(order)
    session.flush()
    for index, ln in enumerate(priced.lines):
        session.add(
            ShopOrderLine(
                order_id=order.id,
                product_id=ln.product_id,
                menu_item_id=ln.menu_item_id,
                name=ln.name,
                size_label=ln.size_label,
                qty=ln.qty,
                unit_price_pence=ln.unit_price_pence,
                options=[
                    {
                        "group": o.group,
                        "name": o.name,
                        "price_delta_pence": o.price_delta_pence,
                        "modifier_id": o.modifier_id,
                        "option_id": o.option_id,
                    }
                    for o in ln.options
                ],
                line_total_pence=ln.line_total_pence,
                sort_order=index,
            )
        )
    detail = (
        f"{len(priced.lines)} line(s), £{order.total_pence / 100:.2f}, "
        f"{'pay online' if method is ShopPaymentMethod.ONLINE else 'pay at the counter'}"
    )
    if priced.reward.applied:
        detail += f"; free drink held (reward #{priced.reward.reward_id})"
    record_event(
        session,
        order,
        "placed" if status is OrderStatus.NEW else "awaiting_payment",
        detail,
        actor="customer",
        at=now,
    )
    session.flush()
    session.refresh(order)
    return order, priced


def _quote_payload(priced: Quote) -> dict[str, object]:
    return {
        "lines": [
            {
                "product_id": ln.product_id,
                "menu_item_id": ln.menu_item_id,
                "qty": ln.qty,
                "option_ids": list(ln.option_ids),
                "name": ln.name,
                "size_label": ln.size_label,
                "unit_price_pence": ln.unit_price_pence,
                "line_total_pence": ln.line_total_pence,
                "options": [
                    {"group": o.group, "name": o.name, "price_delta_pence": o.price_delta_pence}
                    for o in ln.options
                ],
                "problems": list(ln.problems),
            }
            for ln in priced.lines
        ],
        "subtotal_pence": priced.subtotal_pence,
        "discount_pence": priced.discount_pence,
        "total_pence": priced.total_pence,
        "reward": {
            "applied": priced.reward.applied,
            "line_index": priced.reward.line_index,
            "text": priced.reward.text,
        },
        "problems": list(priced.problems),
    }


# --------------------------------------------------------------------------
# customer notices (CONTRACT §3c): queued on the session, fired after its commit
# --------------------------------------------------------------------------


def queue_customer_notice(session: Session, order_id: int, kind: str) -> None:
    """Tell the customer `kind` once this session commits (never before: the message
    must describe a status that exists). Each notice runs on its own thread with its
    own session -- best effort, never failing the request that caused it."""
    queue: list[tuple[int, str]] = session.info.setdefault("shop_customer_notices", [])
    queue.append((order_id, kind))
    if not session.info.get("shop_customer_notices_hooked"):
        session.info["shop_customer_notices_hooked"] = True
        event.listen(session, "after_commit", _fire_customer_notices)


def _fire_customer_notices(session: Session) -> None:
    from cafeops.services.shop.notify import notify_customer_task

    notices: list[tuple[int, str]] = session.info.pop("shop_customer_notices", [])
    for order_id, kind in notices:
        threading.Thread(target=notify_customer_task, args=(order_id, kind), daemon=True).start()


# --------------------------------------------------------------------------
# transitions
# --------------------------------------------------------------------------


def transition(
    session: Session,
    order: ShopOrder,
    target: OrderStatus,
    *,
    actor: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> ShopOrder:
    """Move `order` to `target` (§3.8) or refuse with 409 `bad_transition`."""
    now = now or datetime.now(UTC)
    if not can_transition(order.status.value, target.value):
        raise ShopError(
            409,
            "bad_transition",
            f"An order that is {order.status.value.lower().replace('_', ' ')} cannot become "
            f"{target.value.lower()}.",
        )
    if target in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
        _cancel(
            session,
            order,
            actor=actor,
            reason=reason,
            now=now,
            rejected=target is OrderStatus.REJECTED,
        )
        if actor not in ("customer", "system"):
            # Staff on the web ("staff:…") or on Telegram ("telegram:…") -> tell the
            # customer. A customer's own cancel needs no message; the scheduler's
            # expiry neither.
            queue_customer_notice(session, order.id, target.value.lower())
        return order
    if target is OrderStatus.COLLECTED:
        return _collect(session, order, actor=actor, now=now)
    if target is OrderStatus.ACCEPTED:
        order.accepted_at = now
        queue_customer_notice(session, order.id, "accepted")
    elif target is OrderStatus.READY:
        order.ready_at = now
        queue_customer_notice(session, order.id, "ready")
    elif target is OrderStatus.NEW and order.status is OrderStatus.PENDING_PAYMENT:
        # Paid: the Stripe webhook's move. `mark_paid` records the payment itself.
        pass
    order.status = target
    record_event(session, order, _STATUS_EVENT[target], reason, actor=actor, at=now)
    session.flush()
    return order


def customer_cancel(
    session: Session, order: ShopOrder, *, now: datetime | None = None
) -> ShopOrder:
    if order.status.value not in CUSTOMER_CANCELLABLE:
        raise ShopError(
            409,
            "too_late",
            "This order is already being made. Ask at the counter if you need to change it.",
        )
    return transition(
        session,
        order,
        OrderStatus.CANCELLED,
        actor="customer",
        reason="cancelled by customer",
        now=now,
    )


def _cancel(
    session: Session,
    order: ShopOrder,
    *,
    actor: str,
    reason: str | None,
    now: datetime,
    rejected: bool,
) -> ShopOrder:
    order.status = OrderStatus.REJECTED if rejected else OrderStatus.CANCELLED
    order.cancelled_at = now
    order.cancel_reason = (reason or "").strip()[:300] or None
    order.cancelled_by = "customer" if actor == "customer" else "staff"
    record_event(
        session, order, "rejected" if rejected else "cancelled", reason, actor=actor, at=now
    )
    if order.payment_status is PaymentStatus.PAID:
        pounds = f"£{order.total_pence // 100}.{order.total_pence % 100:02d}"
        if order.payment_method is ShopPaymentMethod.ONLINE:
            detail = (
                f"Paid {pounds} online; refund it with the payment provider "
                f"({order.payment_ref or 'no payment reference'})."
            )
        else:
            detail = f"Paid {pounds} at the counter; refund it from the till."
        record_event(session, order, "refund_needed", detail, actor="system", at=now)
    if order.reward_id is not None:
        record_event(
            session,
            order,
            "reward_released",
            f"The free drink (reward #{order.reward_id}) is back on the card.",
            actor="system",
            at=now,
        )
    session.flush()
    return order


def mark_paid(
    session: Session,
    order: ShopOrder,
    *,
    actor: str,
    now: datetime | None = None,
    payment_ref: str | None = None,
) -> ShopOrder:
    """Record the payment. A PENDING_PAYMENT order becomes NEW (Stripe's success)."""
    now = now or datetime.now(UTC)
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
        raise ShopError(409, "bad_transition", "This order was cancelled.")
    if order.payment_status is PaymentStatus.PAID:
        return order
    order.payment_status = PaymentStatus.PAID
    order.paid_at = now
    if payment_ref:
        order.payment_ref = payment_ref[:120]
    record_event(
        session,
        order,
        "paid",
        f"£{order.total_pence / 100:.2f} "
        + ("online" if order.payment_method is ShopPaymentMethod.ONLINE else "at the counter"),
        actor=actor,
        at=now,
    )
    if order.status is OrderStatus.PENDING_PAYMENT:
        order.status = OrderStatus.NEW
        record_event(session, order, "placed", "payment confirmed", actor=actor, at=now)
    session.flush()
    return order


def add_staff_note(
    session: Session, order: ShopOrder, note: str, *, actor: str, now: datetime | None = None
) -> ShopOrder:
    now = now or datetime.now(UTC)
    order.staff_note = " ".join(note.split())[:300] or None
    record_event(session, order, "note", order.staff_note, actor=actor, at=now)
    session.flush()
    return order


def expire_pending_payments(
    session: Session, *, now: datetime | None = None, ttl: timedelta = PENDING_PAYMENT_TTL
) -> int:
    """PENDING_PAYMENT orders older than `ttl` -> CANCELLED (§3.6). Returns the count."""
    now = now or datetime.now(UTC)
    stale = list(
        session.scalars(
            select(ShopOrder).where(
                ShopOrder.status == OrderStatus.PENDING_PAYMENT,
                ShopOrder.placed_at < now - ttl,
            )
        )
    )
    for order in stale:
        _cancel(
            session,
            order,
            actor="system",
            reason=f"payment not completed within {int(ttl.total_seconds() // 60)} minutes",
            now=now,
            rejected=False,
        )
    return len(stale)


# --------------------------------------------------------------------------
# COLLECTED: the sale, the reward, the stamps
# --------------------------------------------------------------------------


def _reward_line(session: Session, order: ShopOrder, card: LoyaltyCard | None) -> ShopOrderLine:
    """Which line the free drink was: the priciest eligible one, by the same rule as
    the quote. The programme's rule is re-read; if it changed since, the priciest line."""
    lines: Sequence[ShopOrderLine] = order.lines
    eligible: dict[int, bool] = {}
    if card is not None:
        rule = program_rule(card.program)
        facts = item_facts(session, (ln.menu_item_id for ln in lines))
        eligible = {
            ln.id: ln.menu_item_id in facts and item_matches(rule, facts[ln.menu_item_id])
            for ln in lines
        }
    pick = choose_reward_line(
        [(ln.unit_price_pence, eligible.get(ln.id, False)) for ln in lines], cap_pence=None
    )
    if pick.line_index is None:
        pick = choose_reward_line([(ln.unit_price_pence, True) for ln in lines], cap_pence=None)
    assert pick.line_index is not None  # an order always has a line
    return lines[pick.line_index]


def _reward_still_available(reward: LoyaltyReward, now: datetime) -> bool:
    return (
        reward.redeemed_at is None
        and reward.voided_at is None
        and (reward.expires_at is None or reward.expires_at > now)
    )


def _collect(session: Session, order: ShopOrder, *, actor: str, now: datetime) -> ShopOrder:
    shop: ShopSettings = load_settings(session)
    card = session.get(LoyaltyCard, order.card_id) if order.card_id else None
    if card is not None and card.voided_at is not None:
        card = None

    # 1. the reward: still there, or the discount is withdrawn (the counter sees the total)
    reward: LoyaltyReward | None = None
    reward_line: ShopOrderLine | None = None
    if order.reward_id is not None:
        held = session.get(LoyaltyReward, order.reward_id)
        if held is not None and _reward_still_available(held, now) and card is not None:
            reward = held
            reward_line = _reward_line(session, order, card)
        else:
            forfeited = order.discount_pence
            order.discount_pence = 0
            order.total_pence = order.subtotal_pence
            record_event(
                session,
                order,
                "reward_unavailable",
                f"The free drink was already used elsewhere; £{forfeited / 100:.2f} discount "
                f"withdrawn, total is now £{order.total_pence / 100:.2f}.",
                actor="system",
                at=now,
            )

    # 2. status and payment
    order.status = OrderStatus.COLLECTED
    order.collected_at = now
    record_event(session, order, "collected", None, actor=actor, at=now)
    if order.payment_status is not PaymentStatus.PAID:
        order.payment_status = PaymentStatus.PAID
        order.paid_at = now
        record_event(
            session,
            order,
            "paid",
            f"£{order.total_pence / 100:.2f} at the counter",
            actor=actor,
            at=now,
        )

    # 3. the sale, once
    receipt = web_receipt_id(order.code)
    sale_by_line: dict[int, Sale] = {}
    if order.sale_receipt_id is None and not session.scalar(
        select(Sale.id).where(Sale.lightspeed_receipt_id == receipt)
    ):
        for n, ln in enumerate(order.lines, start=1):
            gross = ln.line_total_pence
            if reward_line is not None and ln.id == reward_line.id:
                gross = max(0, gross - order.discount_pence)
            modifiers = [
                int(o["modifier_id"])
                for o in ln.options
                if isinstance(o, dict) and o.get("modifier_id") is not None
            ]
            sale = Sale(
                lightspeed_receipt_id=receipt,
                lightspeed_line_id=f"{receipt}:{n}",
                menu_item_id=ln.menu_item_id,
                qty=Decimal(ln.qty),
                gross_pence=gross
                if ln.qty == 1
                else min(gross, line_gross_pence(ln.unit_price_pence, Decimal(ln.qty))),
                sold_at=now,
                channel=SaleChannel.WEB,
                source=SaleSource.ONLINE,
                recorded_by=ONLINE_RECORDED_BY,
                note=f"online order {display_code(order.code)}",
                applied_modifiers=modifiers,
                voided=False,
                is_refund=False,
                expanded_at=None,
            )
            session.add(sale)
            sale_by_line[ln.id] = sale
        session.flush()
        order.sale_receipt_id = receipt
        record_event(
            session,
            order,
            "sale_recorded",
            f"{len(sale_by_line)} sale line(s) as {receipt}, channel WEB",
            actor="system",
            at=now,
        )

    # 4. the reward is redeemed against the web sale (the redeem pattern, no second sale)
    if reward is not None and reward_line is not None and card is not None:
        sale_row = sale_by_line.get(reward_line.id)
        reward.redeemed_at = now
        reward.redeemed_menu_item_id = reward_line.menu_item_id
        reward.sale_id = sale_row.id if sale_row is not None else None
        reward.staff_user_id = None
        reward.reward_option_id = None
        audit(
            session,
            "redeem",
            f"reward {reward.id} ({reward.kind.value}) given on online order "
            f"{display_code(order.code)} as {reward_line.name}",
            card_id=card.id,
            member_id=card.member_id,
            at=now,
        )
        refresh_reward_available(session, card, now)
        touch(card, now)
        card.member.last_activity_at = now
        enqueue_wallet_update(session, card.id, None)
        record_event(
            session,
            order,
            "reward_redeemed",
            f"free drink: {reward_line.name} (reward #{reward.id})",
            actor="system",
            at=now,
        )

    # 5. stamps
    if shop.loyalty_stamps_online and card is not None and card.program.active:
        program = card.program
        units = 0
        if program.kind is ProgramKind.POINTS:
            units = points_for_spend(order.total_pence, program.points_per_pound or 0)
        else:
            rule = program_rule(program)
            facts = item_facts(session, (ln.menu_item_id for ln in order.lines))
            units = sum(
                ln.qty
                for ln in order.lines
                if ln.menu_item_id in facts and item_matches(rule, facts[ln.menu_item_id])
            )
            if reward is not None:
                units -= 1  # a free drink earns no stamp
            units = max(0, min(units, program.max_stamps_per_scan))
        if units > 0:
            event = apply_units(
                session, card, units, note=f"online order {display_code(order.code)}", now=now
            )
            order.stamp_event_id = event.id
            unit_word = "point" if program.kind is ProgramKind.POINTS else "stamp"
            record_event(
                session,
                order,
                "stamped",
                f"+{units} {unit_word}{'' if units == 1 else 's'} on card {card.id[:8]}",
                actor="system",
                at=now,
            )
    session.flush()
    return order
