// MSW handlers: a small in-memory backend mirroring the auth + chat endpoints, including a
// scriptable SSE stream for chat sends. Tests drive behavior via `resetBackend`, `setStream`,
// and the exported store. The SSE handler emits the exact frame format the real backend uses
// (event:/data:), so the client's parser is exercised end-to-end.

import { http, HttpResponse } from 'msw'
import type { components } from '../lib/api/schema'

type SessionResponse = components['schemas']['SessionResponse']
type MessageResponse = components['schemas']['MessageResponse']

const API = '*/api/v1'

interface Backend {
  validToken: string
  refreshValid: boolean
  sessions: SessionResponse[]
  messages: Record<string, MessageResponse[]>
  feedback: Record<string, { rating: string; comment: string | null }>
  loginCalls: number
  refreshCalls: number
}

function freshBackend(): Backend {
  return {
    validToken: 'access-1',
    refreshValid: true,
    sessions: [],
    messages: {},
    feedback: {},
    loginCalls: 0,
    refreshCalls: 0,
  }
}

export let backend: Backend = freshBackend()

export function resetBackend(): void {
  backend = freshBackend()
  streamFrames = defaultGroundedStream()
  holdStreamOpen = false
}

/** Raw SSE frames the next chat send returns. Tests set this to drive each terminal shape. */
export let streamFrames: string[] = defaultGroundedStream()
/** When true, the stream stays open after emitting frames (until the client aborts) and
 * persists nothing — used to test the stop button. */
export let holdStreamOpen = false

export function setStream(frames: string[]): void {
  streamFrames = frames
}

export function setHoldStreamOpen(value: boolean): void {
  holdStreamOpen = value
}

/** Append the persisted user + assistant rows a content terminal would create, derived from
 * the scripted frames (mirrors the backend's atomic persist at the terminal). An `error`
 * stream persists nothing. */
function persistTurn(sessionId: string, content: string): void {
  const messages = backend.messages[sessionId]
  if (!messages) return
  const hasError = streamFrames.some((f) => f.startsWith('event: error'))
  if (hasError) return
  const refusal = streamFrames.find((f) => f.startsWith('event: refusal'))
  const text = streamFrames
    .filter((f) => f.startsWith('event: token'))
    .map((f) => JSON.parse(f.split('data: ')[1]).text as string)
    .join('')
  const citationsFrame = streamFrames.find((f) => f.startsWith('event: citations'))
  const citations = citationsFrame ? JSON.parse(citationsFrame.split('data: ')[1]).items : []
  const seq = messages.length
  const now = new Date().toISOString()
  messages.push({
    id: `u-${seq}`,
    seq,
    role: 'user',
    content,
    citations: [],
    refusal_reason: null,
    prompt_version: null,
    model_used: null,
    created_at: now,
    feedback: null,
  })
  messages.push(
    refusal
      ? {
          id: 'm-assistant',
          seq: seq + 1,
          role: 'assistant',
          content: '',
          citations: [],
          refusal_reason: JSON.parse(refusal.split('data: ')[1]).reason,
          prompt_version: null,
          model_used: null,
          created_at: now,
          feedback: null,
        }
      : {
          id: 'm-assistant',
          seq: seq + 1,
          role: 'assistant',
          content: text,
          citations,
          refusal_reason: null,
          prompt_version: '1.1.0',
          model_used: 'fake/model',
          created_at: now,
          feedback: null,
        },
  )
}

export function sseFrame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

export function defaultGroundedStream(): string[] {
  return [
    sseFrame('meta', { message_id: 'm-assistant', seq: 1 }),
    sseFrame('token', { text: 'The answer ' }),
    sseFrame('token', { text: 'is 42 [1].' }),
    sseFrame('citations', {
      items: [
        {
          marker_index: 1,
          chunk_id: 'c1',
          document_id: 'd1',
          filename: 'guide.pdf',
          page_ref: 7,
        },
      ],
    }),
    sseFrame('done', { message_id: 'm-assistant', prompt_version: '1.1.0', trace_id: 't1' }),
  ]
}

function isAuthed(request: Request): boolean {
  return request.headers.get('Authorization') === `Bearer ${backend.validToken}`
}

function unauthorized() {
  return HttpResponse.json(
    { error: { code: 'INVALID_TOKEN', message: 'invalid', request_id: 'r' } },
    { status: 401 },
  )
}

