"""Conversation schema: forced RLS on every tenant table, and runtime-role CRUD under
tenant context across the session → message → feedback/summary chain."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.conversation.models import (
    ChatMessage,
    ChatRole,
    ChatSession,
    FeedbackRating,
    MessageFeedback,
    SessionSummary,
)
from src.identity.service import AuthService
from src.shared.database import set_tenant_context

_TABLES = ("chat_sessions", "chat_messages", "message_feedback", "session_summaries")


@pytest.mark.parametrize("table", _TABLES)
async def test_forced_rls_enabled_on_conversation_tables(
    db_session: AsyncSession, table: str
) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = :t AND relkind = 'r'"
            ),
            {"t": table},
        )
    ).one()
    assert row == (True, True)  # RLS enabled AND forced (CLAUDE.md §4)


async def test_runtime_role_crud_session_message_feedback_summary(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org = await auth_service.register_organization(
        name="ChatOrg",
        slug="chat-org",
        owner_email="o@chat.test",
        full_name="O",
        password="password-123456",
    )
    org_id, user_id = org.organization.id, org.owner.id
    await set_tenant_context(db_session, org_id)

    session = ChatSession(org_id=org_id, owner_user_id=user_id, title="First chat")
    db_session.add(session)
    await db_session.flush()

    db_session.add(
        ChatMessage(
            org_id=org_id, session_id=session.id, role=ChatRole.USER, content="hello", seq=0
        )
    )
    assistant = ChatMessage(
        org_id=org_id,
        session_id=session.id,
        role=ChatRole.ASSISTANT,
        content="the answer is 42 [1]",
        citations=[{"chunk_id": str(user_id), "document_id": str(org_id), "marker_index": 1}],
        prompt_version="1.1.0",
        model_used="openai/gpt-4o-mini",
        trace_id="trace-1",
        seq=1,
    )
    db_session.add(assistant)
    await db_session.flush()

    db_session.add(
        MessageFeedback(
            org_id=org_id, message_id=assistant.id, user_id=user_id, rating=FeedbackRating.UP
        )
    )
    db_session.add(
        SessionSummary(org_id=org_id, session_id=session.id, summary="a summary", through_seq=1)
    )
    await db_session.flush()

    count = (await db_session.execute(text("SELECT count(*) FROM chat_messages"))).scalar_one()
    assert count == 2
    cited = (
        await db_session.execute(
            text("SELECT citations FROM chat_messages WHERE role = 'assistant'")
        )
    ).scalar_one()
    assert cited[0]["marker_index"] == 1


async def test_rls_hides_sessions_from_other_org(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org = await auth_service.register_organization(
        name="ChatOrgB",
        slug="chat-org-b",
        owner_email="o@chatb.test",
        full_name="O",
        password="password-123456",
    )
    org_id, user_id = org.organization.id, org.owner.id
    await set_tenant_context(db_session, org_id)
    db_session.add(ChatSession(org_id=org_id, owner_user_id=user_id))
    await db_session.flush()
    assert (await db_session.execute(text("SELECT count(*) FROM chat_sessions"))).scalar_one() == 1

    await set_tenant_context(db_session, user_id)  # any other org_id → RLS hides the row
    assert (await db_session.execute(text("SELECT count(*) FROM chat_sessions"))).scalar_one() == 0
