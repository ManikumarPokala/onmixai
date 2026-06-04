# OnMixAI — Sprint 2 Specification (Phase 1: Knowledge / Ingestion)

Goal: documents go in, become chunks + embeddings, and can be versioned and deleted — asynchronously, idempotently, with quotas and full tenant isolation. No search, no LLM completions (Phases 2–3).

Execution rules: identical to Sprint 1 — strict task order, every VERIFY block green before proceeding, one Conventional Commit per task, CLAUDE.md/patterns.md/performance.md binding throughout. Review pauses: after Task 2 (schema + RLS), after Task 5 (worker infrastructure), after Task 9 (lifecycle). Otherwise run continuously.

Sprint 2 exit criteria (roadmap Phase 1):
1. 100-page text PDF: upload → READY < 5 min locally; status visible at every stage.
2. Worker killed mid-task → sweeper re-queues; re-run yields an identical end state (idempotency proven by test).
3. Document deletion leaves zero orphaned chunks, embeddings, or storage objects (test-verified).
4. Broken-fixture corpus: every file terminates in FAILED with a human-readable reason — never stuck.
5. Quota breach → typed 4xx envelope; isolation suite extended to all knowledge tables, green.
6. All Sprint 1 CI gates remain green; new code ≥80% coverage.

---

## Task 1 — Infra: object storage + queue + storage Protocol

Compose additions (`infra/docker-compose.yml`): MinIO (S3-compatible, console exposed on a free host port, dev credentials via `${VAR:-dev-default}` like Postgres) and Redis 7 (ARQ broker). Healthchecks on both; API/worker `depends_on: service_healthy`.

Settings additions (typed, fail-fast like everything else): `storage_endpoint`, `storage_access_key: SecretStr`, `storage_secret_key: SecretStr`, `storage_bucket`, `redis_url`, `max_upload_bytes: int = 52_428_800`, `max_document_pages: int = 2000`, `embedding_dimension: int` (single source of truth — migration 0002 reads this), `embedding_batch_size: int = 100`, `ingest_max_attempts: int = 3`, `ingest_stuck_after_seconds: int = 1800`.

`shared/storage.py`: `ObjectStorage` Protocol — `async put_stream(key, stream, content_type) -> StoredObject`, `async get_stream(key)`, `async delete(key)`, `async exists(key)`. Adapter `adapters/s3_storage.py` (aioboto3 or miniopy-async — pick one, pin it, justify in commit body): streaming multipart upload, never buffering the whole file (performance.md §4). `tests/fakes/fake_storage.py`: in-memory, records calls. Bucket ensured at startup via lifespan (idempotent).

Dependencies added now (pinned): storage SDK, `arq`, `redis`. Parser/OCR deps wait for Task 6 (scoped commits, as in Sprint 1).

**VERIFY**
```
docker compose -f infra/docker-compose.yml up -d minio redis
cd backend && pytest tests/shared/test_storage.py -q
# required: adapter round-trip put_stream→get_stream→delete against live MinIO;
# 60MB synthetic stream uploads with peak RSS independent of file size (tracemalloc/rss assertion);
# fake passes the SAME contract test suite as the adapter (shared parametrized tests)
mypy src/ && ruff check .
```
Commit: `feat(shared): object storage protocol, S3 adapter, queue infra`

---

## Task 2 — Knowledge schema + migration 0002 + RLS  [PAUSE after VERIFY]

