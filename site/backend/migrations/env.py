"""Alembic for the site's own tables in the SHARED cafeops SQLite file.

Two guards keep this from ever touching an ops table:
  * version_table = site_alembic_version (ops uses alembic_version);
  * include_name / include_object: autogenerate only ever sees ``site_*`` tables,
    so it can never propose dropping one it does not own.
"""

from __future__ import annotations

from logging.config import fileConfig
from typing import Any

from alembic import context
from sashasite.config import get_settings
from sashasite.db import Base, create_db_engine
from sashasite.events import SiteEvent  # noqa: F401  (registers site_event* on Base.metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
VERSION_TABLE = "site_alembic_version"


def _url() -> str:
    return context.get_x_argument(as_dictionary=True).get("db_url") or get_settings().database_url


def _owned(name: str | None) -> bool:
    return bool(name) and str(name).startswith("site_")


def include_name(name: str | None, type_: str, parent_names: Any) -> bool:
    if type_ == "table":
        return _owned(name)
    return True


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    if type_ == "table":
        return _owned(name)
    table = getattr(obj, "table", None)
    if table is not None:
        return _owned(table.name)
    return True


def _configure(**kw: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        include_name=include_name,
        include_object=include_object,
        compare_type=True,
        render_as_batch=True,
        **kw,
    )


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(_url())
    with engine.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        # SQLite DDL is non-transactional to Alembic, so it does not commit for us
        # and SQLAlchemy 2.0 rolls back on close -- commit explicitly.
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
