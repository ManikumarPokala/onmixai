// The in-flight turn (local state from useChatStream). Renders the user's message plus the
// assistant side as it streams and terminates:
//   streaming → live tokens in an aria-live region
//   refused   → the streamed text is REPLACED by the refusal state (ADR 0014 supersede)
//   error     → an infrastructure error with a retry (nothing was persisted)
//   stopped   → the partial text, frozen (server persisted nothing)

import type { PendingTurn } from './useChatStream'
import { MessageContent, SourcesPanel } from './Citations'
import { humanMessage } from '../../lib/api'
import { refusalCopy } from './refusal'

export function PendingTurnView({ turn, onRetry }: { turn: PendingTurn; onRetry: () => void }) {
  return (
    <>
      <div className="bubble bubble--user">{turn.userContent}</div>
      {turn.phase === 'refused' ? (
        <div className="bubble bubble--refusal" role="note">
          <p>{refusalCopy(turn.refusalReason ?? '')}</p>
        </div>
      ) : turn.phase === 'error' ? (
        <div className="bubble bubble--error" role="alert">
          <p>{humanMessage(turn.errorCode ?? 'INTERNAL_ERROR')}</p>
          <button type="button" className="btn" onClick={onRetry}>
            Retry
          </button>
        </div>
      ) : (
        <div className="bubble bubble--assistant">
          <MessageContent text={turn.text} citations={turn.citations} />
          {turn.phase === 'streaming' && (
            <span className="visually-hidden" aria-live="polite">
              Assistant is responding
            </span>
          )}
          {turn.phase === 'stopped' && <p className="stopped-note">Stopped.</p>}
          <SourcesPanel citations={turn.citations} />
        </div>
      )}
    </>
  )
}
