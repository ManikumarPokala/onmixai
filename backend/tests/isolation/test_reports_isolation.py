"""Isolation suite — the reports surface (blocking forever after).

Two axes, both run as the non-superuser/non-bypassrls runtime role so application scoping AND
Postgres RLS are exercised:

  * tenant (org) — org A's actor can never reach org B's reports or exports.
  * user — within ONE org, user A1 can never read, list, or download user A2's reports/exports
    (indistinguishable from missing — a 404, no existence oracle).

Plus a raw-unfiltered-count RLS proof on the reports AND report_exports tables; the DOWNLOAD
path as an explicit isolation surface (a cross-org / non-owner download resolves to a 404,
never another tenant's stored bytes); and a re-proof of the Phase-2 retrieval ACL through the
report's knowledge node (it can never assemble grounded context from chunks the requester
cannot read).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Organization, Role, User
from src.identity.schemas import AuthContext
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.service import ChunkRetrievalService
from src.reports.exceptions import ExportNotFoundError, ReportNotFoundError
from src.reports.graph.nodes import knowledge_agent
from src.reports.graph.state import ReportState
from src.reports.models import (
    ExportFormat,
    ExportStatus,
    Report,
    ReportExport,
    ReportStatus,
    ReportType,
)
from src.reports.repository import ReportExportRepository, ReportRepository
from src.reports.service import ReportExportService, ReportService
from src.search.service import SearchService
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder


class _FakeQueue:
    """Records enqueues instead of touching Redis (no generation in these read/ACL tests)."""

    async def enqueue_ingest(self, *, document_id: UUID, org_id: UUID) -> None:
        return None

    async def enqueue_report(self, *, report_id: UUID, org_id: UUID) -> None:
        return None

    async def enqueue_export(self, *, export_id: UUID, org_id: UUID) -> None:
        return None

    async def close(self) -> None:
        return None


@dataclass(frozen=True)
class _User:
    org_id: UUID
    user_id: UUID
    actor: AuthContext
    report_id: UUID
    export_id: UUID
    storage_key: str


def _reports(session: AsyncSession, settings: Settings) -> ReportService:
    return ReportService(
        session=session,
        repository=ReportRepository(session),
        queue=_FakeQueue(),
        audit=AuditEmitter(),
        settings=settings,
    )


def _exports(session: AsyncSession) -> ReportExportService:
    return ReportExportService(
        session=session,
        exports=ReportExportRepository(session),
        reports=ReportRepository(session),
        queue=_FakeQueue(),
        audit=AuditEmitter(),
    )


async def _seed_user(session: AsyncSession, org_id: UUID, label: str) -> _User:
    """A user in ``org_id`` who owns one ready report with one ready PDF export."""
    user_id, report_id, export_id = uuid4(), uuid4(), uuid4()
    storage_key = f"org/{org_id}/report/{report_id}/{export_id}.pdf"
    session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"{label}-{user_id}@x.test",
            password_hash="x",
            full_name=label,
            role=Role.MEMBER,
        )
    )
    await session.flush()
    session.add(
        Report(
            id=report_id,
            org_id=org_id,
            created_by=user_id,
            report_type=ReportType.EXECUTIVE_SUMMARY,
            title=f"{label} report",
            source_query=f"{label} query",
            status=ReportStatus.READY,
            content={"sections": [], "citations": []},
            generation_metadata={"model": "stub"},
        )
    )
    await session.flush()
    session.add(
        ReportExport(
            id=export_id,
            org_id=org_id,
            report_id=report_id,
            format=ExportFormat.PDF,
            status=ExportStatus.READY,
            storage_key=storage_key,
        )
    )
    await session.flush()
    actor = AuthContext(user_id=user_id, org_id=org_id, role=Role.MEMBER)
    return _User(org_id, user_id, actor, report_id, export_id, storage_key)


@pytest.fixture
async def same_org(db_session: AsyncSession) -> AsyncIterator[tuple[_User, _User]]:
    """Two users (A1, A2) in the SAME org, each owning a report + export."""
    org_id = uuid4()
    await set_tenant_context(db_session, org_id)
    db_session.add(Organization(id=org_id, name="OrgA", slug=f"org-a-{org_id}"))
    await db_session.flush()
    a1 = await _seed_user(db_session, org_id, "a1")
    a2 = await _seed_user(db_session, org_id, "a2")
    yield a1, a2


@pytest.fixture
async def cross_org(db_session: AsyncSession) -> AsyncIterator[tuple[_User, _User]]:
    """One user per org (A in org A, B in org B)."""
    org_a, org_b = uuid4(), uuid4()
    for org, label in ((org_a, "OrgA"), (org_b, "OrgB")):
        await set_tenant_context(db_session, org)
        db_session.add(Organization(id=org, name=label, slug=f"{label}-{org}"))
        await db_session.flush()
    await set_tenant_context(db_session, org_a)
    a = await _seed_user(db_session, org_a, "a")
    await set_tenant_context(db_session, org_b)
    b = await _seed_user(db_session, org_b, "b")
    yield a, b


# --- user-level axis (same org) ---


async def test_user_cannot_read_anothers_report(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    reports = _reports(db_session, settings)
    with pytest.raises(ReportNotFoundError):  # A2's report is invisible to A1 (no oracle)
        await reports.get(a1.actor, a2.report_id)
    own = await reports.get(a1.actor, a1.report_id)
    assert own.id == a1.report_id


async def test_user_list_excludes_other_users_reports(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    page = await _reports(db_session, settings).list(a1.actor, cursor=None, limit=50)
    ids = {r.id for r in page.reports}
    assert a1.report_id in ids and a2.report_id not in ids


async def test_user_cannot_read_or_download_anothers_export(
    same_org: tuple[_User, _User], db_session: AsyncSession
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    exports = _exports(db_session)
    # A1 cannot read A2's export under A2's report (the report is not A1's → ReportNotFound).
    with pytest.raises(ReportNotFoundError):
        await exports.get(a1.actor, a2.report_id, a2.export_id)
    with pytest.raises(ReportNotFoundError):
        await exports.resolve_download(a1.actor, a2.report_id, a2.export_id)
    # A1 cannot smuggle A2's export id under A1's OWN report either (export not under it → 404).
    with pytest.raises(ExportNotFoundError):
        await exports.resolve_download(a1.actor, a1.report_id, a2.export_id)


# --- tenant (org) axis ---


async def test_cross_org_report_and_export_are_invisible(
    cross_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a, b = cross_org
    await set_tenant_context(db_session, a.org_id)
    with pytest.raises(ReportNotFoundError):
        await _reports(db_session, settings).get(a.actor, b.report_id)
    with pytest.raises(ReportNotFoundError):
        await _exports(db_session).resolve_download(a.actor, b.report_id, b.export_id)


# --- the DOWNLOAD path as an explicit isolation surface ---


async def test_download_resolution_is_owner_only_and_never_yields_anothers_bytes(
    same_org: tuple[_User, _User], db_session: AsyncSession
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    exports = _exports(db_session)
    # The owner resolves to THEIR OWN storage key (the only thing the router would stream).
    key = await exports.resolve_download(a1.actor, a1.report_id, a1.export_id)
    assert key == a1.storage_key
    # A non-owner never resolves to a key at all — the 404 fires before any storage access, so
    # A2's bytes are unreachable through A1 (no key returned ⇒ nothing to stream).
    with pytest.raises(ReportNotFoundError):
        await exports.resolve_download(a1.actor, a2.report_id, a2.export_id)


# --- raw-count RLS proof on reports + report_exports ---


async def test_raw_counts_respect_rls_on_reports_and_exports(
    cross_org: tuple[_User, _User], db_session: AsyncSession
) -> None:
    a, b = cross_org
    for table in ("reports", "report_exports"):
        for actor in (a, b):
            await set_tenant_context(db_session, actor.org_id)
            count = (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 1, f"{table} leaked across org for {actor.org_id}"  # RLS, no WHERE


# --- retrieval ACL (Phase-2 guarantee re-proven through the report knowledge node) ---


async def _seed_private_chunks(
    session: AsyncSession, org_id: UUID, owner_user_id: UUID, term: str, dim: int, n: int
) -> None:
    """``n`` chunks in a collection only ``owner_user_id`` can read (no permission for anyone
    else). ``n`` ≥ report_min_sources so the owner's report node clears the source floor."""
    collection_id, document_id = uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(
        Collection(id=collection_id, org_id=org_id, name="private", created_by=owner_user_id)
    )
    await session.flush()
    session.add(
        CollectionPermission(
            org_id=org_id, collection_id=collection_id, user_id=owner_user_id, permission="read"
        )
    )
    session.add(
        Document(
            id=document_id,
            org_id=org_id,
            collection_id=collection_id,
            filename="private.txt",
            content_type="text/plain",
            size_bytes=50,
            storage_key=f"org/{org_id}/doc/{document_id}",
            content_hash=f"{document_id}-h",
            status=DocumentStatus.READY,
            created_by=owner_user_id,
        )
    )
    await session.flush()
    embedder = FakeEmbedder(dim)
    for seq in range(n):
        content = f"The {term} is a private secret recorded only here, fragment {seq}."
        session.add(
            Chunk(
                id=uuid4(),
                org_id=org_id,
                document_id=document_id,
                seq=seq,
                content=content,
                content_hash=f"{uuid4()}-h",
                token_count=len(content.split()),
                chunk_metadata={},
                embedding=embedder._vector(content),
            )
        )
    await session.flush()


