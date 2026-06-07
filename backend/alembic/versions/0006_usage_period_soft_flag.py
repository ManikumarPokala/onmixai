"""Add token_usage_periods.soft_threshold_crossed — the once-per-period dedup flag.

Set by compare-and-set when a period first crosses its soft budget threshold, so the
warn log + audit event fire exactly once per period (Task 5). Tenant table already
under forced RLS from 0005; adding a column does not change the policy.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "token_usage_periods",
        sa.Column(
            "soft_threshold_crossed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("token_usage_periods", "soft_threshold_crossed")
