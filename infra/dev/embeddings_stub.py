"""DEV-ONLY OpenAI-compatible embeddings stub.

Returns deterministic, hash-derived vectors of ``EMBEDDING_DIMENSION`` so the real
``OpenAIEmbedder`` can run the ingestion pipeline end-to-end with no external
account or cost. Implements just ``POST /v1/embeddings`` (float and base64
encodings — the OpenAI SDK requests base64 by default) and ``GET /health``.
Standard library only; never deployed outside local dev. Not part of the product.
"""

import base64
import hashlib
import json
import os
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_DIMENSION = int(os.environ.get("EMBEDDING_DIMENSION", "1536"))


def _vector(text: str) -> list[float]:
    """Deterministic unit-interval vector of width _DIMENSION (same scheme as the
    test FakeEmbedder, so dev and tests agree)."""
    values: list[float] = []
    counter = 0
    while len(values) < _DIMENSION:
        digest = hashlib.sha256(f"{text}:{counter}".encode()).digest()
        for offset in range(0, len(digest), 4):
            if len(values) >= _DIMENSION:
                break
            values.append(int.from_bytes(digest[offset : offset + 4], "big") / 2**32)
        counter += 1
    return values


def _encode(vector: list[float], encoding_format: str) -> list[float] | str:
    if encoding_format == "base64":
        return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")
    return vector


class _Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/health":
            self._json(200, {"status": "ok", "dimension": _DIMENSION})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if not self.path.rstrip("/").endswith("/embeddings"):
            self._json(404, {"error": {"message": "not found"}})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        inputs = payload.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        encoding_format = payload.get("encoding_format", "float")
        data = [
            {"object": "embedding", "index": i, "embedding": _encode(_vector(str(text)), encoding_format)}
            for i, text in enumerate(inputs)
        ]
        self._json(
            200,
            {
                "object": "list",
                "data": data,
                "model": payload.get("model", "dev-stub"),
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            },
        )

    def log_message(self, *_args: object) -> None:
        return  # keep the dev log quiet


if __name__ == "__main__":
    print(f"embeddings-stub listening on :8000 (dimension={_DIMENSION})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), _Handler).serve_forever()
