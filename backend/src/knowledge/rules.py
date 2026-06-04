"""Pure knowledge-domain rules (no I/O) — patterns.md §3 (state machine) and §4.

The document status state machine and all admission/permission checks live here
as pure functions with branch-complete tests; services gather data and call these.
"""

from src.knowledge.exceptions import (
    CollectionAccessDeniedError,
    DocumentProcessingError,
    DocumentQuotaExceededError,
    InvalidStatusTransitionError,
    UnsupportedFormatError,
    UploadTooLargeError,
)
from src.knowledge.models import DocumentStatus, Permission

# Allowed document status transitions (patterns.md §3).
_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.QUEUED: frozenset({DocumentStatus.PROCESSING}),
    DocumentStatus.PROCESSING: frozenset({DocumentStatus.READY, DocumentStatus.FAILED}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.QUEUED}),  # retry
    DocumentStatus.READY: frozenset({DocumentStatus.QUEUED}),  # re-index
}

# Permission ordering: read < write < manage.
_PERMISSION_RANK: dict[Permission, int] = {
    Permission.READ: 0,
    Permission.WRITE: 1,
    Permission.MANAGE: 2,
}

# The five supported upload formats (MIME allow-list).
SUPPORTED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
    }
)


def transition(current: DocumentStatus, target: DocumentStatus) -> DocumentStatus:
    """Return ``target`` if the transition is allowed, else raise. Time/Space: O(1)."""
    if target not in _TRANSITIONS[current]:
        raise InvalidStatusTransitionError(detail=f"{current.value} -> {target.value}")
    return target


def ensure_within_quota(current_count: int, max_documents: int) -> None:
    """Reject a new document once the org is at its quota. Time/Space: O(1)."""
    if current_count >= max_documents:
        raise DocumentQuotaExceededError(detail=f"limit is {max_documents}")


def ensure_upload_acceptable(size_bytes: int, content_type: str, max_bytes: int) -> None:
    """Reject unsupported formats and oversize uploads. Time/Space: O(1)."""
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise UnsupportedFormatError(detail=content_type)
    if size_bytes > max_bytes:
        raise UploadTooLargeError(detail=f"{size_bytes} > {max_bytes}")


def ensure_document_deletable(status: DocumentStatus) -> None:
    """Forbid mutating a document mid-ingestion. Time/Space: O(1)."""
    if status == DocumentStatus.PROCESSING:
        raise DocumentProcessingError()


def ensure_collection_permission(held: Permission, required: Permission) -> None:
    """Enforce read < write < manage: ``held`` must meet ``required``. Time/Space: O(1)."""
    if _PERMISSION_RANK[held] < _PERMISSION_RANK[required]:
        raise CollectionAccessDeniedError(detail=f"requires {required.value}")
