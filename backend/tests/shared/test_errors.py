"""Tests for the error envelope and global exception handlers."""

import httpx
import pytest


async def test_app_error_envelope_shape_and_status(client: httpx.AsyncClient) -> None:
    response = await client.get("/not-found")
    assert response.status_code == 404
    body = response.json()
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == "THING_NOT_FOUND"
    assert body["error"]["request_id"]


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/conflict", 409, "THING_CONFLICT"),
        ("/forbidden", 403, "FORBIDDEN"),
        ("/rate-limited", 429, "RATE_LIMITED"),
    ],
)
async def test_each_error_maps_to_its_status(
    client: httpx.AsyncClient, path: str, status: int, code: str
) -> None:
    response = await client.get(path)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_validation_error_uses_envelope_with_fields(client: httpx.AsyncClient) -> None:
    response = await client.post("/validate", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["error"]["fields"], list)
    assert body["error"]["request_id"]


async def test_unhandled_exception_returns_generic_500_without_internals(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "An internal error occurred"
    # No internals leak: no traceback, exception type, SQL, or internal paths.
    raw = response.text
    for leaked in ("Traceback", "RuntimeError", "super-secret", "SELECT", "/src/"):
        assert leaked not in raw
