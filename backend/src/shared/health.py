"""Liveness and readiness probes (Sprint 1 Task 7).

``/health`` is pure liveness — it touches no dependencies, so orchestrators can
tell the process is up. ``/health/ready`` checks the database with a short
timeout and returns 503 (not 500) when it is unreachable, so traffic is drained
gracefully instead of users seeing errors.
"""

import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.shared.database import get_engine

router = APIRouter()

_READINESS_TIMEOUT_SECONDS = 2.0


def readiness_engine() -> AsyncEngine:
    """Engine used by the readiness probe (overridable in tests)."""
    return get_engine()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up. No dependencies are touched."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(engine: AsyncEngine = Depends(readiness_engine)) -> JSONResponse:
    """Readiness: a bounded ``SELECT 1``; any failure reports degraded (503)."""
    try:
        async with asyncio.timeout(_READINESS_TIMEOUT_SECONDS):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception:  # readiness probe: any failure means not-ready, never a 500
        return JSONResponse(
            status_code=503, content={"status": "degraded", "checks": {"database": "down"}}
        )
    return JSONResponse(status_code=200, content={"status": "ok", "checks": {"database": "up"}})
