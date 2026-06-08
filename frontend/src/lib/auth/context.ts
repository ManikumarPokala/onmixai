import { createContext } from 'react'

export type AuthStatus = 'anonymous' | 'authenticated'

export interface AuthContextValue {
  status: AuthStatus
  orgSlug: string | null
  login: (orgSlug: string, email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
