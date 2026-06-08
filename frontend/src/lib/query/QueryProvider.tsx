// TanStack Query setup (CLAUDE.md §10: server state via TanStack Query, never mirrored into
// a global store). A single QueryClient with a global error sink: any query/mutation error
// surfaces as a toast with the typed human message, so callers don't each re-handle transport
// errors. Created via lazy useState so the client is stable across renders.

import { QueryCache, QueryClient, QueryClientProvider, MutationCache } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useToast } from '../toast/useToast'

export function QueryProvider({ children }: { children: ReactNode }) {
  const { notifyError } = useToast()
  const [client] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError: notifyError }),
        mutationCache: new MutationCache({ onError: notifyError }),
        defaultOptions: {
          queries: { retry: 1, staleTime: 30_000, refetchOnWindowFocus: false },
          mutations: { retry: 0 },
        },
      }),
  )
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
