// Chat data hooks (TanStack Query). Server state only — sessions and persisted messages are
// queried/mutated here; the in-flight streaming turn is local state (useChatStream). Mutations
// invalidate the relevant queries so the UI reflects the canonical server view.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../lib/api'
import type { FeedbackRequest, MessageResponse, SessionResponse } from '../../lib/api'

export const chatKeys = {
  sessions: ['sessions'] as const,
  messages: (sessionId: string) => ['messages', sessionId] as const,
}

export function useSessions() {
  return useQuery({
    queryKey: chatKeys.sessions,
    queryFn: () => apiClient.listSessions(),
  })
}

export function useMessages(sessionId: string | undefined) {
  return useQuery({
    queryKey: chatKeys.messages(sessionId ?? ''),
    queryFn: () => apiClient.listMessages(sessionId as string),
    enabled: Boolean(sessionId),
  })
}

export function useCreateSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (title?: string) => apiClient.createSession(title),
    onSuccess: (session: SessionResponse) => {
      qc.invalidateQueries({ queryKey: chatKeys.sessions })
      return session
    },
  })
}

export function useUpdateSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      title,
      isArchived,
    }: {
      id: string
      title?: string
      isArchived?: boolean
    }) => apiClient.updateSession(id, { title, is_archived: isArchived }),
    onSuccess: () => qc.invalidateQueries({ queryKey: chatKeys.sessions }),
  })
}

export function useDeleteSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiClient.deleteSession(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: chatKeys.sessions }),
  })
}

export function useFeedback(sessionId: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ messageId, body }: { messageId: string; body: FeedbackRequest }) =>
      apiClient.submitFeedback(messageId, body),
    onSuccess: () => {
      if (sessionId) qc.invalidateQueries({ queryKey: chatKeys.messages(sessionId) })
    },
  })
}

export type { MessageResponse, SessionResponse }
