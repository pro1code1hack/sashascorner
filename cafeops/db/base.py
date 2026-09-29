"""Engine, Session factory, and connection pragmas.

Everything in the application talks to the database through a *sync* Session.
Async lives only at the I/O edges (Lightspeed HTTP, Telegram). SQLite permits a
single writer; making repositories async would buy nothing and cost clarity.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from cafeops.config import settings

#: Deterministic names for every constraint and index.
#:
#: Not cosmetic. Alembic's SQLite "batch mode" rebuilds a table by copying it, and it
#: refuses to move a constraint it cannot name -- so without this, any migration that
#: alters a table carrying an unnamed constraint fails with "Constraint must have a
#: name". Setting it here means every future migration can alter any table.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _apply_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    _set_pragmas(dbapi_connection, foreign_keys=True)


def _apply_sqlite_pragmas_no_fk(dbapi_connection: Any, _record: Any) -> None:
    _set_pragmas(dbapi_connection, foreign_keys=False)


def _set_pragmas(dbapi_connection: Any, *, foreign_keys: bool) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def create_db_engine(
    url: str | None = None, *, echo: bool | None = None, enforce_foreign_keys: bool = True
) -> Engine:
    url = url or settings.database_url
    echo = settings.sql_echo if echo is None else echo
    kwargs: dict[str, Any] = {"echo": echo, "future": True}
    if url.startswith("sqlite"):
        # check_same_thread=False so asyncio.to_thread workers can share the pool.
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        # `enforce_foreign_keys=False` exists for Alembic. Batch mode rebuilds a
        # table by copying and dropping it, which SQLite refuses while anything
        # references that table -- and `PRAGMA foreign_keys` is silently IGNORED
        # inside a transaction, so it cannot be turned off from within a migration.
        # It has to be off on the connection from the start.
        event.listen(
            engine,
            "connect",
            _apply_sqlite_pragmas if enforce_foreign_keys else _apply_sqlite_pragmas_no_fk,
        )
    return engine


@functools.cache
def get_engine() -> Engine:
    """The process-wide engine, built on first use rather than at import.

    Importing a model must not open a database: the domain package, the CLI's `--help`
    and every tool that only wants the types would otherwise read `.env` and create an
    engine as a side effect of an `import`.
    """
    return create_db_engine()


@functools.cache
def session_factory() -> sessionmaker[Session]:
    """The process-wide `sessionmaker`, bound to `get_engine()` on first use."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def new_session() -> Session:
    """A fresh session from the process-wide factory. The caller owns commit and close;
    prefer `session_scope` unless the unit of work genuinely needs manual control."""
    return session_factory()()


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """One unit of work. Commit on success, roll back on anything else.

    Keep the body short -- SQLite has one writer.
    """
    session = (factory or session_factory())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
