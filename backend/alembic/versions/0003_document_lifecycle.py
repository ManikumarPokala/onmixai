"""Document lifecycle: superseded flag + storage-deletion outbox with forced RLS.

Adds documents.superseded (versioning) and the storage_deletion_outbox table that
backs cascade-delete storage compensation. The outbox is tenant-owned, so it gets
ENABLE + FORCE ROW LEVEL SECURITY and the tenant_isolation policy in this same
migration (CLAUDE.md §4), exactly like the Task 2 tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("superseded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.create_table(
        "storage_deletion_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_storage_deletion_outbox"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_storage_deletion_outbox_org_id_organizations",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_storage_deletion_outbox_org_id", "storage_deletion_outbox", ["org_id"])

    op.execute("ALTER TABLE storage_deletion_outbox ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE storage_deletion_outbox FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON storage_deletion_outbox FOR ALL "
        "USING (org_id = current_setting('app.current_org_id', true)::uuid) "
        "WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON storage_deletion_outbox")
    op.drop_index("ix_storage_deletion_outbox_org_id", table_name="storage_deletion_outbox")
    op.drop_table("storage_deletion_outbox")
    op.drop_column("documents", "superseded")
