"""API tests for the identity routes (real app via create_app, RLS-backed)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role, User
from src.identity.repository import UserRepository
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from src.shared.security import create_access_token

_PASSWORD = "password-123456"
_REGISTER = {
    "name": "Acme",
    "slug": "acme",
    "owner_email": "owner@acme.test",
    "password": _PASSWORD,
    "full_name": "Owner",
}
_LOGIN = {"org_slug": "acme", "email": "owner@acme.test", "password": _PASSWORD}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_full_auth_flow(api_client: httpx.AsyncClient) -> None:
    register = await api_client.post("/api/v1/auth/register", json=_REGISTER)
    assert register.status_code == 201
    assert "password" not in register.text  # no password field is ever serialized

    login = await api_client.post("/api/v1/auth/login", json=_LOGIN)
    assert login.status_code == 200
    access, refresh_token = login.json()["access_token"], login.json()["refresh_token"]

    me = await api_client.get("/api/v1/users/me", headers=_auth(access))
    assert me.status_code == 200
    assert me.json()["email"] == "owner@acme.test"

    rotated = await api_client.post(
        "/api/v1/auth/refresh", json={"org_slug": "acme", "refresh_token": refresh_token}
    )
    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]

    me_again = await api_client.get(
        "/api/v1/users/me", headers=_auth(rotated.json()["access_token"])
    )
    assert me_again.status_code == 200

    logout = await api_client.post(
        "/api/v1/auth/logout", json={"org_slug": "acme", "refresh_token": new_refresh}
    )
    assert logout.status_code == 204

    reused = await api_client.post(
        "/api/v1/auth/refresh", json={"org_slug": "acme", "refresh_token": new_refresh}
    )
    assert reused.status_code == 401


async def test_owner_can_read_org(api_client: httpx.AsyncClient) -> None:
    await api_client.post("/api/v1/auth/register", json=_REGISTER)
    access = (await api_client.post("/api/v1/auth/login", json=_LOGIN)).json()["access_token"]
    org = await api_client.get("/api/v1/orgs/me", headers=_auth(access))
    assert org.status_code == 200
    assert org.json()["slug"] == "acme"


async def test_missing_token_returns_401_envelope(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/v1/users/me")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "MISSING_TOKEN"
    assert body["error"]["request_id"]


async def test_expired_token_rejected(api_client: httpx.AsyncClient, settings: Settings) -> None:
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
    response = await api_client.get("/api/v1/users/me", headers=_auth(expired))
    assert response.status_code == 401


async def test_member_forbidden_on_org_admin_route(
    api_client: httpx.AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    register = await api_client.post("/api/v1/auth/register", json=_REGISTER)
    org_id = UUID(register.json()["organization"]["id"])

    await set_tenant_context(db_session, org_id)
    member = await UserRepository(db_session).create(
        User(
            org_id=org_id,
            email="member@acme.test",
            password_hash="unused",
            full_name="Member",
            role=Role.MEMBER,
        )
    )
    token = create_access_token(settings=settings, user_id=member.id, org_id=org_id, role="member")
    response = await api_client.get("/api/v1/orgs/me", headers=_auth(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_login_rate_limited_returns_429_envelope(api_client: httpx.AsyncClient) -> None:
    payload = {"org_slug": "acme", "email": "nobody@acme.test", "password": "wrong-password-x"}
    statuses = [
        (await api_client.post("/api/v1/auth/login", json=payload)).status_code for _ in range(11)
    ]
    assert statuses[-1] == 429
    blocked = await api_client.post("/api/v1/auth/login", json=payload)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
