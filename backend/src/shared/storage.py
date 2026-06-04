"""Object storage Protocol owned by us (patterns.md §6).

Business code depends on this Protocol, never a provider SDK — the only file
importing the S3 SDK is ``adapters/s3_storage.py`` (CLAUDE.md §3.6). Uploads and
downloads stream in bounded chunks so peak memory is independent of file size
(performance.md §4).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from src.shared.config import get_settings


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Result of a successful upload."""

    key: str
    size_bytes: int


class ObjectStorage(Protocol):
    """Streaming object storage. One adapter per provider, one fake for tests."""

    async def ensure_bucket(self) -> None:
        """Create the configured bucket if absent (idempotent; called at startup)."""
        ...

    async def put_stream(
        self, key: str, stream: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        """Stream ``stream`` to ``key`` without buffering the whole payload."""
        ...

    def get_stream(self, key: str) -> AsyncIterator[bytes]:
        """Yield the object's bytes in chunks (an async generator)."""
        ...

    async def delete(self, key: str) -> None:
        """Delete ``key`` (idempotent — absent keys are not an error)."""
        ...

    async def exists(self, key: str) -> bool:
        """Return whether ``key`` exists."""
        ...


@lru_cache
def get_object_storage() -> ObjectStorage:
    """Process-wide object storage, constructed from settings on first use.

    Imported lazily so this module stays free of the provider SDK.
    """
    from src.shared.adapters.s3_storage import S3ObjectStorage

    return S3ObjectStorage(get_settings())
