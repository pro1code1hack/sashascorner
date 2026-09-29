"""Stripe Checkout over plain `httpx` -- no SDK (CONTRACT §3.6, §3b).

- `create_checkout`: `POST /v1/checkout/sessions`, basic auth on the secret key, one
  line item per order line (unit amount = the line's unit price in pence, GBP), the
  order code in `client_reference_id` and `metadata`, `success_url` / `cancel_url`
  from the caller. With a Stripe customer id the session is opened on that customer
  so Checkout can offer their saved cards.
- `parse_webhook`: Stripe's `t=…,v1=…` scheme -- HMAC-SHA256 of `"{t}.{body}"` with the
  webhook secret, constant-time compare, 5-minute tolerance. `checkout.session.completed`
  with `payment_status == "paid"` is a `paid` event; `checkout.session.expired` is
  `failed`; `charge.refunded` is `refunded`; everything else `ignored`.
- `refund`: `POST /v1/refunds` with the order's `payment_intent` (stored at webhook
  time; looked up from the checkout session when an older order lacks it). Called by a
  person from the admin; cancelling an order never refunds by itself (§3.6).
- `ensure_customer` / `saved_methods`: `POST /v1/customers` (idempotent on the member
  id) and `GET /v1/customers/{id}/payment_methods?type=card`, labels only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from cafeops.config import settings
from cafeops.db.models import LoyaltyMember, ShopOrder
from cafeops.integrations.payments.providers.base import (
    Checkout,
    PaymentCustomer,
    PaymentEvent,
    PaymentProviderError,
    RefundResult,
    SavedMethod,
)

__all__ = ["StripeProvider", "verify_stripe_signature"]

log = logging.getLogger("cafeops.payments.stripe")
# httpx logs request URLs at INFO; keep the API quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://api.stripe.com"
SIGNATURE_TOLERANCE_SECONDS = 300
_TIMEOUT = 10.0


def verify_stripe_signature(
    body: bytes, header: str | None, secret: str, *, now: float | None = None
) -> bool:
    """Stripe's `Stripe-Signature: t=<ts>,v1=<hex>[,v1=<hex>]` check."""
    if not header or not secret:
        return False
    parts: dict[str, list[str]] = {}
    for piece in header.split(","):
        key, _, value = piece.strip().partition("=")
        parts.setdefault(key, []).append(value)
    ts = parts.get("t", [""])[0]
    if not ts.isdigit():
        return False
    if abs((now or time.time()) - int(ts)) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{ts}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", []))


