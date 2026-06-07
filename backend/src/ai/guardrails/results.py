"""Typed first-class results for guarded AI flows (patterns.md §5): a refusal/degraded
outcome is a value, never an exception. Built here, returned by Phase-4 pipelines."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class GroundedResult:
    """A grounded answer with the source chunks it was built from."""

    answer: str
    source_chunk_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class Refusal:
    """A typed refusal/degraded outcome (low confidence, blocked, or ungrounded)."""

    reason: str


GuardedResult = GroundedResult | Refusal
