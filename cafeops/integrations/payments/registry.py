"""Re-export of `integrations/payments/providers/registry` (CONTRACT §3b names this
path; the providers live one level down because `payments/base.py` was already the
payment-REPORTS protocol)."""

from cafeops.integrations.payments.providers.registry import (
    DEFAULT_PROVIDER_KEY,
    ProviderInfo,
    active,
    available,
    keys,
    provider,
)

__all__ = ["DEFAULT_PROVIDER_KEY", "ProviderInfo", "active", "available", "keys", "provider"]
