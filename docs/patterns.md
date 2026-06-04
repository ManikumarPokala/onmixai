# OnMixAI — Logic Structure Patterns (docs/patterns.md)

This document defines the canonical shape of business logic in this codebase. Every service, repository, pipeline, and state transition follows these patterns exactly. When writing new logic, copy the structure here — do not invent a new shape. CLAUDE.md says where logic lives; this document says what it looks like.

---

## 1. Anatomy of a Service Method

Every service method follows the same 6-step order. Steps may be absent, never reordered.

```python
class DocumentService:
    def __init__(self, repo: DocumentRepository, quota: QuotaService, audit: AuditEmitter):
        self._repo = repo          # dependencies injected via constructor — never instantiated inline
        self._quota = quota
        self._audit = audit

    async def delete_document(self, actor: AuthContext, document_id: UUID) -> None:
        # 1. AUTHORIZE — can this actor do this at all?
        actor.require_any_role(Role.ADMIN, Role.OWNER)

        # 2. LOAD — fetch state, always tenant-scoped; absence is an explicit domain error
        document = await self._repo.get(actor.org_id, document_id)
        if document is None:
            raise NotFoundError("DOCUMENT_NOT_FOUND")

        # 3. CHECK INVARIANTS — pure business rules, no I/O (see §4)
        ensure_document_deletable(document)   # raises ConflictError("DOCUMENT_PROCESSING") if mid-pipeline

        # 4. MUTATE — state changes through the repository; cascades are explicit, not implied
        await self._repo.delete_with_derived_data(actor.org_id, document_id)  # chunks, embeddings, index

        # 5. RECORD — audit/event emission is part of the operation, not an afterthought
        await self._audit.emit(actor, "document.deleted", resource_id=document_id)

        # 6. RETURN — DTO or None. Never ORM models, never raw dicts.
```

Rules:
- One service method = one use case = one transaction (the session/transaction is owned by the request scope, Task 3 of Sprint 1; services never call `commit()` themselves).
- Constructor injection only. A service that builds its own dependencies cannot be tested and does not merge.
- Steps 1–3 must complete before any mutation. Half-applied operations are forbidden — if a later step can fail, validate its preconditions up front.
- A method exceeding ~40 lines or doing two use cases gets split.

## 2. Anatomy of a Repository Method

```python
class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, org_id: UUID, document_id: UUID) -> Document | None:
        stmt = select(Document).where(
            Document.org_id == org_id,            # tenant scope FIRST, always, even with RLS active
            Document.id == document_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_ready(self, org_id: UUID, *, cursor: UUID | None, limit: int = 50) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.org_id == org_id, Document.status == DocumentStatus.READY)
            .order_by(Document.id)
            .limit(min(limit, 100))               # server-side cap — clients cannot request unbounded sets
        )
        if cursor:
            stmt = stmt.where(Document.id > cursor)
        return list((await self._session.execute(stmt)).scalars())
```

Rules:
- Returns ORM models or None/lists — no business decisions (a repository never raises domain errors; the service interprets `None`).
- No method without explicit tenant scope if the table is tenant-owned.
- All list methods paginated with a hard server-side cap.
- Query-building stays here; if a service needs a new access pattern, it gets a new named repository method — never an escape-hatch `execute_raw`.

## 3. State Machines — Explicit, Never Implicit

Any entity with a lifecycle gets an explicit transition map. Status is never set by direct assignment scattered across the codebase.

```python
class DocumentStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.QUEUED:     frozenset({DocumentStatus.PROCESSING}),
    DocumentStatus.PROCESSING: frozenset({DocumentStatus.READY, DocumentStatus.FAILED}),
    DocumentStatus.FAILED:     frozenset({DocumentStatus.QUEUED}),     # retry path
    DocumentStatus.READY:      frozenset({DocumentStatus.QUEUED}),     # re-index path
}

def transition(current: DocumentStatus, target: DocumentStatus) -> DocumentStatus:
    if target not in _TRANSITIONS[current]:
        raise ConflictError("INVALID_STATUS_TRANSITION",
                            detail=f"{current} -> {target} is not allowed")
    return target
```