`knowledge/models.py`:
- `collections`: id, org_id (NOT NULL idx), name, description, created_by, created_at, updated_at; unique (org_id, name).
- `collection_permissions`: id, org_id, collection_id (fk), user_id (fk), permission (enum: read|write|manage), created_at; unique (collection_id, user_id). (Groundwork for Phase 2 ACL-filtered retrieval.)
- `documents`: id, org_id, collection_id (fk), filename, content_type, size_bytes, storage_key (unique), content_hash (sha256 of file bytes), version (int, default 1), supersedes_id (nullable self-fk — version chain), status (enum: queued|processing|ready|failed), failure_reason (nullable text), attempt_count (int default 0), claimed_at (nullable), page_count (nullable), created_by, created_at, updated_at. Indexes: (org_id, collection_id, status), (status, claimed_at) for the sweeper.
- `chunks`: id, org_id, document_id (fk ON DELETE CASCADE), seq (int), content (text), content_hash (sha256 of normalized chunk text), token_count, metadata (jsonb: page/sheet/slide refs), embedding `vector(settings.embedding_dimension)`, created_at. Unique (document_id, content_hash). Index (org_id, document_id, seq). **No HNSW index this sprint** — it lands in Phase 2 with measured parameters (ADR'd then).

Migration 0002: `CREATE EXTENSION IF NOT EXISTS vector;` + tables + **RLS enabled and FORCED on all four tables in this same migration**, identical `tenant_isolation` policy pattern as 0001. Working `downgrade()`. Role-agnostic (default privileges from Sprint 1 handle grants — this is the empirical test of that mechanism on a real migration).

Org quotas: add `max_documents int NOT NULL DEFAULT 500` to `organizations` in this migration (column addition, reversible).

**VERIFY**
```
cd backend
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
alembic check   # models ↔ migration lockstep, as in Sprint 1
psql "$PG_URL" -c "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
  WHERE relname IN ('collections','collection_permissions','documents','chunks');"
# expect t|t on all four
# as onmixai_app with GUC unset: SELECT count(*) FROM documents → 0 rows visible
# as onmixai_app: INSERT a row works under a set GUC (default-privilege grant proven on migration 0002)
```
Commit: `feat(knowledge): collections/documents/chunks schema with forced RLS and quotas`

---

## Task 3 — Domain skeleton: rules, state machine, schemas

`knowledge/rules.py` (pure, zero I/O, branch-complete tests — patterns.md §4):
- `DocumentStatus` enum + `_TRANSITIONS` map + `transition(current, target)` exactly per patterns.md §3 (queued→processing; processing→ready|failed; failed→queued retry; ready→queued re-index).
- `ensure_within_quota(current_count, max_documents)`, `ensure_upload_acceptable(size_bytes, content_type, max_bytes)` (allow-list of the five formats' MIME types), `ensure_document_deletable(document)` (PROCESSING → ConflictError), `ensure_collection_permission(perm: Permission, required: Permission)` (read < write < manage ordering).

`knowledge/schemas.py`: request/response allow-lists (CollectionCreate/Response, DocumentResponse incl. status + failure_reason + version, UploadAccepted with document_id + status). `knowledge/exceptions.py`: codes `COLLECTION_NOT_FOUND`, `DOCUMENT_NOT_FOUND`, `DOCUMENT_QUOTA_EXCEEDED`, `UNSUPPORTED_FORMAT`, `UPLOAD_TOO_LARGE`, `DOCUMENT_PROCESSING`, `INVALID_STATUS_TRANSITION`, `COLLECTION_ACCESS_DENIED`.

**VERIFY**
```
cd backend && pytest tests/knowledge/test_rules.py -q
# every transition pair tested — legal AND illegal; every rule branch covered;
# permission ordering property-tested (read<write<manage)
mypy src/ && ruff check .
```
Commit: `feat(knowledge): domain rules, status state machine, schemas`

---

## Task 4 — Collections + upload endpoint (streaming, quota-enforced)

`knowledge/repository.py`: CollectionRepository (create, get, list paginated+capped, grant/revoke/get permission), DocumentRepository (create, get, list_by_collection paginated, count_for_org — quota check, mark/claim methods arrive Task 5). All methods take org context; service methods follow the 6-step anatomy.

`knowledge/service.py` + `router.py`:
- `POST /api/v1/collections` (write requires authenticated member+), `GET /api/v1/collections` (lists only collections the user can read — creator gets manage automatically), permission grant endpoint (`manage` required).
- `POST /api/v1/collections/{id}/documents` — multipart upload: 1) AUTHORIZE write on collection; 2) rules: format allow-list, size from Content-Length pre-check AND enforced during streaming (reject mid-stream past max — never buffer then check); 3) quota check; 4) stream to storage under key `org/{org_id}/doc/{uuid}` while computing sha256 incrementally; 5) create document row QUEUED; 6) enqueue ingest job (ARQ) AFTER commit (on-commit hook — a job for an uncommitted row is a race); 7) audit; return 202 UploadAccepted.
- `GET /api/v1/documents/{id}` — status polling endpoint.

