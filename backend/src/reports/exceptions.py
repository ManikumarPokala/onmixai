"""Typed reports-domain errors. A report a user does not own is a 404, not a 403 — no
existence oracle. INSUFFICIENT_EVIDENCE / NO_GROUNDED_SECTIONS are NOT errors: they are
typed terminal graph outcomes that fail the report with a reason (a content decline)."""

from src.shared.errors import AppError


class ReportNotFoundError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("REPORT_NOT_FOUND", 404, "Report not found", detail)


class ExportNotFoundError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("EXPORT_NOT_FOUND", 404, "Report export not found", detail)
