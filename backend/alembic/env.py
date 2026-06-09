"""Alembic migration environment (async, migration-owner role).

Migrations connect with elevated privileges (extension creation, RLS, grants)
via ``MIGRATION_DATABASE_URL`` — a separate migration-owner role from the
non-superuser runtime role the application uses (CLAUDE.md §4). If
``MIGRATION_DATABASE_URL`` is unset it falls back to ``DATABASE_URL``.
"""

import asyncio
from logging.config import fileConfig

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import the metadata and every model module so all tables register on Base.
from src.ai import models as ai_models  # noqa: F401
from src.conversation import models as conversation_models  # noqa: F401
from src.governance import models as governance_models  # noqa: F401
from src.identity import models  # noqa: F401
from src.knowledge import models as knowledge_models  # noqa: F401
from src.recommendation import models as recommendation_models  # noqa: F401
from src.reports import models as reports_models  # noqa: F401
from src.shared import audit as shared_audit  # noqa: F401  (AuditEvent registers on Base)
from src.shared.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


class _MigrationSettings(BaseSettings):
    """Resolve the migration-owner database URL from the environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    database_url: str
    migration_database_url: str | None = None

    @property
    def url(self) -> str:
        return self.migration_database_url or self.database_url


def _get_url() -> str:
    return _MigrationSettings().url  # type: ignore[call-arg]


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI connection)."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode against an async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
