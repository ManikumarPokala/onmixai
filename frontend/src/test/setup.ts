// Vitest setup: jest-dom + jest-axe matchers, and the MSW server lifecycle. Each test gets a
// clean in-memory backend and the default SSE script.

import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll, expect } from 'vitest'
import { cleanup } from '@testing-library/react'
import { toHaveNoViolations } from 'jest-axe'
import { server } from './server'
import { resetBackend } from './handlers'
import { apiClient } from '../lib/api'

expect.extend(toHaveNoViolations)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  cleanup() // globals:false → testing-library's auto-cleanup is not wired; do it explicitly
  server.resetHandlers()
  resetBackend()
  apiClient.setAccessToken(null) // the singleton client is shared across tests
  apiClient.setRefreshHandler(null)
})
afterAll(() => server.close())
