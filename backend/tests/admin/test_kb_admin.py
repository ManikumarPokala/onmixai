"""Knowledge-base administration — owner/admin only; admin sees every document in the org across
collections (ACL bypassed, org not); force-reindex and delete are audited under admin.* actions;
cross-org documents are invisible (404). Quota usage reflects the org's document count vs limit."""

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role
from src.knowledge.models import Collection, Document, DocumentStatus
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.admin.conftest import AdminHarness, AdminOrg, auth, seed_org


async def _seed_collection(session: AsyncSession, org: AdminOrg, name: str) -> UUID:
    collection_id = uuid4()
    session.add(
        Collection(
            id=collection_id, org_id=org.org_id, name=name, created_by=org.user_ids[Role.OWNER]
        )
    )
    await session.flush()
    return collection_id


async def _seed_document(
    session: AsyncSession,
    org: AdminOrg,
    collection_id: UUID,
    *,
    filename: str,
    status: DocumentStatus = DocumentStatus.READY,
) -> UUID:
    document_id = uuid4()
    session.add(
        Document(
            id=document_id,
            org_id=org.org_id,
            collection_id=collection_id,
            filename=filename,
            content_type="application/pdf",
            size_bytes=1024,
            storage_key=f"{org.org_id}/{document_id}",
            content_hash="a" * 64,
            status=status,
            created_by=org.user_ids[Role.OWNER],
        )
    )
    await session.flush()
    return document_id


async def test_member_forbidden_on_kb_admin(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    member = auth(org.tokens[Role.MEMBER])
    assert (
        await admin_harness.client.get("/api/v1/admin/knowledge/documents", headers=member)
    ).status_code == 403
    assert (
        await admin_harness.client.get("/api/v1/admin/knowledge/quota", headers=member)
    ).status_code == 403
    doc = uuid4()
    assert (
        await admin_harness.client.post(
            f"/api/v1/admin/knowledge/documents/{doc}/reindex", headers=member
        )
    ).status_code == 403
    assert (
        await admin_harness.client.delete(
            f"/api/v1/admin/knowledge/documents/{doc}", headers=member
        )
    ).status_code == 403


async def test_admin_sees_documents_across_collections(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    # Two collections the admin holds NO per-collection ACL on — only their admin role.
    col_a = await _seed_collection(db_session, org, "Alpha")
    col_b = await _seed_collection(db_session, org, "Beta")
    await _seed_document(db_session, org, col_a, filename="a.pdf")
    await _seed_document(db_session, org, col_b, filename="b.pdf")
    resp = await admin_harness.client.get(
        "/api/v1/admin/knowledge/documents", headers=auth(org.tokens[Role.ADMIN])
    )
    assert resp.status_code == 200
    names = {d["filename"] for d in resp.json()["documents"]}
    assert names == {"a.pdf", "b.pdf"}  # both collections visible to the admin


async def test_quota_usage_and_setting_the_limit(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    col = await _seed_collection(db_session, org, "Docs")
    await _seed_document(db_session, org, col, filename="one.pdf")
    await _seed_document(db_session, org, col, filename="two.pdf")
    # The admin lowers the org's document quota...
    patch = await admin_harness.client.patch(
        "/api/v1/admin/organization",
        json={"name": "Acme", "max_documents": 10},
        headers=auth(org.tokens[Role.OWNER]),
    )
    assert patch.status_code == 200 and patch.json()["max_documents"] == 10
    # ...and usage reflects both the count and the new limit.
    quota = await admin_harness.client.get(
        "/api/v1/admin/knowledge/quota", headers=auth(org.tokens[Role.ADMIN])
    )
    assert quota.json() == {"used": 2, "limit": 10, "remaining": 8}


async def test_reindex_requeues_and_audits(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    col = await _seed_collection(db_session, org, "Docs")
    doc_id = await _seed_document(
        db_session, org, col, filename="r.pdf", status=DocumentStatus.READY
    )
    resp = await admin_harness.client.post(
        f"/api/v1/admin/knowledge/documents/{doc_id}/reindex",
        headers=auth(org.tokens[Role.ADMIN]),
    )
    assert resp.status_code == 202
    assert (doc_id, org.org_id) in admin_harness.queue.enqueued  # re-queued after commit
    await set_tenant_context(db_session, org.org_id)
    status = (
        await db_session.execute(
            text("SELECT status FROM documents WHERE id = :id"), {"id": doc_id}
        )
    ).scalar_one()
    assert status == "queued"
    count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM audit_events "
                "WHERE action = 'admin.document_reindexed' AND resource_id = :id"
            ),
            {"id": doc_id},
        )
    ).scalar_one()
    assert count == 1


async def test_delete_removes_compensates_storage_and_audits(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    col = await _seed_collection(db_session, org, "Docs")
    doc_id = await _seed_document(db_session, org, col, filename="d.pdf")
    resp = await admin_harness.client.delete(
        f"/api/v1/admin/knowledge/documents/{doc_id}", headers=auth(org.tokens[Role.OWNER])
    )
    assert resp.status_code == 204
    assert f"{org.org_id}/{doc_id}" in admin_harness.storage.deleted  # storage compensated
    await set_tenant_context(db_session, org.org_id)
    gone = (
        await db_session.execute(
            text("SELECT count(*) FROM documents WHERE id = :id"), {"id": doc_id}
        )
    ).scalar_one()
    assert gone == 0
    audited = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM audit_events "
                "WHERE action = 'admin.document_deleted' AND resource_id = :id"
            ),
            {"id": doc_id},
        )
    ).scalar_one()
    assert audited == 1


async def test_cross_org_document_is_invisible(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org_a = await seed_org(db_session, settings, slug="a")
    org_b = await seed_org(db_session, settings, slug="b")
    col_b = await _seed_collection(db_session, org_b, "B-docs")
    doc_b = await _seed_document(db_session, org_b, col_b, filename="secret.pdf")
    resp = await admin_harness.client.delete(
        f"/api/v1/admin/knowledge/documents/{doc_b}", headers=auth(org_a.tokens[Role.ADMIN])
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
