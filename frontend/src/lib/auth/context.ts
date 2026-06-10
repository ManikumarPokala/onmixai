import { createContext } from 'react'

export type AuthStatus = 'anonymous' | 'authenticated'
export type UserRole = 'owner' | 'admin' | 'member'

export interface AuthContextValue {
  status: AuthStatus
  orgSlug: string | null
  // Role decoded from the access token, for UI gating only — the server is the real authority.
  role: UserRole | null
  isAdmin: boolean
  login: (orgSlug: string, email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
