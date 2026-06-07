"""Guardrail chains (patterns.md §5): composed, individually-testable steps assembled
per direction and per feature. Inbound runs over retrieved content + user input BEFORE
prompt assembly (redact PII, then structurally neutralize injection). Outbound validates
the model's structured output — a response that ignored the schema (e.g. obeyed an
injection and replied in prose) is caught and becomes a typed refusal. The applied step
names are logged into the trace (``guardrails_applied``)."""

from dataclasses import dataclass

import structlog
from pydantic import BaseModel, ValidationError

from src.ai.gateway import GuardrailViolationError
from src.ai.guardrails.injection import InjectionFilter
from src.ai.guardrails.pii import PIIRedactor
from src.ai.models import UsageFeature

_logger = structlog.get_logger("ai.guardrails")

# Declarative inbound composition per feature. Eval judges raw content, so it neutralizes
# injection but does not redact PII (the judge must see the real answer).
_INBOUND_BY_FEATURE: dict[UsageFeature, tuple[str, ...]] = {
    UsageFeature.CHAT: ("pii_redactor", "injection_filter"),
    UsageFeature.RECOMMENDATION: ("pii_redactor", "injection_filter"),
    UsageFeature.REPORT: ("pii_redactor", "injection_filter"),
    UsageFeature.EVAL: ("injection_filter",),
}


@dataclass(frozen=True, slots=True)
class InboundReport:
    """Neutralized chunks + redaction counts (values absent) + the steps applied (for
    the trace)."""

    neutralized: tuple[str, ...]
    redaction_counts: dict[str, int]
    guardrails_applied: tuple[str, ...]


class InboundGuardrails:
    def __init__(self, injection: InjectionFilter, pii: PIIRedactor) -> None:
        self._injection = injection
        self._pii = pii

    def apply(self, chunks: list[str], *, feature: UsageFeature, redact_pii: bool) -> InboundReport:
        """Run the feature's inbound chain over retrieved ``chunks``. Time: O(total len)."""
        steps = _INBOUND_BY_FEATURE.get(feature, ("injection_filter",))
        processed = list(chunks)
        counts: dict[str, int] = {}
        if "pii_redactor" in steps:
            outcomes = [self._pii.redact(chunk, enabled=redact_pii) for chunk in processed]
            processed = [outcome.text for outcome in outcomes]
            for outcome in outcomes:
                for kind, n in outcome.counts.items():
                    counts[kind] = counts.get(kind, 0) + n
        if "injection_filter" in steps:
            processed = [self._injection.neutralize(chunk) for chunk in processed]
        # Trace evidence: which steps ran + how many of each PII type were redacted.
        # Counts only — the redacted values never appear in a log or trace.
        _logger.info(
            "ai.guardrails.inbound",
            feature=feature.value,
            guardrails_applied=list(steps),
            redaction_counts=counts,
        )
        return InboundReport(tuple(processed), counts, steps)


class OutboundGuardrails:
    def validate_structured(self, text: str, schema: type[BaseModel]) -> BaseModel:
        """Outbound schema gate. A response that ignored the required schema is caught
        here and raised as a guardrail violation (the pipeline returns a Refusal).
        Time: O(len)."""
        try:
            return schema.model_validate_json(text)
        except (ValidationError, ValueError) as exc:
            raise GuardrailViolationError(
                "OUTPUT_SCHEMA_VIOLATION", detail="model output did not satisfy the required schema"
            ) from exc
