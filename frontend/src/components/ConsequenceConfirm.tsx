// A blocking confirm dialog for consequential actions (destructive admin operations, disabling
// PII redaction). Accessible: role="dialog", aria-modal, labelled, Escape cancels, the confirm
// button takes focus on open so the action is never a single stray click.

import { useEffect, useRef } from 'react'

export interface ConsequenceConfirmProps {
  open: boolean
  title: string
  message: string
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
}

export function ConsequenceConfirm({
  open,
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
}: ConsequenceConfirmProps) {
  const confirmRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (open) confirmRef.current?.focus()
  }, [open])
  if (!open) return null
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onCancel()
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="consequence-title">
        <h2 id="consequence-title">{title}</h2>
        <p>{message}</p>
        <div className="modal__actions">
          <button type="button" className="btn btn--ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn btn--danger" ref={confirmRef} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
