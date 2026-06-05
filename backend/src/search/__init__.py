"""Search domain — permission-aware hybrid retrieval (Phase 2).

The only entry point to chunk content. Retrieval filters org_id + collection ACLs
inside the SQL predicate before ranking (CLAUDE.md §4); the candidate SQL lives in
knowledge behind the ``ChunkCandidateReader`` port this domain owns, and search
adds query embedding, reciprocal-rank fusion, filtering, pagination, and source
attribution. See docs/adr/0010.
"""
