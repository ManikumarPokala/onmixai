"""Seed the OnMixAI demo: a demo org + operator, a collection, and the manufacturing-flavored
corpus (scripts/demo_corpus.py), ingested through the REAL pipeline (upload → parse → chunk →
embed → READY) so the demo exercises the actual system. Run it LOCALLY against the running stack
(``docker compose up``); it is not part of CI.

Idempotent (a second run is a no-op that re-prints the credentials) and prod-guarded (refuses to
run when ENV=prod). After seeding, follow DEMO.md: log in as the demo operator, ask the answerable
query (cited answer), then the refusal query (the system refuses to guess a safety parameter it has
no source for).

    cd backend && python -m scripts.seed_demo
"""

import asyncio
import sys
from collections.abc import AsyncIterator

from scripts.demo_corpus import (
    DEMO_COLLECTION,
    DEMO_ORG_NAME,
    DEMO_ORG_SLUG,
    DEMO_USER_EMAIL,
    DEMO_USER_NAME,
    DEMO_USER_PASSWORD,
    DOCS,
)


async def _bytes(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed() -> int:
    from src.ai.dependencies import get_embedder
    from src.identity.models import Role
    from src.identity.repository import (
        OrganizationRepository,
        RefreshTokenRepository,
        UserRepository,
    )
    from src.identity.schemas import AuthContext
    from src.identity.service import AuthService, OrgPolicyService
    from src.knowledge.parsing.ocr_tesseract import TesseractOcrEngine
    from src.knowledge.parsing.registry import ParserRegistry
    from src.knowledge.repository import (
        ChunkRepository,
        CollectionRepository,
        DocumentRepository,
        StorageOutboxRepository,
    )
    from src.knowledge.service import KnowledgeService
    from src.knowledge.worker import ingest_document
    from src.shared.audit import AuditEmitter, AuditEventRepository
    from src.shared.config import get_settings
    from src.shared.database import get_sessionmaker, set_tenant_context
    from src.shared.queue import get_job_queue
    from src.shared.storage import get_object_storage

    settings = get_settings()
    if settings.env == "prod":
        print(
            "✗ refusing to seed demo data in a production environment (ENV=prod).", file=sys.stderr
        )
        return 2

    sessionmaker = get_sessionmaker()
    storage = get_object_storage()
    queue = get_job_queue()
    embedder = get_embedder()
    registry = ParserRegistry(TesseractOcrEngine())

    def _knowledge(session: object) -> KnowledgeService:
        return KnowledgeService(
            session=session,  # type: ignore[arg-type]
            collections=CollectionRepository(session),  # type: ignore[arg-type]
            documents=DocumentRepository(session),  # type: ignore[arg-type]
            chunks=ChunkRepository(session),  # type: ignore[arg-type]
            outbox=StorageOutboxRepository(session),  # type: ignore[arg-type]
            storage=storage,
            queue=queue,
            audit=AuditEmitter(AuditEventRepository(session)),  # type: ignore[arg-type]
            quota_reader=OrgPolicyService(OrganizationRepository(session)),  # type: ignore[arg-type]
            settings=settings,
        )

    # Idempotency: if the demo org already exists, re-print credentials and stop.
    async with sessionmaker() as session:
        if await OrganizationRepository(session).get_by_slug(DEMO_ORG_SLUG) is not None:
            print(f"✓ demo already seeded (org '{DEMO_ORG_SLUG}'). Credentials below.")
            _print_credentials()
            return 0

    # 1) Register the demo org + owner (real auth path: hashed password, proper setup).
    async with sessionmaker() as session:
        auth = AuthService(
            session=session,
            organizations=OrganizationRepository(session),
            users=UserRepository(session),
            refresh_tokens=RefreshTokenRepository(session),
            settings=settings,
        )
        result = await auth.register_organization(
            name=DEMO_ORG_NAME,
            slug=DEMO_ORG_SLUG,
            owner_email=DEMO_USER_EMAIL,
            password=DEMO_USER_PASSWORD,
            full_name=DEMO_USER_NAME,
        )
        await session.commit()
        org_id, owner_id = result.organization.id, result.owner.id
    actor = AuthContext(user_id=owner_id, org_id=org_id, role=Role.OWNER)

    # 2) Create the collection.
    async with sessionmaker() as session:
        await set_tenant_context(session, org_id)
        collection = await _knowledge(session).create_collection(
            actor, name=DEMO_COLLECTION, description="Fictional operations corpus for the demo."
        )
        await session.commit()
        collection_id = collection.id

    # 3) Upload + ingest each document through the real pipeline (inline, no worker needed).
    ctx = {
        "sessionmaker": sessionmaker,
        "storage": storage,
        "settings": settings,
        "registry": registry,
        "embedder": embedder,
    }
    for doc in DOCS:
        payload = doc.content.encode()
        async with sessionmaker() as session:
            await set_tenant_context(session, org_id)
            accepted = await _knowledge(session).upload_document(
                actor,
                collection_id=collection_id,
                filename=doc.filename,
                content_type="text/plain",
                declared_size=len(payload),
                source=_bytes(payload),
            )
            await session.commit()
        await ingest_document(ctx, str(accepted.document_id), str(org_id))
        print(f"  ingested {doc.filename}")

    print(f"\n✓ seeded {len(DOCS)} documents into '{DEMO_COLLECTION}'.")
    _print_credentials()
    return 0


def _print_credentials() -> None:
    print("\n  Demo credentials (demo-only — never use in production):")
    print(f"    org slug : {DEMO_ORG_SLUG}")
    print(f"    email    : {DEMO_USER_EMAIL}")
    print(f"    password : {DEMO_USER_PASSWORD}")
    print("\n  Next: see DEMO.md for the 30-second login → cited answer → refusal walkthrough.")


def main() -> int:
    return asyncio.run(_seed())


if __name__ == "__main__":
    raise SystemExit(main())
