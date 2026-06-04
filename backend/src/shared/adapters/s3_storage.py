"""S3-compatible object storage adapter (aioboto3) — the only S3 SDK import site.

Uploads stream via multipart in fixed-size parts, so peak memory is O(part size),
independent of file size (performance.md §4). Works against AWS S3 or MinIO via
``endpoint_url``.
"""

from collections.abc import AsyncIterator
from typing import Any

import aioboto3
from botocore.exceptions import ClientError

from src.shared.config import Settings
from src.shared.storage import StoredObject

# Multipart part size. S3 requires every part except the last to be >= 5 MiB.
_PART_SIZE = 8 * 1024 * 1024
_DOWNLOAD_CHUNK = 1024 * 1024


class S3ObjectStorage:
    """Streaming object storage backed by an S3-compatible service."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.storage_bucket
        self._endpoint = settings.storage_endpoint
        self._access_key = settings.storage_access_key.get_secret_value()
        self._secret_key = settings.storage_secret_key.get_secret_value()
        self._session = aioboto3.Session()

    def _client(self) -> Any:
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name="us-east-1",
        )

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except ClientError:
                await s3.create_bucket(Bucket=self._bucket)

    async def put_stream(
        self, key: str, stream: AsyncIterator[bytes], content_type: str
    ) -> StoredObject:
        """Multipart-upload ``stream`` to ``key``.

        Buffers at most one part at a time. Files smaller than one part use a
        single ``put_object``. Time: O(n/part) requests; Space: O(part size).
        """
        async with self._client() as s3:
            buffer = bytearray()
            parts: list[dict[str, Any]] = []
            upload_id: str | None = None
            part_number = 1
            total = 0
            try:
                async for chunk in stream:
                    buffer.extend(chunk)
                    total += len(chunk)
                    while len(buffer) >= _PART_SIZE:
                        if upload_id is None:
                            upload_id = await self._begin(s3, key, content_type)
                        part = bytes(buffer[:_PART_SIZE])
                        del buffer[:_PART_SIZE]
                        parts.append(await self._upload_part(s3, key, upload_id, part_number, part))
                        part_number += 1

                if upload_id is None:
                    # Whole payload fit in one part (or is empty): single PUT.
                    await s3.put_object(
                        Bucket=self._bucket, Key=key, Body=bytes(buffer), ContentType=content_type
                    )
                else:
                    if buffer:
                        parts.append(
                            await self._upload_part(s3, key, upload_id, part_number, bytes(buffer))
                        )
                    await s3.complete_multipart_upload(
                        Bucket=self._bucket,
                        Key=key,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                    )
            except Exception:
                if upload_id is not None:
                    await s3.abort_multipart_upload(
                        Bucket=self._bucket, Key=key, UploadId=upload_id
                    )
                raise
            return StoredObject(key=key, size_bytes=total)

    async def _begin(self, s3: Any, key: str, content_type: str) -> str:
        response = await s3.create_multipart_upload(
            Bucket=self._bucket, Key=key, ContentType=content_type
        )
        return str(response["UploadId"])

    async def _upload_part(
        self, s3: Any, key: str, upload_id: str, number: int, body: bytes
    ) -> dict[str, Any]:
        response = await s3.upload_part(
            Bucket=self._bucket, Key=key, PartNumber=number, UploadId=upload_id, Body=body
        )
        return {"ETag": response["ETag"], "PartNumber": number}

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        async with self._client() as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            async for chunk in response["Body"].iter_chunks(_DOWNLOAD_CHUNK):
                yield chunk

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)

    async def exists(self, key: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
            except ClientError:
                return False
            return True