def _flatten(prefix: str, value: Any, out: dict[str, str]) -> None:
    """Stripe's form encoding: `line_items[0][price_data][currency]=gbp`."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]" if prefix else str(k), v, out)
    elif isinstance(value, list | tuple):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif isinstance(value, bool):
        out[prefix] = "true" if value else "false"
    elif value is not None:
        out[prefix] = str(value)


class StripeProvider:
    key = "stripe"
    display_name = "Stripe"

    def configured(self) -> bool:
        return bool(settings.stripe_secret_key)

    def _post(
        self, path: str, form: Mapping[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        if not settings.stripe_secret_key:
            raise PaymentProviderError("Stripe is not configured (CAFEOPS_STRIPE_SECRET_KEY).")
        flat: dict[str, str] = {}
        _flatten("", form, flat)
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        try:
            response = httpx.post(
                f"{API}{path}",
                data=flat,
                auth=(settings.stripe_secret_key, ""),
                headers=headers,
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"Stripe unreachable: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            message = (
                response.json().get("error", {}).get("message", response.text[:200])
                if response.headers.get("content-type", "").startswith("application/json")
                else response.text[:200]
            )
            raise PaymentProviderError(f"Stripe refused {path}: {message}")
        return dict(response.json())

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        if not settings.stripe_secret_key:
            raise PaymentProviderError("Stripe is not configured (CAFEOPS_STRIPE_SECRET_KEY).")
        try:
            response = httpx.get(
                f"{API}{path}",
                params=params,
                auth=(settings.stripe_secret_key, ""),
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"Stripe unreachable: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise PaymentProviderError(f"Stripe refused {path}: HTTP {response.status_code}")
        return dict(response.json())

    def create_checkout(
        self,
        order: ShopOrder,
        customer: PaymentCustomer | None,
        *,
        success_url: str,
        cancel_url: str,
    ) -> Checkout:
        line_items: list[dict[str, Any]] = []
        for ln in order.lines:
            name = f"{ln.name} ({ln.size_label})" if ln.size_label else ln.name
            line_items.append(
                {
                    "quantity": ln.qty,
                    "price_data": {
                        "currency": "gbp",
                        "unit_amount": ln.unit_price_pence,
                        "product_data": {"name": name[:120]},
                    },
                }
            )
        form: dict[str, Any] = {
            "mode": "payment",
            "client_reference_id": order.code,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items": line_items,
            "metadata": {"order_code": order.code},
            "payment_intent_data": {"description": f"Sasha's Corner order SC-{order.code}"},
            # Stripe's minimum is 30 minutes, which is also when `expire_pending_payments`
            # cancels an unpaid order: the checkout page dies with the order, so a late
            # payment cannot land on a cancelled one.
            "expires_at": int(time.time()) + 30 * 60,
        }
        if order.discount_pence > 0:
            # One-off coupon for the free drink, so the customer pays what the order says.
            coupon = self._post(
                "/v1/coupons",
                {
                    "amount_off": order.discount_pence,
                    "currency": "gbp",
                    "duration": "once",
                    "name": "Rewards free drink",
                },
                idempotency_key=f"coupon-{order.code}",
            )
            form["discounts"] = [{"coupon": coupon["id"]}]
        if customer is not None and customer.provider_customer_id:
            form["customer"] = customer.provider_customer_id
        elif customer is not None and customer.email:
            form["customer_email"] = customer.email
        session = self._post(
            "/v1/checkout/sessions",
            form,
            idempotency_key=f"checkout-{order.code}-{order.updated_at.timestamp():.0f}",
        )
        url = session.get("url")
        ref = session.get("id")
        if not isinstance(url, str) or not isinstance(ref, str):
            raise PaymentProviderError("Stripe returned a session without a URL.")
        return Checkout(url=url, ref=ref)

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentEvent:
        secret = settings.stripe_webhook_secret or ""
        header = next((v for k, v in headers.items() if k.lower() == "stripe-signature"), None)
        if not verify_stripe_signature(body, header, secret):
            raise PaymentProviderError("Stripe signature did not verify.")
        try:
            event = json.loads(body)
        except ValueError as exc:
            raise PaymentProviderError("Stripe webhook body is not JSON.") from exc
        kind = str(event.get("type", ""))
        obj = dict(event.get("data", {}).get("object", {}))
        raw_id = str(event.get("id", "")) or None
        if kind == "checkout.session.completed":
            paid = obj.get("payment_status") == "paid"
            amount = obj.get("amount_total")
            intent = obj.get("payment_intent")
            return PaymentEvent(
                kind="paid" if paid else "ignored",
                ref=str(obj.get("id", "")) or None,
                amount_pence=int(amount) if isinstance(amount, int) else None,
                raw_id=raw_id,
                detail=None if paid else f"payment_status={obj.get('payment_status')}",
                payment_intent=intent if isinstance(intent, str) and intent else None,
            )
        if kind in ("checkout.session.expired", "checkout.session.async_payment_failed"):
            return PaymentEvent("failed", str(obj.get("id", "")) or None, None, raw_id, kind)
        if kind == "charge.refunded":
            amount = obj.get("amount_refunded")
            intent = str(obj.get("payment_intent", "")) or None
            return PaymentEvent(
                "refunded",
                None,
                int(amount) if isinstance(amount, int) else None,
                raw_id,
                intent,
                payment_intent=intent,
            )
        return PaymentEvent("ignored", None, None, raw_id, kind)

    def refund(self, order: ShopOrder) -> RefundResult:
        """`POST /v1/refunds {payment_intent}` -- the whole payment, once per order
        (idempotency key on the code). The intent comes from the order when the webhook
        stored it, else from the checkout session."""
        if not order.payment_ref and not order.payment_intent:
            return RefundResult(False, None, "No Stripe session on this order.")
        try:
            intent: object = order.payment_intent
            if not intent:
                session = self._get(f"/v1/checkout/sessions/{order.payment_ref}")
                intent = session.get("payment_intent")
            if not isinstance(intent, str) or not intent:
                return RefundResult(False, None, "The Stripe session has no payment to refund.")
            refund = self._post(
                "/v1/refunds", {"payment_intent": intent}, idempotency_key=f"refund-{order.code}"
            )
        except PaymentProviderError as exc:
            return RefundResult(False, None, str(exc))
        ref = str(refund.get("id", "")) or None
        amount = refund.get("amount")
        pounds = f" £{int(amount) / 100:.2f}" if isinstance(amount, int) else ""
        return RefundResult(True, ref, f"Refunded{pounds} in Stripe.")

    def ensure_customer(self, member: LoyaltyMember) -> str:
        created = self._post(
            "/v1/customers",
            {
                "name": member.first_name,
                "email": member.email,
                "phone": member.phone,
                "metadata": {"loyalty_member_id": member.id},
            },
            idempotency_key=f"customer-member-{member.id}",
        )
        cid = created.get("id")
        if not isinstance(cid, str):
            raise PaymentProviderError("Stripe returned a customer without an id.")
        return cid

    def saved_methods(self, provider_customer_id: str) -> list[SavedMethod]:
        try:
            listing = self._get(
                f"/v1/customers/{provider_customer_id}/payment_methods", {"type": "card"}
            )
            customer = self._get(f"/v1/customers/{provider_customer_id}")
        except PaymentProviderError as exc:
            log.warning("stripe saved methods: %s", exc)
            return []
        default = (customer.get("invoice_settings") or {}).get("default_payment_method")
        out: list[SavedMethod] = []
        for pm in listing.get("data", []):
            card = pm.get("card") or {}
            brand = str(card.get("brand", "card")).title()
            last4 = str(card.get("last4", "????"))
            out.append(
                SavedMethod(
                    label=f"{brand} •••• {last4}",
                    ref=str(pm.get("id", "")),
                    is_default=pm.get("id") == default,
                )
            )
        return out
