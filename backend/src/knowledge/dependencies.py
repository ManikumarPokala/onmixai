"""Knowledge FastAPI dependencies — compose the service with request-scoped deps."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.dependencies import get_tenant_session
from src.knowledge.repository import CollectionRepository, DocumentRepository
from src.knowledge.service import KnowledgeService
from src.shared.audit import AuditEmitter, get_audit_emitter
from src.shared.config import Settings, get_settings
from src.shared.queue import JobQueue, get_job_queue
from src.shared.storage import ObjectStorage, get_object_storage


def get_knowledge_service(
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorage = Depends(get_object_storage),
    queue: JobQueue = Depends(get_job_queue),
    audit: AuditEmitter = Depends(get_audit_emitter),
) -> KnowledgeService:
    return KnowledgeService(
        session=session,
        collections=CollectionRepository(session),
        documents=DocumentRepository(session),
        storage=storage,
        queue=queue,
        audit=audit,
        settings=settings,
    )
