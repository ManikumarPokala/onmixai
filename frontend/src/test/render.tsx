// Test render helpers. `renderApp` mounts the whole app (with its real providers) at a route
// — used for auth/integration flows. `renderWithProviders` mounts a single component with a
// fresh QueryClient + Toast + MemoryRouter — used for isolated chat component tests, where the
// shared apiClient token is seeded directly (see seedAuth).

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { ReactElement, ReactNode } from 'react'
import { App } from '../App'
import { apiClient } from '../lib/api'
import { ToastProvider } from '../lib/toast/ToastProvider'

export function renderApp(route = '/'): RenderResult {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <App />
    </MemoryRouter>,
  )
}

/** Seed the shared client with a valid token (matches the MSW backend's default). */
export function seedAuth(token = 'access-1'): void {
  apiClient.setAccessToken(token)
}

export function renderWithProviders(ui: ReactElement, route = '/'): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <ToastProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
      </QueryClientProvider>
    </ToastProvider>
  )
  return render(ui, { wrapper: Wrapper })
}