export const handlers = [
  http.post(`${API}/auth/login`, async ({ request }) => {
    backend.loginCalls += 1
    const body = (await request.json()) as { password: string }
    if (body.password === 'wrong') {
      return HttpResponse.json(
        { error: { code: 'INVALID_CREDENTIALS', message: 'bad', request_id: 'r' } },
        { status: 401 },
      )
    }
    return HttpResponse.json({
      access_token: backend.validToken,
      refresh_token: 'refresh-1',
      expires_in: 900,
    })
  }),

  http.post(`${API}/auth/refresh`, async () => {
    backend.refreshCalls += 1
    if (!backend.refreshValid) {
      return HttpResponse.json(
        { error: { code: 'REFRESH_TOKEN_REUSED', message: 'reuse', request_id: 'r' } },
        { status: 401 },
      )
    }
    backend.validToken = 'access-2' // rotation
    return HttpResponse.json({
      access_token: backend.validToken,
      refresh_token: 'refresh-2',
      expires_in: 900,
    })
  }),

  http.post(`${API}/auth/logout`, () => new HttpResponse(null, { status: 204 })),

  http.get(`${API}/chat/sessions`, ({ request }) => {
    if (!isAuthed(request)) return unauthorized()
    return HttpResponse.json({ sessions: backend.sessions, next_cursor: null })
  }),

  http.post(`${API}/chat/sessions`, async ({ request }) => {
    if (!isAuthed(request)) return unauthorized()
    const body = (await request.json()) as { title: string | null }
    const now = new Date().toISOString()
    const session: SessionResponse = {
      id: `s-${backend.sessions.length + 1}`,
      title: body.title,
      is_archived: false,
      created_at: now,
      updated_at: now,
      last_message_at: now,
    }
    backend.sessions = [session, ...backend.sessions]
    backend.messages[session.id] = []
    return HttpResponse.json(session, { status: 201 })
  }),

  http.patch(`${API}/chat/sessions/:id`, async ({ request, params }) => {
    if (!isAuthed(request)) return unauthorized()
    const id = params.id as string
    const session = backend.sessions.find((s) => s.id === id)
    if (!session) {
      return HttpResponse.json(
        { error: { code: 'SESSION_NOT_FOUND', message: 'nope', request_id: 'r' } },
        { status: 404 },
      )
    }
    const patch = (await request.json()) as { title?: string; is_archived?: boolean }
    if (patch.title !== undefined && patch.title !== null) session.title = patch.title
    if (patch.is_archived !== undefined) session.is_archived = patch.is_archived
    return HttpResponse.json(session)
  }),

  http.delete(`${API}/chat/sessions/:id`, ({ request, params }) => {
    if (!isAuthed(request)) return unauthorized()
    const id = params.id as string
    backend.sessions = backend.sessions.filter((s) => s.id !== id)
    delete backend.messages[id]
    return new HttpResponse(null, { status: 204 })
  }),

  http.get(`${API}/chat/sessions/:id/messages`, ({ request, params }) => {
    if (!isAuthed(request)) return unauthorized()
    const id = params.id as string
    const messages = backend.messages[id]
    if (!messages) {
      return HttpResponse.json(
        { error: { code: 'SESSION_NOT_FOUND', message: 'nope', request_id: 'r' } },
        { status: 404 },
      )
    }
    return HttpResponse.json({ messages, next_cursor: null })
  }),

  http.post(`${API}/chat/sessions/:id/messages`, async ({ request, params }) => {
    if (!isAuthed(request)) return unauthorized()
    const sessionId = params.id as string
    const { content } = (await request.json()) as { content: string }
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder()
        for (const frame of streamFrames) controller.enqueue(encoder.encode(frame))
        if (holdStreamOpen) return // stay open until the client aborts (stop button)
        persistTurn(sessionId, content) // mirror the backend's atomic persist at the terminal
        controller.close()
      },
      cancel() {
        // Client aborted (disconnect / stop): persist nothing — the turn is re-askable.
      },
    })
    return new HttpResponse(stream, { headers: { 'Content-Type': 'text/event-stream' } })
  }),

  http.post(`${API}/chat/messages/:messageId/feedback`, async ({ request, params }) => {
    if (!isAuthed(request)) return unauthorized()
    const body = (await request.json()) as { rating: string; comment: string | null }
    backend.feedback[params.messageId as string] = body
    return new HttpResponse(null, { status: 204 })
  }),
]
