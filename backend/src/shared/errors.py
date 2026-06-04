"""Typed domain errors and the global exception handlers (CLAUDE.md §5).

Every expected failure raises a typed ``AppError`` subclass; a single set of
handlers renders the consistent envelope ``{"error": {code, message,
request_id}}``. Clients never see stack traces, SQL, provider bodies, or internal
paths — unhandled exceptions are logged server-side with a traceback and returned
as a generic 500. See patterns.md §9 for which situation maps to which error.
"""

from typing import Any, cast

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_logger = structlog.get_logger()


class AppError(Exception):
    """Base class for all expected, client-facing domain errors."""

    def __init__(self, code: str, status: int, message: str, detail: str | None = None) -> None:
        self.code = code
        self.status = status
        self.message = message
        self.detail = detail
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(
        self, code: str, *, message: str = "Resource not found", detail: str | None = None
    ) -> None:
        super().__init__(code=code, status=404, message=message, detail=detail)


class ConflictError(AppError):
    def __init__(self, code: str, *, message: str = "Conflict", detail: str | None = None) -> None:
        super().__init__(code=code, status=409, message=message, detail=detail)


class ValidationFailedError(AppError):
    def __init__(
        self, code: str, *, message: str = "Validation failed", detail: str | None = None
    ) -> None:
        super().__init__(code=code, status=422, message=message, detail=detail)


class AuthenticationError(AppError):
    def __init__(
        self, code: str, *, message: str = "Authentication failed", detail: str | None = None
    ) -> None:
        super().__init__(code=code, status=401, message=message, detail=detail)


class AuthorizationError(AppError):
    def __init__(
        self, code: str, *, message: str = "Not authorized", detail: str | None = None
    ) -> None:
        super().__init__(code=code, status=403, message=message, detail=detail)


class RateLimitedError(AppError):
    def __init__(
        self, code: str, *, message: str = "Too many requests", detail: str | None = None
    ) -> None:
        super().__init__(code=code, status=429, message=message, detail=detail)


def _request_id(request: Request) -> str | None:
    """Read the request id bound by the request-context middleware."""
    return cast(str | None, getattr(request.state, "request_id", None))


def _envelope(code: str, message: str, request_id: str | None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


def _json_error(status: int, body: dict[str, Any], request_id: str | None) -> JSONResponse:
    response = JSONResponse(status_code=status, content=body)
    if request_id is not None:
        response.headers["X-Request-ID"] = request_id
    return response


async def render_app_error(request: Request, error: AppError) -> JSONResponse:
    """Render a typed domain error into the standard envelope (+ logging).

    Public so other layers (e.g. the rate-limit adapter) can reuse the exact
    rendering instead of building a parallel response.
    """
    request_id = _request_id(request)
    event = "app_error"
    if error.status >= 500:
        _logger.error(event, code=error.code, status=error.status, detail=error.detail)
    else:
        _logger.info(event, code=error.code, status=error.status, detail=error.detail)
    return _json_error(error.status, _envelope(error.code, error.message, request_id), request_id)


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    """AppError exception handler. Registered only for ``AppError``."""
    return await render_app_error(request, cast(AppError, exc))


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Render FastAPI request-validation failures in the same envelope."""
    error = cast(RequestValidationError, exc)
    request_id = _request_id(request)
    body = _envelope("VALIDATION_ERROR", "Request validation failed", request_id)
    body["error"]["fields"] = jsonable_encoder(error.errors())
    _logger.info("validation_error", status=422)
    return _json_error(422, body, request_id)


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: log the traceback server-side, return a generic 500.

    The client body never contains the exception text, type, or traceback.
    """
    request_id = _request_id(request)
    _logger.error("unhandled_exception", exc_info=exc)
    return _json_error(
        500, _envelope("INTERNAL_ERROR", "An internal error occurred", request_id), request_id
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the global handlers onto the application (called by create_app)."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
