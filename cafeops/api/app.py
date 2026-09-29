"""The FastAPI application. One box, one process, behind Caddy (spec 3).

No load balancer, no worker pool, no second instance: 40 transactions a day, two users,
one SQLite file with a single writer, and a second app instance would contend on the same
file and make things worse (ARCHITECTURE 8F.8). `uvicorn` with one worker is correct here
and is what `cafeops serve` starts.

**One place translates refusals.** Services refuse by raising; `_translate` is the single
table from exception to `(status, error, detail)`, registered as the app-wide handler for
every refusal type, so a view never catches a service error only to re-raise it as an
`HTTPException`. The body is `{"detail": sentence, "error": kind}`; a `LoyaltyError` /
`ShopError` supplies its own `error` code and may add fields (the fresh quote on 409
`price_changed`). Every mapped 4xx is logged at INFO with the route.

| exception | status | error |
|---|---|---|
| `ComponentSupersededError` | 409 | `component_superseded` (see `api/errors.py`) |
| `RetroactiveEditError` | 409 | `retroactive_edit` -- invariant 3, history is never rewritten |
| `SubstitutionError` | 409 | `substitution_refused` -- spec 4.3, names the slot |
| `BrowserJobRefused` | 409 | `browser_job_refused` |
| `FinanceConflict` | 409 | `conflict` -- a duplicate date, a mirrored field |
| `ProposalConflict` | 409 | `proposal_conflict` -- already decided |
| `AutoOrderGrantRefused` | 403 | `auto_order_grant_refused` -- invariant 2 |
| `SyncRefused` | its `status` | `sync_refused` |
| `PasswordChangeRefused` | its `status` | `password_change_refused` |
| `ShopAdminRefused` | its `status` | `refused` |
| `LoyaltyError`, `ShopError` | their `status` | their `code` (429 adds `Retry-After`) |
| `LookupError` | 404 | `not_found` -- what services raise for a missing row |
| `ValueError` (incl. `FinanceRefused`) | 422 | `invalid_request` |
| `KeyError`, `IndexError`, pydantic `ValidationError` | **500** | `internal` |

The last row is deliberate. `KeyError` and `IndexError` are `LookupError`s and a pydantic
`ValidationError` is a `ValueError`, but no service raises them to refuse: they are bugs
(a missing dict key, a response model built wrong), and answering 404/422 with the key's
repr as the body hid them. They are logged with the traceback and answer a bare 500.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from cafeops.api import areas
from cafeops.api.areas.loyalty_edge import (
    validation_error_handler,
    wallet_not_configured_response,
)
from cafeops.api.errors import ComponentSupersededError
from cafeops.api.params import HTTP_422
from cafeops.api.routers import open_router, router
from cafeops.db.repositories.par import AutoOrderGrantRefused
from cafeops.domain.types import SubstitutionError
from cafeops.services.agent_proposals import ProposalConflict
from cafeops.services.auth import PasswordChangeRefused
from cafeops.services.browser_jobs import BrowserJobRefused
from cafeops.services.edit_composition import RetroactiveEditError
from cafeops.services.finance.common import FinanceConflict
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.shop.admin import ShopAdminRefused
from cafeops.services.shop.errors import ShopError
from cafeops.services.sync_runs import SyncRefused

__all__ = ["HTTP_422", "create_app"]

log = logging.getLogger("cafeops.api")

DESCRIPTION = """
Back-office API for Sasha's Corner.

**Writes go through services.** Stock counts, deliveries, write-offs, supplier and
recipe edits, finance entries, orders and agent-proposal decisions are all
POST/PUT/PATCH/DELETE routes in `cafeops/api/areas/`, each delegating to one service
that enforces the invariants. Recipe and price edits are preview-then-apply and
effective-dated. A purchase order is created as a DRAFT from today's ordering run
(`POST /api/orders/from-draft`) and confirmed only by a named person
(`POST /api/orders/{id}/confirm`) -- invariant 1: nothing is ordered without a human.
Nothing is ever *sent* to a supplier by this API; "mark sent" records that a person did.

**Encoding.** Money is integer pence where the database stores an integer, and an exact
decimal *string* of pence where the figure is derived and fractional. Quantities are
always strings. A missing cost is `null`, never `0`. A low-confidence forecast has
`qty: null` and its reasons populated -- render the reasons *in place of* the number,
because the number is not in the payload. See `GET /api/meta`.

**Errors.** A refusal is `{"detail": sentence, "error": kind}` with the status that
fits it (404 missing, 409 conflict, 422 invalid). An unexpected failure is a 500.

