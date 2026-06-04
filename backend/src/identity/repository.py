"""Identity repositories — the only place identity queries live (patterns.md §2).

Repositories return ORM models / None and make no business decisions. Every
method touching a tenant-owned table takes tenant context (``org_id``)
explicitly; a method without it must not exist (CLAUDE.md §4). Application-level
org scoping sits alongside Postgres RLS as defense in depth.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Organization, RefreshToken, User


class OrganizationRepository:
    """Organizations are the tenant root (no RLS); lookup by slug enables login."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, organization: Organization) -> Organization:
        self._session.add(organization)
        await self._session.flush()
        await self._session.refresh(organization)
        return organization

    async def get_by_slug(self, slug: str) -> Organization | None:
        stmt = select(Organization).where(Organization.slug == slug)
        return (await self._session.execute(stmt)).scalar_one_or_none()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def get_by_email(self, org_id: UUID, email: str) -> User | None:
        stmt = select(User).where(User.org_id == org_id, User.email == email)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, org_id: UUID, user_id: UUID) -> User | None:
        stmt = select(User).where(User.org_id == org_id, User.id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()


class RefreshTokenRepository:
    """Every method is org-scoped — token-theft containment is tenant-scoped too."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, token: RefreshToken) -> RefreshToken:
        self._session.add(token)
        await self._session.flush()
        await self._session.refresh(token)
        return token

    async def get_by_hash(self, org_id: UUID, token_hash: str) -> RefreshToken | None:
        """Return the unique token record for (org_id, hash), in any state.

        Returns revoked/expired rows too so the service can apply the reuse and
        expiry rules; ``token_hash`` is globally unique. Time: O(1) index probe.
        """
        stmt = select(RefreshToken).where(
            RefreshToken.org_id == org_id, RefreshToken.token_hash == token_hash
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def revoke(self, org_id: UUID, token_id: UUID, revoked_at: datetime) -> None:
        """Revoke a single token (no-op if already revoked). Time: O(1)."""
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.org_id == org_id,
                RefreshToken.id == token_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        await self._session.execute(stmt)

    async def revoke_all_for_user(self, org_id: UUID, user_id: UUID, revoked_at: datetime) -> None:
        """Revoke every live token for a user (theft containment). Time: O(k) rows."""
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.org_id == org_id,
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        await self._session.execute(stmt)
