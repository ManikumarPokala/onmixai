// Authenticated-route guard. Anonymous users are redirected to /login, preserving the
// attempted location so login can return them. Memory-only tokens (ADR 0015) mean a reload
// lands here as anonymous → login, by design.

import { Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from './useAuth'

export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return <>{children}</>
}
