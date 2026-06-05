"""Shared helpers for the Sprint 2 exit drills (run against the live dev stack).

Standalone operational scripts, not part of the application; they drive the API on
localhost and read the DB as the runtime role (RLS-scoped via the org GUC).
"""

from __future__ import annotations

import time

import httpx

API = "http://localhost:8008"
PG = "postgresql://onmixai_app:onmixai_app@localhost:5440/onmixai"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_and_login(client: httpx.Client, slug: str) -> tuple[str, str]:
    """Register an org + owner and return (access_token, org_id)."""
    email = f"o@{slug}.test"
    client.post(
        f"{API}/api/v1/auth/register",
        json={
            "name": slug,
            "slug": slug,
            "owner_email": email,
            "password": "password-123456",
            "full_name": "O",
        },
    )
    token = client.post(
        f"{API}/api/v1/auth/login",
        json={"org_slug": slug, "email": email, "password": "password-123456"},
    ).json()["access_token"]
    org_id = client.get(f"{API}/api/v1/users/me", headers=auth(token)).json()["org_id"]
    return token, org_id


def create_collection(client: httpx.Client, token: str, name: str = "Drill") -> str:
    return client.post(
        f"{API}/api/v1/collections", headers=auth(token), json={"name": name}
    ).json()["id"]


def upload(
    client: httpx.Client,
    token: str,
    collection_id: str,
    *,
    filename: str,
    data: bytes,
    content_type: str,
) -> str:
    response = client.post(
        f"{API}/api/v1/collections/{collection_id}/documents",
        headers=auth(token),
        files={"file": (filename, data, content_type)},
    )
    return response.json()["document_id"]


def status(client: httpx.Client, token: str, document_id: str) -> str:
    return client.get(f"{API}/api/v1/documents/{document_id}", headers=auth(token)).json()[
        "status"
    ]


def document(client: httpx.Client, token: str, document_id: str) -> dict:
    return client.get(f"{API}/api/v1/documents/{document_id}", headers=auth(token)).json()


def wait_for_terminal(
    client: httpx.Client, token: str, document_id: str, *, timeout: float
) -> str:
    """Poll until the document reaches a terminal state (ready/failed) or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = status(client, token, document_id)
        if current in ("ready", "failed"):
            return current
        time.sleep(0.2)
    raise SystemExit(f"timeout waiting for {document_id} to reach a terminal state")
