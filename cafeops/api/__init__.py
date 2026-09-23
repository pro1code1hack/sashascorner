"""The read-only HTTP layer the React dashboard consumes.

`cafeops.api.app:app` is the ASGI application; `cafeops serve` runs it under uvicorn.

Layers, outermost first:

* `routers.py` -- async, parses the request, delegates. Nothing else.
* `runtime.py` -- `asyncio.to_thread`, the only async/sync boundary in the package.
* `views/` -- sync, calls the existing services, returns finished Pydantic models.
* `schemas.py` -- the wire contract. `encoding.py` is how values cross it.
* `security.py` -- one shared password.

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
