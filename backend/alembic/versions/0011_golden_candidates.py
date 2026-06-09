"""Feedback-to-golden curation: the golden_candidates table (Phase 6 Task 8).

A reviewed Q&A pair derived from positive message feedback, proposed for the golden eval set.
Question/answer are stored already PII-redacted; only redaction counts are kept. A human curator
gates pending → approved/rejected — approval never writes the eval golden files (no auto-merge).

Tenant-owned: ENABLE + FORCE ROW LEVEL SECURITY + the tenant_isolation policy in this migration
(CLAUDE.md §4). Role-agnostic — runtime access comes from the Sprint-1 default privileges.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP = sa.DateTime(timezone=True)

# feedback_rating already exists (0007); the candidate-status enum is new.
golden_status = postgresql.ENUM(
    "pending", "approved", "rejected", name="golden_candidate_status", create_type=False
)
feedback_rating = postgresql.ENUM("up", "down", name="feedback_rating", create_type=False)


def upgrade() -> None:
    golden_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "golden_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("rating", feedback_rating, nullable=False),
        sa.Column(
            "redaction_counts",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", golden_status, server_default="pending", nullable=False),
        sa.Column("curated_by", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_at", _TIMESTAMP, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_golden_candidates"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_golden_candidates_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["chat_messages.id"],
            name="fk_golden_candidates_message",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["curated_by"], ["users.id"], name="fk_golden_candidates_curator", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_golden_candidates_decider", ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_golden_candidates_org_status", "golden_candidates", ["org_id", "status"]
    )
    op.create_index(
        "ix_golden_candidates_org_created", "golden_candidates", ["org_id", "created_at"]
    )

    op.execute("ALTER TABLE golden_candidates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE golden_candidates FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON golden_candidates FOR ALL "
        "USING (org_id = current_setting('app.current_org_id', true)::uuid) "
        "WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON golden_candidates")
    op.drop_index("ix_golden_candidates_org_created", table_name="golden_candidates")
    op.drop_index("ix_golden_candidates_org_status", table_name="golden_candidates")
    op.drop_table("golden_candidates")
    golden_status.drop(op.get_bind(), checkfirst=True)
