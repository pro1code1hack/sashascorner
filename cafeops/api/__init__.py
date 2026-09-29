"""The HTTP layer the React back office (and the public site's shop and rewards pages)
consumes. It reads and it writes; every write goes through `services/`.

`cafeops.api.app:app` is the ASGI application; `cafeops serve` runs it under uvicorn.

Layers, outermost first:

* `routers.py`, `areas/*.py` -- async, parse the request (`params.py`, `uploads.py`),
  delegate. Nothing else.
* `runtime.py` -- `asyncio.to_thread` and the unit of work; the only async/sync boundary
  in the package, and the only place a session commits.
* `views/`, `areas/*_views.py` -- sync, call the services, return finished Pydantic models.
* `schemas.py` -- the wire contract. `encoding.py` is how values cross it.
* `app.py` -- the one table from a service refusal to an HTTP status (`_translate`).
* `security.py` -- one shared password.

Purchase orders: a DRAFT is created (`POST /api/orders/from-draft`) and confirmed by a
named person (`POST /api/orders/{id}/confirm`) -- invariant 1 holds because confirmation
needs that name. No route sends an order to a supplier.

Nothing here touches the database directly except for a handful of reads that are
genuinely presentational (a template's size profiles, a supplier's name). Every figure
that an invariant governs comes out of `services/` or `domain/`, which is what keeps
those invariants enforceable in one place rather than five (spec 8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["create_app"]


def create_app() -> FastAPI:
    """Lazy re-export, so importing `cafeops.api` does not pull in FastAPI or the routes."""
    from cafeops.api.app import create_app as _create_app

    return _create_app()
