"""Shared fixtures for the error/middleware tests.

A minimal app wires only the request-context middleware and global exception
handlers (the full create_app arrives in Task 7), plus routes that raise each
error kind so the envelope and logging contract can be asserted in isolation.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from src.shared.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    register_exception_handlers,
)
from src.shared.logging import configure_logging
from src.shared.middleware import RequestContextMiddleware


class _Body(BaseModel):
    name: str


def _build_app() -> FastAPI:
    configure_logging("INFO")
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/not-found")
    async def not_found() -> None:
        raise NotFoundError("THING_NOT_FOUND")

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("THING_CONFLICT", detail="already exists")

    @app.get("/forbidden")
    async def forbidden() -> None:
        raise AuthorizationError("FORBIDDEN")

    @app.get("/rate-limited")
    async def rate_limited() -> None:
        raise RateLimitedError("RATE_LIMITED")

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("super-secret internal detail with SQL SELECT * FROM users")

    @app.post("/validate")
    async def validate(body: _Body) -> dict[str, str]:
        return {"name": body.name}

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