**VERIFY**
```
cd backend && pytest tests/knowledge/test_collections_api.py tests/knowledge/test_upload_api.py -q
# required: create/list collections; non-permitted user gets 403 + cannot list others' collections;
# upload happy path → 202 + row QUEUED + object in fake storage + job enqueued (fake queue);
# oversize rejected mid-stream (typed envelope) and NO orphan object remains in storage;
# unsupported MIME → 415-style typed error; quota at limit → DOCUMENT_QUOTA_EXCEEDED;
# enqueue only after commit (test: enqueued job's document_id is readable in a fresh session)
mypy src/ && ruff check .
```
Commit: `feat(knowledge): collections with ACLs and streaming quota-enforced upload`

---

## Task 5 — Worker infrastructure: claim, retry, sweeper  [PAUSE after VERIFY]

ARQ worker app (`src/knowledge/worker.py` + shared worker bootstrap in `shared/queue.py`):
- `ingest_document(ctx, document_id, org_id)` task skeleton per patterns.md §7 — this task ships the machinery with a stub-free pipeline that currently performs: claim → (parsing arrives Task 6; for now the pipeline body is the real orchestration calling a `ParserRegistry` that has TXT registered — a real, complete vertical slice, not a placeholder).
- Atomic claim: `DocumentRepository.claim_for_processing(org_id, id, expected=QUEUED)` → `UPDATE ... SET status='processing', claimed_at=now(), attempt_count=attempt_count+1 WHERE id=:id AND org_id=:org AND status='queued'` — loser gets rowcount 0 and exits silently.
- Retry: `RetryableError` → if attempt_count < ingest_max_attempts, transition failed→queued with exponential backoff (ARQ defer); else terminal FAILED + reason. Non-retryable → FAILED + `safe_reason(exc)` (no internals in user-visible reason), re-raise for operator logs.
- Sweeper: ARQ cron every 5 min — documents in PROCESSING with `claimed_at < now() - ingest_stuck_after_seconds` transition back to QUEUED (attempt-capped, then FAILED "worker died repeatedly"). Sweep actions audited.
- Worker DB sessions set the tenant GUC from the job's org_id (workers obey RLS like requests do).
- Compose: `worker` service (same image, arq entrypoint), healthcheck.

**VERIFY**
```
cd backend && pytest tests/knowledge/test_worker.py -q
# required: duplicate delivery → exactly one claim succeeds (two concurrent claims, one rowcount=1);
# TXT end-to-end: upload→worker→READY with chunks rows present;
# RetryableError path: failed→queued with attempt_count increment, terminal FAILED at max;
# sweeper: artificially stale PROCESSING row re-queued; capped row → FAILED with reason;
# kill-drill (integration): start real worker container, upload TXT, docker kill worker mid-task,
#   start worker again → sweeper re-queues → document reaches READY; chunk set identical (content_hash set equality)
mypy src/ && ruff check .
```
Commit: `feat(knowledge): idempotent ingest worker with CAS claims, bounded retry, sweeper`

---

## Task 6 — Parsers (5 formats) + broken-fixture corpus

`knowledge/parsing/`: `Parser` Protocol — `parse(stream) -> ParsedDocument` (frozen dataclass: pages/sections, text blocks, tables, slide notes, page_count, table_ratio). Registry maps content_type → parser. Implementations (pin deps now): PDF via pymupdf — text extraction page-by-page (O(p) streaming, never whole-doc string concat — performance.md §3/§4); scanned-page detection (page with no text layer) → OCR that page via ocrmypdf/tesseract; DOCX via python-docx; PPTX via python-pptx (slide text + notes); XLSX via openpyxl read-only streaming mode (sheets as tables); TXT with encoding detection (utf-8 → chardet fallback).

