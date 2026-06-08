import { describe, expect, it } from 'vitest'
import {
  apiErrorFromEnvelope,
  ApiError,
  ERROR_CODES,
  ERROR_MESSAGES,
  humanMessage,
} from './errors'
// The canonical backend code set, emitted by backend/scripts/dump_error_codes.py and kept in
// lockstep by CI (the error-codes-sync job). This makes completeness a GATE: a new backend
// error code with no frontend mapping fails here, never shipping as a generic-fallback UI.
import backendErrorCodes from '../../../error-codes.json'

describe('error code → human message map', () => {
  it('maps every backend error code — and carries no dead codes (lockstep with the backend)', () => {
    const frontend = new Set<string>(ERROR_CODES)
    const backend = new Set<string>(backendErrorCodes)
    const unmapped = [...backend].filter((c) => !frontend.has(c)) // backend code, no UI message
    const dead = [...frontend].filter((c) => !backend.has(c)) // UI message, no backend code
    expect(unmapped, 'backend codes missing a frontend message').toEqual([])
    expect(dead, 'frontend codes with no backend counterpart').toEqual([])
  })

  it('renders a non-empty human message for every typed backend code (exhaustive)', () => {
    for (const code of ERROR_CODES) {
      const message = ERROR_MESSAGES[code]
      expect(message, code).toBeTruthy()
      expect(message.length).toBeGreaterThan(5)
      expect(message, code).not.toContain('_') // no raw code leaked into copy
    }
  })

  it('falls back to a generic message for an unknown code', () => {
    expect(humanMessage('SOMETHING_NEW')).toBe('Something went wrong. Please try again.')
  })

  it('builds an ApiError from a backend envelope', () => {
    const err = apiErrorFromEnvelope(404, {
      error: { code: 'SESSION_NOT_FOUND', message: 'x', request_id: 'req-9' },
    })
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe('SESSION_NOT_FOUND')
    expect(err.status).toBe(404)
    expect(err.requestId).toBe('req-9')
    expect(err.message).toBe(ERROR_MESSAGES.SESSION_NOT_FOUND)
  })

  it('degrades to INTERNAL_ERROR when the body is not an envelope', () => {
    const err = apiErrorFromEnvelope(500, 'not json')
    expect(err.code).toBe('INTERNAL_ERROR')
  })
})
