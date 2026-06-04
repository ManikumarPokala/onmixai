"""In-memory ObjectStorage fake — deterministic, records calls."""

from collections.abc import AsyncIterator

from src.shared.storage import StoredObject


class FakeObjectStorage:
    """Implements the ObjectStorage Protocol entirely in memory."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        # Optional failure injection for compensation-path tests (Task 9).
        self.fail_delete: bool = False

    async def ensure_bucket(self) -> None:
        return None

    async def put_stream(
        self, key: str, stream: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        buffer = bytearray()
        async for chunk in stream:
            buffer.extend(chunk)
        self.objects[key] = bytes(buffer)
        return StoredObject(key=key, size_bytes=len(buffer))

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        data = self.objects[key]
        chunk = 1024 * 1024
        for start in range(0, len(data), chunk):
            yield data[start : start + chunk]

    async def delete(self, key: str) -> None:
        if self.fail_delete:
            raise RuntimeError("injected storage delete failure")
        self.objects.pop(key, None)
        self.deleted.append(key)

    async def exists(self, key: str) -> bool:
        return key in self.objects
