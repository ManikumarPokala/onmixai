"""Application factory: wire settings, logging, middleware, handlers, routers.

The lifespan handler disposes the engine pool on shutdown for clean exits.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded

from src.admin.router import router as admin_router
from src.conversation.router import router as conversation_router
from src.identity.router import router as identity_router
from src.knowledge.router import router as knowledge_router
from src.recommendation.router import router as recommendation_router
from src.reports.router import router as reports_router
from src.search.router import router as search_router
from src.shared.config import get_settings
from src.shared.database import dispose_engine
from src.shared.errors import register_exception_handlers
from src.shared.health import router as health_router
from src.shared.logging import configure_logging
from src.shared.middleware import RequestContextMiddleware
from src.shared.queue import get_job_queue
from src.shared.ratelimit import limiter, rate_limit_exceeded_handler
from src.shared.storage import get_object_storage

API_PREFIX = "/api/v1"


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await get_object_storage().ensure_bucket()
    yield
    await get_job_queue().close()
    await dispose_engine()


def create_app() -> FastAPI:
    """Construct and wire the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="OnMixAI", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    app.include_router(identity_router, prefix=API_PREFIX)
    app.include_router(knowledge_router, prefix=API_PREFIX)
    app.include_router(search_router, prefix=API_PREFIX)
    app.include_router(conversation_router, prefix=API_PREFIX)
    app.include_router(recommendation_router, prefix=API_PREFIX)
    app.include_router(reports_router, prefix=API_PREFIX)
    app.include_router(admin_router, prefix=API_PREFIX)
    app.include_router(health_router)
    return app