- The single `transition()` function is the only way status changes. Workers, services, retry handlers all go through it.
- Status updates in the database use compare-and-set (`UPDATE ... WHERE status = :expected`) so two workers can never both claim the same document — the loser gets 0 rows and backs off. This is what "never breaks" means under concurrency.

## 4. Business Rules as Pure Functions

Every non-trivial rule is a pure function in `<domain>/rules.py`: takes data in, returns or raises, performs zero I/O. This is what makes logic testable in milliseconds and reusable across service methods and workers.

```python
# knowledge/rules.py
def ensure_document_deletable(document: Document) -> None:
    if document.status == DocumentStatus.PROCESSING:
        raise ConflictError("DOCUMENT_PROCESSING",
                            detail="Cannot delete while ingestion is running")

def ensure_within_quota(current_count: int, quota: OrgQuota) -> None:
    if current_count >= quota.max_documents:
        raise QuotaExceededError("DOCUMENT_QUOTA_EXCEEDED")

def select_chunking_strategy(doc: ParsedDocument) -> ChunkingStrategy:
    if doc.format in (Format.XLSX,) or doc.table_ratio > 0.6:
        return TableAwareChunking()
    if doc.format is Format.PPTX:
        return SlideChunking()
    return ProseChunking()
```

Rules:
- No `await`, no session, no settings access inside `rules.py`. The service gathers the data; the rule decides.
- Each rule has direct unit tests covering every branch — these are the cheapest, most valuable tests in the codebase.
- If you find an `if` expressing a business decision inside a router, worker, or repository, it is in the wrong place. Move it to `rules.py`.

## 5. Pipelines as Composed Steps (RAG, ingestion, agents)

Multi-step flows are sequences of named, single-responsibility steps with typed inputs/outputs — never one 300-line function.

```python
# search/pipeline.py — the grounded-answer flow
@dataclass(frozen=True)
class RetrievalContext:
    query: str
    chunks: list[ScoredChunk]
    confidence: ConfidenceBand

class GroundedAnswerPipeline:
    def __init__(self, retriever: PermissionAwareRetriever, assembler: ContextAssembler,
                 gateway: LLMGateway, validator: GroundingValidator):
        ...

    async def run(self, actor: AuthContext, query: str) -> GroundedAnswer:
        chunks = await self._retriever.retrieve(actor, query)          # ACL filter INSIDE retrieval
        ctx = self._assembler.assemble(query, chunks)                  # pure: truncation, dedup, ordering
        if ctx.confidence is ConfidenceBand.BELOW_THRESHOLD:
            return GroundedAnswer.refusal(reason="INSUFFICIENT_SOURCES")   # refusal is a first-class result
        draft = await self._gateway.complete(prompt=build_prompt(ctx))     # only step that costs money
        return self._validator.validate(draft, ctx)                        # citations check; fails → refusal
```

Rules:
- Each step is independently unit-testable; only the gateway step touches an LLM.
- Refusal/degraded outcomes are typed results, not exceptions — they are expected business outcomes.
- Steps share data through immutable dataclasses, never through mutation of a shared dict.

## 6. External Integrations Behind Protocols

Every external system (LLM providers, embeddings, OCR, object storage) is accessed through a `Protocol` owned by us, with one real adapter per provider and one fake for tests.

```python
class LLMGateway(Protocol):
    async def complete(self, *, prompt: RenderedPrompt, model: ModelRef | None = None) -> Completion: ...

# adapters/litellm_gateway.py — the ONLY file that imports the provider SDK.
# Owns: timeout, bounded retry w/ backoff+jitter, fallback chain, token metering, tracing.
# tests/fakes/fake_gateway.py — deterministic, scriptable responses; used by every test.
```

- Business logic imports the Protocol, never a provider SDK. Swapping providers touches one adapter file.
- Resilience policy (timeouts, retries, circuit breaker) lives in the adapter once — features cannot opt out and cannot duplicate it.

