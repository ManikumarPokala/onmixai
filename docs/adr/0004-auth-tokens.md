# ADR 0004 — Authentication Tokens

Status: Accepted (2026-06-04)

## Context

V1 authentication is JWT-based (SSO is V2). We need password storage, short-lived
access tokens, refresh-token rotation with theft containment, and no user
enumeration — all interacting correctly with the tenant RLS model (ADR 0003).

## Decision

**Password hashing.** argon2id via `argon2-cffi` with explicit parameters
`time_cost=3, memory_cost=64 MiB, parallelism=4`. Verification is constant-time;
hashes are transparently upgraded (`check_needs_rehash`) at next login. On a
missing user, a dummy hash is still verified so timing does not reveal whether an
email exists. Wrong org, wrong email, wrong password, and inactive user all return
the identical `AuthenticationError("INVALID_CREDENTIALS")`.

**Access tokens.** Short-lived JWT (default 15 min) with claims
`sub, org_id, role, iat, exp, jti`, verified with `leeway=0`. Any JWT problem
(expired, malformed, bad signature, unknown/inactive user) maps to
`AuthenticationError` — never a 500.

**Refresh tokens.** Opaque, 32 random URL-safe bytes; only the SHA-256 hash is
stored. Rotation on every refresh: the presented token is revoked and a new pair
issued. **Reuse of an already-revoked token revokes ALL of that user's tokens**
(theft containment) and returns 401. A token's `org_id` is always written from the
authenticated user server-side, never from request input.

**`org_slug` on refresh and logout.** `refresh()` and `logout()` take an
`org_slug` argument in addition to the token. Rationale: refresh tokens are opaque
(they reveal no tenant), but `refresh_tokens` is under RLS and the application
connects as a non-bypassrls role — so a token lookup returns zero rows unless the
`app.current_org_id` GUC is set first. The org cannot be derived from an opaque
token, so the caller supplies `org_slug` (the client already has it from login,
mirroring `authenticate(org_slug, ...)`); the service resolves the org (the
`organizations` table has no RLS), sets the GUC, then performs the org-scoped,
RLS-protected lookup. Alternatives considered and rejected: embedding `org_id` in
the token (changes the opaque-token contract) and a `SECURITY DEFINER` lookup
function (a sharp-edged DB object and extra migration surface).

## Consequences

- Brute force is throttled (rate limiting, ADR/Task 7) and never enumerates users.
- A stolen-and-replayed refresh token is detected and contains the breach to the
  affected user, tenant-scoped.
- The refresh/logout API carries `org_slug`; clients must send it. This is the
  cost of keeping refresh tokens opaque while enforcing RLS on their storage.
