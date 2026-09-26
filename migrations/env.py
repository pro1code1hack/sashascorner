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


def _include_name(name: str | None, type_: str, _parent: object) -> bool:
    # The public website (site/) keeps its own tables in this same SQLite file, under
    # its own Alembic history (version table `site_alembic_version`). Without this
    # filter an ops `--autogenerate` sees them as unknown and proposes dropping them.
    return not (type_ == "table" and name is not None and name.startswith("site_"))


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
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # foreign_keys is OFF on this connection: Alembic's SQLite batch mode rebuilds
    # tables by copy-and-drop, which FK enforcement blocks, and the pragma cannot be
    # changed from inside a transaction. Constraints are still recreated in the new
    # table; `PRAGMA foreign_key_check` below proves nothing was left dangling.
    engine = create_db_engine(_url(), enforce_foreign_keys=False)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
            include_name=_include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
        # SQLite reports non-transactional DDL, so Alembic does not commit for us and
        # SQLAlchemy 2.0 rolls back on close. Without this, `alembic downgrade` exits
        # 0 having changed nothing at all.
        connection.commit()

    # Verify on a fresh, FK-enforcing connection: a rebuild that left a dangling
    # reference is a broken migration and must not pass silently.
    verifier = create_db_engine(_url())
    with verifier.connect() as connection:
        if connection.dialect.name == "sqlite":
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"migration left {len(violations)} foreign-key violation(s): {violations[:5]}"
                )
    verifier.dispose()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
