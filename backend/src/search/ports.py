"""Outbound ports the search domain depends on (CLAUDE.md §3.3, §6).

``ChunkCandidateReader`` is owned by search; knowledge's service satisfies it
structurally — it returns knowledge-owned ``ChunkCandidate``s and takes a
knowledge-owned ``RetrievalFilters``, so knowledge never imports search (no upward
dependency). Both arms apply the org_id + collection-ACL predicate before ranking.
"""

from typing import Protocol
from uuid import UUID

from src.ai.embedding import Vector
from src.knowledge.schemas import ChunkCandidate, RetrievalFilters


class ChunkCandidateReader(Protocol):
    """Permission-aware candidate retrieval, one method per hybrid arm."""

    async def vector_candidates(
        self,
        org_id: UUID,
        user_id: UUID,
        *,
        embedding: Vector,
        filters: RetrievalFilters,
        top_k: int,
        ef_search: int,
    ) -> list[ChunkCandidate]:
        """Nearest chunks by cosine distance (HNSW), ACL-filtered before ranking."""
        ...

    async def keyword_candidates(
        self,
        org_id: UUID,
        user_id: UUID,
        *,
        query: str,
        filters: RetrievalFilters,
        top_k: int,
    ) -> list[ChunkCandidate]:
        """Top chunks by full-text rank (GIN/tsvector), ACL-filtered before ranking."""
        ...
