"""Per-org PII-redaction toggle on model_configs (Phase 6 Task 9).

When enabled (the default), the conversation pipeline redacts PII in retrieved grounding sources
before they enter the prompt. The toggle governs only what the MODEL sees — it is decoupled from
observability: tracing/logging/audit record source IDs + counts, never raw content, so disabling
redaction never causes raw PII to leak into traces, logs, or the audit trail.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column(
            "pii_redaction_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "pii_redaction_enabled")
