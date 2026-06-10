"""Admin-facing AI configuration schemas (request allow-lists + response DTOs, kept separate
from ORM models per CLAUDE.md §10). Sensitive fields (provider keys) are structurally absent —
the admin surface never reads or returns them."""

from pydantic import BaseModel, ConfigDict, Field

from src.ai.models import BudgetPeriod, ModelConfig, TokenBudget


class SetModelConfigRequest(BaseModel):
    """Replace the org's LLM routing config. Validated by ai.rules.ensure_valid_model_config
    (known providers, non-empty fallback chain) before it is written."""

    default_model: str = Field(min_length=1, max_length=255)
    fallback_chain: list[str] = Field(default_factory=list)
    temperature_default: float | None = Field(default=None, ge=0.0, le=2.0)
    pii_redaction_enabled: bool = True


class ModelConfigResponse(BaseModel):
    """The org's effective model config. When no row exists yet the admin surface returns the
    platform defaults from Settings (``ModelConfig`` row absent → Settings, see models.py)."""

    model_config = ConfigDict(from_attributes=True)

    default_model: str
    fallback_chain: list[str]
    temperature_default: float | None
    pii_redaction_enabled: bool

    @classmethod
    def from_model(cls, config: ModelConfig) -> "ModelConfigResponse":
        return cls(
            default_model=config.default_model,
            fallback_chain=list(config.fallback_chain),
            temperature_default=config.temperature_default,
            pii_redaction_enabled=config.pii_redaction_enabled,
        )

    @classmethod
    def from_defaults(
        cls, *, default_model: str, fallback_chain: list[str], temperature_default: float
    ) -> "ModelConfigResponse":
        return cls(
            default_model=default_model,
            fallback_chain=list(fallback_chain),
            temperature_default=temperature_default,
            pii_redaction_enabled=True,
        )


class SetBudgetRequest(BaseModel):
    """Set the org's monthly token budget. ``limit_tokens`` 0 freezes spend (the next metered
    call is blocked); ``soft_threshold_pct`` is the warn-at percentage."""

    limit_tokens: int = Field(ge=0)
    soft_threshold_pct: int = Field(default=80, ge=0, le=100)


class BudgetResponse(BaseModel):
    """The org's budget for a period."""

    period: BudgetPeriod
    limit_tokens: int
    soft_threshold_pct: int

    @classmethod
    def from_model(cls, budget: TokenBudget) -> "BudgetResponse":
        return cls(
            period=budget.period,
            limit_tokens=budget.limit_tokens,
            soft_threshold_pct=budget.soft_threshold_pct,
        )
