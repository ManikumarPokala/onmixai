"""Tenant isolation suite (blocking forever after).

Runs against real Postgres as the non-superuser, non-bypassrls runtime role, so
both application scoping AND Postgres RLS are exercised. Proves org A can never
read org B's tenant rows through any public method, by direct id (IDOR), by email
lookup, by refresh-token hash, or by a raw unfiltered count; and that a token is
rejected once its user is deactivated.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.dependencies import get_current_user
from src.identity.repository import RefreshTokenRepository, UserRepository
from src.identity.schemas import RegistrationResult
from src.identity.service import AuthService
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from src.shared.errors import AuthenticationError
from src.shared.security import hash_refresh_token

_ORG_A = {"name": "OrgA", "slug": "org-a", "owner_email": "a@a.test", "full_name": "A"}
_ORG_B = {"name": "OrgB", "slug": "org-b", "owner_email": "b@b.test", "full_name": "B"}
_PASSWORD = "password-aaaaaa"


@pytest.fixture
async def seeded(
    auth_service: AuthService,
) -> AsyncIterator[tuple[RegistrationResult, RegistrationResult]]:
    org_a = await auth_service.register_organization(password=_PASSWORD, **_ORG_A)
    org_b = await auth_service.register_organization(password=_PASSWORD, **_ORG_B)
    yield org_a, org_b


async def test_cannot_read_other_orgs_user_by_id_or_email(
    seeded: tuple[RegistrationResult, RegistrationResult], db_session: AsyncSession
) -> None:
    org_a, org_b = seeded
    await set_tenant_context(db_session, org_a.organization.id)
    users = UserRepository(db_session)

    assert await users.get_by_id(org_a.organization.id, org_b.owner.id) is None  # IDOR
    assert await users.get_by_email(org_a.organization.id, org_b.owner.email) is None
    # Own user is still visible.
    assert await users.get_by_id(org_a.organization.id, org_a.owner.id) is not None


async def test_raw_count_respects_rls(
    seeded: tuple[RegistrationResult, RegistrationResult], db_session: AsyncSession
) -> None:
    org_a, org_b = seeded
    await set_tenant_context(db_session, org_a.organization.id)
    count_a = (await db_session.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert count_a == 1  # RLS hides org B even with no WHERE clause

    await set_tenant_context(db_session, org_b.organization.id)
    count_b = (await db_session.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert count_b == 1


async def test_cannot_read_other_orgs_refresh_token(
    seeded: tuple[RegistrationResult, RegistrationResult],
    db_session: AsyncSession,
    auth_service: AuthService,
) -> None:
    _, org_b = seeded
    tokens_b = await auth_service.authenticate(
        org_slug=org_b.organization.slug, email=org_b.owner.email, password=_PASSWORD
    )
    org_a = seeded[0]
    await set_tenant_context(db_session, org_a.organization.id)
    refresh = RefreshTokenRepository(db_session)
    found = await refresh.get_by_hash(
        org_a.organization.id, hash_refresh_token(tokens_b.refresh_token)
    )
    assert found is None


async def test_token_rejected_after_user_deactivated(
    seeded: tuple[RegistrationResult, RegistrationResult],
    db_session: AsyncSession,
    auth_service: AuthService,
    settings: Settings,
) -> None:
    org_a, _ = seeded
    tokens = await auth_service.authenticate(
        org_slug=org_a.organization.slug, email=org_a.owner.email, password=_PASSWORD
    )
    await set_tenant_context(db_session, org_a.organization.id)
    user = await UserRepository(db_session).get_by_id(org_a.organization.id, org_a.owner.id)
    assert user is not None
    user.is_active = False
    await db_session.flush()

    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=tokens.access_token)
    with pytest.raises(AuthenticationError):
        await get_current_user(credentials=credentials, session=db_session, settings=settings)
