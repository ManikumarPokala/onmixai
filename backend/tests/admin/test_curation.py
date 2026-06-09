"""Feedback → golden curation — owner/admin only; surfaced content is PII-redacted; candidates are
human-gated (compare-and-set, decided once) and never auto-merged into the eval golden set."""

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.conversation.models import (
    ChatMessage,
    ChatRole,
    ChatSession,
    FeedbackRating,
    MessageFeedback,
)
from src.identity.models import Role
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.admin.conftest import AdminHarness, AdminOrg, auth, seed_org

_PII_ANSWER = "Reach the owner at jane@acme.com or 555-123-4567 for the Zarvon."


async def _seed_qa_with_up_feedback(
    session: AsyncSession, org: AdminOrg, *, answer_content: str = _PII_ANSWER
) -> UUID:
    """A session with a user question + assistant answer (seq 1, 2) and an UP vote on the answer.
    Returns the assistant message id."""
    user_id = org.user_ids[Role.MEMBER]
    session_id, answer_id = uuid4(), uuid4()
    session.add(ChatSession(id=session_id, org_id=org.org_id, owner_user_id=user_id, title="t"))
    await session.flush()
    session.add(
        ChatMessage(
            org_id=org.org_id,
            session_id=session_id,
            role=ChatRole.USER,
            content="Who do I contact about a Zarvon?",
            seq=1,
            citations=[],
        )
    )
    session.add(
        ChatMessage(
            id=answer_id,
            org_id=org.org_id,
            session_id=session_id,
            role=ChatRole.ASSISTANT,
            content=answer_content,
            seq=2,
            citations=[],
        )
    )
    await session.flush()
    session.add(
        MessageFeedback(
            org_id=org.org_id, message_id=answer_id, user_id=user_id, rating=FeedbackRating.UP
        )
    )
    await session.flush()
    return answer_id


async def test_member_forbidden_on_curation(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    member = auth(org.tokens[Role.MEMBER])
    assert (
        await admin_harness.client.get("/api/v1/admin/feedback/review", headers=member)
    ).status_code == 403
    assert (
        await admin_harness.client.get("/api/v1/admin/golden-candidates", headers=member)
    ).status_code == 403
    assert (
        await admin_harness.client.post(f"/api/v1/admin/feedback/{uuid4()}/promote", headers=member)
    ).status_code == 403


async def test_review_queue_surfaces_redacted_content(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    await _seed_qa_with_up_feedback(db_session, org)
    resp = await admin_harness.client.get(
        "/api/v1/admin/feedback/review", headers=auth(org.tokens[Role.ADMIN])
    )
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    # The raw email/phone never reach the reviewer; placeholders + counts do.
    assert "jane@acme.com" not in item["answer"] and "555-123-4567" not in item["answer"]
    assert "[REDACTED_EMAIL]" in item["answer"] and "[REDACTED_PHONE]" in item["answer"]
    assert item["redaction_counts"] == {"email": 1, "phone": 1}


async def test_promote_stores_redacted_candidate_and_audits(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    answer_id = await _seed_qa_with_up_feedback(db_session, org)
    resp = await admin_harness.client.post(
        f"/api/v1/admin/feedback/{answer_id}/promote", headers=auth(org.tokens[Role.OWNER])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending" and body["source_message_id"] == str(answer_id)
    assert "jane@acme.com" not in body["answer"]  # candidate stores redacted content only
    await set_tenant_context(db_session, org.org_id)
    # Raw PII is not persisted on the candidate row, and the promotion is audited.
    stored = (
        await db_session.execute(
            text("SELECT answer FROM golden_candidates WHERE id = :id"), {"id": body["id"]}
        )
    ).scalar_one()
    assert "jane@acme.com" not in stored and "[REDACTED_EMAIL]" in stored
    audited = (
        await db_session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'golden_candidate.promoted'")
        )
    ).scalar_one()
    assert audited == 1


async def test_promote_unknown_message_is_404(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.post(
        f"/api/v1/admin/feedback/{uuid4()}/promote", headers=auth(org.tokens[Role.ADMIN])
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "MESSAGE_NOT_FOUND"


async def test_decision_is_human_gated_and_decided_once(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    answer_id = await _seed_qa_with_up_feedback(db_session, org)
    owner = auth(org.tokens[Role.OWNER])
    candidate_id = (
        await admin_harness.client.post(
            f"/api/v1/admin/feedback/{answer_id}/promote", headers=owner
        )
    ).json()["id"]
    # First decision wins...
    approve = await admin_harness.client.post(
        f"/api/v1/admin/golden-candidates/{candidate_id}/decision",
        json={"decision": "approve"},
        headers=owner,
    )
    assert approve.status_code == 200 and approve.json()["status"] == "approved"
    # ...a second decision on the now-terminal candidate is rejected (compare-and-set).
    again = await admin_harness.client.post(
        f"/api/v1/admin/golden-candidates/{candidate_id}/decision",
        json={"decision": "reject"},
        headers=owner,
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "GOLDEN_CANDIDATE_ALREADY_DECIDED"
    # Approval recorded the decision and audited it — and wrote nothing to the eval golden set.
    await set_tenant_context(db_session, org.org_id)
    audited = (
        await db_session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'golden_candidate.approved'")
        )
    ).scalar_one()
    assert audited == 1


async def test_candidates_are_org_isolated(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org_a = await seed_org(db_session, settings, slug="a")
    org_b = await seed_org(db_session, settings, slug="b")
    answer_b = await _seed_qa_with_up_feedback(db_session, org_b)
    # Org A cannot promote org B's message — it is invisible (404).
    resp = await admin_harness.client.post(
        f"/api/v1/admin/feedback/{answer_b}/promote", headers=auth(org_a.tokens[Role.ADMIN])
    )
    assert resp.status_code == 404
    # Org A's candidate list never shows org B's data.
    listing = await admin_harness.client.get(
        "/api/v1/admin/golden-candidates", headers=auth(org_a.tokens[Role.ADMIN])
    )
    assert listing.json()["candidates"] == []
