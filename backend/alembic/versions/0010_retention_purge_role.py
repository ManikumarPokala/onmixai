"""Retention purge: make audit actor nullable (system-initiated events) and grant a dedicated,
least-privilege purger role the authority to delete expired data (ADR 0019).

Two changes:
  1. audit_events.actor_user_id becomes NULLABLE. The retention purge (Task 7) writes audit rows
     with no human actor — the first system-initiated audit events. Human-initiated events still
     always carry an actor (the emit seam requires one).
  2. The purger role (the username in PURGE_DATABASE_URL) is GRANTed exactly what the purge job
     needs: SELECT/INSERT/DELETE on audit_events (find candidates, write the purge record, delete
     expired rows) and SELECT/DELETE on the conversation tables (children cascade), plus SELECT on
     retention_policies + organizations. The runtime role's audit DELETE stays REVOKEd (0009) —
     deletion authority lives ONLY in this separate role, reached via its own connection. Guarded:
     skipped when no purge role is configured, the role is the current user (owner), or it does not
     exist — so the migration stays role-agnostic, CI-safe, and reversible.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-09
"""

from collections.abc import Sequence

from alembic import op
from src.shared.config import get_purge_db_role

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The purger role may DELETE from these (chat_sessions children cascade, but Postgres still checks
# DELETE privilege per table, so grant all four explicitly).
_CONVERSATION_TABLES = ("chat_sessions", "chat_messages", "message_feedback", "session_summaries")


def _grant_purge_role(grant: bool) -> None:
    """GRANT (or REVOKE) the purger role's deletion privileges, guarded so it is a no-op when no
    purge role is configured / it is the owner / it is absent."""
    role = get_purge_db_role()
    if role is None:
        return
    verb = "GRANT" if grant else "REVOKE"
    direction = "TO" if grant else "FROM"
    conversation = ", ".join(_CONVERSATION_TABLES)
    statements = [
        f"{verb} SELECT, INSERT, DELETE ON audit_events {direction} %I",
        f"{verb} SELECT, DELETE ON {conversation} {direction} %I",
        f"{verb} SELECT ON retention_policies, organizations {direction} %I",
    ]
    body = " ".join(f"EXECUTE format('{stmt}', '{role}');" for stmt in statements)
    op.execute(
        "DO $$ BEGIN "
        f"IF '{role}' <> current_user "
        f"AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN {body} "
        "END IF; END $$"
    )


def upgrade() -> None:
    op.execute("ALTER TABLE audit_events ALTER COLUMN actor_user_id DROP NOT NULL")
    _grant_purge_role(grant=True)


def downgrade() -> None:
    _grant_purge_role(grant=False)
    # Re-NOT-NULL only succeeds if no system (null-actor) rows exist; the two-step deploy pattern
    # would null-backfill first, but a fresh down→up in CI has none.
    op.execute("ALTER TABLE audit_events ALTER COLUMN actor_user_id SET NOT NULL")
