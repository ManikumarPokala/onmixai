// Persisted messages (the canonical server view). A user message is a plain bubble; an
// assistant message is either a cited answer (content + sources + feedback) or a typed refusal
// — never both (the cite-or-refuse invariant holds in storage).

import type { MessageResponse } from './api'
import { MessageContent, SourcesPanel } from './Citations'
import { MessageFeedback } from './MessageFeedback'
import { refusalCopy } from './refusal'

export function MessageList({
  messages,
  sessionId,
}: {
  messages: MessageResponse[]
  sessionId: string
}) {
  return (
    <>
      {messages.map((message) =>
        message.role === 'user' ? (
          <div key={message.id} className="bubble bubble--user">
            {message.content}
          </div>
        ) : message.refusal_reason ? (
          <div key={message.id} className="bubble bubble--refusal" role="note">
            <p>{refusalCopy(message.refusal_reason)}</p>
          </div>
        ) : (
          <div key={message.id} className="bubble bubble--assistant">
            <MessageContent text={message.content} citations={message.citations} />
            <SourcesPanel citations={message.citations} />
            <MessageFeedback message={message} sessionId={sessionId} />
          </div>
        ),
      )}
    </>
  )
}
