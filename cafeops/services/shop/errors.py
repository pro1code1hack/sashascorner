"""The shop's one refusal type: a `LoyaltyError` that may carry a payload.

`{"error": code, "detail": sentence}` like the loyalty API (CONTRACT §3.10). `extra`
is merged into the body: 409 `price_changed` carries the freshly priced basket so a
stale tab can show the right total instead of a bare refusal.
"""

from __future__ import annotations

from typing import Any

from cafeops.services.loyalty.errors import LoyaltyError

__all__ = ["ShopError"]


class ShopError(LoyaltyError):
    def __init__(
        self, status: int, code: str, detail: str, *, extra: dict[str, Any] | None = None
    ) -> None:
        super().__init__(status, code, detail)
        self.extra: dict[str, Any] = extra or {}
