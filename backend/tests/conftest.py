"""Integration test harness: real Postgres via testcontainers, RLS active.

A session-scoped container provisions the non-superuser runtime role exactly as
the infra init script does, then the real Alembic migration runs as the owner
role (subprocess — isolated from the test event loop). Per-test sessions connect
as the runtime role so Row-Level Security is enforced; each test runs in a
transaction that is rolled back, so no data leaks between tests.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from src.identity.repository import (
    OrganizationRepository,
    RefreshTokenRepository,
    UserRepository,
)
from src.identity.service import AuthService
from src.shared.config import Settings, get_embedding_dimension

# Keep litellm OFFLINE for the whole session: use its bundled model-cost map instead of fetching
# the remote one (paired with litellm.telemetry = False in tests/ai/conftest.py). This root
# conftest's body runs before any test module imports litellm, so setting the env here is early
# enough. Together these stop litellm's background external calls, which otherwise leak/blocked
# sockets that surface as unraisable ResourceWarnings under filterwarnings=error.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

BACKEND = Path(__file__).resolve().parents[1]

_OWNER, _OWNER_PW = "onmixai", "onmixai"
_APP, _APP_PW = "onmixai_app", "onmixai_app"
_DB = "onmixai"


def _provision_runtime_role(host: str, port: int) -> None:
    """Create the non-superuser/non-bypassrls runtime role + default grants."""
    with psycopg.connect(
        host=host, port=port, user=_OWNER, password=_OWNER_PW, dbname=_DB, autocommit=True
    ) as conn:
        conn.execute(
            f"CREATE ROLE {_APP} WITH LOGIN PASSWORD '{_APP_PW}' "
            "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
        )
        conn.execute(f"GRANT CONNECT ON DATABASE {_DB} TO {_APP}")
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {_APP}")
        conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {_OWNER} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP}"
        )
        conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {_OWNER} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {_APP}"
        )


def _run_migrations(owner_url: str, app_url: str) -> None:
    """Apply migrations as the owner role, isolated in a subprocess."""
    env = {**os.environ, "MIGRATION_DATABASE_URL": owner_url, "DATABASE_URL": app_url}
    alembic = Path(sys.prefix) / "bin" / "alembic"
    subprocess.run([str(alembic), "upgrade", "head"], cwd=BACKEND, env=env, check=True)


@pytest.fixture(scope="session")
def pg_container() -> Iterator[dict[str, str]]:
    container = PostgresContainer(
        "pgvector/pgvector:pg16",
        username=_OWNER,
        password=_OWNER_PW,
        dbname=_DB,
        driver="psycopg",
    )
    with container as pg:
        host = pg.get_container_host_ip()
        port = int(pg.get_exposed_port(5432))
        owner_url = f"postgresql+asyncpg://{_OWNER}:{_OWNER_PW}@{host}:{port}/{_DB}"
        app_url = f"postgresql+asyncpg://{_APP}:{_APP_PW}@{host}:{port}/{_DB}"
        _provision_runtime_role(host, port)
        _run_migrations(owner_url, app_url)
        yield {"owner_url": owner_url, "app_url": app_url}
    # The `with` has stopped the container; now close the docker-py daemon connection it left
    # open. That connection is an AF_UNIX socket — under filterwarnings=error its unclosed-socket
    # ResourceWarning, collected during a LATER test, becomes a fatal
    # PytestUnraisableExceptionWarning. It's order/volume-dependent, which is why it sinks the
    # full `test` job but not the smaller `isolation` job that uses the same fixture.
    try:
        container.get_docker_client().client.close()
    except Exception:  # noqa: BLE001 — best-effort teardown cleanup; never fail teardown on it
        pass


@pytest.fixture(scope="session")
def settings(pg_container: dict[str, str]) -> Settings:
    return Settings(
        env="test",
        database_url=pg_container["app_url"],
        jwt_secret="test-secret-key-at-least-32-characters-long",
        storage_endpoint="http://localhost:9100",
        storage_access_key="access",
        storage_secret_key="secret",
        storage_bucket="onmixai-test",
        redis_url="redis://localhost:6390/0",
        embedding_dimension=get_embedding_dimension(),
        _env_file=None,
    )


@pytest.fixture
async def app_engine(pg_container: dict[str, str]) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg_container["app_url"])
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(app_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
def auth_service(db_session: AsyncSession, settings: Settings) -> AuthService:
    return AuthService(
        session=db_session,
        organizations=OrganizationRepository(db_session),
        users=UserRepository(db_session),
        refresh_tokens=RefreshTokenRepository(db_session),
        settings=settings,
    )
