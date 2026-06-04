"""Async database core: engine, session factory, and per-request sessions.

One async engine and one session factory serve the whole process. The
request-scoped ``get_db_session`` dependency owns the transaction boundary —
business code never calls ``commit()``/``rollback()`` itself (patterns.md §1).
Tenant isolation is applied per request via ``set_tenant_context``, which sets
the Postgres GUC that Row-Level Security policies read (CLAUDE.md §4).

The engine/session factory are built lazily (PEP 562 ``__getattr__`` + cached
accessors) so importing this module never requires configuration to be present;
configuration is only read the first time a connection is actually needed.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache
from uuid import UUID

import structlog
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.shared.config import Settings, get_settings

# Stable constraint/index names so Alembic autogenerate diffs are deterministic.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Name of the Postgres session variable RLS policies filter on (see migration 0001).
TENANT_GUC = "app.current_org_id"
_AFTER_COMMIT_KEY = "after_commit_callbacks"

_logger = structlog.get_logger()


def register_after_commit(session: AsyncSession, callback: Callable[[], Awaitable[None]]) -> None:
    """Register a coroutine to run after this request's transaction commits.

    Used to enqueue jobs only once the row they reference is durably committed
    (avoids a worker racing an uncommitted document).
    """
    callbacks: list[Callable[[], Awaitable[None]]] = session.info.setdefault(_AFTER_COMMIT_KEY, [])
    callbacks.append(callback)


class Base(DeclarativeBase):
    """Declarative base for all ORM models, with stable naming conventions."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _build_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine from settings.

    ``pool_pre_ping`` validates pooled connections before use so a Postgres
    restart does not require an API restart (Sprint 1 robustness checklist).
    """
    return create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
    )


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, constructing it once on first use."""
    return _build_engine(get_settings())


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory (``expire_on_commit=False``)."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def set_tenant_context(session: AsyncSession, org_id: UUID) -> None:
    """Bind the tenant GUC so Postgres RLS scopes every query to ``org_id``.

    Uses ``set_config(..., is_local => true)`` (transaction-scoped, parameter-safe)
    rather than ``SET LOCAL``, which cannot take bind parameters. The setting is
    cleared automatically at transaction end, so it never leaks across pooled
    connections. Time: O(1). Space: O(1).
    """
    await session.execute(
        text("SELECT set_config(:key, :value, true)"),
        {"key": TENANT_GUC, "value": str(org_id)},
    )


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one session/transaction per request.

    Commits on success, rolls back on any exception, and always closes the
    session. This is the single owner of the transaction boundary; services and
    repositories never commit themselves (patterns.md §1).
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await run_after_commit(session)


async def run_after_commit(session: AsyncSession) -> None:
    """Run (and consume) post-commit callbacks.

    A callback failure is logged, never failing the already-committed request.
    """
    for callback in session.info.pop(_AFTER_COMMIT_KEY, []):
        try:
            await callback()
        except Exception:
            _logger.exception("after_commit_callback_failed")


async def dispose_engine() -> None:
    """Dispose the engine's connection pool (called on application shutdown)."""
    await get_engine().dispose()


def __getattr__(name: str) -> AsyncEngine:
    """Expose ``engine`` as a lazily-constructed module attribute (PEP 562)."""
    if name == "engine":
        return get_engine()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
