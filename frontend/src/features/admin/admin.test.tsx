import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AdminPage } from './AdminPage'
import { RequireAdmin } from '../../lib/auth/RequireAdmin'
import { useAuth } from '../../lib/auth/useAuth'
import { renderWithProviders, seedAuth } from '../../test/render'
import { server } from '../../test/server'

vi.mock('../../lib/auth/useAuth', () => ({ useAuth: vi.fn() }))
const mockUseAuth = vi.mocked(useAuth)

const API = '*/api/v1'

const USER = {
  id: 'u-1',
  email: 'm@x.test',
  full_name: 'Mia Member',
  role: 'member',
  is_active: true,
  created_at: '2026-06-08T00:00:00Z',
  org_slug: 'acme',
}

const CONFIG = {
  default_model: 'openai/gpt-4o-mini',
  fallback_chain: ['anthropic/claude-3-5-sonnet-latest'],
  temperature_default: null,
  pii_redaction_enabled: true,
}

function seedAdminApi(): void {
  server.use(
    http.get(`${API}/admin/users`, () => HttpResponse.json({ users: [USER], next_cursor: null })),
    http.get(`${API}/admin/ai/model-config`, () => HttpResponse.json(CONFIG)),
  )
}

describe('AdminPage', () => {
  it('deactivating a user is gated behind a consequence-confirm', async () => {
    seedAuth()
    seedAdminApi()
    let deactivated = false
    server.use(
      http.post(`${API}/admin/users/u-1/deactivate`, () => {
        deactivated = true
        return HttpResponse.json({ ...USER, is_active: false })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders(<AdminPage />, '/admin')

    await user.click(await screen.findByRole('button', { name: 'Deactivate' }))
    expect(deactivated).toBe(false) // not until the dialog is confirmed
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Deactivate user?')
    await user.click(within(dialog).getByRole('button', { name: 'Deactivate' }))
    await waitFor(() => expect(deactivated).toBe(true))
  })

  it('disabling PII redaction is gated behind a consequence-confirm and sends the flag', async () => {
    seedAuth()
    seedAdminApi()
    let sentPii: boolean | undefined
    server.use(
      http.put(`${API}/admin/ai/model-config`, async ({ request }) => {
        const body = (await request.json()) as { pii_redaction_enabled: boolean }
        sentPii = body.pii_redaction_enabled
        return HttpResponse.json({ ...CONFIG, pii_redaction_enabled: body.pii_redaction_enabled })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders(<AdminPage />, '/admin')

    const toggle = await screen.findByRole('checkbox', { name: 'Redact PII in answers' })
    expect(toggle).toBeChecked()
    await user.click(toggle) // attempt to disable
    expect(sentPii).toBeUndefined() // nothing sent yet — confirmation required
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Disable PII redaction?')
    await user.click(within(dialog).getByRole('button', { name: 'Disable redaction' }))
    await waitFor(() => expect(sentPii).toBe(false))
  })

  it('shows an error state when the admin API fails', async () => {
    seedAuth()
    server.use(
      http.get(`${API}/admin/users`, () => HttpResponse.json({ error: {} }, { status: 500 })),
      http.get(`${API}/admin/ai/model-config`, () => HttpResponse.json(CONFIG)),
    )
    renderWithProviders(<AdminPage />, '/admin')
    expect(await screen.findByText('Could not load users. Please try again.')).toBeInTheDocument()
  })
})

describe('RequireAdmin', () => {
  function renderGuard() {
    return render(
      <MemoryRouter initialEntries={['/admin']}>
        <Routes>
          <Route path="/chat" element={<div>chat page</div>} />
          <Route
            path="/admin"
            element={
              <RequireAdmin>
                <div>admin console</div>
              </RequireAdmin>
            }
          />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('renders children for an admin', () => {
    mockUseAuth.mockReturnValue({ isAdmin: true } as ReturnType<typeof useAuth>)
    renderGuard()
    expect(screen.getByText('admin console')).toBeInTheDocument()
  })

  it('redirects a member to /chat', () => {
    mockUseAuth.mockReturnValue({ isAdmin: false } as ReturnType<typeof useAuth>)
    renderGuard()
    expect(screen.getByText('chat page')).toBeInTheDocument()
    expect(screen.queryByText('admin console')).not.toBeInTheDocument()
  })
})
