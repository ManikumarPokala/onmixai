import { describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ApiClient } from './client'
import { ApiError } from './errors'

const BASE = '/api/v1'

function client(): ApiClient {
  const c = new ApiClient(BASE)
  c.setAccessToken('stale')
  return c
}

describe('ApiClient silent refresh', () => {
  it('refreshes once on a 401 and retries the original request', async () => {
    let attempts = 0
    server.use(
      http.get('*/api/v1/chat/sessions', ({ request }) => {
        attempts += 1
        const auth = request.headers.get('Authorization')
        if (auth !== 'Bearer fresh') {
          return HttpResponse.json(
            { error: { code: 'INVALID_TOKEN', message: 'x', request_id: 'r' } },
            { status: 401 },
          )
        }
        return HttpResponse.json({ sessions: [], next_cursor: null })
      }),
    )
    const c = client()
    const refresh = vi.fn(async () => {
      c.setAccessToken('fresh')
      return true
    })
    c.setRefreshHandler(refresh)

    const page = await c.listSessions()
    expect(page.sessions).toEqual([])
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(attempts).toBe(2) // original 401 + retry after refresh
  })

  it('throws the typed error when refresh fails', async () => {
    server.use(
      http.get('*/api/v1/chat/sessions', () =>
        HttpResponse.json(
          { error: { code: 'INVALID_TOKEN', message: 'x', request_id: 'r' } },
          { status: 401 },
        ),
      ),
    )
    const c = client()
    c.setRefreshHandler(async () => false)
    await expect(c.listSessions()).rejects.toMatchObject({ code: 'INVALID_TOKEN' })
  })

  it('surfaces a pre-stream 4xx from streamMessage as a typed ApiError', async () => {
    server.use(
      http.post('*/api/v1/chat/sessions/:id/messages', () =>
        HttpResponse.json(
          { error: { code: 'SESSION_ARCHIVED', message: 'x', request_id: 'r' } },
          { status: 409 },
        ),
      ),
    )
    const c = client()
    c.setAccessToken('ok')
    await expect(
      c.streamMessage('s1', 'hello', () => undefined),
    ).rejects.toBeInstanceOf(ApiError)
  })
})
