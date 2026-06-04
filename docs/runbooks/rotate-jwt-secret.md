# Runbook — Rotate the JWT signing secret

Rotates `JWT_SECRET` without forcing every active session to fail at once. Access
tokens are short-lived (default 15 min), so a dual-secret grace window equal to
the access-token TTL lets in-flight tokens drain.

## When

- Suspected secret compromise (rotate immediately, skip the grace window —
  accept that all access tokens are invalidated; refresh tokens still work and
  clients re-mint access tokens).
- Routine rotation (use the grace window for zero user-visible disruption).

## Procedure (graceful, dual-secret window)

1. **Generate a new secret** (≥32 chars):
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. **Deploy with verification accepting both secrets.** Configure the new secret
   as the active signing key and the old secret as an additional accepted
   verification key. (V1 signs/verifies with a single `JWT_SECRET`; until
   multi-key verification ships, treat this as: deploy new secret, accept a brief
   window where old access tokens 401 and clients silently refresh.)
3. **Wait one access-token TTL** (`ACCESS_TOKEN_TTL_SECONDS`, default 900s) so all
   tokens signed with the old secret have expired.
4. **Remove the old secret** from the accepted set; new secret is now sole key.
5. **Verify**: a token minted before rotation no longer authenticates; a fresh
   `login` works; `refresh` issues tokens signed with the new secret.

## Guardrails

- The new secret must be ≥32 chars or the app fails fast at startup.
- In `prod`, the secret must not be a value in `DENYLISTED_SECRETS` (the documented
  dev default) — startup refuses it (CLAUDE.md §4, `shared/config.py`).
- Never commit the real secret; it lives only in the environment / secret store.
  `.env.example` documents the variable, not the value.

## Compromise path (immediate)

Set the new `JWT_SECRET` and redeploy without a grace window. All existing access
tokens become invalid immediately; clients use their refresh tokens to obtain new
access tokens. If refresh tokens are also suspected compromised, additionally
revoke refresh tokens for affected users.
