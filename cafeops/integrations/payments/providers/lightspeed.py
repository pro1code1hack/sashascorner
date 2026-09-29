"""Lightspeed as a *payment* provider -- and why it mostly is not one (CONTRACT §3b.2).

**Untested against the live API: no credentials here.** Written against the K-Series
API reference (https://api-docs.lsk.lightspeed.app/, read 2026-09-29), the same way
the loyalty POS mapping was, and to be confirmed on the first real call.

What the documented Order & Pay API offers a third party:

- `POST /o/op/1/order/toGo` / `.../order/local`: push an order into the POS, with an
  optional `payment {paymentMethod, paymentAmount, tipAmount}` that RECORDS a payment
  the third party has already taken. That is the POS sink's job
  (`integrations/pos/lightspeed.py`), not a checkout.
- `POST /o/op/1/pay`: apply a payment to an open account -- again a record of money
  taken elsewhere (`paymentMethod` is a merchant-configured code), with a
  `POST /onlinepaymentnotification` webhook reporting the outcome.
- `GET /f/v2/business-location/{id}/lightspeed-payments`: reporting on Lightspeed
  Payments settlements.

What it does NOT offer: a hosted checkout page, a payment link, a card-on-file API or
a customer endpoint. Lightspeed Payments' online card acceptance is delivered through
Lightspeed's own ordering products, not the public K-Series API. So:

- `configured()` is **False** whatever the environment holds: choosing "lightspeed" as
  the payment provider turns `pay.online` off rather than pretending. This is the
  honest answer, and the admin's provider list says why in `display_name`.
- `create_checkout` raises `PaymentProviderError` with the same explanation.
- `parse_webhook` reads the documented payment-notification shape
  (`thirdPartyPaymentReference`, `status`, `errors`) for the day the `/pay` path is
  used to record a counter card payment against a pushed order; the shop ignores
  events for references it did not create.
- `ensure_customer` returns the member's existing `lightspeed_customer_id` when the
  loyalty POS sync has linked one (there is no create-customer endpoint; a customer is
  created implicitly from `customerInfo` on the first pushed order), else raises.
- `saved_methods` is always empty: nothing is stored at Lightspeed for us to list.

If Lightspeed publishes a hosted payment for API integrators, this module is where it
goes; everything above it is already shaped for that.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from cafeops.db.models import LoyaltyMember, ShopOrder
from cafeops.integrations.payments.providers.base import (
    Checkout,
    PaymentCustomer,
    PaymentEvent,
    PaymentProviderError,
    RefundResult,
    SavedMethod,
)

__all__ = ["LightspeedPaymentProvider"]

_NO_CHECKOUT = (
    "Lightspeed K-Series exposes no hosted online payment to API integrators (only "
    "recording a payment taken elsewhere). Use Stripe for paying online, or Lightspeed as "
    "the POS sink so orders reach the till and are paid at the counter."
)


class LightspeedPaymentProvider:
    key = "lightspeed"
    display_name = "Lightspeed (no hosted checkout: record-only)"

    def configured(self) -> bool:
        return False

    def create_checkout(
        self,
        order: ShopOrder,
        customer: PaymentCustomer | None,
        *,
        success_url: str,
        cancel_url: str,
    ) -> Checkout:
        raise PaymentProviderError(_NO_CHECKOUT)

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentEvent:
        """The documented `onlinepaymentnotification` body. No signature scheme is
        documented, so the route only accepts it when a provider secret is set (see
        `services/shop/payments.py`); here the shape is read and nothing more."""
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise PaymentProviderError("Lightspeed payment notification is not JSON.") from exc
        ref = payload.get("thirdPartyPaymentReference") or payload.get("thirdPartyReference")
        status = str(payload.get("status", "")).upper()
        errors = payload.get("errors") or []
        amount = payload.get("paymentAmount")
        pence = round(float(amount) * 100) if isinstance(amount, int | float) else None
        if status in ("OK", "SUCCESS", "PAID", "COMPLETED") and not errors:
            return PaymentEvent("paid", str(ref) if ref else None, pence, None, "lightspeed")
        if status in ("FAIL", "FAILED", "ERROR") or errors:
            return PaymentEvent(
                "failed", str(ref) if ref else None, pence, None, "; ".join(map(str, errors))[:300]
            )
        return PaymentEvent("ignored", str(ref) if ref else None, pence, None, status or None)

    def refund(self, order: ShopOrder) -> RefundResult:
        return RefundResult(False, None, "Refund on the till: K-Series has no refund endpoint.")

    def ensure_customer(self, member: LoyaltyMember) -> str:
        if member.lightspeed_customer_id:
            return member.lightspeed_customer_id
        raise PaymentProviderError(
            "K-Series has no create-customer endpoint; the customer is created by the first "
            "pushed order's customerInfo and linked by the loyalty POS sync."
        )

    def saved_methods(self, provider_customer_id: str) -> list[SavedMethod]:
        return []
