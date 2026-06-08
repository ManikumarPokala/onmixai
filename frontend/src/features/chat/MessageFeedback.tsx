// Thumbs up/down on an assistant message. Reflects the current rating (from the persisted
// message) and submits via the feedback mutation. Buttons are labeled and toggle-pressed for
// assistive tech.

import type { MessageResponse } from './api'
import { useFeedback } from './api'

export function MessageFeedback({
  message,
  sessionId,
}: {
  message: MessageResponse
  sessionId: string
}) {
  const feedback = useFeedback(sessionId)
  const current = message.feedback?.rating ?? null

  const submit = (rating: 'up' | 'down') => {
    feedback.mutate({ messageId: message.id, body: { rating, comment: null } })
  }

  return (
    <div className="msg-feedback" role="group" aria-label="Was this answer helpful?">
      <button
        type="button"
        className={current === 'up' ? 'is-selected' : ''}
        aria-pressed={current === 'up'}
        aria-label="Helpful"
        disabled={feedback.isPending}
        onClick={() => submit('up')}
      >
        👍
      </button>
      <button
        type="button"
        className={current === 'down' ? 'is-selected' : ''}
        aria-pressed={current === 'down'}
        aria-label="Not helpful"
        disabled={feedback.isPending}
        onClick={() => submit('down')}
      >
        👎
      </button>
    </div>
  )
}
