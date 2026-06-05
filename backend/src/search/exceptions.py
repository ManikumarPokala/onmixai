"""Typed search-domain errors (CLAUDE.md §5). The global handler renders them in
the standard envelope."""

from src.shared.errors import AppError


class InvalidSearchFilterError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("INVALID_SEARCH_FILTER", 422, "Invalid search filter", detail)
