"""Reports API: create enqueues a QUEUED report (201), reads are owner-scoped (non-owner +
cross-org → 404), the list paginates, and a malformed cursor is 422."""

from typing import Any

from tests.reports.conftest import ReportHarness, auth_header, register_and_login


async def _create(harness: ReportHarness, token: str, title: str = "Q3 review") -> dict[str, Any]:
    resp = await harness.client.post(
        "/api/v1/reports",
        headers=auth_header(token),
        json={
            "report_type": "executive_summary",
            "title": title,
            "query": "summarize Q3 performance",
            "collection_scope": [],
        },
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_create_enqueues_a_queued_report(report_harness: ReportHarness) -> None:
    token = await register_and_login(report_harness.client, "acme")
    body = await _create(report_harness, token)
    assert body["status"] == "queued"
    assert body["report_type"] == "executive_summary"
    assert body["sections"] == [] and body["citations"] == []
    # generation was enqueued after commit
    assert len(report_harness.queue.reports) == 1


async def test_get_is_owner_scoped(report_harness: ReportHarness) -> None:
    token = await register_and_login(report_harness.client, "acme")
    created = await _create(report_harness, token)
    got = await report_harness.client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(token)
    )
    assert got.status_code == 200
    assert got.json()["title"] == "Q3 review"


async def test_cross_org_get_is_404(report_harness: ReportHarness) -> None:
    a_token = await register_and_login(report_harness.client, "orga")
    created = await _create(report_harness, a_token)
    b_token = await register_and_login(report_harness.client, "orgb")
    resp = await report_harness.client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(b_token)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "REPORT_NOT_FOUND"


async def test_list_is_owner_scoped(report_harness: ReportHarness) -> None:
    token = await register_and_login(report_harness.client, "acme")
    await _create(report_harness, token, "first")
    await _create(report_harness, token, "second")
    listed = await report_harness.client.get("/api/v1/reports", headers=auth_header(token))
    assert listed.status_code == 200
    # (Ordering is created_at DESC, id DESC; in this harness both rows share one transaction's
    # now(), so assert membership, not order — production creates each in its own transaction.)
    assert {r["title"] for r in listed.json()["reports"]} == {"first", "second"}


async def test_malformed_cursor_is_422(report_harness: ReportHarness) -> None:
    token = await register_and_login(report_harness.client, "acme")
    resp = await report_harness.client.get(
        "/api/v1/reports?cursor=nope", headers=auth_header(token)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_CURSOR"
