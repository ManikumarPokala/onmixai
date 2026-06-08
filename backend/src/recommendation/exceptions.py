"""Typed recommendation-domain errors. A recommendation a user does not own is a 404
(NotFound), not a 403 — no existence oracle (consistent with the rest of the codebase).

INSUFFICIENT_EVIDENCE is NOT here: a decline is a valid, persisted outcome (status=declined),
not an error — it is a ``DeclineReason`` (rules.py), surfaced as a 200 with the reason.
"""

from src.shared.errors import AppError


class RecommendationNotFoundError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("RECOMMENDATION_NOT_FOUND", 404, "Recommendation not found", detail)
