#!/usr/bin/env bash
# JWT-secret rotation drill (Phase 7 / Task 2). Two parts:
#   1. A self-contained proof of the dual-secret GRACE WINDOW (runs here, no stack needed):
#      a token signed by the OLD secret still verifies under {current=new, previous=old}, and is
#      rejected once previous is cleared. This is the mechanism that lets live sessions survive.
#   2. The live procedure to execute against your running stack (user-run) to confirm end-to-end.
set -euo pipefail
cd "$(dirname "$0")/../.."   # backend/
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"

echo "→ Part 1: grace-window mechanism proof (no stack required)"
"$PY" - <<'PY'
from uuid import uuid4
from pydantic import SecretStr
from src.shared.config import Settings
from src.shared.security import create_access_token, decode_access_token
import jwt

OLD = "old-" + "x" * 36
NEW = "new-" + "x" * 36
def s(cur, prev=None):
    return Settings(_env_file=None, env="test",
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        jwt_secret=SecretStr(cur), jwt_secret_previous=SecretStr(prev) if prev else None,
        storage_endpoint="http://localhost:9000", storage_access_key="a", storage_secret_key="s",
        storage_bucket="b", redis_url="redis://localhost:6379/0", embedding_dimension=8)

tok = create_access_token(settings=s(OLD), user_id=uuid4(), org_id=uuid4(), role="member")
assert decode_access_token(tok, settings=s(NEW, OLD))["role"] == "member", "grace window failed"
print("  ✓ old-secret token verifies during the grace window (previous=old)")
try:
    decode_access_token(tok, settings=s(NEW, None))
    raise SystemExit("✗ old token still accepted after window closed")
except jwt.InvalidSignatureError:
    print("  ✓ old-secret token rejected once the window closes (previous cleared)")
print("  → live sessions survive rotation: the access token stays valid until it expires,")
print("    and the rotating-refresh flow issues new-secret tokens thereafter.")
PY

echo
echo "→ Part 2: live procedure (RUN BY YOU against the running stack)"
cat <<'TXT'
  1. Set JWT_SECRET_PREVIOUS = <current JWT_SECRET>, then JWT_SECRET = <new secret>; reload the API.
  2. Confirm existing sessions keep working (no forced logout) — old + new access tokens both verify.
  3. After one access-token TTL has elapsed (default 15 min), clear JWT_SECRET_PREVIOUS; reload.
  4. Confirm old-secret tokens are now rejected (clients silently refresh). Rotation complete.
  Provider keys / PURGE_DATABASE_URL rotate the same way: add new, drain, remove old — no downtime.
TXT
echo "✓ rotation drill: mechanism proven; run Part 2 to confirm end-to-end on your stack."
