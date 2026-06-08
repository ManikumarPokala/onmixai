// Chat page: the session sidebar + the conversation pane. With no session selected, the pane
// shows a prompt to start or pick a chat.

import { useParams } from 'react-router-dom'
import { ChatView } from './ChatView'
import { SessionList } from './SessionList'

export function ChatPage() {
  const { sessionId } = useParams()
  return (
    <div className="chat-layout">
      <SessionList />
      {sessionId ? (
        <ChatView sessionId={sessionId} />
      ) : (
        <section className="chat-view" aria-label="Conversation">
          <div className="empty-state" style={{ margin: 'auto' }}>
            <h2>Select a chat</h2>
            <p>Pick a conversation on the left, or start a new one.</p>
          </div>
        </section>
      )}
    </div>
  )
}