async def test_report_knowledge_node_cannot_ground_on_chunks_outside_the_requesters_acl(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    term = "zarvolium"
    await _seed_private_chunks(
        db_session, a1.org_id, a2.user_id, term, settings.embedding_dimension, n=3
    )  # only A2 may read them

    retriever = SearchService(
        reader=ChunkRetrievalService(ChunkRepository(db_session), settings),
        embedder=FakeEmbedder(settings.embedding_dimension),
        audit=AuditEmitter(),
        settings=settings,
    )
    state: ReportState = {"query": f"Summarize the {term}.", "collection_scope": []}

    # A1 (no permission) → zero permitted sources → the node declines INSUFFICIENT_EVIDENCE,
    # never assembling grounded context from A2's chunks.
    await set_tenant_context(db_session, a1.org_id)
    a1_result = await knowledge_agent(state, retriever=retriever, actor=a1.actor, settings=settings)
    assert a1_result["error"] == "INSUFFICIENT_EVIDENCE"
    assert not a1_result.get("grounded_context")

    # Positive control: A2 (who has access) retrieves the chunks → the node proceeds.
    await set_tenant_context(db_session, a2.org_id)
    a2_result = await knowledge_agent(state, retriever=retriever, actor=a2.actor, settings=settings)
    assert a2_result["error"] is None
    assert a2_result["retrieved"]
