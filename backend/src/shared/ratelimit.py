"""Rate limiting for auth endpoints (slowapi), rendered through our envelope.

The limiter keys on client IP + organization slug so brute force against one
tenant is throttled before reaching the database. slowapi's ``RateLimitExceeded``
is adapted to our ``RateLimitedError`` so the 429 goes through the global handler
and returns the standard ``{"error": {...}}`` envelope, never slowapi's default
body. Default storage is in-memory (per process); a shared store (e.g. Redis) is
the production path.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import Response

from src.shared.errors import RateLimitedError, render_app_error

AUTH_RATE_LIMIT = "10/minute"


def _rate_limit_key(request: Request) -> str:
    """Use the IP+org_slug key set by :func:`set_org_scoped_key`, else the IP."""
    key: str | None = getattr(request.state, "rate_limit_key", None)
    return key or get_remote_address(request) or "anonymous"


limiter = Limiter(key_func=_rate_limit_key)


async def set_org_scoped_key(request: Request) -> None:
    """Dependency: derive the auth rate-limit key from client IP + body org_slug.

    Reads (and caches) the JSON body so the endpoint's schema parsing reuses it.
    """
    org_slug = ""
    try:
        body = await request.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        org_slug = str(body.get("org_slug", ""))
    ip = get_remote_address(request) or "anonymous"
    request.state.rate_limit_key = f"{ip}:{org_slug}"


async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> Response:
    """Render slowapi's rejection as our standard rate-limit envelope."""
    return await render_app_error(request, RateLimitedError("RATE_LIMITED"))
