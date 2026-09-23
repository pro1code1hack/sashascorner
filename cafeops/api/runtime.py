"""The async edge, and the only place it exists.

Spec 3: *"Do not make everything async. SQLite has one writer."* Handlers are `async
def` because that is what FastAPI's edge is, and every one of them hands its database
work to `asyncio.to_thread`, where a sync `Session` does the work through the existing
services. No repository becomes async and no service learns about the event loop.

`in_session` takes a function of one argument -- the `Session` -- and returns whatever
that function returns. The function must finish everything it needs from the ORM
*before* returning, because the session closes when it does: build the Pydantic model
inside the thread, not the router. A lazy attribute touched after the session closes
raises `DetachedInstanceError`, and the whole reason the views in `views.py` return
finished response models rather than ORM rows is to make that impossible rather than
merely unlikely.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy.orm import Session

from cafeops.db.base import session_scope

__all__ = ["in_session"]


def _run[T](work: Callable[[Session], T]) -> T:
    with session_scope() as session:
        return work(session)


async def in_session[T](work: Callable[[Session], T]) -> T:
    """Run `work` against a sync Session on a worker thread."""
    return await asyncio.to_thread(_run, work)
