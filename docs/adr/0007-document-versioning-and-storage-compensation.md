# ADR 0007 — Document Versioning, Supersession, and Storage-Deletion Compensation

Status: Accepted (2026-06-05)

## Context

Phase 1 documents have a lifecycle beyond first ingest: a user re-uploads a newer
version, deletes a document, or re-indexes it. Two correctness hazards arise:

1. **Two live versions.** A re-upload must not leave retrieval able to see both the
   old and new content of the "same" document.
2. **Orphaned storage objects.** A document's bytes live in object storage, outside
   the database transaction. Deleting the DB row and the object is a two-system
   operation; a crash between them would orphan the object (cost + privacy risk)
   or — in the other order — delete bytes for a row that still exists.

## Decision

### Versioning via a supersedes chain + `superseded` flag

A re-upload (`POST /documents/{id}/versions`) creates a **new** document row with
`version = prior + 1` and `supersedes_id = prior.id`, its own storage key, and a
full ingest. When the new version reaches READY, the worker — in the same
transaction as `mark_ready` — deletes the prior version's chunks and sets its
`superseded = true`. The prior row is retained (status stays READY) for history and
audit, but with no chunks it is invisible to retrieval.

We add the boolean `documents.superseded` (migration 0003) rather than inferring
"superseded" from "READY with zero chunks": the flag is explicit, lets list/UX
queries hide old versions cheaply, and disambiguates a legitimately empty document
from a retired one. Retrieval (Phase 2) still defensively filters
`embedding IS NOT NULL`, so a chunk-less version is doubly excluded.

### Cascade delete with a transactional storage-deletion outbox

`DELETE /documents/{id}` (forbidden while PROCESSING) deletes the document row —
chunks cascade via the FK — and, **in the same transaction**, inserts a
`storage_deletion_outbox` row holding the object's storage key. After the request
commits, an after-commit hook best-effort deletes the object. If that hook fails
(or never runs because the process died), the durable outbox row remains and the
`sweep_storage_outbox` cron retries deletion and clears the row. Storage delete is
idempotent, so a key processed twice is harmless.

This is the transactional-outbox pattern: the DB commit is the single source of
truth for "this object must be deleted", and the actual deletion is an at-least-once
retried side effect. The ordering (commit the intent first, delete the object
second) guarantees no orphan can outlive the row — the opposite ordering could
delete bytes for a row a rollback then kept.

### Re-index

`POST /documents/{id}/reindex` (MANAGE) moves READY → QUEUED via a compare-and-set
and re-runs ingest. Rebuild is idempotent: chunks are upserted by content hash and
hashes no longer produced are deleted (`delete_stale`), so re-indexing unchanged
content is a no-op and changed content converges to exactly the new chunk set.

### Collection delete

`DELETE /collections/{id}` succeeds only when the collection holds no documents
(`COLLECTION_NOT_EMPTY` otherwise). Bulk cascade of a non-empty collection is
deliberately deferred to a later phase.

## Consequences

- A new version is a new row and counts toward the org document quota; reclaiming
  superseded rows is a future cleanup job (the `superseded` flag makes them easy to
  find).
- The outbox is tenant-owned: it has `org_id NOT NULL` + forced RLS (migration
  0003), like every other tenant table.
- The supersede step runs inside the new version's ingest transaction, so a crash
  before commit leaves the old version intact and the new one re-processable.
- `storage_deletion_outbox` rows are observable: a growing table signals a storage
  outage, and `attempts` surfaces repeatedly failing keys.
