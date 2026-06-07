"""The dev LLM stub: deterministic OpenAI-compatible completions + header-driven
fault injection (the resilience-drill substrate for Task 4). Loaded from its file
(it lives outside ``src``) and exercised over a real loopback HTTP round-trip."""

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

_STUB_PATH = Path(__file__).resolve().parents[3] / "infra" / "dev" / "llm_stub.py"


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("llm_stub", _STUB_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_stub = _load_stub()
_PAYLOAD = {"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "hello there"}]}


@pytest.fixture
def base_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _stub._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _post(
    url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_health_ok(base_url: str) -> None:
    with urllib.request.urlopen(f"{base_url}/health") as resp:
        assert resp.status == 200
        assert json.loads(resp.read())["status"] == "ok"


def test_completion_is_deterministic_with_token_usage(base_url: str) -> None:
    url = f"{base_url}/v1/chat/completions"
    code1, body1 = _post(url, _PAYLOAD)
    code2, body2 = _post(url, _PAYLOAD)
    assert code1 == code2 == 200
    assert body1 == body2  # byte-identical across runs → repeatable eval scores
    usage = body1["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    assert "hello there" in body1["choices"][0]["message"]["content"]


def test_json_mode_returns_valid_json_object(base_url: str) -> None:
    code, body = _post(
        f"{base_url}/v1/chat/completions",
        {**_PAYLOAD, "response_format": {"type": "json_object"}},
    )
    assert code == 200
    parsed = json.loads(body["choices"][0]["message"]["content"])
    assert "answer" in parsed


def test_fail_header_returns_retryable_503(base_url: str) -> None:
    code, _ = _post(f"{base_url}/v1/chat/completions", _PAYLOAD, {"X-Stub-Fail": "1"})
    assert code == 503


def test_status_header_returns_exact_status(base_url: str) -> None:
    code, _ = _post(f"{base_url}/v1/chat/completions", _PAYLOAD, {"X-Stub-Status": "400"})
    assert code == 400  # non-retryable 4xx for the no-retry-on-rejection drill


def test_delay_header_slows_response(base_url: str) -> None:
    started = time.monotonic()
    code, _ = _post(f"{base_url}/v1/chat/completions", _PAYLOAD, {"X-Stub-Delay-Ms": "120"})
    elapsed_ms = (time.monotonic() - started) * 1000
    assert code == 200 and elapsed_ms >= 120
