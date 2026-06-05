# Knowledge domain

Ingestion of source documents into retrievable, embedded chunks: collections with
per-user ACLs, streaming quota-enforced upload, an idempotent async ingest worker
(parse → chunk → embed → store), and the document lifecycle (versioning, delete,
re-index).

## Public service interface (`service.py`)

`KnowledgeService` — `create_collection`, `list_collections`, `grant_permission`,
`delete_collection`; `upload_document`, `create_version`, `get_document`,
`delete_document`, `reindex_document`. Cross-domain reads (org document quota,
tenant enumeration) go through ports knowledge owns (`OrgQuotaReader`,
`TenantLister`) that identity's `OrgPolicyService` satisfies structurally — the
embedder likewise via the `ai` domain's `Embedder` Protocol.

## Invariants

- **READY ⇒ no null embeddings.** A document reaches READY only after every chunk
  it produced has been embedded and stored (`worker._embed_and_store` runs before
  `mark_ready`). Phase 2 retrieval still filters `embedding IS NOT NULL` defensively.
- **One live version.** When a re-uploaded version reaches READY, the prior
  version's chunks are deleted and it is flagged `superseded` in the same
  transaction, so retrieval never sees two versions (ADR 0007).
- **Deterministic, idempotent chunks.** Chunk content hashes are
  whitespace-normalized; ingest upserts `ON CONFLICT (document_id, content_hash) DO
  NOTHING` and deletes hashes a rebuild no longer produces, so re-ingest/re-index
  converges to exactly the current set with zero new rows.
- **No orphaned storage objects.** Delete records the object key in
  `storage_deletion_outbox` in the same transaction as the row delete; the object is
  removed after commit, and `sweep_storage_outbox` retries any row left behind
  (transactional outbox — ADR 0007).
- **Tenant isolation.** All five tables (`collections`, `collection_permissions`,
  `documents`, `chunks`, `storage_deletion_outbox`) are `org_id NOT NULL` with
  forced RLS; every repository method takes tenant context.

## Ingest worker (`worker.py`)

ARQ. `ingest_document` claims a QUEUED row by compare-and-set, parses (format-aware,
OCR fallback), chunks (strategy by format), embeds in bounded-concurrency batches,
bulk-upserts, and marks READY — idempotent under duplicate delivery and re-run.
Crons: `sweep_stuck_documents` (recovers documents abandoned by a dead worker) and
`sweep_storage_outbox` (storage-deletion compensation).

## Known limitations

- Superseded document rows are retained and still count toward the org quota;
  reclamation is a later phase.
- Non-empty collections cannot be deleted (no bulk cascade yet).
- Parser tests run in a separate, asyncio-free pytest pass (PyMuPDF/pytest-asyncio
  native conflict — ADR 0008); see `tests/knowledge/README.md`.
