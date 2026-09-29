"""Online payment for an order, through whichever provider is chosen (CONTRACT §3.6, §3b).

The provider (`integrations/payments/providers`) talks to Stripe or whoever; this
module owns what that means for a `shop_order`:

- `online_payment_offered`: `pay_online` is true only when the setting says so AND
  the chosen provider is configured (§3b.1). Without keys the API says `false`
  whatever the admin ticked.
- `start_checkout`: opens the hosted checkout for a PENDING_PAYMENT order and stores
  its reference. A signed-in member first gets a provider customer (`ensure_customer`,
  §3b.4) so the checkout can offer their saved cards.
- `handle_webhook`: verifies and reads the delivery, then -- once, by `payment_ref` --
  marks the order paid (PENDING_PAYMENT -> NEW), failed, or refunded. A second delivery
  of the same event is a no-op; an unknown reference is logged and ignored (200, so
  the provider stops retrying).
- `payment_summary`: `GET /api/shop/me`'s `payment` block: the provider and the
  member's saved methods, labels only.

Refunds are never automatic (§3.6): `refund_order` is the admin's button. It refunds a
PAID ONLINE order through the provider; a COUNTER payment is refused ("refund it from
the till") because the app never held that money.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    LoyaltyCard,
    LoyaltyMember,
    OrderStatus,
    PaymentStatus,
    ShopOrder,
    ShopPaymentCustomer,
    ShopPaymentMethod,
    ShopSettings,
)
from cafeops.domain.shop import display_code
from cafeops.integrations.payments.providers import registry
from cafeops.integrations.payments.providers.base import (
    PaymentCustomer,
    PaymentProvider,
    PaymentProviderError,
    RefundResult,
    SavedMethod,
)
from cafeops.services.shop.errors import ShopError
from cafeops.services.shop.orders import mark_paid, record_event, transition

__all__ = [
    "PaymentSummary",
    "WebhookOutcome",
    "chosen_provider",
    "ensure_customer",
    "handle_webhook",
    "online_payment_offered",
    "payment_summary",
    "refund_order",
    "refundable",
    "start_checkout",
]

log = logging.getLogger("cafeops.shop.payments")


@dataclass(frozen=True, slots=True)
class PaymentSummary:
    provider: str
    saved_methods: tuple[SavedMethod, ...]


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    kind: str
    order_id: int | None
    detail: str


def chosen_provider(shop: ShopSettings) -> PaymentProvider | None:
    return registry.provider(shop.payment_provider)


def online_payment_offered(shop: ShopSettings) -> bool:
    return bool(shop.pay_online and registry.active(shop.payment_provider) is not None)


def _checkout_urls(order: ShopOrder) -> tuple[str, str]:
    base = settings.shop_url
    return (
        f"{base}/order/status/{order.code}?t={order.access_token}&paid=1",
        f"{base}/order/checkout?resume={order.code}",
    )


def ensure_customer(
    session: Session,
    provider: PaymentProvider,
    member: LoyaltyMember,
    *,
    now: datetime | None = None,
) -> ShopPaymentCustomer:
    """The member's account at `provider`, created on first use (§3b.4)."""
    now = now or datetime.now(UTC)
    row = session.scalar(
        select(ShopPaymentCustomer).where(
            ShopPaymentCustomer.member_id == member.id,
            ShopPaymentCustomer.provider == provider.key,
        )
    )
    if row is not None:
        return row
    customer_id = provider.ensure_customer(member)
    row = ShopPaymentCustomer(
        member_id=member.id,
        provider=provider.key,
        provider_customer_id=customer_id[:120],
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def start_checkout(
    session: Session, order: ShopOrder, *, card: LoyaltyCard | None, now: datetime | None = None
) -> str:
    """Open the hosted checkout for a PENDING_PAYMENT order; returns the URL."""
    now = now or datetime.now(UTC)
    if order.payment_method is not ShopPaymentMethod.ONLINE:
        raise ShopError(409, "not_online", "This order is paid at the counter.")
    if order.status is not OrderStatus.PENDING_PAYMENT:
        raise ShopError(409, "bad_transition", "This order is no longer awaiting payment.")
    shop = session.get(ShopSettings, 1)
    provider = registry.active(shop.payment_provider if shop else None)
    if provider is None:
        raise ShopError(503, "payment_unavailable", "Paying online is not available right now.")

    customer: PaymentCustomer | None = None
    provider_customer_id: str | None = None
    if card is not None and card.voided_at is None:
        member = card.member
        try:
            provider_customer_id = ensure_customer(
                session, provider, member, now=now
            ).provider_customer_id
        except PaymentProviderError as exc:
            # A saved-card convenience, never a blocker: pay as a guest instead.
            log.warning("%s ensure_customer failed for member %s: %s", provider.key, member.id, exc)
    customer = PaymentCustomer(
        provider_customer_id=provider_customer_id,
        name=order.customer_name,
        email=order.customer_email,
        phone=order.customer_phone,
    )
    success_url, cancel_url = _checkout_urls(order)
    try:
        checkout = provider.create_checkout(
            order, customer, success_url=success_url, cancel_url=cancel_url
        )
    except PaymentProviderError as exc:
        record_event(session, order, "payment_error", str(exc), actor=provider.key, at=now)
        raise ShopError(
            503, "payment_unavailable", "Could not start the payment. Try again."
        ) from exc
    order.payment_ref = checkout.ref[:120]
    record_event(
        session,
        order,
        "checkout_opened",
        f"{provider.display_name} {checkout.ref}",
        actor=provider.key,
        at=now,
    )
    session.flush()
    return checkout.url


def handle_webhook(
    session: Session,
    provider_key: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    now: datetime | None = None,
) -> WebhookOutcome:
    """Read one delivery and apply it to the order it names, once."""
    now = now or datetime.now(UTC)
    provider = registry.provider(provider_key)
    if provider is None or provider.key != provider_key.strip().lower():
        raise ShopError(404, "unknown_provider", "No such payment provider.")
    if provider.key == "lightspeed" and not settings.lightspeed_configured:
        # No documented signature scheme: refuse rather than trust anything unsigned.
        raise ShopError(403, "bad_signature", "Lightspeed notifications are not enabled.")
    try:
        event = provider.parse_webhook(headers, body)
    except PaymentProviderError as exc:
        raise ShopError(400, "bad_signature", str(exc)) from exc
    if event.kind == "ignored" or not (event.ref or event.payment_intent):
        return WebhookOutcome("ignored", None, event.detail or "not for us")
    order = session.scalar(select(ShopOrder).where(ShopOrder.payment_ref == event.ref))
    if order is None and event.payment_intent:
        # `charge.refunded` names the payment, not the checkout session.
        order = session.scalar(
            select(ShopOrder).where(ShopOrder.payment_intent == event.payment_intent)
        )
    if order is None:
        log.info("%s webhook for unknown ref %s ignored", provider.key, event.ref)
        return WebhookOutcome("ignored", None, "unknown reference")
    if event.payment_intent and not order.payment_intent:
        order.payment_intent = event.payment_intent[:120]
    stamp = f"{provider.key}:{event.raw_id or event.kind}"
    already = any(
        e.kind in ("paid", "payment_failed", "refunded") and e.detail and stamp in e.detail
        for e in order.events
    )
    if already:
        return WebhookOutcome("duplicate", order.id, "already applied")

    if event.kind == "paid":
        if event.amount_pence is not None and event.amount_pence != order.total_pence:
            record_event(
                session,
                order,
                "payment_mismatch",
                f"{provider.key} reports £{event.amount_pence / 100:.2f}, order is "
                f"£{order.total_pence / 100:.2f} ({stamp})",
                actor=provider.key,
                at=now,
            )
        mark_paid(session, order, actor=provider.key, now=now, payment_ref=event.ref)
        # `mark_paid` wrote the "paid" event; stamp it so a redelivery is a no-op.
        for e in reversed(order.events):
            if e.kind == "paid":
                e.detail = f"{e.detail or ''} ({stamp})"[:400]
                break
        return WebhookOutcome("paid", order.id, "order is NEW and paid")
    if event.kind == "failed":
        if order.status is OrderStatus.PENDING_PAYMENT:
            order.payment_status = PaymentStatus.FAILED
            record_event(
                session,
                order,
                "payment_failed",
                f"{event.detail or 'failed'} ({stamp})",
                actor=provider.key,
                at=now,
            )
            transition(
                session,
                order,
                OrderStatus.CANCELLED,
                actor="system",
                reason="payment failed or expired",
                now=now,
            )
        return WebhookOutcome("failed", order.id, "order cancelled")
    if event.kind == "refunded":
        order.payment_status = PaymentStatus.REFUNDED
        record_event(
            session,
            order,
            "refunded",
            f"{provider.key} refund ({stamp})",
            actor=provider.key,
            at=now,
        )
        session.flush()
        return WebhookOutcome("refunded", order.id, "marked refunded")
    return WebhookOutcome("ignored", order.id, event.kind)


def refundable(order: ShopOrder) -> bool:
    """`OrderAdmin.refundable`: money the app took online and still holds."""
    return (
        order.payment_method is ShopPaymentMethod.ONLINE
        and order.payment_status is PaymentStatus.PAID
    )


def refund_order(
    session: Session,
    order: ShopOrder,
    *,
    actor: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> RefundResult:
    """A person's refund from the admin. Never called by a cancel (§3.6).

    Refuses (409) what cannot be refunded here: a COUNTER payment ("refund it from the
    till"), an order that is not PAID, a missing provider. A provider failure is NOT
    raised: it is written as a `refund_failed` event and returned as `ok=False` so the
    caller can commit the event and still answer 409 with the provider's sentence.
    """
    now = now or datetime.now(UTC)
    code = display_code(order.code)
    if order.payment_method is ShopPaymentMethod.COUNTER:
        raise ShopError(
            409,
            "not_refundable",
            f"{code} was paid at the counter; refund it from the till.",
        )
    if order.payment_status is PaymentStatus.REFUNDED:
        raise ShopError(409, "not_refundable", f"{code} has already been refunded.")
    if order.payment_status is not PaymentStatus.PAID:
        raise ShopError(409, "not_refundable", f"Nothing was paid online on {code}.")
    shop = session.get(ShopSettings, 1)
    provider = registry.provider(shop.payment_provider if shop else None)
    if provider is None:
        raise ShopError(409, "not_refundable", "No payment provider is configured.")
    result = provider.refund(order)
    why = f" ({reason.strip()})" if reason and reason.strip() else ""
    if result.ok:
        order.payment_status = PaymentStatus.REFUNDED
        pounds = f"£{order.total_pence / 100:.2f}"
        record_event(
            session,
            order,
            "refunded",
            f"{pounds} refunded with {provider.display_name}{why}: {result.ref or result.detail}",
            actor=actor,
            at=now,
        )
    else:
        record_event(
            session,
            order,
            "refund_failed",
            f"{provider.display_name}: {result.detail}{why}",
            actor=actor,
            at=now,
        )
    session.flush()
    return result


def payment_summary(session: Session, card: LoyaltyCard) -> PaymentSummary | None:
    shop = session.get(ShopSettings, 1)
    provider = registry.active(shop.payment_provider if shop else None)
    if provider is None or not (shop and shop.pay_online):
        return None
    row = session.scalar(
        select(ShopPaymentCustomer).where(
            ShopPaymentCustomer.member_id == card.member_id,
            ShopPaymentCustomer.provider == provider.key,
        )
    )
    methods: tuple[SavedMethod, ...] = ()
    if row is not None:
        try:
            methods = tuple(provider.saved_methods(row.provider_customer_id))
        except PaymentProviderError as exc:
            log.warning("saved methods: %s", exc)
    return PaymentSummary(provider=provider.key, saved_methods=methods)
