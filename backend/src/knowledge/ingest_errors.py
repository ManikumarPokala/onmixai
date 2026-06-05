"""Ingestion error types shared by the worker pipeline and the parsers.

Kept in their own module so ``parsing`` and ``worker`` can both import them
without a cycle.
"""


class RetryableError(Exception):
    """A transient ingestion failure that should be retried."""


class IngestError(Exception):
    """A permanent ingestion failure carrying a user-safe reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ParserError(IngestError):
    """A document could not be parsed (permanent). Reason is user-safe."""


def safe_reason(exc: Exception) -> str:
    """User-visible failure reason that never leaks internals."""
    if isinstance(exc, IngestError):
        return exc.reason
    return "ingestion failed"
