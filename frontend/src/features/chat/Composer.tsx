// Message composer. Enter sends (Shift+Enter for a newline); while a turn streams the send
// button becomes a Stop button (client-side cancel). Labeled textarea, disabled when no
// session is selected.

import { useState, type FormEvent, type KeyboardEvent } from 'react'

export function Composer({
  disabled,
  isStreaming,
  onSend,
  onStop,
}: {
  disabled: boolean
  isStreaming: boolean
  onSend: (content: string) => void
  onStop: () => void
}) {
  const [value, setValue] = useState('')

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const text = value.trim()
    if (!text || isStreaming) return
    onSend(text)
    setValue('')
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit(event)
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      <label htmlFor="composer-input" className="visually-hidden">
        Message
      </label>
      <textarea
        id="composer-input"
        placeholder="Ask about your documents…"
        value={value}
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
      />
      {isStreaming ? (
        <button type="button" className="btn" onClick={onStop}>
          Stop
        </button>
      ) : (
        <button type="submit" className="btn btn--primary" disabled={disabled || !value.trim()}>
          Send
        </button>
      )}
    </form>
  )
}
