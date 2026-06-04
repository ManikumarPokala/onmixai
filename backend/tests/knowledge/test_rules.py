"""Branch-complete tests for the knowledge domain rules (pure, no I/O)."""

import itertools

import pytest

from src.knowledge.exceptions import (
    CollectionAccessDeniedError,
    DocumentProcessingError,
    DocumentQuotaExceededError,
    InvalidStatusTransitionError,
    UnsupportedFormatError,
    UploadTooLargeError,
)
from src.knowledge.models import DocumentStatus, Permission
from src.knowledge.rules import (
    SUPPORTED_CONTENT_TYPES,
    ensure_collection_permission,
    ensure_document_deletable,
    ensure_upload_acceptable,
    ensure_within_quota,
    transition,
)

_LEGAL: set[tuple[DocumentStatus, DocumentStatus]] = {
    (DocumentStatus.QUEUED, DocumentStatus.PROCESSING),
    (DocumentStatus.PROCESSING, DocumentStatus.READY),
    (DocumentStatus.PROCESSING, DocumentStatus.FAILED),
    (DocumentStatus.FAILED, DocumentStatus.QUEUED),
    (DocumentStatus.READY, DocumentStatus.QUEUED),
}


@pytest.mark.parametrize(("current", "target"), sorted(_LEGAL))
def test_legal_transitions_allowed(current: DocumentStatus, target: DocumentStatus) -> None:
    assert transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [pair for pair in itertools.product(DocumentStatus, repeat=2) if pair not in _LEGAL],
)
def test_illegal_transitions_rejected(current: DocumentStatus, target: DocumentStatus) -> None:
    with pytest.raises(InvalidStatusTransitionError):
        transition(current, target)


def test_quota_blocks_at_or_above_limit() -> None:
    ensure_within_quota(4, 5)  # under limit ok
    with pytest.raises(DocumentQuotaExceededError):
        ensure_within_quota(5, 5)
    with pytest.raises(DocumentQuotaExceededError):
        ensure_within_quota(6, 5)


@pytest.mark.parametrize("content_type", sorted(SUPPORTED_CONTENT_TYPES))
def test_supported_formats_accepted(content_type: str) -> None:
    ensure_upload_acceptable(10, content_type, max_bytes=100)


def test_unsupported_format_rejected() -> None:
    with pytest.raises(UnsupportedFormatError):
        ensure_upload_acceptable(10, "image/png", max_bytes=100)


def test_oversize_rejected_at_boundary() -> None:
    ensure_upload_acceptable(100, "text/plain", max_bytes=100)  # equal ok
    with pytest.raises(UploadTooLargeError):
        ensure_upload_acceptable(101, "text/plain", max_bytes=100)


def test_document_deletable_only_when_not_processing() -> None:
    for status in (DocumentStatus.QUEUED, DocumentStatus.READY, DocumentStatus.FAILED):
        ensure_document_deletable(status)
    with pytest.raises(DocumentProcessingError):
        ensure_document_deletable(DocumentStatus.PROCESSING)


@pytest.mark.parametrize(
    ("held", "required", "ok"),
    [
        (Permission.READ, Permission.READ, True),
        (Permission.WRITE, Permission.READ, True),
        (Permission.MANAGE, Permission.WRITE, True),
        (Permission.READ, Permission.WRITE, False),
        (Permission.WRITE, Permission.MANAGE, False),
        (Permission.READ, Permission.MANAGE, False),
    ],
)
def test_permission_ordering(held: Permission, required: Permission, ok: bool) -> None:
    if ok:
        ensure_collection_permission(held, required)
    else:
        with pytest.raises(CollectionAccessDeniedError):
            ensure_collection_permission(held, required)
