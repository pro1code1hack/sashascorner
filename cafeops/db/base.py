"""Engine, Session factory, and connection pragmas.

Everything in the application talks to the database through a *sync* Session.
Async lives only at the I/O edges (Lightspeed HTTP, Telegram). SQLite permits a
single writer; making repositories async would buy nothing and cost clarity.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from cafeops.config import settings


class Base(DeclarativeBase):
    """Declarative base for every model."""


def _apply_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def create_db_engine(url: str | None = None, *, echo: bool | None = None) -> Engine:
    url = url or settings.database_url
    echo = settings.sql_echo if echo is None else echo
    kwargs: dict[str, Any] = {"echo": echo, "future": True}
    if url.startswith("sqlite"):
        # check_same_thread=False so asyncio.to_thread workers can share the pool.
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


engine: Engine = create_db_engine()
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """One unit of work. Commit on success, roll back on anything else.

    Keep the body short -- SQLite has one writer.
    """
    session = (factory or SessionFactory)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
