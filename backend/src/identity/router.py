"""Identity HTTP routes — thin: validate, call one service method, shape response."""

from fastapi import APIRouter, Depends, Request, status

from src.identity.dependencies import get_auth_service, get_current_user, require_role
from src.identity.models import Role
from src.identity.schemas import (
    AuthContext,
    LoginRequest,
    LogoutRequest,
    OrganizationResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserResponse,
)
from src.identity.service import AuthService
from src.shared.ratelimit import AUTH_RATE_LIMIT, limiter, set_org_scoped_key

router = APIRouter()

# Built once (not inside a default-argument call) so ruff B008 stays satisfied.
_require_owner_or_admin = require_role(Role.OWNER, Role.ADMIN)


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, service: AuthService = Depends(get_auth_service)
) -> RegisterResponse:
    result = await service.register_organization(
        name=body.name,
        slug=body.slug,
        owner_email=body.owner_email,
        password=body.password,
        full_name=body.full_name,
    )
    return RegisterResponse.from_result(result)


@router.post("/auth/login", dependencies=[Depends(set_org_scoped_key)])
@limiter.limit(AUTH_RATE_LIMIT)
async def login(
    request: Request, body: LoginRequest, service: AuthService = Depends(get_auth_service)
) -> TokenResponse:
    tokens = await service.authenticate(
        org_slug=body.org_slug, email=body.email, password=body.password
    )
    return TokenResponse.from_tokens(tokens)


@router.post("/auth/refresh", dependencies=[Depends(set_org_scoped_key)])
@limiter.limit(AUTH_RATE_LIMIT)
async def refresh(
    request: Request, body: RefreshRequest, service: AuthService = Depends(get_auth_service)
) -> TokenResponse:
    tokens = await service.refresh(org_slug=body.org_slug, raw_token=body.refresh_token)
    return TokenResponse.from_tokens(tokens)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: LogoutRequest, service: AuthService = Depends(get_auth_service)) -> None:
    await service.logout(org_slug=body.org_slug, raw_token=body.refresh_token)


@router.get("/users/me")
async def read_current_user(
    actor: AuthContext = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    return UserResponse.from_dto(await service.get_user(actor))


@router.get("/orgs/me")
async def read_current_org(
    actor: AuthContext = Depends(_require_owner_or_admin),
    service: AuthService = Depends(get_auth_service),
) -> OrganizationResponse:
    return OrganizationResponse.from_dto(await service.get_organization(actor))
