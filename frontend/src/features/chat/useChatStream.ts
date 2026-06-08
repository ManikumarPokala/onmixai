// The in-flight chat turn — a local state machine over the SSE protocol (ADR 0014). Persisted
// messages live in TanStack Query; this hook owns ONLY the turn currently streaming. Phases:
//
//   idle → streaming → done      (grounded answer; then the persisted message takes over)
//                    → refused   (terminal refusal SUPERSEDES the streamed text)
//                    → error     (infrastructure failure; re-askable, not persisted)
//   streaming → (stop) stopped   (client abort; server persists nothing)
//
// On done/refused the turn is finalized: the messages query is invalidated and, once the
// canonical message has loaded, the local turn is cleared (no duplicate, no flicker). An
// `error` or a `stop` keeps the turn local (nothing was persisted) so the user can retry.

import { useCallback, useRef, useState } from 'react'
import { apiClient, ApiError, type Citation } from '../../lib/api'

export type StreamPhase = 'streaming' | 'done' | 'refused' | 'error' | 'stopped'

export interface PendingTurn {
  userContent: string
  phase: StreamPhase
  text: string
  citations: Citation[]
  refusalReason: string | null
  errorCode: string | null
}

export interface ChatStream {
  turn: PendingTurn | null
  isStreaming: boolean
  send: (content: string) => Promise<void>
  stop: () => void
  retry: () => void
}

export function useChatStream(
  sessionId: string | undefined,
  onFinalized: () => Promise<unknown>,
): ChatStream {
  const [turn, setTurn] = useState<PendingTurn | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const lastContent = useRef<string>('')

  const send = useCallback(
    async (content: string) => {
      if (!sessionId) return
      lastContent.current = content
      const controller = new AbortController()
      abortRef.current = controller
      setTurn({
        userContent: content,
        phase: 'streaming',
        text: '',
        citations: [],
        refusalReason: null,
        errorCode: null,
      })

      try {
        await apiClient.streamMessage(
          sessionId,
          content,
          (event) => {
            switch (event.event) {
              case 'token':
                setTurn((t) => (t ? { ...t, text: t.text + event.text } : t))
                break
              case 'citations':
                setTurn((t) => (t ? { ...t, citations: event.items } : t))
                break
              case 'refusal':
                // SUPERSEDE: the streamed text is replaced by the refusal (ADR 0014).
                setTurn((t) =>
                  t ? { ...t, phase: 'refused', refusalReason: event.reason } : t,
                )
                break
              case 'done':
                setTurn((t) => (t ? { ...t, phase: 'done' } : t))
                break
              case 'error':
                setTurn((t) => (t ? { ...t, phase: 'error', errorCode: event.code } : t))
                break
              // 'meta' carries no visible change
            }
          },
          controller.signal,
        )
      } catch (err) {
        if (controller.signal.aborted) return // stop() already handled the turn
        const code = err instanceof ApiError ? err.code : 'INTERNAL_ERROR'
        setTurn((t) => (t ? { ...t, phase: 'error', errorCode: code } : t))
        return
      }

      // The stream closed. A content terminal (done/refused) is persisted server-side:
      // load the canonical message, then drop the local turn. error/stopped stay local.
      setTurn((current) => {
        if (current && (current.phase === 'done' || current.phase === 'refused')) {
          void onFinalized().then(() => setTurn(null))
        }
        return current
      })
    },
    [sessionId, onFinalized],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
    // Freeze whatever streamed so far; the server persisted nothing (re-askable).
    setTurn((t) => (t && t.phase === 'streaming' ? { ...t, phase: 'stopped' } : t))
  }, [])

  const retry = useCallback(() => {
    void send(lastContent.current)
  }, [send])

  return { turn, isStreaming: turn?.phase === 'streaming', send, stop, retry }
}
