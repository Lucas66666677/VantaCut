from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.db.session import Base
from app import models  # noqa: F401

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata

# This repo's revision ids are descriptive slugs (e.g.
# "0009_add_gaming_highlight_analysis_type", 39 chars), not short hashes.
# Alembic's MigrationContext hardcodes alembic_version.version_num as
# VARCHAR(32) — see version_table_impl() in the installed alembic/ddl/impl.py:
#
#   def version_table_impl(self, *, version_table, version_table_schema,
#                           version_table_pk, **kw):
#       vt = Table(version_table, MetaData(),
#                  Column("version_num", String(32), nullable=False), ...)
#
# It accepts **kw but never reads a "version_table_column_size" key out of
# it, and MigrationContext.__init__ likewise only pulls version_table /
# version_table_schema / version_table_pk out of opts. So
# context.configure(version_table_column_size=64) — the setting previously
# attempted here — is silently swallowed: nothing in Alembic 1.14 ever
# consults it. Confirmed by reading the exact installed version's source
# (alembic/runtime/migration.py and alembic/ddl/impl.py for the
# rel_1_14_0 tag), not assumed from documentation.
#
# There is no supported context.configure() kwarg for this. The fix widens
# the column directly, once, before any migration runs. This is safe on a
# fresh database (the table is created — checkfirst=True, so a no-op if
# Alembic already made it — and widened before any revision is written)
# and safe on an existing database already stamped mid-chain (ALTER COLUMN
# ... TYPE only widens; it preserves existing values and is not
# destructive), so it can run unconditionally on every `alembic upgrade`.
ALEMBIC_VERSION_COLUMN_SIZE = 64


def _widen_alembic_version_column(connection) -> None:
    """Ensure alembic_version exists, then widen version_num past Alembic's
    hardcoded VARCHAR(32) so it can hold this repo's long revision ids."""
    mc = context.get_context()
    mc._ensure_version_table()
    connection.execute(
        text(
            f"ALTER TABLE {mc.version_table} "
            f"ALTER COLUMN version_num TYPE VARCHAR({ALEMBIC_VERSION_COLUMN_SIZE})"
        )
    )
    # Hand Alembic a clean (not-in-transaction) connection: it manages its
    # own transaction around run_migrations(), and our DDL above may have
    # autobegun one on this connection.
    connection.commit()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        _widen_alembic_version_column(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