## 7. Workers — Idempotent by Construction

```python
async def process_document_task(document_id: UUID, org_id: UUID) -> None:
    # 1. CLAIM atomically (compare-and-set; loser exits silently — safe under duplicate delivery)
    claimed = await repo.claim_for_processing(org_id, document_id,
                                              expected=DocumentStatus.QUEUED)
    if not claimed:
        return

    try:
        parsed   = await ocr.parse(document)          # each step resumable / re-runnable
        chunks   = chunker.chunk(parsed)              # pure
        await embedder.embed_and_store(org_id, document_id, chunks)  # upsert by content hash — re-run safe
        await repo.mark(org_id, document_id, DocumentStatus.READY)
    except RetryableError:
        await repo.schedule_retry(org_id, document_id, max_attempts=3)   # bounded; then FAILED + reason
    except Exception as exc:
        await repo.mark_failed(org_id, document_id, reason=safe_reason(exc))
        raise   # still logged/alerted — failure is recorded for the user AND visible to operators
```

Worker rules:
- Claim before work, via compare-and-set. Two workers can never process the same document.
- Every write is an upsert keyed deterministically (content hash) — running a task twice produces the same end state.
- Retries are bounded with backoff; the terminal state is always user-visible (FAILED + reason), never a silently stuck PROCESSING. A sweeper job re-queues documents stuck in PROCESSING past a deadline (worker died mid-task).

## 8. Schemas and Boundaries

- Three schema layers, never mixed: request/response schemas (`schemas.py`, what clients see), domain DTOs (internal, full data), ORM models (persistence only).
- Response schemas are allow-lists: fields are explicitly declared. Sensitive fields (password_hash, token_hash, internal flags) are structurally impossible to leak because they were never in the schema.
- Conversions live in `schemas.py` as classmethods (`UserResponse.from_model(user)`) — one place, not scattered `.dict()` calls.

## 9. The Error Decision Table

| Situation | What to do |
|---|---|
| Caller's fault (bad input, not found, conflict) | Raise typed domain error from §4 rules or step 2/3 of the service |
| External provider failed, retryable | Adapter retries (bounded); then raise `UpstreamUnavailableError` → 503 envelope |
| Provider failed, not retryable | `UpstreamRejectedError` with safe message; full detail logged |
| Our bug (invariant violated) | Let it raise → global handler → 500 envelope + ERROR log with traceback |
| Expected business outcome (low confidence, empty results) | NOT an error. Typed result (`GroundedAnswer.refusal`, empty page) |

Never: catch-and-continue with a default value that hides the failure; convert all exceptions to one generic error; raise strings; use exceptions for control flow of expected outcomes.

## 10. Banned Shapes (instant PR rejection)

- God service: one class with 20 methods spanning use cases → split by use case cluster.
- Logic in routers/workers: any business `if` outside service/rules.
- Direct status assignment: `doc.status = "ready"` anywhere outside `transition()` + repository mark-methods.
- Boolean parameter pile-ups: `process(doc, True, False, True)` → use enums/dataclass options.
- Dict-shaped data crossing layer boundaries → typed schemas/dataclasses only.
- Shared mutable module-level state (caches, lists) → DI-provided objects with explicit lifecycle.
- `try/except` around an entire function body → narrow scopes per §9.
- Provider SDK imports outside `adapters/`.

## 11. Definition of "Well-Structured" (review checklist per PR)

- [ ] Each new service method follows the 6-step anatomy; ≤ ~40 lines
- [ ] Business decisions live in `rules.py` as pure functions with branch-complete tests
- [ ] Lifecycle changes go through the transition map; concurrent paths use compare-and-set
- [ ] Multi-step flows are composed steps with typed, immutable intermediates
- [ ] External calls behind a Protocol; fake exists and is used in tests
- [ ] Workers idempotent: claim, upsert, bounded retry, visible terminal state
- [ ] Errors follow the decision table; expected outcomes are typed results, not exceptions
- [ ] No banned shapes from §10 anywhere in the diff
