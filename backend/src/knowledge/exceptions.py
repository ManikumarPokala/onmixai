"""Typed knowledge-domain errors. Each carries a stable code + HTTP status; the
global handler renders them in the standard envelope (CLAUDE.md §5)."""

from src.shared.errors import AppError


class CollectionNotFoundError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("COLLECTION_NOT_FOUND", 404, "Collection not found", detail)


class DocumentNotFoundError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("DOCUMENT_NOT_FOUND", 404, "Document not found", detail)


class CollectionNameTakenError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("COLLECTION_NAME_TAKEN", 409, "Collection name already in use", detail)


class DocumentQuotaExceededError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("DOCUMENT_QUOTA_EXCEEDED", 409, "Document quota exceeded", detail)


class UnsupportedFormatError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("UNSUPPORTED_FORMAT", 415, "Unsupported document format", detail)


class UploadTooLargeError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("UPLOAD_TOO_LARGE", 413, "Upload exceeds the size limit", detail)


class DocumentProcessingError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__(
            "DOCUMENT_PROCESSING", 409, "Cannot modify a document while it is processing", detail
        )


class InvalidStatusTransitionError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("INVALID_STATUS_TRANSITION", 409, "Invalid status transition", detail)


class CollectionAccessDeniedError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("COLLECTION_ACCESS_DENIED", 403, "Collection access denied", detail)
