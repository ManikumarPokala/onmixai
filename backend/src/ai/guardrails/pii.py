"""Configurable PII redaction (CLAUDE.md §4): deterministic patterns replace matches
with type placeholders. Only redaction COUNTS are returned/traced — the matched values
never appear in counts, logs, or traces. Per-org opt-in via a model_configs flag."""

import re
from dataclasses import dataclass

NAME = "pii_redactor"

# Applied in this order (email/gov-id before phone so a 9-digit gov id isn't mis-eaten).
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("gov_id", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),  # US SSN shape
    ("phone", re.compile(r"\b(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")),
)


@dataclass(frozen=True, slots=True)
class RedactionOutcome:
    text: str
    counts: dict[str, int]  # by type; the redacted VALUES are never included


class PIIRedactor:
    name = NAME

    def redact(self, text: str, *, enabled: bool) -> RedactionOutcome:
        """Replace PII with ``[REDACTED_<TYPE>]`` and count it. When ``enabled`` is False
        (org opted out), the text passes through untouched. Time: O(len · patterns)."""
        if not enabled:
            return RedactionOutcome(text, {})
        counts: dict[str, int] = {}
        result = text
        for kind, pattern in _PATTERNS:
            result, n = pattern.subn(f"[REDACTED_{kind.upper()}]", result)
            if n:
                counts[kind] = n
        return RedactionOutcome(result, counts)