Page-count limit enforced during parse (abort past max_document_pages → non-retryable FAILED "page limit exceeded").

`tests/fixtures/broken/`: committed corpus — truncated PDF, password-protected PDF, zero-byte file, mislabeled extension (PNG as .pdf), DOCX with corrupted zip, XLSX with 1M-row bomb header (synthetic), TXT in legacy encoding. Plus `tests/fixtures/valid/` minimal real files per format (generated by a checked-in script, not binaries from the internet).

**VERIFY**
```
cd backend && pytest tests/knowledge/test_parsers.py -q
# required: each valid fixture parses to expected structure (page/slide/sheet counts asserted);
# EVERY broken fixture → ParserError with human-readable reason; none hangs (per-test timeout);
# scanned-PDF fixture routes through OCR branch; page-limit abort is non-retryable;
# parse of 100-page synthetic PDF: peak memory bounded (single-pass assertion), <30s CPU
mypy src/ && ruff check .
```
Commit: `feat(knowledge): format-aware parsers with OCR fallback and failure corpus`

---

## Task 7 — Chunking strategies (pure) + selection rule

`knowledge/chunking/`: pure functions/classes, zero I/O — `ProseChunking` (token-target ~512 with ~64 overlap, sentence-boundary aware), `TableAwareChunking` (row-group chunks preserving header context per chunk), `SlideChunking` (slide+notes per chunk). `select_chunking_strategy(parsed)` in rules.py exactly per patterns.md §4 (XLSX or table_ratio>0.6 → table; PPTX → slide; else prose). Each chunk carries metadata (page/sheet/slide ref) and normalized content_hash. Complexity annotations mandatory (single pass O(tokens)).

**VERIFY**
```
cd backend && pytest tests/knowledge/test_chunking.py -q
# branch-complete strategy selection; prose: no chunk exceeds token cap, overlap correct,
# sentence boundaries respected (property tests); table: every chunk contains header row;
# determinism: same input → identical chunk hashes (run twice, compare);
# empty-document edge → zero chunks, no error
mypy src/ && ruff check .
```
Commit: `feat(knowledge): pure chunking strategies with deterministic hashing`

---

## Task 8 — Embedder Protocol + batched idempotent upsert

`ai/embedding.py` (lives in the ai domain — Phase 3 gateway will join it): `Embedder` Protocol — `async embed(texts: list[str]) -> list[Vector]`. Adapter `adapters/openai_embedder.py` (OpenAI-compatible endpoint; timeout, bounded retry+jitter, batch ≤ embedding_batch_size — the ONLY file importing the SDK; import-linter contract extended). `tests/fakes/fake_embedder.py`: deterministic hash-derived vectors of configured dimension.

Pipeline integration (worker step): chunks embedded in batches with bounded concurrency (`Semaphore(2)` over batch calls); upsert `INSERT ... ON CONFLICT (document_id, content_hash) DO NOTHING` in bulk statements (performance.md §5 — never per-row awaits). Peak memory O(batch). Dimension mismatch between settings and adapter response → non-retryable config error at startup, not mid-ingest.

**VERIFY**
```
cd backend && pytest tests/knowledge/test_embedding_pipeline.py tests/ai/test_embedder.py -q
# required: fake passes adapter contract suite; re-running embed step on same document inserts 0 new rows;
# batching: 250 chunks → ceil(250/batch) calls (call-count assertion on fake);
# bulk insert: query-count test proves O(batches) statements, not O(chunks);
# dimension mismatch fails fast with clear message
lint-imports   # SDK import contract holds
mypy src/ && ruff check .
```
Commit: `feat(ai,knowledge): embedder protocol with batched idempotent vector upsert`

---

