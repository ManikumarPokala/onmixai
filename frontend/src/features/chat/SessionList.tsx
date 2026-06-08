// Session sidebar: create, open, rename, archive, delete. Keyboard-complete (every action is
// a real button/input). Loading and empty are explicit states.

import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  useCreateSession,
  useDeleteSession,
  useSessions,
  useUpdateSession,
  type SessionResponse,
} from './api'

export function SessionList() {
  const navigate = useNavigate()
  const { sessionId } = useParams()
  const { data, isLoading } = useSessions()
  const create = useCreateSession()

  const onNew = async () => {
    const session = await create.mutateAsync(undefined)
    navigate(`/chat/${session.id}`)
  }

  return (
    <aside className="session-list" aria-label="Chats">
      <div className="session-list__head">
        <strong>Chats</strong>
        <button type="button" className="btn btn--primary" onClick={onNew} disabled={create.isPending}>
          New chat
        </button>
      </div>
      {isLoading ? (
        <div className="session-list__items" aria-busy="true">
          <div className="skeleton" style={{ margin: '0.5rem' }} />
          <div className="skeleton" style={{ margin: '0.5rem' }} />
        </div>
      ) : data && data.sessions.length > 0 ? (
        <ul className="session-list__items">
          {data.sessions.map((session) => (
            <SessionItem key={session.id} session={session} activeId={sessionId} />
          ))}
        </ul>
      ) : (
        <p className="empty-state">No chats yet. Start a new one.</p>
      )}
    </aside>
  )
}

function SessionItem({
  session,
  activeId,
}: {
  session: SessionResponse
  activeId: string | undefined
}) {
  const navigate = useNavigate()
  const update = useUpdateSession()
  const remove = useDeleteSession()
  const [renaming, setRenaming] = useState(false)
  const [title, setTitle] = useState(session.title ?? '')
  const isActive = session.id === activeId
  const label = session.title || 'New chat'

  const submitRename = () => {
    update.mutate({ id: session.id, title: title.trim() || 'New chat' })
    setRenaming(false)
  }

  const onDelete = () => {
    remove.mutate(session.id)
    if (isActive) navigate('/chat')
  }

  return (
    <li className={`session-item ${isActive ? 'is-active' : ''}`}>
      {renaming ? (
        <input
          aria-label="Rename chat"
          value={title}
          autoFocus
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submitRename()}
          onBlur={submitRename}
        />
      ) : (
        <button
          type="button"
          className="session-item__open"
          aria-current={isActive ? 'page' : undefined}
          onClick={() => navigate(`/chat/${session.id}`)}
        >
          {label}
          {session.is_archived && ' (archived)'}
        </button>
      )}
      <button
        type="button"
        className="session-item__menu"
        aria-label={`Rename ${label}`}
        onClick={() => setRenaming(true)}
      >
        ✎
      </button>
      <button
        type="button"
        className="session-item__menu"
        aria-label={`${session.is_archived ? 'Unarchive' : 'Archive'} ${label}`}
        onClick={() => update.mutate({ id: session.id, isArchived: !session.is_archived })}
      >
        🗄
      </button>
      <button
        type="button"
        className="session-item__menu"
        aria-label={`Delete ${label}`}
        onClick={onDelete}
      >
        🗑
      </button>
    </li>
  )
}
