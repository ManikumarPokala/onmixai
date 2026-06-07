"""Rolling summary: the CAS upsert is idempotent (a stale/older through_seq never
overwrites a fresher summary), and summary generation is best-effort (a gateway failure
skips it without raising)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.gateway import UpstreamUnavailableError
from src.ai.prompt_registry import get_prompt_registry
from src.conversation.models import ChatMessage, ChatRole, ChatSession
from src.conversation.repository import SessionSummaryRepository
from src.conversation.summary import update_session_summary
from src.identity.service import AuthService
from src.shared.database import set_tenant_context
from tests.fakes.fake_gateway import FakeGateway


async def _session(
    auth_service: AuthService, db_session: AsyncSession, slug: str
) -> tuple[UUID, UUID, UUID]:
    org = await auth_service.register_organization(
        name=slug,
        slug=slug,
        owner_email=f"o@{slug}.test",
        full_name="O",
        password="password-123456",
    )
    org_id, user_id = org.organization.id, org.owner.id
    await set_tenant_context(db_session, org_id)
    chat = ChatSession(org_id=org_id, owner_user_id=user_id)
    db_session.add(chat)
    await db_session.flush()
    return org_id, user_id, chat.id


def _messages(org_id: UUID, session_id: UUID) -> list[ChatMessage]:
    return [
        ChatMessage(org_id=org_id, session_id=session_id, role=ChatRole.USER, content="hi", seq=0),
        ChatMessage(
            org_id=org_id, session_id=session_id, role=ChatRole.ASSISTANT, content="hello", seq=1
        ),
    ]


async def test_upsert_if_newer_is_cas_idempotent(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org_id, _user, session_id = await _session(auth_service, db_session, "sumcas")
    summaries = SessionSummaryRepository(db_session)

    assert await summaries.upsert_if_newer(org_id, session_id, "v2", 2, "1.0.0") is True
    assert await summaries.upsert_if_newer(org_id, session_id, "stale-same", 2, "1.0.0") is False
    assert await summaries.upsert_if_newer(org_id, session_id, "stale-older", 1, "1.0.0") is False
    # the fresher summary is intact
    kept = await summaries.get(org_id, session_id)
    assert kept is not None and kept.summary == "v2"
    # a newer through_seq advances it
    assert await summaries.upsert_if_newer(org_id, session_id, "v3", 3, "1.0.0") is True
    db_session.expire_all()  # the Core UPSERT bypassed the ORM identity map; re-read fresh
    advanced = await summaries.get(org_id, session_id)
    assert advanced is not None and advanced.summary == "v3"


async def test_update_session_summary_writes_via_gateway(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org_id, user_id, session_id = await _session(auth_service, db_session, "sumwrite")
    fake = FakeGateway()
    fake.queue_completion(text="a concise running summary")
    summaries = SessionSummaryRepository(db_session)
    wrote = await update_session_summary(
        org_id=org_id,
        owner_user_id=user_id,
        session_id=session_id,
        through_seq=1,
        messages=_messages(org_id, session_id),
        gateway=fake,
        summaries=summaries,
        registry=get_prompt_registry(),
    )
    assert wrote is True
    written = await summaries.get(org_id, session_id)
    assert written is not None and written.summary == "a concise running summary"


async def test_update_session_summary_is_best_effort_on_gateway_failure(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org_id, user_id, session_id = await _session(auth_service, db_session, "sumfail")
    fake = FakeGateway()
    fake.queue_error(UpstreamUnavailableError())
    summaries = SessionSummaryRepository(db_session)
    wrote = await update_session_summary(
        org_id=org_id,
        owner_user_id=user_id,
        session_id=session_id,
        through_seq=1,
        messages=_messages(org_id, session_id),
        gateway=fake,
        summaries=summaries,
        registry=get_prompt_registry(),
    )
    assert wrote is False  # gateway failure → skipped, never raised
    assert await summaries.get(org_id, session_id) is None
