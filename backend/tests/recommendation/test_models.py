"""Recommendation schema: forced RLS on the tenant table, runtime-role CRUD under tenant
context (completed + declined rows), and cross-org RLS hiding."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.service import AuthService
from src.recommendation.models import ConfidenceBand, Recommendation, RecommendationStatus
from src.shared.database import set_tenant_context


async def test_forced_rls_enabled_on_recommendations(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'recommendations' AND relkind = 'r'"
            )
        )
    ).one()
    assert row == (True, True)  # RLS enabled AND forced (CLAUDE.md §4)


async def test_runtime_role_crud_completed_and_declined(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org = await auth_service.register_organization(
        name="RecOrg",
        slug="rec-org",
        owner_email="o@rec.test",
        full_name="O",
        password="password-123456",
    )
    org_id, user_id = org.organization.id, org.owner.id
    await set_tenant_context(db_session, org_id)

    completed = Recommendation(
        org_id=org_id,
        created_by=user_id,
        query="Which vendor should we pick?",
        collection_scope=[str(user_id)],
        status=RecommendationStatus.COMPLETED,
        confidence_band=ConfidenceBand.HIGH,
        payload={"recommendation": "Vendor A", "justifications": []},
        prompt_version="1.0.0",
        trace_id="trace-1",
    )
    declined = Recommendation(
        org_id=org_id,
        created_by=user_id,
        query="Something unanswerable",
        status=RecommendationStatus.DECLINED,
        decline_reason="INSUFFICIENT_EVIDENCE",
    )
    db_session.add_all([completed, declined])
    await db_session.flush()

    rows = (
        await db_session.execute(
            text("SELECT status, confidence_band, payload, decline_reason FROM recommendations")
        )
    ).all()
    by_status = {r.status: r for r in rows}
    assert by_status["completed"].confidence_band == "high"
    assert by_status["completed"].payload["recommendation"] == "Vendor A"
    assert by_status["declined"].confidence_band is None
    assert by_status["declined"].decline_reason == "INSUFFICIENT_EVIDENCE"


async def test_rls_hides_recommendations_from_other_org(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org = await auth_service.register_organization(
        name="RecOrgB",
        slug="rec-org-b",
        owner_email="o@recb.test",
        full_name="O",
        password="password-123456",
    )
    org_id, user_id = org.organization.id, org.owner.id
    await set_tenant_context(db_session, org_id)
    db_session.add(
        Recommendation(
            org_id=org_id,
            created_by=user_id,
            query="q",
            status=RecommendationStatus.DECLINED,
            decline_reason="INSUFFICIENT_EVIDENCE",
        )
    )
    await db_session.flush()
    assert (
        await db_session.execute(text("SELECT count(*) FROM recommendations"))
    ).scalar_one() == 1

    await set_tenant_context(db_session, user_id)  # any other org_id → RLS hides the row
    assert (
        await db_session.execute(text("SELECT count(*) FROM recommendations"))
    ).scalar_one() == 0
