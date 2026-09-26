"""The FastAPI application. One box, one process, behind Caddy (spec 3).

No load balancer, no worker pool, no second instance: 40 transactions a day, two users,
one SQLite file with a single writer, and a second app instance would contend on the same
file and make things worse (ARCHITECTURE 8F.8). `uvicorn` with one worker is correct here
and is what `cafeops serve` starts.

The exception handlers exist so the frontend gets a *reason* rather than a 500. Three
domain errors have a right HTTP answer and it is not 500:

* `LookupError` -> **404**. A template or ingredient that does not exist.
* `RetroactiveEditError` -> **409**. Invariant 3: history is never rewritten, and the
  editor needs to render that refusal rather than a stack trace.
* `SubstitutionError` -> **409**. Spec 4.3: a SUBSTITUTE into a non-substitutable slot is
  an error, not a silent no-op, and the message names the slot.
* `ComponentSupersededError` -> **409**. Editing a component a later edit already closed.
  See `api/errors.py`: it is the one failure a preview cannot report on its own, because a
  superseded component honestly previews as "nothing changes".
* `AutoOrderGrantRefused` -> **403**. Invariant 2: nobody grants auto-ordering by hand.
  No route tries to, and this handler is here so that if one ever does it fails loudly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cafeops.api import areas
from cafeops.api.errors import ComponentSupersededError
from cafeops.api.routers import open_router, router
from cafeops.db.repositories.par import AutoOrderGrantRefused
from cafeops.domain.types import SubstitutionError
from cafeops.services.edit_composition import RetroactiveEditError

__all__ = ["create_app"]

#: Starlette renamed `HTTP_422_UNPROCESSABLE_ENTITY` to `..._CONTENT` and deprecated the
#: old name. The number is the stable thing; spelling it avoids a warning on one version
#: and an AttributeError on the other.
HTTP_422 = 422

DESCRIPTION = """
Back-office API for Sasha's Corner.

**Writes go through services.** Stock counts, deliveries, write-offs, supplier and
recipe edits, finance entries and agent-proposal decisions are all POST/PUT/PATCH/DELETE
routes in `cafeops/api/areas/`, each delegating to one service that enforces the
invariants. Recipe and price edits are preview-then-apply and effective-dated. There is
still **no route that creates, confirms or sends a purchase order** -- invariant 1 says
that needs a human in Telegram (owner decision, docs/design/specs/DECISIONS.md §1).

**Encoding.** Money is integer pence where the database stores an integer, and an exact
decimal *string* of pence where the figure is derived and fractional. Quantities are
always strings. A missing cost is `null`, never `0`. A low-confidence forecast has
`qty: null` and its reasons populated -- render the reasons *in place of* the number,
because the number is not in the payload. See `GET /api/meta`.

**Auth.** One shared password (spec 1: no user management). `POST /api/auth/session`
exchanges it for a session token, sent as `Authorization: Bearer <token>`; the raw
password still works as `X-API-Key` (CLI, fixtures). A password changed in Settings is
stored hashed and wins over `CAFEOPS_API_PASSWORD`. With neither set every route but
`/api/health` and sign-in answers 503.
"""


def _handler(
    status_code: int, kind: str
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handle(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"detail": str(exc), "error": kind},
        )

    return handle


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

    app.add_exception_handler(LookupError, _handler(status.HTTP_404_NOT_FOUND, "not_found"))
    app.add_exception_handler(
        RetroactiveEditError, _handler(status.HTTP_409_CONFLICT, "retroactive_edit")
    )
    app.add_exception_handler(
        SubstitutionError, _handler(status.HTTP_409_CONFLICT, "substitution_refused")
    )
    app.add_exception_handler(
        ComponentSupersededError, _handler(status.HTTP_409_CONFLICT, "component_superseded")
    )
    app.add_exception_handler(
        AutoOrderGrantRefused, _handler(status.HTTP_403_FORBIDDEN, "auto_order_grant_refused")
    )
    # ValueError last: `SubstitutionError` and `RetroactiveEditError` are both ValueErrors
    # and must match their own handler first. Starlette dispatches on the most specific
    # registered class, so order is not what decides it -- but registering the base case
    # here keeps a bad quantity string a 422 rather than a 500.
    app.add_exception_handler(ValueError, _handler(HTTP_422, "invalid_request"))

    app.include_router(open_router)
    app.include_router(router)
    app.include_router(areas.shell.open_router)
    # /media/<sha>.<ext>: menu photos. Caddy serves the files directly in production;
    # this route is the dev/fallback path.
    app.include_router(areas.menu.open_router)
    for area in (areas.shell, areas.stock, areas.menu, areas.finance):
        app.include_router(area.router)
    return app


app = create_app()
