"""Governance: immutable audit_events store + retention_policies, with forced RLS.

audit_events is append-only and immutable at the DATABASE:
  * a BEFORE UPDATE trigger rejects every UPDATE (audit is never updated by anyone), and
  * UPDATE/DELETE are REVOKEd from the non-superuser runtime role (the username in DATABASE_URL),
    so the application can only INSERT + SELECT — retention deletion runs as the owner (Task 7).
The revoke is guarded: skipped when DATABASE_URL points at the owner (the CI migrations job) or
the role doesn't exist, so the migration stays role-agnostic and reversible everywhere.

Both tables are tenant-owned: ENABLE + FORCE ROW LEVEL SECURITY + the tenant_isolation policy in
this same migration (CLAUDE.md §4). retention_policies is one-row-per-org (unique org_id).

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from src.shared.config import get_runtime_db_role

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP = sa.DateTime(timezone=True)
_RLS_TABLES = ("audit_events", "retention_policies")


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_audit_events_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_events_actor_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_audit_events_org_created", "audit_events", ["org_id", "created_at"])
    op.create_index(
        "ix_audit_events_org_action_created", "audit_events", ["org_id", "action", "created_at"]
    )
    op.create_index(
        "ix_audit_events_org_resource", "audit_events", ["org_id", "resource_type", "resource_id"]
    )

    op.create_table(
        "retention_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("audit_retention_days", sa.Integer(), nullable=True),
        sa.Column("conversation_retention_days", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_retention_policies"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_retention_policies_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_retention_policies_updated_by_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", name="uq_retention_policies_org_id"),
    )

    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            "USING (org_id = current_setting('app.current_org_id', true)::uuid) "
            "WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)"
        )

    # Immutability (1/2): a trigger rejects every UPDATE — append-only, no one updates audit.
    op.execute(
        "CREATE OR REPLACE FUNCTION audit_events_block_update() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'audit_events is append-only; UPDATE is not permitted'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_block_update()"
    )

    # Immutability (2/2): revoke UPDATE/DELETE from the runtime role so the app can only
    # INSERT+SELECT. Guarded: skip when DATABASE_URL is the owner (CI migrations job) or the role
    # is absent — keeps the migration role-agnostic and reversible.
    role = get_runtime_db_role()
    op.execute(
        "DO $$ BEGIN "
        f"IF '{role}' <> current_user "
        f"AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
        f"EXECUTE format('REVOKE UPDATE, DELETE ON audit_events FROM %I', '{role}'); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_update()")
    for table in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("retention_policies")
    op.drop_index("ix_audit_events_org_resource", table_name="audit_events")
    op.drop_index("ix_audit_events_org_action_created", table_name="audit_events")
    op.drop_index("ix_audit_events_org_created", table_name="audit_events")
    op.drop_table("audit_events")
