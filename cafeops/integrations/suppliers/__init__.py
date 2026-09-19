"""Supplier order channels.

Importing this package registers every adapter, so `adapter_for(channel)` works
without the caller having to know which module to import first. It previously did
not: the registry is populated by the `@register` decorator in `channels`, so any
caller that imported only `base` got a `LookupError` for every channel.
"""

from cafeops.integrations.suppliers import channels as _channels  # noqa: F401
from cafeops.integrations.suppliers.base import (
    DispatchResult,
    OrderChannelAdapter,
    OrderItem,
    PreparedOrder,
    adapter_for,
    register,
    registered_channels,
)

__all__ = [
    "DispatchResult",
    "OrderChannelAdapter",
    "OrderItem",
    "PreparedOrder",
    "adapter_for",
    "register",
    "registered_channels",
]
