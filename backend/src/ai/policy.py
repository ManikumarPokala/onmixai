"""Read-only model-policy interface other domains consume (CLAUDE.md §3.3): the conversation
pipeline reads the org's PII-redaction toggle through this service, never ``ai``'s repository.
Mirrors identity's OrgPolicyService."""

from uuid import UUID

from src.ai.repository import ModelConfigRepository


class ModelPolicyService:
    """The org's model-policy reads. Absent config row → platform defaults (redaction ON)."""

    def __init__(self, configs: ModelConfigRepository) -> None:
        self._configs = configs

    async def pii_redaction_enabled(self, org_id: UUID) -> bool:
        """Whether the conversation pipeline should redact PII in grounding sources for this org.
        Defaults to True (safe) when the org has no model config. Time: O(1)."""
        config = await self._configs.get(org_id)
        return config.pii_redaction_enabled if config is not None else True
