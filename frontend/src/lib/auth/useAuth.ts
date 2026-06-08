import { useContext } from 'react'
import { AuthContext, type AuthContextValue } from './context'

/** Access auth state + actions. Throws if used outside <AuthProvider>. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === null) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
