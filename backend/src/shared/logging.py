"""Structured logging configuration (CLAUDE.md §6).

structlog with a JSON renderer, configured once at startup from the configured
log level. Request-scoped context (request_id, and org_id/user_id once
authenticated) is carried via structlog contextvars so every log line within a
request is automatically annotated. ``print()`` is banned (ruff T201); all
output goes through structlog to stdout.
"""

import logging
import sys

import structlog

# structlog's processor pipeline returns loosely-typed event dicts; the third-party
# callables are not parameterised, so annotate the public surface only.


def configure_logging(log_level: str) -> None:
    """Configure structlog for JSON output at ``log_level``.

    Idempotent: safe to call more than once (e.g. app startup and tests).
    """
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
