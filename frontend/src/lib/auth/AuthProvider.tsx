// Auth state for the SPA (ADR 0015: memory-only tokens). The access + refresh tokens and
// the org slug live in memory only — never localStorage — so an XSS payload cannot exfiltrate
// a persisted token; the documented trade-off is that a full page reload returns the user to
// login. A silent refresh is scheduled before the access token expires, and the API client's
// 401 hook triggers an on-demand refresh; a failed refresh logs the user out locally.

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { apiClient } from '../api'
import type { ApiClient } from '../api'
import { AuthContext, type AuthContextValue, type AuthStatus } from './context'

const REFRESH_SKEW_SECONDS = 60 // refresh this long before the access token expires

export function AuthProvider({
  children,
  client = apiClient,
}: {
  children: ReactNode
  client?: ApiClient
}) {
  const [status, setStatus] = useState<AuthStatus>('anonymous')
  const [orgSlug, setOrgSlug] = useState<string | null>(null)
  // Mutable, non-rendering session secrets — held in refs (memory-only, never persisted).
  const session = useRef<{ refreshToken: string | null; orgSlug: string | null }>({
    refreshToken: null,
    orgSlug: null,
  })
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Indirection so the refresh timer always calls the latest closure without a dep cycle.
  const refreshRef = useRef<() => Promise<boolean>>(async () => false)

  const clearTimer = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  const clearSession = useCallback(() => {
    clearTimer()
    session.current = { refreshToken: null, orgSlug: null }
    client.setAccessToken(null)
    setStatus('anonymous')
    setOrgSlug(null)
  }, [client, clearTimer])

  const scheduleRefresh = useCallback(
    (expiresIn: number) => {
      clearTimer()
      const delay = Math.max(expiresIn - REFRESH_SKEW_SECONDS, 1) * 1000
      timer.current = setTimeout(() => void refreshRef.current(), delay)
    },
    [clearTimer],
  )

  const applyTokens = useCallback(
    (access: string, refresh: string, expiresIn: number) => {
      session.current.refreshToken = refresh
      client.setAccessToken(access)
      setStatus('authenticated')
      scheduleRefresh(expiresIn)
    },
    [client, scheduleRefresh],
  )

  // Refresh via the rotating refresh flow. Returns success; on failure clears the local
  // session (the client's 401 hook then surfaces the auth error to the UI).
  const doRefresh = useCallback(async (): Promise<boolean> => {
    const { refreshToken, orgSlug: slug } = session.current
    if (!refreshToken || !slug) return false
    try {
      const tokens = await client.refresh({ org_slug: slug, refresh_token: refreshToken })
      applyTokens(tokens.access_token, tokens.refresh_token, tokens.expires_in)
      return true
    } catch {
      clearSession()
      return false
    }
  }, [client, applyTokens, clearSession])

  const login = useCallback(
    async (slug: string, email: string, password: string) => {
      const tokens = await client.login({ org_slug: slug, email, password })
      session.current.orgSlug = slug
      setOrgSlug(slug)
      applyTokens(tokens.access_token, tokens.refresh_token, tokens.expires_in)
    },
    [client, applyTokens],
  )

  const logout = useCallback(async () => {
    const token = session.current.refreshToken
    clearSession()
    if (token) await client.logout({ refresh_token: token }).catch(() => undefined)
  }, [client, clearSession])

  useEffect(() => {
    refreshRef.current = doRefresh
  }, [doRefresh])

  // Install the client's silent-refresh hook for the lifetime of the provider.
  useEffect(() => {
    client.setRefreshHandler(doRefresh)
    return () => {
      client.setRefreshHandler(null)
      clearTimer()
    }
  }, [client, doRefresh, clearTimer])

  const value = useMemo<AuthContextValue>(
    () => ({ status, orgSlug, login, logout }),
    [status, orgSlug, login, logout],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}
