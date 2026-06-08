// The conversation pane for one session. Every async state is explicit (CLAUDE.md §10):
//   loading  → skeletons
//   error    → typed message + retry
//   empty    → a prompt to ask the first question
//   success / streaming → persisted messages + the in-flight turn
// On session switch the composer is focused (focus management).

import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { chatKeys, useMessages } from './api'
import { Composer } from './Composer'
import { MessageList } from './MessageList'
import { PendingTurnView } from './PendingTurn'
import { useChatStream } from './useChatStream'

export function ChatView({ sessionId }: { sessionId: string }) {
  const qc = useQueryClient()
  const { data, isLoading, isError, refetch } = useMessages(sessionId)
  const { turn, isStreaming, send, stop, retry } = useChatStream(sessionId, () =>
    qc.invalidateQueries({ queryKey: chatKeys.messages(sessionId) }),
  )
  const threadRef = useRef<HTMLDivElement>(null)

  // Focus the composer + scroll to the latest on session switch and as the turn grows.
  useEffect(() => {
    document.getElementById('composer-input')?.focus()
  }, [sessionId])
  useEffect(() => {
    threadRef.current?.scrollTo?.({ top: threadRef.current.scrollHeight })
  }, [data, turn])

  const messages = data?.messages ?? []
  const isEmpty = !isLoading && !isError && messages.length === 0 && turn === null

  return (
    <section className="chat-view" aria-label="Conversation">
      <div className="chat-thread" ref={threadRef}>
        {isLoading && (
          <div aria-busy="true" aria-label="Loading messages">
            <div className="skeleton" style={{ width: '60%' }} />
            <div className="skeleton" style={{ width: '80%', marginTop: '0.6rem' }} />
          </div>
        )}
        {isError && (
          <div className="bubble bubble--error" role="alert">
            <p>We couldn’t load this conversation.</p>
            <button type="button" className="btn" onClick={() => refetch()}>
              Retry
            </button>
          </div>
        )}
        {isEmpty && (
          <div className="empty-state">
            <h2>Ask about your documents</h2>
            <p>Answers are grounded in your sources, with citations — or a clear refusal.</p>
          </div>
        )}
        {!isLoading && !isError && <MessageList messages={messages} sessionId={sessionId} />}
        {turn && <PendingTurnView turn={turn} onRetry={retry} />}
      </div>
      <Composer disabled={false} isStreaming={isStreaming} onSend={send} onStop={stop} />
    </section>
  )
}
