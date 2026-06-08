import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { axe } from 'jest-axe'
import { ChatPage } from './ChatPage'
import { ToastProvider } from '../../lib/toast/ToastProvider'
import { seedAuth } from '../../test/render'
import { backend, setHoldStreamOpen, setStream, sseFrame } from '../../test/handlers'
import { server } from '../../test/server'

function seedSession(id = 's1'): void {
  const now = new Date().toISOString()
  backend.sessions.unshift({
    id,
    title: 'Existing chat',
    is_archived: false,
    created_at: now,
    updated_at: now,
    last_message_at: now,
  })
  backend.messages[id] = []
}

function renderChat(path = '/chat/s1') {
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

async function ask(text: string): Promise<void> {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Message'), text)
  await user.click(screen.getByRole('button', { name: 'Send' }))
}

describe('chat — streaming + states', () => {
  it('shows a skeleton while messages load', () => {
    seedSession()
    renderChat()
    // Initial render is the loading state before the query resolves.
    expect(screen.getByLabelText('Loading messages')).toBeInTheDocument()
  })

  it('shows the empty state for a session with no messages', async () => {
    seedSession()
    renderChat()
    expect(await screen.findByText('Ask about your documents')).toBeInTheDocument()
  })

  it('streams a grounded answer, then shows the persisted cited message', async () => {
    seedSession()
    renderChat()
    await screen.findByText('Ask about your documents')
    await ask('what is the answer?')

    // The answer text (streamed, then canonical) and a resolved citation chip + source card.
    expect(await screen.findByText(/The answer is 42/)).toBeInTheDocument()
    expect(
      await screen.findByRole('button', { name: 'Source 1: guide.pdf, p.7' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Sources')).toHaveTextContent('guide.pdf, p.7')
    expect(screen.queryByRole('note')).not.toBeInTheDocument() // not a refusal
  })

  it('refusal supersedes the streamed text', async () => {
    seedSession()
    setStream([
      sseFrame('meta', { message_id: 'm-assistant', seq: 1 }),
      sseFrame('token', { text: 'Here is a guess that should vanish' }),
      sseFrame('refusal', { reason: 'UNGROUNDED_ANSWER' }),
    ])
    renderChat()
    await screen.findByText('Ask about your documents')
    await ask('tell me something')

    // The streamed guess is gone; the refusal state (with its guidance) replaces it.
    expect(await screen.findByText(/won’t guess/)).toBeInTheDocument()
    expect(screen.queryByText(/guess that should vanish/)).not.toBeInTheDocument()
    expect(screen.getAllByRole('note').length).toBeGreaterThan(0)
  })

  it('refuses before generating on low confidence (no tokens)', async () => {
    seedSession()
    setStream([
      sseFrame('meta', { message_id: 'm-assistant', seq: 1 }),
      sseFrame('refusal', { reason: 'INSUFFICIENT_SOURCES' }),
    ])
    renderChat()
    await screen.findByText('Ask about your documents')
    await ask('anything?')
    expect(await screen.findByText(/couldn’t find enough relevant information/)).toBeInTheDocument()
  })

  it('renders an infrastructure error with retry and persists nothing', async () => {
    seedSession()
    setStream([
      sseFrame('meta', { message_id: 'm-assistant', seq: 1 }),
      sseFrame('token', { text: 'partial' }),
      sseFrame('error', { code: 'UPSTREAM_UNAVAILABLE' }),
    ])
    renderChat()
    await screen.findByText('Ask about your documents')
    await ask('hello?')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('AI service is temporarily unavailable')
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(backend.messages['s1']).toHaveLength(0) // re-askable, nothing persisted
  })

  it('stop cancels the stream and freezes the partial text', async () => {
    seedSession()
    setHoldStreamOpen(true)
    setStream([
      sseFrame('meta', { message_id: 'm-assistant', seq: 1 }),
      sseFrame('token', { text: 'partial answer ' }),
    ])
    renderChat()
    await screen.findByText('Ask about your documents')
    await ask('slow question')

    expect(await screen.findByText(/partial answer/)).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Stop' }))

    // Streaming ended (Send is back); the partial fragment is frozen and clearly marked
    // unverified (it was never grounding-validated and carries no citations); nothing persisted.
    expect(await screen.findByText('Stopped — partial, unverified')).toBeInTheDocument()
    const stopped = screen.getByRole('note', { name: 'Stopped response' })
    expect(within(stopped).getByText(/partial answer/)).toBeInTheDocument()
    expect(within(stopped).queryByLabelText('Sources')).not.toBeInTheDocument() // no citations
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument()
    expect(backend.messages['s1']).toHaveLength(0)
  })

  it('surfaces a load error with retry', async () => {
    seedSession()
    server.use(
      http.get('*/api/v1/chat/sessions/:id/messages', () =>
        HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'x', request_id: 'r' } },
          { status: 500 },
        ),
      ),
    )
    renderChat()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('couldn’t load this conversation')
  })

  it('has no axe violations on a streamed grounded answer', async () => {
    seedSession()
    const { container } = renderChat()
    await screen.findByText('Ask about your documents')
    await ask('what is the answer?')
    await screen.findByText(/The answer is 42/)
    expect(await axe(container)).toHaveNoViolations()
  })
})
