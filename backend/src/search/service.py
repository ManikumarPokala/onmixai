"""Search service — the permission-aware hybrid retrieval use case (patterns §1/§5).

Composed, individually-testable steps: validate filters → embed the query → run the
ACL-filtered vector + keyword arms concurrently → reciprocal-rank fuse → paginate →
audit. An empty result is a normal outcome (a 200 with no rows), never an exception,
and the query text is never logged or echoed.
"""

import asyncio

from src.ai.embedding import Embedder
from src.identity.schemas import AuthContext
from src.search.ports import ChunkCandidateReader
from src.search.rules import build_filters, paginate, rrf_fuse
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem
from src.shared.audit import AuditEmitter
from src.shared.config import Settings


class SearchService:
    def __init__(
        self,
        *,
        reader: ChunkCandidateReader,
        embedder: Embedder,
        audit: AuditEmitter,
        settings: Settings,
    ) -> None:
        self._reader = reader
        self._embedder = embedder
        self._audit = audit
        self._settings = settings

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult:
        """Run hybrid retrieval for ``actor`` and return one ranked, attributed page.

        Time: O(top_k) candidates per arm + O(k log k) fusion. Space: O(top_k).
        Raises ``InvalidSearchFilterError`` (422) on a bad filter.
        """
        filters = build_filters(query)
        embedding = (await self._embedder.embed([query.query]))[0]
        top_k = self._settings.search_top_k
        vector, keyword = await asyncio.gather(
            self._reader.vector_candidates(
                actor.org_id,
                actor.user_id,
                embedding=embedding,
                filters=filters,
                top_k=top_k,
                ef_search=self._settings.search_ef_search,
            ),
            self._reader.keyword_candidates(
                actor.org_id, actor.user_id, query=query.query, filters=filters, top_k=top_k
            ),
        )
        fused = rrf_fuse([vector, keyword], k=self._settings.search_rrf_k)
        page, next_cursor = paginate(
            fused,
            cursor=query.cursor,
            limit=query.limit,
            max_results=self._settings.search_max_results,
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="search.executed",
            result_count=len(page),  # query text is intentionally not recorded
        )
        return SearchResult(
            results=[SearchResultItem.from_scored(chunk) for chunk in page],
            next_cursor=next_cursor,
        )
