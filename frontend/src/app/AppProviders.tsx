// Provider composition (router-free, so tests can supply their own router). Order matters:
// ErrorBoundary catches render crashes; ToastProvider is outermost of the data providers so
// Query's global error sink and Auth can surface toasts; QueryProvider then AuthProvider.

import type { ReactNode } from 'react'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { AuthProvider } from '../lib/auth/AuthProvider'
import { QueryProvider } from '../lib/query/QueryProvider'
import { ToastProvider } from '../lib/toast/ToastProvider'

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary>
      <ToastProvider>
        <QueryProvider>
          <AuthProvider>{children}</AuthProvider>
        </QueryProvider>
      </ToastProvider>
    </ErrorBoundary>
  )
}
