"""Identity internal DTOs.

These are the domain DTOs returned by services and consumed by routers — never
ORM models cross the layer boundary (patterns.md §1, §8). Task 7 adds the
request/response Pydantic schemas; conversions live here as classmethods.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

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
    max_documents: int
    created_at: datetime

    @classmethod
    def from_model(cls, org: Organization) -> "OrganizationDTO":
        return cls(
            id=org.id,
            name=org.name,
            slug=org.slug,
            max_documents=org.max_documents,
            created_at=org.created_at,
        )


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


# --- Request schemas (validated input from clients) ---


class RegisterRequest(BaseModel):
    name: str
    slug: str
    owner_email: str
    password: str
    full_name: str


class LoginRequest(BaseModel):
    org_slug: str
    email: str
    password: str


class RefreshRequest(BaseModel):
    org_slug: str
    refresh_token: str


class LogoutRequest(BaseModel):
    org_slug: str
    refresh_token: str


# --- Response schemas (allow-lists; sensitive fields are structurally absent) ---


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    max_documents: int
    created_at: datetime

    @classmethod
    def from_dto(cls, org: OrganizationDTO) -> "OrganizationResponse":
        return cls(
            id=org.id,
            name=org.name,
            slug=org.slug,
            max_documents=org.max_documents,
            created_at=org.created_at,
        )


class UserResponse(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime

    @classmethod
    def from_dto(cls, user: UserDTO) -> "UserResponse":
        return cls(
            id=user.id,
            org_id=user.org_id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
        )


class RegisterResponse(BaseModel):
    organization: OrganizationResponse
    user: UserResponse

    @classmethod
    def from_result(cls, result: RegistrationResult) -> "RegisterResponse":
        return cls(
            organization=OrganizationResponse.from_dto(result.organization),
            user=UserResponse.from_dto(result.owner),
        )


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int

    @classmethod
    def from_tokens(cls, tokens: IssuedTokens) -> "TokenResponse":
        return cls(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_type=tokens.token_type,
            expires_in=tokens.expires_in,
        )


class UserPage(BaseModel):
    users: list[UserResponse]
    next_cursor: str | None


class ChangeRoleRequest(BaseModel):
    role: Role


class UpdateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # The org's document quota; None leaves it unchanged. A positive cap only.
    max_documents: int | None = Field(default=None, ge=1)