## Task 9 — Document lifecycle: versioning, cascade delete, re-index  [PAUSE after VERIFY]

Service methods (6-step anatomy, all audited):
- **Re-upload version**: `POST /documents/{id}/versions` — new document row (version = prior+1, supersedes_id chain), new storage key, full ingest; on the new version reaching READY, the superseded version's chunks are removed (worker post-step) so retrieval never sees both; superseded row retained (status READY, chunks gone — flagged `superseded` boolean added in migration 0003 if needed → decide and ADR).
- **Delete**: `DELETE /documents/{id}` — rules: not PROCESSING; transactionally delete row (chunks cascade FK) then delete storage object; storage delete failure → compensating outbox row + retry job so no orphaned objects persist (document the pattern in the domain README).
- **Re-index**: `POST /documents/{id}/reindex` (manage permission) — ready→queued via transition map; chunks rebuilt idempotently (hash-keyed upsert + removal of hashes no longer produced).
- **Collection delete**: only when empty (ConflictError otherwise) — bulk cascade deferred to a later phase deliberately.

**VERIFY**
```
cd backend && pytest tests/knowledge/test_lifecycle.py -q
# required: version chain correct; after v2 READY, v1 chunks count == 0; query for collection chunks
#   returns only v2 hashes; delete → zero chunks rows, zero fake-storage objects, audit row exists;
# delete during PROCESSING → DOCUMENT_PROCESSING envelope; reindex → READY with identical hashes;
# storage-delete failure path → outbox retry empties orphan (failure-injected fake)
mypy src/ && ruff check .
```
Commit: `feat(knowledge): document versioning, cascading delete with storage compensation, re-index`

---

## Task 10 — Phase exit: isolation extension, drills, docs, close

- Isolation suite extended: collections, collection_permissions, documents, chunks — IDOR by document_id/chunk_id across orgs, collection-permission bypass attempts, raw-count RLS proof per table, worker-context isolation (org A job cannot touch org B rows).
- Exit-criteria drills, scripted in `scripts/drills/`: 100-page PDF timing run; worker-kill idempotency drill (from Task 5, re-run on final commit); broken-corpus sweep (all FAILED with reasons, none stuck — assert no PROCESSING older than threshold after run).
- Coverage gate, full CI command set locally (as Sprint 1), `alembic` up/down/up including 0002.
- Docs: `knowledge/README.md` (invariants: tenant scoping, idempotent ingest, version-chain semantics, outbox compensation), ADR 0006 (queue choice: ARQ vs Celery rationale), ADR 0007 (versioning + supersede semantics), runbook `stuck-ingestion.md`, README quickstart updated (MinIO/Redis ports, worker service).

**VERIFY**
```
cd backend && pytest -q --cov=src --cov-fail-under=80     # full suite incl. isolation
bash scripts/drills/run_all.sh                            # all three drills green, timings printed
alembic downgrade base && alembic upgrade head
git grep -nE "TODO|FIXME" -- src/ ; test $? -ne 0
```
Commit: `docs,test: phase 1 exit — extended isolation, drills, ADRs, runbooks`

---

## Phase 1 robustness checklist (final gate)

- [ ] Upload of max-size file: peak API memory independent of file size (streaming proven)
- [ ] Mid-stream oversize rejection leaves zero orphan storage objects
- [ ] Two workers racing one document: exactly one processes (CAS proven under real concurrency)
- [ ] Worker killed mid-ingest: sweeper recovery → READY, chunk-hash set identical to uninterrupted run
- [ ] Every broken fixture terminates FAILED with human-readable reason; zero stuck PROCESSING
- [ ] Delete leaves zero orphans across DB and storage (incl. storage-failure compensation path)
- [ ] Superseded versions never contribute chunks alongside their successor
- [ ] All four knowledge tables: forced RLS, isolation suite green, worker context isolated
- [ ] Quota and page limits enforced with typed envelopes
- [ ] No per-row awaits in any bulk path; complexity annotations on chunking/fusion-adjacent code
