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
export type RegisterRequest = Schemas['RegisterRequest']
export type RegisterResponse = Schemas['RegisterResponse']
export type RecommendationResponse = Schemas['RecommendationResponse']
export type RecommendationPage = Schemas['RecommendationPage']
export type CreateRecommendationRequest = Schemas['CreateRecommendationRequest']
export type ReportResponse = Schemas['ReportResponse']
export type ReportPage = Schemas['ReportPage']
export type CreateReportRequest = Schemas['CreateReportRequest']
export type ExportResponse = Schemas['ExportResponse']
export type UserPage = Schemas['UserPage']
export type UserResponse = Schemas['UserResponse']
export type ModelConfigResponse = Schemas['ModelConfigResponse']
export type SetModelConfigRequest = Schemas['SetModelConfigRequest']
export type BudgetResponse = Schemas['BudgetResponse']
export type SetBudgetRequest = Schemas['SetBudgetRequest']
export type CollectionCreate = Schemas['CollectionCreate']
export type CollectionResponse = Schemas['CollectionResponse']
export type DocumentResponse = Schemas['DocumentResponse']
export type DocumentStatus = Schemas['DocumentStatus']
export type UploadAccepted = Schemas['UploadAccepted']


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
    const isFormData = opts.body instanceof FormData
    const init: RequestInit = {
      method: opts.method ?? 'GET',
      headers: this.headers(opts.body !== undefined && !isFormData),
      signal: opts.signal,
    }
    if (opts.body !== undefined) {
      init.body = isFormData ? (opts.body as FormData) : JSON.stringify(opts.body)
    }
    let response = await fetch(this.buildUrl(path, opts.query), init)
    if (response.status === 401 && !opts.skipRefresh && this.refreshHandler) {
      const refreshed = await this.refreshHandler()
      if (refreshed) {
        init.headers = this.headers(opts.body !== undefined && !isFormData)
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

  register(body: RegisterRequest): Promise<RegisterResponse> {
    return this.request<RegisterResponse>('/auth/register', { method: 'POST', body, skipRefresh: true })
  }

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

  // --- recommendations ---

  createRecommendation(body: CreateRecommendationRequest): Promise<RecommendationResponse> {
    return this.request<RecommendationResponse>('/recommendations', { method: 'POST', body })
  }

  listRecommendations(cursor?: string, limit = 50): Promise<RecommendationPage> {
    return this.request<RecommendationPage>('/recommendations', { query: { cursor, limit } })
  }

  getRecommendation(id: string): Promise<RecommendationResponse> {
    return this.request<RecommendationResponse>(`/recommendations/${id}`)
  }

  // --- reports + exports ---

  createReport(body: CreateReportRequest): Promise<ReportResponse> {
    return this.request<ReportResponse>('/reports', { method: 'POST', body })
  }

  listReports(cursor?: string, limit = 50): Promise<ReportPage> {
    return this.request<ReportPage>('/reports', { query: { cursor, limit } })
  }

  getReport(id: string): Promise<ReportResponse> {
    return this.request<ReportResponse>(`/reports/${id}`)
  }

  createExport(reportId: string): Promise<ExportResponse> {
    return this.request<ExportResponse>(`/reports/${reportId}/exports`, { method: 'POST' })
  }

  getExport(reportId: string, exportId: string): Promise<ExportResponse> {
    return this.request<ExportResponse>(`/reports/${reportId}/exports/${exportId}`)
  }

  /** Download a ready export's PDF as a Blob (authed fetch — the download endpoint requires the
   * bearer token, so a plain anchor href can't carry it). A non-ready/forbidden export throws. */
  async downloadExport(reportId: string, exportId: string): Promise<Blob> {
    const response = await this.raw(
      `/reports/${reportId}/exports/${exportId}/download`,
      { method: 'GET' },
    )
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null)
      throw apiErrorFromEnvelope(response.status, body)
    }
    return response.blob()
  }

  // --- admin (owner/admin only; the server enforces require_admin on every route) ---

  listUsers(cursor?: string, limit = 50): Promise<UserPage> {
    return this.request<UserPage>('/admin/users', { query: { cursor, limit } })
  }

  deactivateUser(userId: string): Promise<UserResponse> {
    return this.request<UserResponse>(`/admin/users/${userId}/deactivate`, { method: 'POST' })
  }

  activateUser(userId: string): Promise<UserResponse> {
    return this.request<UserResponse>(`/admin/users/${userId}/activate`, { method: 'POST' })
  }

  getModelConfig(): Promise<ModelConfigResponse> {
    return this.request<ModelConfigResponse>('/admin/ai/model-config')
  }

  setModelConfig(body: SetModelConfigRequest): Promise<ModelConfigResponse> {
    return this.request<ModelConfigResponse>('/admin/ai/model-config', { method: 'PUT', body })
  }

  setBudget(body: SetBudgetRequest): Promise<BudgetResponse> {
    return this.request<BudgetResponse>('/admin/ai/budget', { method: 'PUT', body })
  }

  // --- collections & documents ---

  listCollections(): Promise<CollectionResponse[]> {
    return this.request<CollectionResponse[]>('/collections')
  }

  createCollection(body: CollectionCreate): Promise<CollectionResponse> {
    return this.request<CollectionResponse>('/collections', { method: 'POST', body })
  }

  deleteCollection(collectionId: string): Promise<void> {
    return this.request<void>(`/collections/${collectionId}`, { method: 'DELETE' })
  }

  listDocuments(collectionId: string): Promise<DocumentResponse[]> {
    return this.request<DocumentResponse[]>(`/collections/${collectionId}/documents`)
  }

  uploadDocument(collectionId: string, file: File): Promise<UploadAccepted> {
    const formData = new FormData()
    formData.append('file', file)
    return this.request<UploadAccepted>(`/collections/${collectionId}/documents`, {
      method: 'POST',
      body: formData,
    })
  }

  uploadDocumentVersion(documentId: string, file: File): Promise<UploadAccepted> {
    const formData = new FormData()
    formData.append('file', file)
    return this.request<UploadAccepted>(`/documents/${documentId}/versions`, {
      method: 'POST',
      body: formData,
    })
  }

  reindexDocument(documentId: string): Promise<void> {
    return this.request<void>(`/documents/${documentId}/reindex`, { method: 'POST' })
  }

  deleteDocument(documentId: string): Promise<void> {
    return this.request<void>(`/documents/${documentId}`, { method: 'DELETE' })
  }
}
