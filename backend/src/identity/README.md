# Identity domain

Authentication, authorization, and organization/user management — the tenancy
foundation every other domain builds on.

## Responsibility

- Organizations (tenants) and users, with org-scoped unique emails.
- Registration of an organization + its owner user.
- Password-based authentication (argon2id), JWT access tokens, and rotating
  opaque refresh tokens with reuse detection.
- Request authentication, tenant-context binding, and role-based access control.

## Public service interface (`AuthService`)

| Method | Purpose |
|---|---|
| `register_organization(name, slug, owner_email, password, full_name)` | Create org + owner (one transaction); `ConflictError("ORG_SLUG_TAKEN")` on duplicate slug. |
| `authenticate(org_slug, email, password)` | Verify credentials, issue access + refresh tokens. |
| `refresh(org_slug, raw_token)` | Rotate refresh token; reuse revokes all of the user's tokens. |
| `logout(org_slug, raw_token)` | Revoke the presented refresh token (idempotent). |
| `get_user(actor)` / `get_organization(actor)` | Read the authenticated profile / org. |

Dependencies: `get_current_user` (verifies the token, binds tenant + log context),
`get_tenant_session` (the tenant-bound request session), `require_role(*roles)`.

## Invariants

- **Tenant scoping**: every repository method touching `users`/`refresh_tokens`
  takes `org_id`; RLS is enforced (the app connects as a non-bypassrls role).
- **No enumeration**: wrong org / email / password / inactive user are
  indistinguishable (`INVALID_CREDENTIALS`), with constant-time behavior.
- **Refresh rotation + theft containment**: tokens are single-use; replay of a
  revoked token revokes every token for that user.
- **Opaque tokens, no raw storage**: only SHA-256 hashes of refresh tokens are
  stored; access tokens are short-lived JWTs verified with zero clock leeway.
- **`org_slug` on refresh/logout**: required so the RLS-scoped token lookup has
  tenant context (see [ADR 0004](../../../docs/adr/0004-auth-tokens.md)).

## Known limitations (V1)

- No SSO / OIDC / SAML — planned for V2.
- No self-service user invitation/management endpoints yet (admin surfaces land in
  the Administration domain); non-owner users are created out of band in V1.
- Rate-limit storage is in-process (per worker); a shared store (Redis) is the
  production path.
