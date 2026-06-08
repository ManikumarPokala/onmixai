// Wire types + parser for the chat SSE protocol (backend conversation/schemas.py, ADR 0014).
// These events are not in the OpenAPI schema (the endpoint streams text/event-stream), so
// they are declared here as the typed contract. A terminal `refusal` supersedes streamed
// `token` text; an `error` is an infrastructure failure (no assistant message persisted).

import type { components } from './schema'

export type Citation = components['schemas']['Citation']

export interface MetaEvent {
  event: 'meta'
  message_id: string
  seq: number
}
export interface TokenEvent {
  event: 'token'
  text: string
}
export interface CitationsEvent {
  event: 'citations'
  items: Citation[]
}
export interface DoneEvent {
  event: 'done'
  message_id: string
  prompt_version: string | null
  trace_id: string | null
}
export interface RefusalEvent {
  event: 'refusal'
  reason: string
}
export interface ChatErrorEvent {
  event: 'error'
  code: string
}

export type ChatStreamEvent =
  | MetaEvent
  | TokenEvent
  | CitationsEvent
  | DoneEvent
  | RefusalEvent
  | ChatErrorEvent

/**
 * Parse a `text/event-stream` body into typed chat events. Comment lines (`:` heartbeats)
 * are skipped. Frames are split on the blank-line delimiter; a partial trailing frame is
 * buffered until its terminator arrives. Unknown event names are ignored (forward-compat).
 */
export async function* parseChatStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<ChatStreamEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let split = buffer.indexOf('\n\n')
      while (split !== -1) {
        const frame = buffer.slice(0, split)
        buffer = buffer.slice(split + 2)
        const parsed = parseFrame(frame)
        if (parsed) yield parsed
        split = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
}

function parseFrame(frame: string): ChatStreamEvent | null {
  let name = ''
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith(':') || line.trim() === '') continue
    if (line.startsWith('event:')) name = line.slice('event:'.length).trim()
    else if (line.startsWith('data:')) data = line.slice('data:'.length).trim()
  }
  if (!name || !data) return null
  try {
    return { event: name, ...JSON.parse(data) } as ChatStreamEvent
  } catch {
    return null
  }
}
