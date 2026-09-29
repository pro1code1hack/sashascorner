"""The payment-provider protocol and the frozen results it hands back (CONTRACT §3b).

A provider is a thin adapter over one payment service. The rule that keeps the shop
honest: **a provider reports, it never decides.** `parse_webhook` says what the
service said; whether that moves an order is `services/shop/payments.py`'s call, and
it is made once, in one place, against the order's own state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from cafeops.db.models import LoyaltyMember, ShopOrder

__all__ = [
    "Checkout",
    "PaymentCustomer",
    "PaymentEvent",
    "PaymentEventKind",
    "PaymentProvider",
    "PaymentProviderError",
    "RefundResult",
    "SavedMethod",
]

PaymentEventKind = Literal["paid", "failed", "refunded", "ignored"]


class PaymentProviderError(RuntimeError):
    """The provider could not do what was asked (network, refusal, not configured).
    Never raised for "the customer's card was declined" -- that is an event."""


@dataclass(frozen=True, slots=True)
class PaymentCustomer:
    """What a provider may know about the payer. Built from the member; never stored."""

    provider_customer_id: str | None
    name: str
    email: str | None
    phone: str | None


@dataclass(frozen=True, slots=True)
class Checkout:
    #: Where to send the customer.
    url: str
    #: The provider's reference for this attempt (`shop_order.payment_ref`).
    ref: str


@dataclass(frozen=True, slots=True)
class PaymentEvent:
    kind: PaymentEventKind
    #: The reference the checkout was created with, so the order can be found.
    ref: str | None
    amount_pence: int | None
    #: The provider's own event id, for idempotency and the event row.
    raw_id: str | None
    detail: str | None = None
    #: The provider's payment id (Stripe `payment_intent`), when the event carries one:
    #: stored on the order at webhook time so a refund needs no second lookup.
    payment_intent: str | None = None


@dataclass(frozen=True, slots=True)
class RefundResult:
    ok: bool
    ref: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class SavedMethod:
    #: "Visa ---- 4242".
    label: str
    ref: str
    is_default: bool


@runtime_checkable
class PaymentProvider(Protocol):
    key: str
    display_name: str

    def configured(self) -> bool:
        """True only when a live checkout can be created right now."""
        ...

    def create_checkout(
        self,
        order: ShopOrder,
        customer: PaymentCustomer | None,
        *,
        success_url: str,
        cancel_url: str,
    ) -> Checkout: ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentEvent:
        """Verify and read one webhook delivery. Raises `PaymentProviderError` on a bad
        signature; an event the shop does not act on comes back as `kind="ignored"`."""
        ...

    def refund(self, order: ShopOrder) -> RefundResult: ...

    def ensure_customer(self, member: LoyaltyMember) -> str:
        """The provider's customer id for this member, created if missing."""
        ...

    def saved_methods(self, provider_customer_id: str) -> list[SavedMethod]: ...