**Auth.** One shared password (spec 1: no user management). `POST /api/auth/session`
exchanges it for a session token, sent as `Authorization: Bearer <token>`; the raw
password still works as `X-API-Key` (CLI, fixtures). A password changed in Settings is
stored hashed and wins over `CAFEOPS_API_PASSWORD`. With neither set every route but
`/api/health` and sign-in answers 503.
"""


@dataclass(frozen=True, slots=True)
class Translated:
    status: int
    error: str
    detail: str
    headers: dict[str, str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


#: A bug, not a refusal: nothing about it is the caller's to read.
_INTERNAL = Translated(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal", "Internal Server Error")


def _translate(exc: Exception) -> Translated:
    """The one exception -> HTTP table (see the module docstring). Order matters: a
    subclass is tested before its base."""
    if isinstance(exc, LoyaltyError):  # ShopError included
        headers = None
        if exc.status == 429:
            # The detail says how long; the header is for clients that read it.
            digits = "".join(ch for ch in exc.detail if ch.isdigit())
            headers = {"Retry-After": digits or "60"}
        extra = exc.extra if isinstance(exc, ShopError) else {}
        return Translated(exc.status, exc.code, exc.detail, headers, extra)
    if isinstance(exc, ShopAdminRefused):
        return Translated(exc.status, "refused", exc.detail)
    if isinstance(exc, SyncRefused):
        return Translated(exc.status, "sync_refused", exc.message)
    if isinstance(exc, PasswordChangeRefused):
        return Translated(exc.status, "password_change_refused", exc.message)
    conflicts: tuple[tuple[type[Exception], str], ...] = (
        (ComponentSupersededError, "component_superseded"),
        (RetroactiveEditError, "retroactive_edit"),
        (SubstitutionError, "substitution_refused"),
        (BrowserJobRefused, "browser_job_refused"),
        (FinanceConflict, "conflict"),
        (ProposalConflict, "proposal_conflict"),
    )
    for cls, kind in conflicts:
        if isinstance(exc, cls):
            return Translated(status.HTTP_409_CONFLICT, kind, str(exc))
    if isinstance(exc, AutoOrderGrantRefused):
        return Translated(status.HTTP_403_FORBIDDEN, "auto_order_grant_refused", str(exc))
    if isinstance(exc, KeyError | IndexError | ValidationError):
        return Translated(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "internal", "Internal Server Error"
        )
    if isinstance(exc, LookupError):
        return Translated(status.HTTP_404_NOT_FOUND, "not_found", str(exc))
    if isinstance(exc, ValueError):
        return Translated(HTTP_422, "invalid_request", str(exc))
    return _INTERNAL


#: Registered with `_handle`. Starlette picks the handler by the exception's MRO, so
#: `KeyError` reaches `_handle` through `LookupError`; `_translate` then sends it to 500.
_TRANSLATED: tuple[type[Exception], ...] = (
    LoyaltyError,
    ShopAdminRefused,
    SyncRefused,
    PasswordChangeRefused,
    FinanceConflict,
    ProposalConflict,
    AutoOrderGrantRefused,
    LookupError,
    ValueError,
)


async def _handle(request: Request, exc: Exception) -> JSONResponse:
    t = _translate(exc)
    route = f"{request.method} {request.url.path}"
    if t.status >= 500:
        log.error("%s failed: %s", route, type(exc).__name__, exc_info=exc)
    else:
        log.info("%s -> %s %s: %s", route, t.status, t.error, t.detail)
    return JSONResponse(
        status_code=t.status,
        content={"detail": t.detail, "error": t.error, **t.extra},
        headers=t.headers,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="Cafe Ops",
        version="0.1.0",
        description=DESCRIPTION,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    # Vite on localhost during development. Deliberately not `*`: the shared password
    # travels in a header, and a wildcard origin plus a header credential is how a
    # password ends up readable by any page the browser happens to be on.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Authorization", "Content-Type"],
    )

    for exc_type in _TRANSLATED:
        app.add_exception_handler(exc_type, _handle)

    app.include_router(open_router)
    app.include_router(router)
    app.include_router(areas.shell.open_router)
    # /media/<sha>.<ext>: menu photos. Caddy serves the files directly in production;
    # this route is the dev/fallback path.
    app.include_router(areas.menu.open_router)
    for area in (
        areas.shell,
        areas.stock,
        areas.menu,
        areas.finance,
        areas.website,
        areas.shop_admin,
        areas.integrations,
    ):
        app.include_router(area.router)
    app.include_router(areas.website.open_router)
    _include_loyalty(app)
    _include_shop(app)
    return app


def _include_loyalty(app: FastAPI) -> None:
    """Sasha's Corner Rewards (docs/loyalty/CONTRACT.md): three routers of ours, two of the
    wallet module's.

    `LoyaltyError` renders as `{"error", "detail"}` with its own status (`_translate`).
    Pydantic's 422 is re-shaped the same way for loyalty paths only (the handler defers to
    FastAPI's default everywhere else, so no existing screen sees a change).

    The wallet routers (Apple's web service, the public strip images) are included only if
    their modules import: the wallet package is optional -- no certificate is the normal
    state -- and the app must start whatever state it is in.
    """
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(areas.loyalty.open_router)
    app.include_router(areas.staff.open_router)
    app.include_router(areas.members.router)

    try:
        config = importlib.import_module("cafeops.integrations.wallet.config")
        app.add_exception_handler(config.WalletNotConfigured, wallet_not_configured_response)
    except Exception as exc:
        log.info("wallet config unavailable (%s); wallet 503s come from the routes", exc)
    for name in ("apple_webservice", "public_routes"):
        try:
            module = importlib.import_module(f"cafeops.integrations.wallet.{name}")
            app.include_router(module.router)
        except Exception as exc:
            log.warning("wallet router %s not mounted: %s", name, exc)


def _include_shop(app: FastAPI) -> None:
    """Order online (docs/shop/CONTRACT.md §4): the public ordering routes. The admin
    router is Agent B's and sits in the `for area in (...)` tuple above. `ShopError` is
    translated by `_translate` like every other refusal."""
    app.include_router(areas.shop.open_router)


app = create_app()
