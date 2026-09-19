"""Alembic environment. Reads the URL from cafeops.config, never from alembic.ini."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from cafeops.config import settings
from cafeops.db.base import create_db_engine
from cafeops.db.models import Base  # importing populates Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # -x db_url=... wins, then the app settings.
    return context.get_x_argument(as_dictionary=True).get("db_url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite cannot ALTER most things. Batch mode makes future migrations
            # portable without sqlite-specific SQL in a version file.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
