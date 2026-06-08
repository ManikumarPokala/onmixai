"""DEV-ONLY OpenAI-compatible chat-completions stub.

Returns deterministic, echo-derived completions so the gateway + adapter run
end-to-end with no external account or cost, and so eval scores are repeatable.
Implements ``POST /v1/chat/completions`` and ``GET /health``. Standard library
only; never deployed outside local dev. Not part of the product.

Fault injection (for the resilience + timeout drills, Task 4):
- by header — ``X-Stub-Fail: 1`` → 503; ``X-Stub-Status: 400`` → that status;
  ``X-Stub-Delay-Ms: N`` → sleep N ms (to trip client timeouts).
- by model name — a model containing ``fail`` → 503 (retryable), ``reject`` → 400
  (non-retryable). This lets a multi-model fallback chain fail specific entries
  without per-request header plumbing through the client.

``REQUEST_LOG`` records every chat request's model so a test can prove how many
attempts reached the provider (e.g. that an open circuit skipped a model). It is
dev/test observability only — tests clear it; never used by the product.
"""

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Fixed so a completion is byte-identical across runs (deterministic eval scores).
_CREATED = 1_700_000_000

REQUEST_LOG: list[dict[str, Any]] = []

# Distinctive tokens (length ≥ 6) used for the grounded-answer "is this supported?" heuristic.
_DISTINCTIVE = re.compile(r"[a-z0-9]{6,}")
# Split a sources block on line-leading [n] markers; a source segment is framed/multi-line
# (the injection guard wraps each source), so we scan the whole segment, not just its first line.
_SOURCE_SPLIT = re.compile(r"(?m)^\s*\[(\d+)\]\s*")


def _word_count(text: str) -> int:
    return max(1, len(text.split()))


def _last_user_content(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return str(messages[-1].get("content", "")) if messages else ""


def _distinctive_tokens(text: str) -> set[str]:
    return set(_DISTINCTIVE.findall(text.lower()))


def _grounded_completion(user: str) -> str:
    """Deterministic, grounding-aware answer for a grounded-answer prompt: cite the FIRST
    numbered source whose content shares a distinctive (≥6-char) token with the question —
    so an answerable question yields a valid citation to the supporting source. If no source
    supports the question, return a no-citation answer (the pipeline then refuses it as
    ungrounded). This makes the chat eval's answerable / refusal split deterministic while
    exercising the real cite-or-refuse pipeline. Dev-only; never product behavior."""
    after = user.split("Sources:", 1)[1]
    sources_block, _, question = after.partition("Question:")
    question_tokens = _distinctive_tokens(question)
    parts = _SOURCE_SPLIT.split(sources_block)  # ['', '1', seg1, '2', seg2, ...]
    for number, segment in zip(parts[1::2], parts[2::2]):
        if _distinctive_tokens(segment) & question_tokens:
            return (
                f"Based on the sources, the answer to “{question.strip()[:160]}” is "
                f"supported [{number}]."
            )
    return "I do not have enough information in the provided sources to answer that question."


def _completion_text(payload: dict) -> str:
    """Deterministic, echo-derived answer. In JSON mode, return a valid object: a
    faithfulness score for an eval-judge prompt (so the generation-eval harness gets a
    deterministic, repeatable score), otherwise an echo answer object. A grounded-answer
    prompt (numbered Sources + Question) is answered grounding-aware (see above)."""
    messages = payload.get("messages", [])
    user = _last_user_content(messages)
    if (payload.get("response_format") or {}).get("type") == "json_object":
        joined = " ".join(str(m.get("content", "")) for m in messages).lower()
        if "faithfulness" in joined:
            return json.dumps({"faithfulness": 1.0, "reason": "deterministic stub score"})
        if "decision assistant" in joined:  # the recommendation prompt — emit a grounded rec
            return json.dumps(
                {
                    "recommendation": "Based on the sources, proceed with the leading option.",
                    "alternatives": [{"option": "Defer", "rationale": "await more evidence"}],
                    "justifications": [
                        {"claim": "the leading source supports this", "citation_markers": [1]}
                    ],
                    "caveats": ["limited to the retrieved corpus"],
                }
            )
        return json.dumps({"answer": user[:500]})
    if "Sources:" in user and "Question:" in user:
        return _grounded_completion(user)
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

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        model = str(payload.get("model", ""))
        REQUEST_LOG.append({"model": model})

        delay_ms = int(self.headers.get("X-Stub-Delay-Ms", "0") or "0")
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        if self.headers.get("X-Stub-Status"):
            code = int(self.headers["X-Stub-Status"])
            self._json(code, {"error": {"message": f"stub injected status {code}", "type": "stub"}})
            return
        if self.headers.get("X-Stub-Fail") == "1" or "fail" in model:
            self._json(503, {"error": {"message": "stub injected failure", "type": "stub"}})
            return
        if "reject" in model:
            self._json(400, {"error": {"message": "stub injected rejection", "type": "stub"}})
            return

        content = _completion_text(payload)
        prompt_tokens = sum(
            _word_count(str(m.get("content", ""))) for m in payload.get("messages", [])
        )
        completion_tokens = _word_count(content)
        if payload.get("stream"):
            self._stream(payload, content, prompt_tokens, completion_tokens)
            return
        # Non-stream structured-call latency model (the recommendation drill): sleep a fixed
        # per-call delay so a single blocking JSON completion has a representative cost. Default
        # 0 → the generation/recommendation evals are unaffected.
        json_ms = int(os.environ.get("STUB_JSON_MS", "0") or "0")
        if json_ms and (payload.get("response_format") or {}).get("type") == "json_object":
            time.sleep(json_ms / 1000.0)
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

    def _stream(
        self, payload: dict, content: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        """Emit the completion as an OpenAI-compatible SSE token stream (so the chat path —
        gateway ``complete_stream`` → litellm ``stream=True`` → this stub — works in dev and
        the latency drill measures real first-token / inter-token timing). The delay model is
        injected via env: ``STUB_STREAM_FIRST_MS`` (time to first token) and
        ``STUB_STREAM_TOKEN_MS`` (per subsequent token)."""
        first_ms = int(os.environ.get("STUB_STREAM_FIRST_MS", "0") or "0")
        token_ms = int(os.environ.get("STUB_STREAM_TOKEN_MS", "0") or "0")
        model = payload.get("model", "dev-stub")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        words = content.split(" ")
        for i, word in enumerate(words):
            delay = first_ms if i == 0 else token_ms
            if delay:
                time.sleep(delay / 1000.0)
            delta = word if i == len(words) - 1 else word + " "
            self._sse(
                {
                    "id": "chatcmpl-stub",
                    "object": "chat.completion.chunk",
                    "created": _CREATED,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                }
            )
        self._sse(
            {
                "id": "chatcmpl-stub",
                "object": "chat.completion.chunk",
                "created": _CREATED,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _sse(self, obj: dict) -> None:
        self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
        self.wfile.flush()

    def log_message(self, *_args: object) -> None:
        return  # keep the dev log quiet


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"llm-stub listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), _Handler).serve_forever()
