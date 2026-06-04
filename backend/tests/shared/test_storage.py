"""Contract tests for ObjectStorage: the fake and the live S3 adapter must agree.

The adapter runs against a MinIO testcontainer (self-contained — identical local
and CI, no reliance on the compose stack being up). A separate adapter-only test
proves uploads stream with peak memory independent of file size.
"""

import re
import tracemalloc
from collections.abc import AsyncIterator, Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from src.shared.adapters.s3_storage import S3ObjectStorage
from src.shared.config import Settings
from src.shared.storage import ObjectStorage
from tests.fakes.fake_storage import FakeObjectStorage

_MINIO_IMAGE = "minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
_ACCESS = "accesskey"
_SECRET = "secretkey-min8"  # MinIO requires the secret to be >= 8 chars


@pytest.fixture(scope="session")
def minio_endpoint() -> Iterator[str]:
    container = (
        DockerContainer(_MINIO_IMAGE)
        .with_command("server /data")
        .with_env("MINIO_ROOT_USER", _ACCESS)
        .with_env("MINIO_ROOT_PASSWORD", _SECRET)
        .with_exposed_ports(9000)
        .waiting_for(LogMessageWaitStrategy(re.compile(r"API:")))
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9000)
        yield f"http://{host}:{port}"
    finally:
        container.stop()


def _settings(endpoint: str) -> Settings:
    return Settings(
        env="test",
        database_url="postgresql+asyncpg://u:p@localhost:5432/d",
        jwt_secret="x" * 40,
        storage_endpoint=endpoint,
        storage_access_key=_ACCESS,
        storage_secret_key=_SECRET,
        storage_bucket="contract-bucket",
        redis_url="redis://localhost:6379/0",
        embedding_dimension=8,
        _env_file=None,
    )


async def _bytes_stream(data: bytes, chunk: int = 1024 * 1024) -> AsyncIterator[bytes]:
    for start in range(0, len(data), chunk):
        yield data[start : start + chunk]


async def _zero_stream(total: int, chunk: int = 1024 * 1024) -> AsyncIterator[bytes]:
    """Yield ``total`` zero bytes in fixed chunks without ever holding it all."""
    block = b"\0" * chunk
    remaining = total
    while remaining > 0:
        take = min(chunk, remaining)
        yield block[:take]
        remaining -= take


@pytest.fixture(params=["fake", "adapter"])
async def storage(
    request: pytest.FixtureRequest, minio_endpoint: str
) -> AsyncIterator[ObjectStorage]:
    if request.param == "fake":
        yield FakeObjectStorage()
    else:
        adapter = S3ObjectStorage(_settings(minio_endpoint))
        await adapter.ensure_bucket()
        yield adapter


async def _drain(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])


async def test_round_trip_put_get_delete(storage: ObjectStorage) -> None:
    await storage.put_stream("k/round", _bytes_stream(b"hello world"), "text/plain")
    assert await storage.exists("k/round") is True
    assert await _drain(storage.get_stream("k/round")) == b"hello world"
    await storage.delete("k/round")
    assert await storage.exists("k/round") is False


async def test_delete_is_idempotent(storage: ObjectStorage) -> None:
    await storage.delete("k/never-existed")  # no error


async def test_empty_object_round_trips(storage: ObjectStorage) -> None:
    await storage.put_stream("k/empty", _bytes_stream(b""), "application/octet-stream")
    assert await storage.exists("k/empty") is True
    assert await _drain(storage.get_stream("k/empty")) == b""
    await storage.delete("k/empty")


async def test_adapter_upload_memory_is_bounded(minio_endpoint: str) -> None:
    adapter = S3ObjectStorage(_settings(minio_endpoint))
    await adapter.ensure_bucket()
    total = 60 * 1024 * 1024
    tracemalloc.start()
    await adapter.put_stream("k/large", _zero_stream(total), "application/octet-stream")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # Peak well under the file size proves streaming, not whole-file buffering.
    assert peak < 45 * 1024 * 1024
    await adapter.delete("k/large")
