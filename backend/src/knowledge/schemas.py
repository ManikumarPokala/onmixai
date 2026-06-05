"""Knowledge DTOs and request/response schemas (patterns.md §8).

Internal DTOs carry full data between layers; response schemas are allow-lists
returned to clients. ORM models never cross the layer boundary.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from src.knowledge.models import Collection, Document, DocumentStatus, Permission

# --- Internal DTOs ---


@dataclass(frozen=True, slots=True)
class CollectionDTO:
    id: UUID
    name: str
    description: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, collection: Collection) -> "CollectionDTO":
        return cls(
            id=collection.id,
            name=collection.name,
            description=collection.description,
            created_at=collection.created_at,
        )


@dataclass(frozen=True, slots=True)
class DocumentDTO:
    id: UUID
    collection_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    failure_reason: str | None
    version: int
    superseded: bool
    page_count: int | None
    created_at: datetime

    @classmethod
    def from_model(cls, document: Document) -> "DocumentDTO":
        return cls(
            id=document.id,
            collection_id=document.collection_id,
            filename=document.filename,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
            status=document.status,
            failure_reason=document.failure_reason,
            version=document.version,
            superseded=document.superseded,
            page_count=document.page_count,
            created_at=document.created_at,
        )


# --- Request schemas ---


class CollectionCreate(BaseModel):
    name: str
    description: str | None = None


class PermissionGrant(BaseModel):
    user_id: UUID
    permission: Permission


# --- Response schemas (allow-lists) ---


class CollectionResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime

    @classmethod
    def from_dto(cls, dto: CollectionDTO) -> "CollectionResponse":
        return cls(id=dto.id, name=dto.name, description=dto.description, created_at=dto.created_at)


class DocumentResponse(BaseModel):
    id: UUID
    collection_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    failure_reason: str | None
    version: int
    superseded: bool
    page_count: int | None
    created_at: datetime

    @classmethod
    def from_dto(cls, dto: DocumentDTO) -> "DocumentResponse":
        return cls(
            id=dto.id,
            collection_id=dto.collection_id,
            filename=dto.filename,
            content_type=dto.content_type,
            size_bytes=dto.size_bytes,
            status=dto.status,
            failure_reason=dto.failure_reason,
            version=dto.version,
            superseded=dto.superseded,
            page_count=dto.page_count,
            created_at=dto.created_at,
        )


class UploadAccepted(BaseModel):
    document_id: UUID
    status: DocumentStatus
