"""Identity internal DTOs.

These are the domain DTOs returned by services and consumed by routers — never
ORM models cross the layer boundary (patterns.md §1, §8). Task 7 adds the
request/response Pydantic schemas; conversions live here as classmethods.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.identity.models import Organization, Role, User
from src.shared.errors import AuthorizationError


@dataclass(frozen=True, slots=True)
class AuthContext:
    """The authenticated actor, derived from a verified access token."""

    user_id: UUID
    org_id: UUID
    role: Role

    def require_role(self, *roles: Role) -> None:
        """Raise AuthorizationError unless the actor holds one of ``roles``."""
        if self.role not in roles:
            raise AuthorizationError("FORBIDDEN")


@dataclass(frozen=True, slots=True)
class OrganizationDTO:
    id: UUID
    name: str
    slug: str
    created_at: datetime

    @classmethod
    def from_model(cls, org: Organization) -> "OrganizationDTO":
        return cls(id=org.id, name=org.name, slug=org.slug, created_at=org.created_at)


@dataclass(frozen=True, slots=True)
class UserDTO:
    id: UUID
    org_id: UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime

    @classmethod
    def from_model(cls, user: User) -> "UserDTO":
        return cls(
            id=user.id,
            org_id=user.org_id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
        )


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    organization: OrganizationDTO
    owner: UserDTO


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"
