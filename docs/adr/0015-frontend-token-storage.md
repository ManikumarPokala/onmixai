# ADR 0015 — Frontend token storage: memory-only

Status: Accepted (2026-06-08)

## Context

The SPA authenticates against the backend's JWT flow (short-lived access token + rotating
refresh token, both returned as JSON from `/auth/login` and `/auth/refresh`). The frontend
must hold these tokens to authorize API calls and to silently refresh before the access
token expires. Where the tokens live is a security decision with real trade-offs:

- **localStorage / sessionStorage**: survives reload, but is readable by any JavaScript —
  an XSS payload can exfiltrate a long-lived refresh token. Persisted bearer tokens are the
  highest-value XSS target.
- **httpOnly cookie (refresh) + in-memory access token**: the refresh token is unreadable
  by JS (mitigates XSS exfiltration) and survives reload. But it requires the backend to set
  cookies, CSRF defenses, and — because dev runs cross-origin (Vite `:5173` vs FastAPI
  `:8000`) and the API returns tokens as JSON today — backend auth changes (cookie issuance,
  `SameSite`, CORS credentials) that are out of this phase's scope.
- **In-memory only**: tokens live in JS closures (never persisted). XSS during an active
  session can still use the in-memory token, but nothing durable is left to steal, and the
  blast radius ends when the tab closes. The cost is that a full page reload loses the
  session and returns the user to login.

## Decision

**Store both tokens in memory only** (React refs inside `AuthProvider`), never in
localStorage/sessionStorage/cookies. A silent refresh is scheduled before the access token
expires, and the API client performs one on-demand refresh on a 401 and retries; a failed
refresh clears the local session. This matches the current JSON-token, cross-origin-dev
backend without expanding scope into backend cookie/CSRF work.

## Consequences

- **Reload returns to login.** Memory-only tokens do not survive a page reload or a new tab;
  the route guard sends an anonymous user to `/login`. This is the accepted, documented
  trade-off — acceptable for an internal tool and removable later (see below).
- **No persisted token to exfiltrate.** An XSS payload cannot read a stored refresh token,
  which is the worst-case credential theft. In-session token use by XSS remains possible —
  XSS is still defended in depth (React's escaping, no `dangerouslySetInnerHTML` on model
  output, the backend guardrail chain).
- **Single refresh path.** The client triggers at most one refresh per 401 and retries once;
  the auth endpoints themselves skip the refresh hook (no recursion). Rotation is honored —
  each refresh swaps in the new refresh token.
- **Upgrade path (not now).** Moving to httpOnly-cookie refresh later is additive: the
  backend issues the refresh token as a `Secure; HttpOnly; SameSite` cookie, the client drops
  the in-memory refresh token, and `AuthProvider.doRefresh` calls `/auth/refresh` with
  credentials. The access token stays in memory either way. This ADR is superseded if/when
  that lands.
