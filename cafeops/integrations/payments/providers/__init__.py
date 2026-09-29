"""Online payment providers for the shop (docs/shop/CONTRACT.md §3b).

The shop services import only `base` (the protocol and its frozen results) and
`registry` (which provider is chosen). A provider takes a hosted checkout, reads its
own webhook, refunds, and knows the customer at its end; it never touches a
`shop_order` -- `services/shop/payments.py` does that with what the provider returns.

Sits under `integrations/payments/` beside the (older, unrelated) payment REPORTS
package: that package already owns `base.py` -- takings by day and method -- so the
providers live in this subpackage rather than clobbering it.
"""

from cafeops.integrations.payments.providers.base import (
    Checkout,
    PaymentCustomer,
    PaymentEvent,
    PaymentProvider,
    PaymentProviderError,
    RefundResult,
    SavedMethod,
)

__all__ = [
    "Checkout",
    "PaymentCustomer",
    "PaymentEvent",
    "PaymentProvider",
    "PaymentProviderError",
    "RefundResult",
    "SavedMethod",
]
