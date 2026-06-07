"""Generation golden-set v0 — the Phase-3 generation regression gate. Runs each golden
case through the REAL pipeline (prompt registry → inbound guardrail neutralization →
gateway) against the deterministic stub, then judges the answer with
eval_judge_faithfulness@1.0.0 through the same gateway. With the stub the faithfulness
score is a fixed, repeatable value, so this gate proves the harness + pipeline are
correct and deterministic — NOT generation quality. The rubric and threshold
(faithfulness ≥ 0.9) are wired now and become meaningful only against a real model
(the honesty caveat in ADR 0013, mirroring the retrieval golden set).
"""

import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import litellm
import pytest
from pydantic import BaseModel

from src.ai.adapters.circuit_breaker import CircuitBreaker
from src.ai.adapters.litellm_gateway import LiteLLMGateway
from src.ai.gateway import GatewayContext, LLMGateway
from src.ai.guardrails import InjectionFilter
from src.ai.models import UsageFeature
from src.ai.prompt_registry import get_prompt_registry
from tests.ai.conftest import NoModelConfig, llm_settings

pytestmark = pytest.mark.generation

litellm.disable_aiohttp_transport = True

_GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "generation_v0.jsonl"
_STUB = Path(__file__).resolve().parents[3] / "infra" / "dev" / "llm_stub.py"
_THRESHOLD = 0.9


class _Faithfulness(BaseModel):
    faithfulness: float
    reason: str


def _load_golden() -> list[dict[str, str]]:
    return [json.loads(line) for line in _GOLDEN.read_text().splitlines() if line.strip()]


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("llm_stub_gen", _STUB)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ctx(feature: UsageFeature) -> GatewayContext:
    return GatewayContext(uuid4(), uuid4(), feature, "eval")


async def _score_case(gateway: LLMGateway, case: dict[str, str]) -> float:
    registry = get_prompt_registry()
    neutralized = InjectionFilter().neutralize(case["context"])
    answer_prompt = registry.render(
        "grounded_answer",
        summary="",
        history="",
        sources=f"[1] {neutralized}",
        question=case["question"],
    )
    answer = (await gateway.complete(prompt=answer_prompt, ctx=_ctx(UsageFeature.CHAT))).text
    judge_prompt = registry.render(
        "eval_judge_faithfulness",
        question=case["question"],
        context=case["context"],
        answer=answer,
    )
    judged = await gateway.complete(
        prompt=judge_prompt, ctx=_ctx(UsageFeature.EVAL), response_schema=_Faithfulness
    )
    return _Faithfulness.model_validate_json(judged.text).faithfulness


async def _run_eval(base_url: str) -> list[float]:
    gateway = LiteLLMGateway(
        settings=llm_settings(base_url), configs=NoModelConfig(), breaker=CircuitBreaker(5, 60)
    )
    return [await _score_case(gateway, case) for case in _load_golden()]


async def test_generation_eval_is_deterministic_and_above_threshold() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _load_stub()._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        golden = _load_golden()
        assert len(golden) >= 20
        run1 = await _run_eval(base_url)
        run2 = await _run_eval(base_url)
        mean = sum(run1) / len(run1)
        print(
            f"\n[generation v0] faithfulness mean={mean:.3f} over {len(run1)} cases "
            f"(deterministic stub — harness correctness, not quality)"
        )
        assert run1 == run2  # two runs → identical scores (determinism)
        assert min(run1) >= _THRESHOLD  # gate
    finally:
        await litellm.close_litellm_async_clients()
        server.shutdown()
        server.server_close()
