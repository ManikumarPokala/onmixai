"""Azure OpenAI LIVE smoke test — RUN BY YOU against your real Azure deployment.

OnMixAI is Azure-READY: the gateway routes ``azure/<logical>`` model refs to your deployment, but
the app never assumes a live Azure endpoint. This script is how you VERIFY that — it sends one real
completion through the same gateway the app uses and prints the response, the trace
(model_used / tokens / latency / trace_id), and the usage that would be metered. It is NOT run in
CI (no live credentials there); it is a manual, user-executed verification tool.

Usage:
    AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com \
    AZURE_OPENAI_API_VERSION=2024-06-01 \
    AZURE_OPENAI_API_KEY=<key> \
    AZURE_DEPLOYMENT_MAP='{"gpt-4o-mini": "<your-deployment>"}' \
    python scripts/azure_smoke.py --model azure/gpt-4o-mini --prompt "Say hello from Azure."

Run ``python scripts/azure_smoke.py --help`` to see the four required env vars. With missing or
fake config it exits with a clean, typed message naming what is missing — never a stack trace.
"""

import argparse
import asyncio
import json
import os
import sys
from time import monotonic
from uuid import uuid4

# The four Azure env vars this tool requires (documented in --help and the runbook).
_AZURE_ENV = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_API_KEY",
    "AZURE_DEPLOYMENT_MAP",
)


def _build_settings(model: str):  # type: ignore[no-untyped-def]
    """A minimal Settings carrying only the Azure + LLM config (placeholders for the unrelated
    required fields, since this tool exercises only the gateway). Constructing it runs the same
    fail-fast Azure validation the app uses — a missing piece raises here, named."""
    from src.shared.config import Settings

    return Settings(
        _env_file=None,
        env="dev",
        database_url="postgresql+asyncpg://smoke:smoke@localhost:5432/smoke",
        jwt_secret="x" * 40,
        storage_endpoint="http://localhost:9000",
        storage_access_key="a",
        storage_secret_key="s",
        storage_bucket="b",
        redis_url="redis://localhost:6379/0",
        embedding_dimension=1536,
        llm_default_model=model,
        llm_timeout_seconds=int(os.environ.get("AZURE_SMOKE_TIMEOUT", "30")),
        azure_openai_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        azure_openai_api_version=os.environ.get("AZURE_OPENAI_API_VERSION"),
        azure_openai_api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
        azure_deployment_map=json.loads(os.environ.get("AZURE_DEPLOYMENT_MAP", "{}")),
    )


async def _run(model: str, prompt_text: str) -> int:
    from src.ai.adapters.circuit_breaker import CircuitBreaker
    from src.ai.adapters.litellm_gateway import LiteLLMGateway
    from src.ai.gateway import ChatMessage, GatewayContext, RenderedPrompt
    from src.ai.models import UsageFeature
    from src.ai.tracing import CompletionTrace, TracingGateway, TracingPort

    try:
        settings = _build_settings(model)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"✗ Azure config invalid: {exc}", file=sys.stderr)
        print(f"  set: {', '.join(_AZURE_ENV)}", file=sys.stderr)
        return 1

    class _PrintTracer(TracingPort):
        trace: CompletionTrace | None = None

        def span(self, name: str, **attrs: object):  # type: ignore[no-untyped-def]
            from contextlib import nullcontext

            return nullcontext()

        def record_completion(self, trace: CompletionTrace) -> None:
            self.trace = trace

    class _NoConfig:
        async def get(self, org_id: object) -> None:
            return None

    tracer = _PrintTracer()
    gateway = TracingGateway(
        inner=LiteLLMGateway(
            settings=settings,
            configs=_NoConfig(),
            breaker=CircuitBreaker(failure_threshold=5, reset_seconds=60),
        ),
        tracer=tracer,
    )
    prompt = RenderedPrompt(
        template_name="azure_smoke",
        template_version="1.0.0",
        messages=(ChatMessage("user", prompt_text),),
        variables_hash="smoke",
    )
    ctx = GatewayContext(
        org_id=uuid4(), user_id=uuid4(), feature=UsageFeature.CHAT, request_id="azure-smoke"
    )

    print(f"→ sending one completion via {model} (Azure) …")
    started = monotonic()
    try:
        completion = await gateway.complete(prompt=prompt, ctx=ctx)
    except Exception as exc:  # noqa: BLE001 — a smoke tool reports any failure cleanly, no traceback
        print(f"✗ Azure call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    elapsed_ms = (monotonic() - started) * 1000

    print("\n✓ Azure OpenAI responded through the gateway.\n")
    print(f"  response : {completion.text[:500]}")
    print(f"  model    : {completion.model_used}")
    print(
        f"  tokens   : prompt={completion.prompt_tokens} completion={completion.completion_tokens}"
        f" total={completion.prompt_tokens + completion.completion_tokens}"
    )
    print(f"  latency  : {elapsed_ms:.0f} ms")
    print(f"  trace_id : {completion.trace_id}")
    if tracer.trace is not None:
        print(f"  traced   : {tracer.trace.as_attributes()}")
    print("\nVerified: this OnMixAI gateway ran against your live Azure OpenAI deployment.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Send one live completion through OnMixAI's gateway to a real Azure OpenAI deployment. "
            f"Requires these env vars: {', '.join(_AZURE_ENV)}. Not run in CI."
        )
    )
    parser.add_argument("--model", default="azure/gpt-4o-mini", help="azure/<logical> model ref")
    parser.add_argument("--prompt", default="Say hello from Azure OpenAI in one sentence.")
    args = parser.parse_args()
    if not args.model.startswith("azure/"):
        print("✗ --model must be an azure/<logical> ref (e.g. azure/gpt-4o-mini)", file=sys.stderr)
        return 2
    return asyncio.run(_run(args.model, args.prompt))


if __name__ == "__main__":
    raise SystemExit(main())
