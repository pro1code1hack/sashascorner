"""What the three loyalty routers share: errors, rate limits, and the after-commit kicks.

- **Errors** are `{"error": code, "detail": sentence}` (CONTRACT §4). `LoyaltyError` carries
  both; `loyalty_error_handler` renders it. Pydantic's own 422 is re-shaped the same way
  for loyalty paths only (`validation_error_handler`), so the customer pages and the
  scanner switch on one field everywhere, while the back office's existing 422 shape is
  untouched.
- **After commit**, a route that changed a card kicks the wallet drain (`kick_wallets`)
  and, for a stamp, the alert sender (`send_pending_alerts`), as FastAPI background tasks:
  the response has already gone, so the till never waits on Apple, Google or Telegram.
  Both swallow every error -- the scheduler retries what they miss.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from cafeops.services.auth import RateLimiter
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.messaging import Outgoing, deliver_all
from cafeops.services.loyalty.wallets import wallet_module

__all__ = [
    "LOYALTY_PREFIXES",
    "deliver_after_commit",
    "err",
    "is_wallet_not_configured",
    "kick_wallets",
    "limit",
    "loyalty_error_handler",
    "send_pending_alerts",
    "validation_error_handler",
    "wallet_not_configured_response",
]

log = logging.getLogger("cafeops.loyalty")

LOYALTY_PREFIXES = ("/api/loyalty", "/api/staff", "/api/members", "/api/shop")


def err(status: int, code: str, detail: str) -> LoyaltyError:
    return LoyaltyError(status, code, detail)


async def loyalty_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, LoyaltyError)
    headers = None
    if exc.status == 429:
        # The detail says how long; the header is for clients that read it.
        digits = "".join(ch for ch in exc.detail if ch.isdigit())
        headers = {"Retry-After": digits or "60"}
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": exc.detail},
        headers=headers,
    )


async def validation_error_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    if not request.url.path.startswith(LOYALTY_PREFIXES):
        return await request_validation_exception_handler(request, exc)
    errors = exc.errors()
    first = errors[0] if errors else {}
    where = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body", "query"))
    message = str(first.get("msg", "invalid request"))
    return JSONResponse(
        status_code=422,
        content={
            "error": "invalid_request",
            "detail": f"{where}: {message}" if where else message,
            "fields": jsonable_encoder(errors),
        },
    )


def is_wallet_not_configured(exc: BaseException) -> bool:
    """By name: the class lives in a module that may not import (agent B's)."""
    return any(cls.__name__ == "WalletNotConfigured" for cls in type(exc).__mro__)


def wallet_not_configured_response(
    _request: Request | None = None, exc: Any = None
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "wallet_not_configured",
            "detail": str(exc) if exc else "This wallet is not set up yet. Use the web card.",
        },
    )


# --------------------------------------------------------------------------
# rate limits (in-process, per client IP; see services/auth.py for why)
# --------------------------------------------------------------------------

_LIMITERS: dict[str, RateLimiter] = {
    "join": RateLimiter(per_minute=5, per_hour=30),
    "recover": RateLimiter(per_minute=3, per_hour=12),
    "verify": RateLimiter(per_minute=6, per_hour=30),
}


def limit(kind: str, client_ip: str) -> None:
    """Count this attempt and refuse with 429 if the window is full.

    Every attempt counts, not only failures: joining and asking for a code are the actions
    being limited, and a successful spammer is still a spammer.
    """
    limiter = _LIMITERS[kind]
    wait = limiter.retry_after(client_ip)
    if wait is not None:
        raise LoyaltyError(429, "rate_limited", f"Too many tries. Wait {wait} seconds.")
    limiter.record_failure(client_ip)


# --------------------------------------------------------------------------
# after-commit work
# --------------------------------------------------------------------------


async def kick_wallets() -> None:
    """Drain due wallet outbox rows now. Never raises (CONTRACT §3: B's `kick`)."""
    sync = wallet_module("sync")
    kick: Callable[[], Any] | None = getattr(sync, "kick", None) if sync else None
    if kick is None:
        return
    try:
        await kick()
    except Exception as exc:  # the scheduler's 1-minute drain retries
        log.warning("wallet kick failed: %s", exc)


async def send_pending_alerts() -> None:
    try:
        from cafeops.jobs.loyalty_jobs import flush_alerts

        await flush_alerts()
    except Exception as exc:
        log.warning("loyalty alert send failed: %s", exc)


async def deliver_after_commit(items: list[Outgoing]) -> None:
    if items:
        await asyncio.to_thread(deliver_all, items)
