// Admin-route guard. Non-admins (members) are bounced to /chat. UI-only gating — the server
// enforces require_admin on every admin API route, so this is defense-in-depth for navigation.

import { Navigate } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from './useAuth'

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth()
  if (!isAdmin) return <Navigate to="/chat" replace />
  return <>{children}</>
}
