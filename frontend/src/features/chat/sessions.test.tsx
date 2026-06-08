import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ChatPage } from './ChatPage'
import { ToastProvider } from '../../lib/toast/ToastProvider'
import { seedAuth } from '../../test/render'
import { backend } from '../../test/handlers'

function renderChat(path = '/chat') {
  seedAuth()
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <ToastProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:sessionId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ToastProvider>,
  )
}

function seedSession(id = 's1', title = 'Planning'): void {
  const now = new Date().toISOString()
  backend.sessions.unshift({
    id,
    title,
    is_archived: false,
    created_at: now,
    updated_at: now,
    last_message_at: now,
  })
  backend.messages[id] = []
}

describe('session list', () => {
  it('shows the empty state with no sessions', async () => {
    renderChat()
    expect(await screen.findByText('No chats yet. Start a new one.')).toBeInTheDocument()
  })

  it('creates a new chat', async () => {
    renderChat()
    await screen.findByText('No chats yet. Start a new one.')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'New chat' }))
    // Creating navigates into the new (empty) session.
    expect(await screen.findByText('Ask about your documents')).toBeInTheDocument()
    expect(backend.sessions).toHaveLength(1)
  })

  it('renames a session', async () => {
    seedSession()
    renderChat()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Rename Planning' }))
    const input = screen.getByLabelText('Rename chat')
    await user.clear(input)
    await user.type(input, 'Roadmap{Enter}')
    expect(await screen.findByRole('button', { name: 'Roadmap' })).toBeInTheDocument()
    expect(backend.sessions[0].title).toBe('Roadmap')
  })

  it('archives a session', async () => {
    seedSession()
    renderChat()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Archive Planning' }))
    expect(await screen.findByRole('button', { name: /archived/ })).toBeInTheDocument()
    expect(backend.sessions[0].is_archived).toBe(true)
  })

  it('deletes a session', async () => {
    seedSession()
    renderChat()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete Planning' }))
    expect(await screen.findByText('No chats yet. Start a new one.')).toBeInTheDocument()
    expect(backend.sessions).toHaveLength(0)
  })
})
