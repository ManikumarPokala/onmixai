"""Admin RBAC foundation (CLAUDE.md §4). Every admin endpoint depends on ``require_admin`` —
owner or admin only — reusing the Phase-1 role gate; a member is a 403 (FORBIDDEN), tested
exhaustively. This single gate is what every later admin surface (users, AI config, KB,
retention, feedback) builds on."""

from src.identity.dependencies import require_role
from src.identity.models import Role

# Owner or admin may reach any /admin endpoint; anyone else → 403 FORBIDDEN.
require_admin = require_role(Role.OWNER, Role.ADMIN)
