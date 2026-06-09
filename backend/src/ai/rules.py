"""Pure budget/period rules — zero I/O (CLAUDE.md §3.1), branch-tested.

The usage period is bucketed monthly (the only BudgetPeriod today); a budget, when
set, compares against that bucket's running total.
"""

from datetime import UTC, datetime


def monthly_period_start(now: datetime) -> datetime:
    """First instant (UTC) of ``now``'s month — the period bucket key. ``now`` must be
    timezone-aware (naive datetimes are banned, CLAUDE.md §4). Time/Space: O(1)."""
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def crossed_soft_threshold(total: int, limit_tokens: int, soft_pct: int) -> bool:
    """Whether ``total`` has reached ``soft_pct`` % of the hard limit. Time/Space: O(1)."""
    return total >= (limit_tokens * soft_pct) // 100


# Providers the gateway can route to (matches the per-provider keys in Settings). A model ref is
# "provider/model" (litellm style, see ai/gateway.ModelRef).
KNOWN_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic", "azure"})


def _is_valid_model_ref(ref: str) -> bool:
    """True for a non-empty ``provider/model`` ref whose provider is known. Time: O(1)."""
    provider, _, model = ref.partition("/")
    return bool(model) and provider in KNOWN_PROVIDERS


def ensure_valid_model_config(default_model: str, fallback_chain: list[str]) -> None:
    """Validate an org model-config update (raises VALIDATION_ERROR): the default model and every
    fallback entry must be a known ``provider/model`` ref, and the fallback chain must be
    non-empty (no single point of failure). Time: O(chain)."""
    from src.shared.errors import ValidationFailedError

    if not _is_valid_model_ref(default_model):
        raise ValidationFailedError(
            "INVALID_MODEL_CONFIG", message=f"Unknown or malformed default model: {default_model!r}"
        )
    if not fallback_chain:
        raise ValidationFailedError(
            "INVALID_MODEL_CONFIG", message="The fallback chain must not be empty"
        )
    for ref in fallback_chain:
        if not _is_valid_model_ref(ref):
            raise ValidationFailedError(
                "INVALID_MODEL_CONFIG", message=f"Unknown or malformed fallback model: {ref!r}"
            )
