# ruff: noqa: RUF001, RUF002 -- Russian text, like the bot (pyproject per-file-ignores)
"""Telling people about an order (CONTRACT §3.7, §3c): the owner on Telegram, the
customer by email, Web Push and -- when they asked and the shop allows -- SMS.

Always called from a background task or thread after the request's commit, so a
Telegram or push outage never fails or slows a customer's order. Each notification
opens its own session; every attempt is an order event (`notified` / `notify_failed`)
so the status page and the admin can say what went out and what did not.

**Cost order** (§3c): email and push are free and on by default; SMS is ~4p a text and
goes only to customers who ticked "text me" (`shop_order.sms_opt_in`) and only for the
statuses `shop_settings.sms_notify` allows ("ready" by default -- one text per order).

The owner's message is Russian, like the bot:
"🛍 Новый онлайн-заказ SC-ABC123 · Имя · к 14:20 · с собой · 2 позиции · £9.10 · оплата на кассе".
It carries an inline keyboard (Принять · Готовим · Готов · Выдан · Отклонить) whose
callback data is `shop:<order_id>:<action>`; `cafeops/bot/shop_handlers.py` answers a
tap by calling `telegram_action` here, which is `orders.transition` plus the message
text and the still-allowed buttons for the edit. The keyboard is plain Bot API JSON on
purpose: this module sends over `httpx` with no aiogram `Bot`, and the handler
rebuilds an `InlineKeyboardMarkup` from the same dict.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.base import session_scope
from cafeops.db.models import (
    DiningOption,
    OrderStatus,
    ShopOrder,
    ShopPaymentMethod,
    ShopPushSubscription,
    ShopSettings,
)
from cafeops.domain.shop import TRANSITIONS, can_transition, display_code
from cafeops.domain.units import pounds
from cafeops.services.loyalty.messaging import send_email, send_sms
from cafeops.services.shop.errors import ShopError
from cafeops.services.shop.orders import load_order, record_event, transition
from cafeops.services.shop.push import push_configured, send_push

__all__ = [
    "CUSTOMER_KINDS",
    "TELEGRAM_ACTIONS",
    "CustomerMessage",
    "TelegramReply",
    "customer_message",
    "new_order_text",
    "notify_customer",
    "notify_customer_task",
    "notify_new_order",
    "notify_state",
    "order_keyboard",
    "order_message_text",
    "owner_payload",
    "send_owner",
    "telegram_action",
    "telegram_callback",
]

log = logging.getLogger("cafeops.shop.notify")
logging.getLogger("httpx").setLevel(logging.WARNING)

CUSTOMER_KINDS: tuple[str, ...] = ("accepted", "ready", "cancelled", "rejected", "delayed")
_CAFE = "Sasha's Corner, 23 Commercial Street"


# --------------------------------------------------------------------------
# the owner
# --------------------------------------------------------------------------


def _positions(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} позиция"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} позиции"
    return f"{n} позиций"


#: Button action (`shop:<order_id>:<action>`) -> the status it asks for.
TELEGRAM_ACTIONS: Mapping[str, OrderStatus] = {
    "accept": OrderStatus.ACCEPTED,
    "start": OrderStatus.PREPARING,
    "ready": OrderStatus.READY,
    "collected": OrderStatus.COLLECTED,
    "reject": OrderStatus.REJECTED,
}
_ACTION_LABELS: Mapping[str, str] = {
    "accept": "✅ Принять",
    "start": "👨‍🍳 Готовим",
    "ready": "🔔 Готов",
    "collected": "📦 Выдан",
    "reject": "✖️ Отклонить",
}
_STATUS_RU: Mapping[OrderStatus, str] = {
    OrderStatus.PENDING_PAYMENT: "ждёт оплаты",
    OrderStatus.NEW: "новый",
    OrderStatus.ACCEPTED: "принят",
    OrderStatus.PREPARING: "готовим",
    OrderStatus.READY: "готов к выдаче",
    OrderStatus.COLLECTED: "выдан",
    OrderStatus.CANCELLED: "отменён",
    OrderStatus.REJECTED: "отклонён",
}


def telegram_callback(order_id: int, action: str) -> str:
    return f"shop:{order_id}:{action}"


def order_keyboard(order: ShopOrder) -> dict[str, Any] | None:
    """Bot API `reply_markup` with only the moves allowed from the order's status
    (`domain/shop.TRANSITIONS`); None when there is nothing left to press."""
    allowed = TRANSITIONS.get(order.status.name, frozenset())
    progress = [
        {"text": _ACTION_LABELS[action], "callback_data": telegram_callback(order.id, action)}
        for action, target in TELEGRAM_ACTIONS.items()
        if action != "reject" and target.name in allowed
    ]
    rows = [progress] if progress else []
    if TELEGRAM_ACTIONS["reject"].name in allowed:
        rows.append(
            [
                {
                    "text": _ACTION_LABELS["reject"],
                    "callback_data": telegram_callback(order.id, "reject"),
                }
            ]
        )
    return {"inline_keyboard": rows} if rows else None


def order_message_text(
    order: ShopOrder, *, actor: str | None = None, at: datetime | None = None
) -> str:
    """The owner's message as it should read now: the order, then its status line
    once it has moved on from NEW."""
    text = new_order_text(order)
    if order.status is OrderStatus.NEW:
        return text
    line = f"Статус: {_STATUS_RU.get(order.status, order.status.value.lower())}"
    if at is not None:
        line += f" · {at.astimezone(settings.tz).strftime('%H:%M')}"
    if actor:
        line += f" · {actor.split(':', 1)[-1]}"
    return f"{text}\n\n{line}"


@dataclass(frozen=True, slots=True)
class TelegramReply:
    #: The edited message.
    text: str
    #: Bot API `reply_markup`, or None to drop the buttons.
    keyboard: dict[str, Any] | None
    #: The short toast for `callback.answer`.
    toast: str


def telegram_action(session: Session, order_id: int, action: str, *, actor: str) -> TelegramReply:
    """A button tap: `orders.transition` by `actor` ("telegram:<first name>"), then what
    the message should now say. Refusals are `ShopError`s in Russian for the toast --
    a stale keyboard (the order moved on from the web) is the common one."""
    target = TELEGRAM_ACTIONS.get(action)
    if target is None:
        raise ShopError(422, "unknown_action", "Неизвестная кнопка.")
    order = load_order(session, order_id)
    code = display_code(order.code)
    if not can_transition(order.status.value, target.value):
        raise ShopError(
            409,
            "bad_transition",
            f"{code} уже {_STATUS_RU.get(order.status, order.status.value.lower())}; "
            "кнопка устарела.",
        )
    now = datetime.now(UTC)
    transition(
        session,
        order,
        target,
        actor=actor,
        reason="из Telegram" if target is OrderStatus.REJECTED else None,
        now=now,
    )
    return TelegramReply(
        text=order_message_text(order, actor=actor, at=now),
        keyboard=order_keyboard(order),
        toast=f"{code}: {_STATUS_RU[order.status]}",
    )


def new_order_text(order: ShopOrder) -> str:
    when = order.requested_at.astimezone(settings.tz).strftime("%H:%M")
    dining = "с собой" if order.dining is DiningOption.TAKEAWAY else "в кафе"
    if order.dining is DiningOption.EAT_IN and order.table:
        dining += f", стол {order.table}"
    pay = (
        "оплата на кассе"
        if order.payment_method is ShopPaymentMethod.COUNTER
        else "оплачено онлайн"
    )
    head = (
        f"🛍 Новый онлайн-заказ {display_code(order.code)} · {order.customer_name} · к {when} · "
        f"{dining} · {_positions(len(order.lines))} · {pounds(order.total_pence)} · {pay}"
    )
    lines = []
    for ln in order.lines:
        opts = ", ".join(str(o.get("name", "")) for o in ln.options if isinstance(o, dict))
        size = f" ({ln.size_label})" if ln.size_label else ""
        lines.append(f"• {ln.qty} × {ln.name}{size}" + (f" — {opts}" if opts else ""))
    text = head + ("\n" + "\n".join(lines) if lines else "")
    if order.note:
        text += f"\nЗаметка: {order.note}"
    if order.discount_pence:
        text += f"\nБесплатный напиток по карте: −{pounds(order.discount_pence)}"
    return text


def owner_payload(text: str, reply_markup: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The `sendMessage` body (no token in it), so it can be logged and read."""
    payload: dict[str, Any] = {
        "chat_id": settings.telegram_owner_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = dict(reply_markup)
    return payload


def send_owner(text: str, *, reply_markup: Mapping[str, Any] | None = None) -> bool:
    """Telegram Bot API `sendMessage` to the owner, with an inline keyboard when given.
    False (and a log line) on any failure."""
    payload = owner_payload(text, reply_markup)
    log.debug("telegram sendMessage payload: %s", json.dumps(payload, ensure_ascii=False))
    if not settings.telegram_configured:
        log.info("telegram not configured; skipped: %s", text.splitlines()[0])
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        resp = httpx.post(url, json=payload, timeout=5.0)
    except httpx.HTTPError as exc:
        # Never log the URL: it carries the bot token.
        log.error("telegram notify failed: %s", type(exc).__name__)
        return False
    if resp.status_code != 200:
        log.error("telegram notify failed: HTTP %s %s", resp.status_code, resp.text[:200])
        return False
    return True


def notify_new_order(order_id: int) -> None:
    """Background task: tell the owner. Own session; never raises."""
    try:
        with session_scope() as session:
            order = session.get(ShopOrder, order_id)
            shop = session.get(ShopSettings, 1)
            if order is None or shop is None or not shop.notify_telegram:
                return
            if send_owner(new_order_text(order), reply_markup=order_keyboard(order)):
                record_event(
                    session,
                    order,
                    "notified",
                    "owner told on Telegram",
                    actor="system",
                    at=datetime.now(UTC),
                )
    except Exception as exc:
        log.warning("notify_new_order(%s) failed: %s", order_id, exc)


# --------------------------------------------------------------------------
# the customer (§3c)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CustomerMessage:
    title: str
    body: str
    url: str


def customer_message(order: ShopOrder, kind: str) -> CustomerMessage:
    code = display_code(order.code)
    when = order.requested_at.astimezone(settings.tz).strftime("%H:%M")
    url = f"{settings.shop_url}/order/status/{order.code}?t={order.access_token}"
    reason = f" ({order.cancel_reason})" if order.cancel_reason else ""
    if kind == "accepted":
        return CustomerMessage(
            f"We've got your order {code}",
            f"We've got your order {code} — ready around {when} at {_CAFE}.",
            url,
        )
    if kind == "ready":
        return CustomerMessage(
            f"Your order {code} is ready",
            f"Your order {code} is ready to collect at {_CAFE}. Say your name or show the code.",
            url,
        )
    if kind == "cancelled":
        return CustomerMessage(
            f"Your order {code} was cancelled",
            f"Sorry — your order {code} has been cancelled{reason}. "
            "Any online payment will be refunded.",
            url,
        )
    if kind == "rejected":
        return CustomerMessage(
            f"We can't take order {code}",
            f"Sorry — we can't take your order {code} right now{reason}. "
            "Any online payment will be refunded.",
            url,
        )
    if kind == "delayed":
        return CustomerMessage(
            f"Your order {code} is running late",
            f"Your order {code} is taking a little longer than planned. "
            "We'll tell you the moment it's ready.",
            url,
        )
    raise ValueError(f"unknown customer notice kind {kind!r}")


def _sms_allowed(shop: ShopSettings, kind: str) -> bool:
    mode = (shop.sms_notify or "off").lower()
    return mode == "all" or (mode == "ready" and kind == "ready")


def _live_subscriptions(session: Session, order: ShopOrder) -> list[ShopPushSubscription]:
    return list(
        session.scalars(
            select(ShopPushSubscription).where(
                ShopPushSubscription.order_id == order.id,
                ShopPushSubscription.expired_at.is_(None),
            )
        )
    )


def notify_state(session: Session, shop: ShopSettings, order: ShopOrder) -> dict[str, bool]:
    """`GET /api/shop/orders/{code}`'s `notify` block: which channels are on for it."""
    return {
        "email": bool(shop.email_notify and order.customer_email and settings.smtp_configured),
        "push_subscribed": bool(
            shop.push_notify and push_configured() and _live_subscriptions(session, order)
        ),
        "sms": bool(
            order.sms_opt_in
            and order.customer_phone
            and settings.twilio_configured
            and (shop.sms_notify or "off").lower() != "off"
        ),
    }


def notify_customer(session: Session, order: ShopOrder, kind: str) -> list[str]:
    """Send `kind` on every channel that applies; one event row per attempt.

    Returns the event details written, for logs and the report. Never raises for a
    delivery failure -- that is what `notify_failed` is for.
    """
    if kind not in CUSTOMER_KINDS:
        raise ValueError(f"unknown customer notice kind {kind!r}")
    shop = session.get(ShopSettings, 1)
    if shop is None:
        return []
    now = datetime.now(UTC)
    message = customer_message(order, kind)
    written: list[str] = []

    def ok(detail: str) -> None:
        record_event(session, order, "notified", detail, actor="system", at=now)
        written.append(detail)

    def failed(detail: str) -> None:
        record_event(session, order, "notify_failed", detail, actor="system", at=now)
        written.append(detail)

    # email: free, every kind
    if shop.email_notify and order.customer_email:
        if not settings.smtp_configured:
            failed(f"email: {kind} not sent, SMTP is not configured")
        else:
            try:
                send_email(order.customer_email, message.title, f"{message.body}\n\n{message.url}")
                ok(f"email: {kind}")
            except Exception as exc:
                failed(f"email: {kind} failed, {type(exc).__name__}")

    # push: free, every kind, every live subscription of this order
    if shop.push_notify:
        subs = _live_subscriptions(session, order)
        if subs and not push_configured():
            failed(f"push: {kind} not sent, VAPID keys are not configured")
        elif subs:
            payload = {
                "title": message.title,
                "body": message.body,
                "url": message.url,
                "code": display_code(order.code),
                "status": order.status.value,
            }
            sent = 0
            reasons: list[str] = []
            for sub in subs:
                outcome = send_push(
                    endpoint=sub.endpoint, p256dh=sub.p256dh, auth=sub.auth, payload=payload
                )
                if outcome.ok:
                    sent += 1
                    continue
                if outcome.expired:
                    sub.expired_at = now
                reasons.append(outcome.detail)
            if sent:
                ok(f"push: {kind} ({sent} device{'' if sent == 1 else 's'})")
            if reasons:
                failed(f"push: {kind} failed for {len(reasons)}: {'; '.join(reasons)[:300]}")

    # sms: paid, opted in, allowed for this kind
    if order.sms_opt_in and order.customer_phone and _sms_allowed(shop, kind):
        if not settings.twilio_configured:
            failed(f"sms: {kind} not sent, Twilio is not configured")
        else:
            try:
                send_sms(order.customer_phone, f"{message.body} {message.url}")
                ok(f"sms: {kind}")
            except Exception as exc:
                failed(f"sms: {kind} failed, {type(exc).__name__}")
    session.flush()
    return written


def notify_customer_task(order_id: int, kind: str) -> None:
    """Thread/background entry: own session, never raises."""
    try:
        with session_scope() as session:
            order = session.get(ShopOrder, order_id)
            if order is None:
                return
            details = notify_customer(session, order, kind)
            if details:
                log.info("order %s %s: %s", order.code, kind, "; ".join(details))
    except Exception as exc:
        log.warning("notify_customer_task(%s, %s) failed: %s", order_id, kind, exc)
