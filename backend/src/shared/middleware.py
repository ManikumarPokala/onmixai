"""Per-request context middleware (CLAUDE.md §6).

Generates a ``request_id`` for every request, binds it to structlog contextvars
(so all log lines in the request — including org_id/user_id once auth binds them —
are annotated), echoes it in the ``X-Request-ID`` response header, and emits one
structured line per request: method, path, status, duration_ms. Expected 4xx are
logged at INFO, 5xx at ERROR.
"""

from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

_logger = structlog.get_logger()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind request context, time the request, and emit the access log line."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Unhandled error: log the access line (the 500 envelope and traceback
            # are produced by the global exception handler), then propagate.
            duration_ms = round((perf_counter() - start) * 1000, 2)
            _logger.error(
                "request.completed",
                method=request.method,
                path=request.url.path,
                status=500,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
            raise

        duration_ms = round((perf_counter() - start) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        log = _logger.info if response.status_code < 500 else _logger.error
        log(
            "request.completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        structlog.contextvars.clear_contextvars()
        return response
