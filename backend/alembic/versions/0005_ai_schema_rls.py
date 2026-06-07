"""AI schema (model_configs, token_budgets, usage events + materialized periods) with forced RLS.

All four tables are tenant-owned and get RLS + FORCE ROW LEVEL SECURITY and the
tenant_isolation policy in this same migration (CLAUDE.md §4). Role-agnostic: the
runtime role's access comes from the Sprint 1 default privileges. token_usage_events
is append-only by convention (no repository UPDATEs it); token_usage_periods is the
O(1) running-total row maintained transactionally with each event.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "model_configs",
    "token_budgets",
    "token_usage_events",
    "token_usage_periods",
)
_TIMESTAMP = sa.DateTime(timezone=True)

budget_period = postgresql.ENUM("monthly", name="budget_period", create_type=False)
usage_feature = postgresql.ENUM(
    "chat", "recommendation", "report", "eval", "embedding", name="usage_feature", create_type=False
)


def upgrade() -> None:
    budget_period.create(op.get_bind(), checkfirst=True)
    usage_feature.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "model_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("default_model", sa.String(length=255), nullable=False),
        sa.Column(
            "fallback_chain",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("temperature_default", sa.Float(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_configs"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_model_configs_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_model_configs_updated_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("org_id", name="uq_model_configs_org_id"),
    )
    op.create_index("ix_model_configs_org_id", "model_configs", ["org_id"])

    op.create_table(
        "token_budgets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("period", budget_period, nullable=False),
        sa.Column("limit_tokens", sa.BigInteger(), nullable=False),
        sa.Column("soft_threshold_pct", sa.Integer(), server_default=sa.text("80"), nullable=False),
        sa.Column("updated_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_token_budgets"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_token_budgets_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", "period", name="uq_token_budgets_org_id_period"),
    )
    op.create_index("ix_token_budgets_org_id", "token_budgets", ["org_id"])

    op.create_table(
        "token_usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("feature", usage_feature, nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_token_usage_events"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_token_usage_events_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_token_usage_events_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_token_usage_events_org_id_created_at", "token_usage_events", ["org_id", "created_at"]
    )
    op.create_index(
        "ix_token_usage_events_org_id_feature_created_at",
        "token_usage_events",
        ["org_id", "feature", "created_at"],
    )

    op.create_table(
        "token_usage_periods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", _TIMESTAMP, nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_token_usage_periods"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_token_usage_periods_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "org_id", "period_start", name="uq_token_usage_periods_org_id_period_start"
        ),
    )
    op.create_index("ix_token_usage_periods_org_id", "token_usage_periods", ["org_id"])

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            "USING (org_id = current_setting('app.current_org_id', true)::uuid) "
            "WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    op.drop_index("ix_token_usage_periods_org_id", table_name="token_usage_periods")
    op.drop_table("token_usage_periods")
    op.drop_index(
        "ix_token_usage_events_org_id_feature_created_at", table_name="token_usage_events"
    )
    op.drop_index("ix_token_usage_events_org_id_created_at", table_name="token_usage_events")
    op.drop_table("token_usage_events")
    op.drop_index("ix_token_budgets_org_id", table_name="token_budgets")
    op.drop_table("token_budgets")
    op.drop_index("ix_model_configs_org_id", table_name="model_configs")
    op.drop_table("model_configs")

    usage_feature.drop(op.get_bind(), checkfirst=True)
    budget_period.drop(op.get_bind(), checkfirst=True)
