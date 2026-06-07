"""DEV-ONLY OpenAI-compatible chat-completions stub.

Returns deterministic, echo-derived completions so the gateway + adapter run
end-to-end with no external account or cost, and so eval scores are repeatable.
Implements ``POST /v1/chat/completions`` and ``GET /health``. Standard library
only; never deployed outside local dev. Not part of the product.

Fault injection (for the resilience + timeout drills, Task 4) via request headers:
- ``X-Stub-Fail: 1``    → respond 503 (a retryable upstream failure).
- ``X-Stub-Status: 400``→ respond with that exact status (e.g. a non-retryable 4xx).
- ``X-Stub-Delay-Ms: N``→ sleep N ms before responding (to trip client timeouts).
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Fixed so a completion is byte-identical across runs (deterministic eval scores).
_CREATED = 1_700_000_000


def _word_count(text: str) -> int:
    return max(1, len(text.split()))


def _last_user_content(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return str(messages[-1].get("content", "")) if messages else ""


def _completion_text(payload: dict) -> str:
    """Deterministic, echo-derived answer. If JSON mode is requested, return a JSON
    object string so the adapter's structured-output path has something valid."""
    user = _last_user_content(payload.get("messages", []))
    if (payload.get("response_format") or {}).get("type") == "json_object":
        return json.dumps({"answer": user[:500]})
    return f"stub completion for: {user[:500]}"


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
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": {"message": "not found"}})
            return

        delay_ms = int(self.headers.get("X-Stub-Delay-Ms", "0") or "0")
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        if self.headers.get("X-Stub-Status"):
            code = int(self.headers["X-Stub-Status"])
            self._json(code, {"error": {"message": f"stub injected status {code}", "type": "stub"}})
            return
        if self.headers.get("X-Stub-Fail") == "1":
            self._json(503, {"error": {"message": "stub injected failure", "type": "stub"}})
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        content = _completion_text(payload)
        prompt_tokens = sum(
            _word_count(str(m.get("content", ""))) for m in payload.get("messages", [])
        )
        completion_tokens = _word_count(content)
        self._json(
            200,
            {
                "id": "chatcmpl-stub",
                "object": "chat.completion",
                "created": _CREATED,
                "model": payload.get("model", "dev-stub"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            },
        )

    def log_message(self, *_args: object) -> None:
        return  # keep the dev log quiet


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"llm-stub listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), _Handler).serve_forever()
