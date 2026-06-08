"""Recommendation + reports schema (recommendations, reports, report_exports) with forced RLS.

All three tables are tenant-owned and get RLS + FORCE ROW LEVEL SECURITY and the
tenant_isolation policy in this same migration (CLAUDE.md §4). Per-user ownership
(created_by) is enforced in the application layer (Task 4/6/10). Role-agnostic: the runtime
role's access comes from the Sprint 1 default privileges.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = ("recommendations", "reports", "report_exports")
_TIMESTAMP = sa.DateTime(timezone=True)

recommendation_status = postgresql.ENUM(
    "completed", "declined", name="recommendation_status", create_type=False
)
confidence_band = postgresql.ENUM(
    "high", "medium", "low", name="confidence_band", create_type=False
)
report_type = postgresql.ENUM(
    "executive_summary", "technical", "recommendation", name="report_type", create_type=False
)
report_status = postgresql.ENUM(
    "queued", "generating", "ready", "failed", name="report_status", create_type=False
)
export_format = postgresql.ENUM("pdf", name="export_format", create_type=False)
export_status = postgresql.ENUM(
    "queued", "generating", "ready", "failed", name="export_status", create_type=False
)

_ENUMS = (
    recommendation_status,
    confidence_band,
    report_type,
    report_status,
    export_format,
    export_status,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column(
            "collection_scope",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", recommendation_status, nullable=False),
        sa.Column("confidence_band", confidence_band, nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("decline_reason", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_recommendations"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_recommendations_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_recommendations_created_by_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_recommendations_org_creator_created",
        "recommendations",
        ["org_id", "created_by", "created_at"],
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("report_type", report_type, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_query", sa.Text(), nullable=False),
        sa.Column(
            "collection_scope",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", report_status, nullable=False),
        sa.Column("failure_reason", sa.String(length=128), nullable=True),
        sa.Column("content", postgresql.JSONB(), nullable=True),
        sa.Column("generation_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("claimed_at", _TIMESTAMP, nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reports"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_reports_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_reports_created_by_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_reports_org_creator_created", "reports", ["org_id", "created_by", "created_at"]
    )
    op.create_index("ix_reports_status_claimed", "reports", ["status", "claimed_at"])

    op.create_table(
        "report_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("format", export_format, nullable=False),
        sa.Column("status", export_status, nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("failure_reason", sa.String(length=128), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("claimed_at", _TIMESTAMP, nullable=True),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_report_exports"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_report_exports_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name="fk_report_exports_report_id_reports",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("report_id", "format", name="uq_report_exports_report_id_format"),
    )
    op.create_index("ix_report_exports_status_claimed", "report_exports", ["status", "claimed_at"])

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

    op.drop_index("ix_report_exports_status_claimed", table_name="report_exports")
    op.drop_table("report_exports")
    op.drop_index("ix_reports_status_claimed", table_name="reports")
    op.drop_index("ix_reports_org_creator_created", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_recommendations_org_creator_created", table_name="recommendations")
    op.drop_table("recommendations")

    for enum in reversed(_ENUMS):
        enum.drop(op.get_bind(), checkfirst=True)
