"""Tests for the request-context middleware."""

import httpx


async def test_request_id_present_and_echoed_on_success(client: httpx.AsyncClient) -> None:
    response = await client.get("/ok")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


async def test_request_id_header_matches_body_on_error(client: httpx.AsyncClient) -> None:
    response = await client.get("/not-found")
    header_id = response.headers["X-Request-ID"]
    assert header_id == response.json()["error"]["request_id"]


async def test_request_id_is_unique_per_request(client: httpx.AsyncClient) -> None:
    first = (await client.get("/ok")).headers["X-Request-ID"]
    second = (await client.get("/ok")).headers["X-Request-ID"]
    assert first != second


async def test_request_id_echoed_on_unhandled_error(client: httpx.AsyncClient) -> None:
    response = await client.get("/boom")
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
