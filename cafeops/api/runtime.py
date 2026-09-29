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

**Views never commit.** `session_scope` commits when `work` returns and rolls back when
it raises. The one case that needs both -- a refusal whose audit row or attempt counter
must survive the refusal (a wrong password, a wrong recovery code, a sync lock that
closed an abandoned run on the way to saying no) -- is `in_session_committing` /
`run_committing`: name the refusal types, and the session commits before they
propagate. Anything else still rolls back. Nobody calls `session.commit()` by hand.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy.orm import Session

from cafeops.db.base import session_scope

__all__ = ["in_session", "in_session_committing", "run_committing"]


def _run[T](work: Callable[[Session], T]) -> T:
    with session_scope() as session:
        return work(session)


def run_committing[T](
    work: Callable[[Session], T], *, refusals: tuple[type[BaseException], ...]
) -> T:
    """Sync `in_session_committing`, for code already on a worker thread (a sync
    FastAPI dependency). Commits on success AND when `work` raises one of `refusals`,
    then re-raises it; any other exception rolls back."""
    with session_scope() as session:
        try:
            return work(session)
        except refusals:
            session.commit()
            raise


async def in_session[T](work: Callable[[Session], T]) -> T:
    """Run `work` against a sync Session on a worker thread."""
    return await asyncio.to_thread(_run, work)


async def in_session_committing[T](
    work: Callable[[Session], T], *, refusals: tuple[type[BaseException], ...]
) -> T:
    """`in_session`, except that a refusal in `refusals` is committed before it is
    re-raised: what the refusal recorded (an audit row, an attempt counter) stands."""
    return await asyncio.to_thread(lambda: run_committing(work, refusals=refusals))
