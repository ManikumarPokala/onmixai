// One typed API client — the single doorway to the backend (CLAUDE.md §10: no scattered
// fetch). It injects the in-memory access token, performs a single silent refresh on a 401
// and retries once, and renders every non-2xx body as a typed ApiError. The chat send is a
// streaming POST (SSE) with AbortSignal support for the stop button (ADR 0014).

import { apiErrorFromEnvelope, ApiError } from './errors'
import type { components } from './schema'
import { parseChatStream, type ChatStreamEvent } from './sse'

type Schemas = components['schemas']
export type SessionResponse = Schemas['SessionResponse']
export type SessionPage = Schemas['SessionPage']
export type MessagePage = Schemas['MessagePage']
export type MessageResponse = Schemas['MessageResponse']
export type CreateSessionRequest = Schemas['CreateSessionRequest']
export type UpdateSessionRequest = Schemas['UpdateSessionRequest']
export type FeedbackRequest = Schemas['FeedbackRequest']
export type TokenResponse = Schemas['TokenResponse']
export type LoginRequest = Schemas['LoginRequest']

interface RequestOptions {
  method?: string
  body?: unknown
  query?: Record<string, string | number | undefined>
  signal?: AbortSignal
  skipRefresh?: boolean
}

export type RefreshHandler = () => Promise<boolean>

export class ApiClient {
  private readonly baseUrl: string
  private accessToken: string | null = null
  private refreshHandler: RefreshHandler | null = null

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl
  }

  setAccessToken(token: string | null): void {
    this.accessToken = token
  }

  /** AuthProvider installs the silent-refresh routine here; called once on a 401. */
  setRefreshHandler(handler: RefreshHandler | null): void {
    this.refreshHandler = handler
  }

  private buildUrl(path: string, query?: RequestOptions['query']): string {
    const url = new URL(this.baseUrl + path, window.location.origin)
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined) url.searchParams.set(key, String(value))
      }
    }
    return url.toString()
  }

  private headers(hasBody: boolean): Headers {
    const headers = new Headers()
    if (hasBody) headers.set('Content-Type', 'application/json')
    if (this.accessToken) headers.set('Authorization', `Bearer ${this.accessToken}`)
    return headers
  }

  private async raw(path: string, opts: RequestOptions): Promise<Response> {
    const init: RequestInit = {
      method: opts.method ?? 'GET',
      headers: this.headers(opts.body !== undefined),
      signal: opts.signal,
    }
    if (opts.body !== undefined) init.body = JSON.stringify(opts.body)
    let response = await fetch(this.buildUrl(path, opts.query), init)
    if (response.status === 401 && !opts.skipRefresh && this.refreshHandler) {
      const refreshed = await this.refreshHandler()
      if (refreshed) {
        init.headers = this.headers(opts.body !== undefined)
        response = await fetch(this.buildUrl(path, opts.query), init)
      }
    }
    return response
  }

  /** JSON request → parsed body, or a typed ApiError on any non-2xx. */
  async request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const response = await this.raw(path, opts)
    if (response.status === 204) return undefined as T
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) throw apiErrorFromEnvelope(response.status, body)
    return body as T
  }

  // --- auth (no silent-refresh on these) ---

  login(body: LoginRequest): Promise<TokenResponse> {
    return this.request<TokenResponse>('/auth/login', { method: 'POST', body, skipRefresh: true })
  }

  refresh(body: { org_slug: string; refresh_token: string }): Promise<TokenResponse> {
    return this.request<TokenResponse>('/auth/refresh', {
      method: 'POST',
      body,
      skipRefresh: true,
    })
  }

  logout(body: { refresh_token: string }): Promise<void> {
    return this.request<void>('/auth/logout', { method: 'POST', body, skipRefresh: true })
  }

  // --- chat ---

  listSessions(cursor?: string, limit = 50): Promise<SessionPage> {
    return this.request<SessionPage>('/chat/sessions', { query: { cursor, limit } })
  }

  createSession(title?: string): Promise<SessionResponse> {
    return this.request<SessionResponse>('/chat/sessions', {
      method: 'POST',
      body: { title: title ?? null } satisfies CreateSessionRequest,
    })
  }

  updateSession(id: string, patch: UpdateSessionRequest): Promise<SessionResponse> {
    return this.request<SessionResponse>(`/chat/sessions/${id}`, { method: 'PATCH', body: patch })
  }

  deleteSession(id: string): Promise<void> {
    return this.request<void>(`/chat/sessions/${id}`, { method: 'DELETE' })
  }

  listMessages(sessionId: string, afterSeq?: number, limit = 100): Promise<MessagePage> {
    return this.request<MessagePage>(`/chat/sessions/${sessionId}/messages`, {
      query: { after_seq: afterSeq, limit },
    })
  }

  submitFeedback(messageId: string, body: FeedbackRequest): Promise<void> {
    return this.request<void>(`/chat/messages/${messageId}/feedback`, { method: 'POST', body })
  }

  /**
   * Stream a chat turn. Resolves once the stream completes (or aborts). Pre-stream caller
   * errors (4xx, e.g. archived session) throw an ApiError before any event is emitted; an
   * infrastructure failure arrives as a terminal `error` event. `onEvent` is invoked per
   * SSE event; `signal` (the stop button / unmount) aborts generation server-side.
   */
  async streamMessage(
    sessionId: string,
    content: string,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const path = `/chat/sessions/${sessionId}/messages`
    const response = await this.raw(path, { method: 'POST', body: { content }, signal })
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null)
      throw apiErrorFromEnvelope(response.status, body)
    }
    if (!response.body) throw new ApiError('INTERNAL_ERROR', 502)
    for await (const event of parseChatStream(response.body)) onEvent(event)
  }
}
