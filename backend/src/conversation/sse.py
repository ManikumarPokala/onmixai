"""Server-Sent Events framing for the chat stream (Task 6 / ADR 0014).

Pure wire-format helpers: each event schema carries a Literal ``event`` discriminator,
serialized as one ``event:``/``data:`` SSE frame. A heartbeat comment keeps idle
connections (e.g. a slow first token) from being dropped by proxies. Kept separate from
the router so the framing is unit-testable without HTTP.
"""

from typing import Protocol


class _SSEEvent(Protocol):
    """Any chat stream event schema — carries a Literal ``event`` discriminator and
    serializes to JSON. Structural so the framing never couples to the concrete union."""

    @property
    def event(self) -> str: ...

    def model_dump_json(self) -> str: ...


# An SSE comment line (starts with ':') — ignored by clients, resets proxy idle timers.
HEARTBEAT_FRAME = ": keep-alive\n\n"


def format_event(event: _SSEEvent) -> str:
    """Render one event schema as an SSE frame: ``event: <name>\\ndata: <json>\\n\\n``.
    The event name is the schema's ``event`` discriminator. Time/Space: O(payload)."""
    return f"event: {event.event}\ndata: {event.model_dump_json()}\n\n"
