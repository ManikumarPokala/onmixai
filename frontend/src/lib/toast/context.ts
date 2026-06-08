import { createContext } from 'react'

export type ToastVariant = 'error' | 'success' | 'info'

export interface Toast {
  id: number
  message: string
  variant: ToastVariant
}

export interface ToastContextValue {
  toasts: Toast[]
  notify: (message: string, variant?: ToastVariant) => void
  dismiss: (id: number) => void
}

export const ToastContext = createContext<ToastContextValue | null>(null)
