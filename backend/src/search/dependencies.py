"""Search FastAPI dependencies — compose SearchService from its ports.

The candidate reader is provided by knowledge's dependency (get_chunk_retrieval_service),
so search depends on knowledge's interface, never its repository (import-linter).
"""

from fastapi import Depends

from src.ai.dependencies import get_embedder
from src.ai.embedding import Embedder
from src.knowledge.dependencies import get_chunk_retrieval_service
from src.search.ports import ChunkCandidateReader
from src.search.service import SearchService
from src.shared.audit import AuditEmitter, get_audit_emitter
from src.shared.config import Settings, get_settings


def get_search_service(
    reader: ChunkCandidateReader = Depends(get_chunk_retrieval_service),
    embedder: Embedder = Depends(get_embedder),
    audit: AuditEmitter = Depends(get_audit_emitter),
    settings: Settings = Depends(get_settings),
) -> SearchService:
    return SearchService(reader=reader, embedder=embedder, audit=audit, settings=settings)
