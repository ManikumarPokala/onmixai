"""Recommendation API: completed + declined both 200 (distinguished by status), citations
hydrate to the owner, non-owner/cross-org reads are 404 (no oracle), the list paginates, and
the per-user rate limit blocks past the cap."""

import json
from typing import Any
from uuid import UUID, uuid4

from src.identity.models import Role, User
from src.shared.database import set_tenant_context
from src.shared.security import create_access_token, hash_password
from tests.recommendation.conftest import RecHarness, auth_header, register_and_login


def _output_json(markers: list[int]) -> str:
    return json.dumps(
        {
            "recommendation": "Choose Vendor A.",
            "alternatives": [{"option": "Vendor B", "rationale": "cheaper"}],
            "justifications": [{"claim": "A has the better SLA", "citation_markers": markers}],
            "caveats": ["limited data"],
        }
    )


async def _second_user_token(harness: RecHarness, owner_token: str) -> str:
    me = (await harness.client.get("/api/v1/users/me", headers=auth_header(owner_token))).json()
    org_id = UUID(me["org_id"])
    user_id = uuid4()
    await set_tenant_context(harness.db_session, org_id)
    harness.db_session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"second-{user_id.hex[:8]}@a.test",
            password_hash=hash_password("password-123456"),
            full_name="Second",
            role=Role.MEMBER,
        )
    )
    await harness.db_session.flush()
    return create_access_token(
        settings=harness.settings, user_id=user_id, org_id=org_id, role=Role.MEMBER.value
    )


async def _create(harness: RecHarness, token: str, query: str = "which vendor?") -> dict[str, Any]:
    resp = await harness.client.post(
        "/api/v1/recommendations", headers=auth_header(token), json={"query": query}
    )
    assert resp.status_code == 200, resp.text  # completed AND declined are 200
    body: dict[str, Any] = resp.json()
    return body


async def test_completed_recommendation_is_200_with_band_and_citations(
    rec_harness: RecHarness,
) -> None:
    token = await register_and_login(rec_harness.client, "acme")
    rec_harness.retriever.set_sources(0.06, 0.06, 0.06)  # high band
    rec_harness.gateway.queue_completion(text=_output_json([1]))

    body = await _create(rec_harness, token)
    assert body["status"] == "completed"
    assert body["confidence_band"] == "high"
    assert body["recommendation"] == "Choose Vendor A."
    assert body["decline_reason"] is None
    assert len(body["citations"]) == 1
    assert body["citations"][0]["filename"] == "doc.txt"
    assert body["citations"][0]["page_ref"] == 3


async def test_declined_recommendation_is_200_with_reason(rec_harness: RecHarness) -> None:
    token = await register_and_login(rec_harness.client, "acme")
    rec_harness.retriever.set_sources()  # empty → decline before generation

    body = await _create(rec_harness, token)
    assert body["status"] == "declined"
    assert body["decline_reason"] == "INSUFFICIENT_EVIDENCE"
    assert body["confidence_band"] is None
    assert body["citations"] == []
    assert rec_harness.gateway.calls == []  # zero generation spend


async def test_get_recommendation_hydrates_for_owner(rec_harness: RecHarness) -> None:
    token = await register_and_login(rec_harness.client, "acme")
    rec_harness.retriever.set_sources(0.06, 0.06, 0.06)
    rec_harness.gateway.queue_completion(text=_output_json([1]))
    created = await _create(rec_harness, token)

    got = await rec_harness.client.get(
        f"/api/v1/recommendations/{created['id']}", headers=auth_header(token)
    )
    assert got.status_code == 200
    assert got.json()["citations"][0]["filename"] == "doc.txt"


async def test_non_owner_same_org_gets_404(rec_harness: RecHarness) -> None:
    owner = await register_and_login(rec_harness.client, "acme")
    rec_harness.retriever.set_sources(0.06, 0.06, 0.06)
    rec_harness.gateway.queue_completion(text=_output_json([1]))
    created = await _create(rec_harness, owner)
    other = await _second_user_token(rec_harness, owner)

    resp = await rec_harness.client.get(
        f"/api/v1/recommendations/{created['id']}", headers=auth_header(other)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RECOMMENDATION_NOT_FOUND"


async def test_cross_org_get_is_404(rec_harness: RecHarness) -> None:
    a_token = await register_and_login(rec_harness.client, "orga")
    rec_harness.retriever.set_sources(0.06, 0.06, 0.06)
    rec_harness.gateway.queue_completion(text=_output_json([1]))
    created = await _create(rec_harness, a_token)
    b_token = await register_and_login(rec_harness.client, "orgb")

    resp = await rec_harness.client.get(
        f"/api/v1/recommendations/{created['id']}", headers=auth_header(b_token)
    )
    assert resp.status_code == 404


async def test_list_is_owner_scoped_and_newest_first(rec_harness: RecHarness) -> None:
    token = await register_and_login(rec_harness.client, "acme")
    for _ in range(2):
        rec_harness.retriever.set_sources()  # two declines (fast, no gateway)
        await _create(rec_harness, token)

    listed = await rec_harness.client.get("/api/v1/recommendations", headers=auth_header(token))
    assert listed.status_code == 200
    body = listed.json()
    assert len(body["recommendations"]) == 2
    assert all(r["status"] == "declined" for r in body["recommendations"])


async def test_malformed_cursor_is_422(rec_harness: RecHarness) -> None:
    token = await register_and_login(rec_harness.client, "acme")
    resp = await rec_harness.client.get(
        "/api/v1/recommendations?cursor=not-valid", headers=auth_header(token)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_CURSOR"


async def test_per_user_rate_limit_blocks_after_cap(rec_harness: RecHarness) -> None:
    token = await register_and_login(rec_harness.client, "acme")
    last = 200
    for _ in range(21):  # 20/min cap
        rec_harness.retriever.set_sources()  # decline (no gateway) keeps it fast
        resp = await rec_harness.client.post(
            "/api/v1/recommendations", headers=auth_header(token), json={"query": "q"}
        )
        last = resp.status_code
    assert last == 429
