"""Identity auth service — registration, authentication, refresh rotation, logout.

Service methods follow the patterns.md §1 anatomy and own no transaction (the
request scope commits). Tenant context is set on the session before any
tenant-scoped read/write so Postgres RLS applies. Refresh tokens are opaque and
stored only as SHA-256 hashes; their org_id always comes from the resolved
user/org server-side, never from request input.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Organization, RefreshToken, Role, User
from src.identity.repository import (
    OrganizationRepository,
    RefreshTokenRepository,
    UserRepository,
)
from src.identity.rules import (
    ensure_password_strong,
    is_refresh_token_expired,
    is_refresh_token_reused,
)
from src.identity.schemas import IssuedTokens, OrganizationDTO, RegistrationResult, UserDTO
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from src.shared.errors import AuthenticationError, ConflictError
from src.shared.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    verify_password,
)

# A throwaway hash verified when no user matches, so authentication timing does
# not reveal whether an email exists (no user enumeration).
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-constant-time-compare")

_INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
_INVALID_REFRESH_TOKEN = "INVALID_REFRESH_TOKEN"


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        organizations: OrganizationRepository,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        settings: Settings,
    ) -> None:
        self._session = session
        self._orgs = organizations
        self._users = users
        self._refresh = refresh_tokens
        self._settings = settings

    async def register_organization(
        self, *, name: str, slug: str, owner_email: str, password: str, full_name: str
    ) -> RegistrationResult:
        """Create an organization and its owner user in one transaction."""
        ensure_password_strong(password)
        if await self._orgs.get_by_slug(slug) is not None:
            raise ConflictError("ORG_SLUG_TAKEN", message="Organization slug already in use")

        org = await self._orgs.create(Organization(name=name, slug=slug))
        # RLS (FORCE + WITH CHECK) requires the tenant context before inserting
        # the owner row into the now-existing organization.
        await set_tenant_context(self._session, org.id)
        owner = await self._users.create(
            User(
                org_id=org.id,
                email=owner_email,
                password_hash=hash_password(password),
                full_name=full_name,
                role=Role.OWNER,
            )
        )
        return RegistrationResult(
            organization=OrganizationDTO.from_model(org), owner=UserDTO.from_model(owner)
        )

    async def authenticate(self, *, org_slug: str, email: str, password: str) -> IssuedTokens:
        """Verify credentials and issue an access + refresh token pair.

        Wrong organization, wrong email, wrong password, and inactive users all
        return the identical error — no enumeration.
        """
        org = await self._orgs.get_by_slug(org_slug)
        if org is None:
            verify_password(_DUMMY_PASSWORD_HASH, password)
            raise AuthenticationError(_INVALID_CREDENTIALS)

        await set_tenant_context(self._session, org.id)
        user = await self._users.get_by_email(org.id, email)
        if user is None:
            verify_password(_DUMMY_PASSWORD_HASH, password)
            raise AuthenticationError(_INVALID_CREDENTIALS)
        if not verify_password(user.password_hash, password) or not user.is_active:
            raise AuthenticationError(_INVALID_CREDENTIALS)

        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        return await self._issue_tokens(user)

    async def refresh(self, *, org_slug: str, raw_token: str) -> IssuedTokens:
        """Rotate a refresh token; reuse of a revoked token revokes all of the user's."""
        org = await self._orgs.get_by_slug(org_slug)
        if org is None:
            raise AuthenticationError(_INVALID_REFRESH_TOKEN)
        await set_tenant_context(self._session, org.id)

        token = await self._refresh.get_by_hash(org.id, hash_refresh_token(raw_token))
        if token is None:
            raise AuthenticationError(_INVALID_REFRESH_TOKEN)

        now = datetime.now(UTC)
        if is_refresh_token_reused(token):
            await self._refresh.revoke_all_for_user(org.id, token.user_id, now)
            raise AuthenticationError("REFRESH_TOKEN_REUSED")
        if is_refresh_token_expired(token, now):
            raise AuthenticationError(_INVALID_REFRESH_TOKEN)

        user = await self._users.get_by_id(org.id, token.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError(_INVALID_REFRESH_TOKEN)

        await self._refresh.revoke(org.id, token.id, now)
        return await self._issue_tokens(user)

    async def logout(self, *, org_slug: str, raw_token: str) -> None:
        """Revoke the presented refresh token. Idempotent; never errors."""
        org = await self._orgs.get_by_slug(org_slug)
        if org is None:
            return
        await set_tenant_context(self._session, org.id)
        token = await self._refresh.get_by_hash(org.id, hash_refresh_token(raw_token))
        if token is not None and token.revoked_at is None:
            await self._refresh.revoke(org.id, token.id, datetime.now(UTC))

    async def _issue_tokens(self, user: User) -> IssuedTokens:
        """Mint an access JWT and a freshly-stored rotating refresh token."""
        access = create_access_token(
            settings=self._settings, user_id=user.id, org_id=user.org_id, role=user.role.value
        )
        raw_refresh = generate_refresh_token()
        expires_at = datetime.now(UTC) + timedelta(seconds=self._settings.refresh_token_ttl_seconds)
        await self._refresh.create(
            RefreshToken(
                org_id=user.org_id,
                user_id=user.id,
                token_hash=hash_refresh_token(raw_refresh),
                expires_at=expires_at,
            )
        )
        return IssuedTokens(
            access_token=access,
            refresh_token=raw_refresh,
            expires_in=self._settings.access_token_ttl_seconds,
        )
