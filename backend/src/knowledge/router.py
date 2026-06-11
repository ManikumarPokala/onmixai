"""Knowledge HTTP routes — thin: validate, one service call, shape response."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile, status

from src.identity.dependencies import get_current_user
from src.identity.schemas import AuthContext
from src.knowledge.dependencies import get_knowledge_service
from src.knowledge.schemas import (
    CollectionCreate,
    CollectionResponse,
    DocumentResponse,
    PermissionGrant,
    UploadAccepted,
)
from src.knowledge.service import KnowledgeService

router = APIRouter()

_UPLOAD_CHUNK = 1024 * 1024


async def _stream_upload(file: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await file.read(_UPLOAD_CHUNK):
        yield chunk


@router.post("/collections", status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CollectionCreate,
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> CollectionResponse:
    dto = await service.create_collection(actor, name=body.name, description=body.description)
    return CollectionResponse.from_dto(dto)


@router.get("/collections")
async def list_collections(
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> list[CollectionResponse]:
    return [CollectionResponse.from_dto(dto) for dto in await service.list_collections(actor)]


@router.get("/collections/{collection_id}/documents")
async def list_documents(
    collection_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> list[DocumentResponse]:
    dtos = await service.list_documents(actor, collection_id)
    return [DocumentResponse.from_dto(d) for d in dtos]


@router.post("/collections/{collection_id}/permissions", status_code=status.HTTP_204_NO_CONTENT)
async def grant_permission(
    collection_id: UUID,
    body: PermissionGrant,
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> None:
    await service.grant_permission(
        actor, collection_id=collection_id, user_id=body.user_id, permission=body.permission
    )


@router.post("/collections/{collection_id}/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    collection_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> UploadAccepted:
    declared_size = int(request.headers.get("content-length", 0))
    return await service.upload_document(
        actor,
        collection_id=collection_id,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        declared_size=declared_size,
        source=_stream_upload(file),
    )


@router.get("/documents/{document_id}")
async def get_document(
    document_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> DocumentResponse:
    return DocumentResponse.from_dto(await service.get_document(actor, document_id))


@router.post("/documents/{document_id}/versions", status_code=status.HTTP_202_ACCEPTED)
async def create_version(
    document_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> UploadAccepted:
    declared_size = int(request.headers.get("content-length", 0))
    return await service.create_version(
        actor,
        document_id=document_id,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        declared_size=declared_size,
        source=_stream_upload(file),
    )


@router.post("/documents/{document_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
async def reindex_document(
    document_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> None:
    await service.reindex_document(actor, document_id)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> None:
    await service.delete_document(actor, document_id)


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> None:
    await service.delete_collection(actor, collection_id)
