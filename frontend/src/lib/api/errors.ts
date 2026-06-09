// Typed mirror of the backend error envelope (`{"error": {code, message, request_id}}`)
// and a human-message map. CLAUDE.md §10: every async UI handles error explicitly; the
// toast layer renders these codes. The map is keyed by a closed union, so adding a backend
// code without a frontend message is a compile error (exhaustiveness is enforced by the
// type, and asserted at runtime in errors.test.ts).

export const ERROR_CODES = [
  // identity / auth
  'MISSING_TOKEN',
  'INVALID_TOKEN',
  'INVALID_CREDENTIALS',
  'INVALID_REFRESH_TOKEN',
  'REFRESH_TOKEN_REUSED',
  'ORGANIZATION_NOT_FOUND',
  'ORG_SLUG_TAKEN',
  'WEAK_PASSWORD',
  'USER_NOT_FOUND',
  'FORBIDDEN',
  // shared
  'RATE_LIMITED',
  'VALIDATION_ERROR',
  'INTERNAL_ERROR',
  // knowledge
  'COLLECTION_NOT_FOUND',
  'COLLECTION_NAME_TAKEN',
  'COLLECTION_NOT_EMPTY',
  'COLLECTION_ACCESS_DENIED',
  'DOCUMENT_NOT_FOUND',
  'DOCUMENT_PROCESSING',
  'DOCUMENT_QUOTA_EXCEEDED',
  'UNSUPPORTED_FORMAT',
  'UPLOAD_TOO_LARGE',
  'INVALID_STATUS_TRANSITION',
  // search
  'INVALID_SEARCH_FILTER',
  // ai gateway
  'UPSTREAM_UNAVAILABLE',
  'UPSTREAM_REJECTED',
  'BUDGET_EXCEEDED',
  'INVALID_MODEL_CONFIG',
  'GUARDRAIL_VIOLATION',
  'SCHEMA_VALIDATION_FAILED',
  'OUTPUT_SCHEMA_VIOLATION',
  // conversation
  'SESSION_NOT_FOUND',
  'SESSION_ARCHIVED',
  'SESSION_LIMIT_EXCEEDED',
  'MESSAGE_TOO_LONG',
  'MESSAGE_EMPTY',
  'MESSAGE_NOT_FOUND',
  'INVALID_CURSOR',
  // recommendation / reports
  'RECOMMENDATION_NOT_FOUND',
  'REPORT_NOT_FOUND',
  'EXPORT_NOT_FOUND',
] as const

export type ErrorCode = (typeof ERROR_CODES)[number]

export const ERROR_MESSAGES: Record<ErrorCode, string> = {
  MISSING_TOKEN: 'Please sign in to continue.',
  INVALID_TOKEN: 'Your session has expired. Please sign in again.',
  INVALID_CREDENTIALS: 'The email or password is incorrect.',
  INVALID_REFRESH_TOKEN: 'Your session could not be renewed. Please sign in again.',
  REFRESH_TOKEN_REUSED: 'For your security we signed you out. Please sign in again.',
  ORGANIZATION_NOT_FOUND: 'We could not find that organization.',
  ORG_SLUG_TAKEN: 'That organization slug is already taken. Please choose another.',
  WEAK_PASSWORD: 'Password must be at least 12 characters.',
  USER_NOT_FOUND: 'That user could not be found.',
  FORBIDDEN: 'You do not have permission to do that.',
  RATE_LIMITED: 'You are going a little fast — please wait a moment and try again.',
  VALIDATION_ERROR: 'Some of the information provided was invalid.',
  INTERNAL_ERROR: 'Something went wrong on our end. Please try again.',
  COLLECTION_NOT_FOUND: 'That collection could not be found.',
  COLLECTION_NAME_TAKEN: 'A collection with that name already exists.',
  COLLECTION_NOT_EMPTY: 'Remove the documents in this collection before deleting it.',
  COLLECTION_ACCESS_DENIED: 'You do not have access to that collection.',
  DOCUMENT_NOT_FOUND: 'That document could not be found.',
  DOCUMENT_PROCESSING: 'This document is still being processed. Please try again shortly.',
  DOCUMENT_QUOTA_EXCEEDED: 'You have reached your document limit.',
  UNSUPPORTED_FORMAT: 'That file format is not supported.',
  UPLOAD_TOO_LARGE: 'That file is too large to upload.',
  INVALID_STATUS_TRANSITION: 'That action is not allowed in the document’s current state.',
  INVALID_SEARCH_FILTER: 'One of the search filters was invalid.',
  UPSTREAM_UNAVAILABLE: 'The AI service is temporarily unavailable. Please try again.',
  UPSTREAM_REJECTED: 'The AI service could not process that request.',
  BUDGET_EXCEEDED: 'Your organization has reached its usage limit for this period.',
  INVALID_MODEL_CONFIG: 'That model configuration is invalid. Check the model names and try again.',
  GUARDRAIL_VIOLATION: 'That request could not be processed for safety reasons.',
  SCHEMA_VALIDATION_FAILED: 'The AI returned an unexpected response. Please try again.',
  OUTPUT_SCHEMA_VIOLATION: 'The AI returned a response in an unexpected format. Please try again.',
  SESSION_NOT_FOUND: 'That chat could not be found.',
  SESSION_ARCHIVED: 'This chat is archived. Restore it to continue the conversation.',
  SESSION_LIMIT_EXCEEDED: 'You have reached the maximum number of chats.',
  MESSAGE_TOO_LONG: 'That message is too long. Please shorten it.',
  MESSAGE_EMPTY: 'Please type a message before sending.',
  MESSAGE_NOT_FOUND: 'That message could not be found.',
  INVALID_CURSOR: 'The page could not be loaded. Please refresh.',
  RECOMMENDATION_NOT_FOUND: 'That recommendation could not be found.',
  REPORT_NOT_FOUND: 'That report could not be found.',
  EXPORT_NOT_FOUND: 'That export could not be found.',
}

const GENERIC_MESSAGE = 'Something went wrong. Please try again.'

export interface ErrorEnvelope {
  code: string
  message: string
  request_id?: string
}

/** A typed transport/domain error carrying the backend envelope (or a synthetic one). */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly requestId?: string

  constructor(code: string, status: number, message?: string, requestId?: string) {
    super(message ?? humanMessage(code))
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.requestId = requestId
  }
}

function isErrorCode(code: string): code is ErrorCode {
  return (ERROR_CODES as readonly string[]).includes(code)
}

/** The human message for a backend code, falling back to a generic message for the unknown. */
export function humanMessage(code: string): string {
  return isErrorCode(code) ? ERROR_MESSAGES[code] : GENERIC_MESSAGE
}

/** Build an ApiError from a parsed envelope (or a generic one when the body is unusable). */
export function apiErrorFromEnvelope(status: number, body: unknown): ApiError {
  if (
    typeof body === 'object' &&
    body !== null &&
    'error' in body &&
    typeof (body as { error: unknown }).error === 'object' &&
    (body as { error: unknown }).error !== null
  ) {
    const env = (body as { error: ErrorEnvelope }).error
    return new ApiError(env.code, status, humanMessage(env.code), env.request_id)
  }
  return new ApiError('INTERNAL_ERROR', status)
}
