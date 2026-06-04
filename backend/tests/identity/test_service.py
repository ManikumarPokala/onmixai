"""Integration tests for the identity auth service (real Postgres, RLS active)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.dependencies import get_current_user
from src.identity.models import Role
from src.identity.repository import UserRepository
from src.identity.schemas import RegistrationResult
from src.identity.service import AuthService
from src.shared.config import Settings
from src.shared.errors import AuthenticationError, ConflictError, ValidationFailedError
from src.shared.security import create_access_token

_PASSWORD = "password-123456"
_SLUG = "acme"
_EMAIL = "owner@acme.test"


async def _register(
    service: AuthService, *, slug: str = _SLUG, email: str = _EMAIL
) -> RegistrationResult:
    return await service.register_organization(
        name="Acme", slug=slug, owner_email=email, password=_PASSWORD, full_name="Owner"
    )


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_register_creates_org_and_owner(auth_service: AuthService) -> None:
    result = await _register(auth_service)
    assert result.organization.slug == _SLUG
    assert result.owner.email == _EMAIL
    assert result.owner.role is Role.OWNER


async def test_duplicate_slug_conflicts(auth_service: AuthService) -> None:
    await _register(auth_service)
    with pytest.raises(ConflictError) as exc:
        await _register(auth_service, email="other@acme.test")
    assert exc.value.code == "ORG_SLUG_TAKEN"


async def test_weak_password_rejected(auth_service: AuthService) -> None:
    with pytest.raises(ValidationFailedError):
        await auth_service.register_organization(
            name="A", slug="weak", owner_email="a@a.test", password="short", full_name="A"
        )


async def test_wrong_password_and_wrong_email_are_indistinguishable(
    auth_service: AuthService,
) -> None:
    await _register(auth_service)
    with pytest.raises(AuthenticationError) as wrong_pw:
        await auth_service.authenticate(org_slug=_SLUG, email=_EMAIL, password="wrong-password-x")
    with pytest.raises(AuthenticationError) as wrong_email:
        await auth_service.authenticate(org_slug=_SLUG, email="ghost@acme.test", password=_PASSWORD)
    assert wrong_pw.value.code == wrong_email.value.code == "INVALID_CREDENTIALS"


async def test_inactive_user_rejected(auth_service: AuthService, db_session: AsyncSession) -> None:
    result = await _register(auth_service)
    user = await UserRepository(db_session).get_by_id(result.organization.id, result.owner.id)
    assert user is not None
    user.is_active = False
    await db_session.flush()
    with pytest.raises(AuthenticationError) as exc:
        await auth_service.authenticate(org_slug=_SLUG, email=_EMAIL, password=_PASSWORD)
    assert exc.value.code == "INVALID_CREDENTIALS"


async def test_refresh_rotates_tokens(auth_service: AuthService) -> None:
    await _register(auth_service)
    tokens = await auth_service.authenticate(org_slug=_SLUG, email=_EMAIL, password=_PASSWORD)
    rotated = await auth_service.refresh(org_slug=_SLUG, raw_token=tokens.refresh_token)
    assert rotated.refresh_token != tokens.refresh_token
    assert rotated.access_token


async def test_refresh_reuse_revokes_all_tokens(auth_service: AuthService) -> None:
    await _register(auth_service)
    tokens = await auth_service.authenticate(org_slug=_SLUG, email=_EMAIL, password=_PASSWORD)
    rotated = await auth_service.refresh(org_slug=_SLUG, raw_token=tokens.refresh_token)

    with pytest.raises(AuthenticationError) as exc:
        await auth_service.refresh(org_slug=_SLUG, raw_token=tokens.refresh_token)
    assert exc.value.code == "REFRESH_TOKEN_REUSED"

    # Theft containment: the rotated token is now revoked too.
    with pytest.raises(AuthenticationError):
        await auth_service.refresh(org_slug=_SLUG, raw_token=rotated.refresh_token)


async def test_logout_revokes_refresh_token(auth_service: AuthService) -> None:
    await _register(auth_service)
    tokens = await auth_service.authenticate(org_slug=_SLUG, email=_EMAIL, password=_PASSWORD)
    await auth_service.logout(org_slug=_SLUG, raw_token=tokens.refresh_token)
    with pytest.raises(AuthenticationError):
        await auth_service.refresh(org_slug=_SLUG, raw_token=tokens.refresh_token)


async def test_expired_access_token_rejected(db_session: AsyncSession, settings: Settings) -> None:
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "org_id": str(uuid4()),
            "role": "owner",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "jti": uuid4().hex,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(AuthenticationError):
        await get_current_user(credentials=_bearer(expired), session=db_session, settings=settings)


async def test_tampered_access_token_rejected(db_session: AsyncSession, settings: Settings) -> None:
    token = create_access_token(settings=settings, user_id=uuid4(), org_id=uuid4(), role="owner")
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(AuthenticationError):
        await get_current_user(credentials=_bearer(tampered), session=db_session, settings=settings)


async def test_get_current_user_returns_bound_context(
    auth_service: AuthService, db_session: AsyncSession, settings: Settings
) -> None:
    result = await _register(auth_service)
    tokens = await auth_service.authenticate(org_slug=_SLUG, email=_EMAIL, password=_PASSWORD)
    actor = await get_current_user(
        credentials=_bearer(tokens.access_token), session=db_session, settings=settings
    )
    assert actor.org_id == result.organization.id
    assert actor.user_id == result.owner.id
    assert actor.role is Role.OWNER
