"""Knowledge repositories — the only place knowledge queries live (patterns.md §2).

Every method is tenant-scoped; results are ORM models / None with no business
decisions. Application org-scoping sits alongside Postgres RLS (defense in depth).
"""

from uuid import UUID

from sqlalchemy import column, func, select, table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.knowledge.models import Collection, CollectionPermission, Document, Permission

_LIST_CAP = 100

# Lightweight Core handle for the org-quota config value. The organizations table
# is the tenant root (identity domain, no RLS); knowledge reads only this config
# scalar — no identity import, no escape-hatch raw SQL.
_organizations = table("organizations", column("id"), column("max_documents"))


class CollectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, collection: Collection) -> Collection:
        self._session.add(collection)
        await self._session.flush()
        await self._session.refresh(collection)
        return collection

    async def get(self, org_id: UUID, collection_id: UUID) -> Collection | None:
        stmt = select(Collection).where(Collection.org_id == org_id, Collection.id == collection_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_name(self, org_id: UUID, name: str) -> Collection | None:
        stmt = select(Collection).where(Collection.org_id == org_id, Collection.name == name)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_readable(
        self, org_id: UUID, user_id: UUID, *, cursor: UUID | None = None, limit: int = 50
    ) -> list[Collection]:
        """Collections the user has any permission on, cursor-paginated. Time: O(limit)."""
        stmt = (
            select(Collection)
            .join(CollectionPermission, CollectionPermission.collection_id == Collection.id)
            .where(Collection.org_id == org_id, CollectionPermission.user_id == user_id)
            .order_by(Collection.id)
            .limit(min(limit, _LIST_CAP))
        )
        if cursor is not None:
            stmt = stmt.where(Collection.id > cursor)
        return list((await self._session.execute(stmt)).scalars())

    async def get_permission(
        self, org_id: UUID, collection_id: UUID, user_id: UUID
    ) -> CollectionPermission | None:
        stmt = select(CollectionPermission).where(
            CollectionPermission.org_id == org_id,
            CollectionPermission.collection_id == collection_id,
            CollectionPermission.user_id == user_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_permission(
        self, org_id: UUID, collection_id: UUID, user_id: UUID, permission: Permission
    ) -> None:
        """Grant or update a user's permission on a collection (idempotent)."""
        stmt = insert(CollectionPermission).values(
            org_id=org_id,
            collection_id=collection_id,
            user_id=user_id,
            permission=permission,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_collection_permissions_collection_id_user_id",
            set_={"permission": permission},
        )
        await self._session.execute(stmt)


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def get(self, org_id: UUID, document_id: UUID) -> Document | None:
        stmt = select(Document).where(Document.org_id == org_id, Document.id == document_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_by_collection(
        self, org_id: UUID, collection_id: UUID, *, cursor: UUID | None = None, limit: int = 50
    ) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.org_id == org_id, Document.collection_id == collection_id)
            .order_by(Document.id)
            .limit(min(limit, _LIST_CAP))
        )
        if cursor is not None:
            stmt = stmt.where(Document.id > cursor)
        return list((await self._session.execute(stmt)).scalars())

    async def count_for_org(self, org_id: UUID) -> int:
        stmt = select(func.count()).select_from(Document).where(Document.org_id == org_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def org_max_documents(self, org_id: UUID) -> int:
        """Read the org's document quota (tenant-root config). Time: O(1) indexed."""
        stmt = select(_organizations.c.max_documents).where(_organizations.c.id == org_id)
        return int((await self._session.execute(stmt)).scalar_one())
