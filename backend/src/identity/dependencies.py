"""Identity FastAPI dependencies: authentication, RBAC, and DI wiring.

``get_current_user`` verifies the access token, binds the tenant context onto the
request session (so RLS applies to every subsequent query) plus the structlog
context, and loads the active user. ``get_tenant_session`` hands routes the same
tenant-bound session. Constructor injection composes the service here, never
inside the service itself.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

import jwt
import structlog
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role
from src.identity.repository import (
    OrganizationRepository,
    RefreshTokenRepository,
    UserRepository,
)
from src.identity.schemas import AuthContext
from src.identity.service import AuthService
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session, set_tenant_context
from src.shared.errors import AuthenticationError
from src.shared.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_auth_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    """Compose the auth service with repositories bound to the request session."""
    return AuthService(
        session=session,
        organizations=OrganizationRepository(session),
        users=UserRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        settings=settings,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    """Authenticate the bearer token and bind tenant + log context.

    Any token problem (missing, malformed, expired, bad signature, unknown or
    inactive user) maps to AuthenticationError — never a 500.
    """
    if credentials is None:
        raise AuthenticationError("MISSING_TOKEN")
    try:
        claims = decode_access_token(credentials.credentials, settings=settings)
        user_id = UUID(claims["sub"])
        org_id = UUID(claims["org_id"])
        role = Role(claims["role"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise AuthenticationError("INVALID_TOKEN") from None

    await set_tenant_context(session, org_id)
    structlog.contextvars.bind_contextvars(org_id=str(org_id), user_id=str(user_id))

    user = await UserRepository(session).get_by_id(org_id, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("INVALID_TOKEN")
    return AuthContext(user_id=user_id, org_id=org_id, role=role)


async def get_tenant_session(
    session: AsyncSession = Depends(get_db_session),
    _actor: AuthContext = Depends(get_current_user),
) -> AsyncSession:
    """Return the request session with tenant context already bound by auth."""
    return session


def require_role(*roles: Role) -> Callable[[AuthContext], Awaitable[AuthContext]]:
    """Dependency factory enforcing that the actor holds one of ``roles``."""

    async def _require(actor: AuthContext = Depends(get_current_user)) -> AuthContext:
        actor.require_role(*roles)
        return actor

    return _require
