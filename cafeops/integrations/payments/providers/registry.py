"""Which payment provider the shop uses (CONTRACT §3b.1).

Two things decide it, and both must agree: `shop_settings.payment_provider` (the
owner's choice in the back office) and the environment (`configured()` -- the keys are
in `.env`). `provider(key)` returns the chosen adapter whether or not it is
configured, so the admin can show "Stripe: keys missing"; `active(key)` returns it
only when a checkout could actually be created, which is what `pay.online` reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from cafeops.integrations.payments.providers.base import PaymentProvider
from cafeops.integrations.payments.providers.lightspeed import LightspeedPaymentProvider
from cafeops.integrations.payments.providers.stripe import StripeProvider

__all__ = ["DEFAULT_PROVIDER_KEY", "ProviderInfo", "active", "available", "keys", "provider"]

DEFAULT_PROVIDER_KEY = "stripe"

_PROVIDERS: Mapping[str, PaymentProvider] = {
    "stripe": StripeProvider(),
    "lightspeed": LightspeedPaymentProvider(),
}


class ProviderInfo(TypedDict):
    key: str
    display_name: str
    configured: bool


def keys() -> tuple[str, ...]:
    return tuple(_PROVIDERS)


def provider(key: str | None) -> PaymentProvider | None:
    """The adapter for `key`, configured or not; None for an unknown key."""
    return _PROVIDERS.get((key or DEFAULT_PROVIDER_KEY).strip().lower())


def active(key: str | None) -> PaymentProvider | None:
    """The adapter for `key` only when it can take a payment right now."""
    chosen = provider(key)
    return chosen if chosen is not None and chosen.configured() else None


def available() -> list[ProviderInfo]:
    """For the admin: every registered provider and whether its keys are in place."""
    return [
        {"key": p.key, "display_name": p.display_name, "configured": p.configured()}
        for p in _PROVIDERS.values()
    ]
