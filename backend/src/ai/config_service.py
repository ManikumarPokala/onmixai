"""Owner/admin AI configuration: the org's LLM routing config and token budget. Changes here
take effect on the very next metered call — the metering gateway reads both rows fresh per call
(no cache), so a lowered budget blocks immediately without a restart (CLAUDE.md §6 enforcement).

Cross-domain note (§3.3): this service lives in ``ai`` because ``ai`` owns the model_configs /
token_budgets tables; the admin router calls it through this interface, never the repositories.
It takes the actor as primitives (org_id, actor_id), not identity's AuthContext — ``ai`` sits
below ``identity`` in the layered contract and must not import upward.
"""

from uuid import UUID

from src.ai.config_schemas import (
    BudgetResponse,
    ModelConfigResponse,
    SetBudgetRequest,
    SetModelConfigRequest,
)
from src.ai.models import BudgetPeriod
from src.ai.repository import ModelConfigRepository, TokenBudgetRepository
from src.ai.rules import ensure_valid_model_config
from src.shared.audit import AuditEmitter
from src.shared.config import Settings


class AIConfigService:
    """Read/update the org's model config and token budget; every mutation is audited."""

    def __init__(
        self,
        *,
        model_configs: ModelConfigRepository,
        budgets: TokenBudgetRepository,
        audit: AuditEmitter,
        settings: Settings,
    ) -> None:
        self._model_configs = model_configs
        self._budgets = budgets
        self._audit = audit
        self._settings = settings

    async def get_model_config(self, org_id: UUID) -> ModelConfigResponse:
        """The org's effective model config — its row, or platform defaults when none is set.
        Tenant-scoped by RLS on the caller's session. Time: O(1)."""
        config = await self._model_configs.get(org_id)
        if config is None:
            return ModelConfigResponse.from_defaults(
                default_model=self._settings.llm_default_model,
                fallback_chain=self._settings.llm_fallback_chain,
                temperature_default=self._settings.llm_temperature_default,
            )
        return ModelConfigResponse.from_model(config)

    async def set_model_config(
        self, *, org_id: UUID, actor_id: UUID, body: SetModelConfigRequest
    ) -> ModelConfigResponse:
        """Replace the org's model config (audited). Raises INVALID_MODEL_CONFIG (422) for an
        unknown/malformed model ref or an empty fallback chain. Time: O(chain)."""
        ensure_valid_model_config(body.default_model, body.fallback_chain)
        config = await self._model_configs.upsert(
            org_id,
            default_model=body.default_model,
            fallback_chain=body.fallback_chain,
            temperature_default=body.temperature_default,
            pii_redaction_enabled=body.pii_redaction_enabled,
            updated_by=actor_id,
        )
        self._audit.emit(
            org_id=org_id,
            actor_id=actor_id,
            action="ai.model_config_changed",
            resource_type="model_config",
            resource_id=config.id,
            default_model=config.default_model,
            fallback_chain=list(config.fallback_chain),
            pii_redaction_enabled=config.pii_redaction_enabled,
        )
        return ModelConfigResponse.from_model(config)

    async def get_budget(self, org_id: UUID) -> BudgetResponse | None:
        """The org's monthly budget, or None when none is set (no cap). Time: O(1)."""
        budget = await self._budgets.get(org_id, BudgetPeriod.MONTHLY)
        return BudgetResponse.from_model(budget) if budget is not None else None

    async def set_budget(
        self, *, org_id: UUID, actor_id: UUID, body: SetBudgetRequest
    ) -> BudgetResponse:
        """Set the org's monthly token budget (audited). Effective on the next metered call —
        the gateway re-reads the budget every call. Time: O(1)."""
        budget = await self._budgets.upsert(
            org_id,
            BudgetPeriod.MONTHLY,
            limit_tokens=body.limit_tokens,
            soft_threshold_pct=body.soft_threshold_pct,
        )
        self._audit.emit(
            org_id=org_id,
            actor_id=actor_id,
            action="ai.budget_changed",
            resource_type="token_budget",
            resource_id=budget.id,
            limit_tokens=budget.limit_tokens,
            soft_threshold_pct=budget.soft_threshold_pct,
        )
        return BudgetResponse.from_model(budget)
