import { useContext } from 'react'
import { ApiError } from '../api'
import { ToastContext, type ToastContextValue } from './context'

export interface ToastApi extends ToastContextValue {
  /** Toast a typed backend error (its already-humanized message), or a generic fallback. */
  notifyError: (error: unknown) => void
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (ctx === null) throw new Error('useToast must be used within a ToastProvider')
  const notifyError = (error: unknown) => {
    const message =
      error instanceof ApiError
        ? error.message
        : 'Something went wrong. Please try again.'
    ctx.notify(message, 'error')
  }
  return { ...ctx, notifyError }
}
