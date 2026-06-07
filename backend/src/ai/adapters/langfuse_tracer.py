"""langfuse tracing exporter — the ONLY module importing the langfuse SDK (import-linter,
CLAUDE.md §3.6). Satisfies ``TracingPort`` by emitting langfuse events; the client is
injected (a real ``langfuse.Langfuse`` in prod, a fake in tests), so the same path runs.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Any

import langfuse

from src.ai.tracing import CompletionTrace
from src.shared.config import Settings


def build_langfuse_client(settings: Settings) -> Any:
    """Construct the production langfuse client from settings."""
    return langfuse.Langfuse(
        public_key=(
            settings.langfuse_public_key.get_secret_value()
            if settings.langfuse_public_key
            else None
        ),
        secret_key=(
            settings.langfuse_secret_key.get_secret_value()
            if settings.langfuse_secret_key
            else None
        ),
        host=settings.langfuse_host,
    )


class LangfuseTracer:
    def __init__(self, client: Any) -> None:
        self._client = client

    @contextmanager
    def span(self, name: str, **attrs: object) -> Iterator[None]:
        started = monotonic()
        try:
            yield
        finally:
            self._client.create_event(
                name=name,
                metadata={**attrs, "latency_ms": round((monotonic() - started) * 1000, 3)},
            )

    def record_completion(self, trace: CompletionTrace) -> None:
        name = "ai.completion.error" if trace.error else "ai.completion"
        self._client.create_event(name=name, metadata=trace.as_attributes())
