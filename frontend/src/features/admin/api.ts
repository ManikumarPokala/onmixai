// TanStack Query hooks for the admin console. Server state only — never mirrored into a store.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient, type SetModelConfigRequest } from '../../lib/api'

const USERS_KEY = ['admin', 'users']
const MODEL_CONFIG_KEY = ['admin', 'model-config']

export function useAdminUsers() {
  return useQuery({ queryKey: USERS_KEY, queryFn: () => apiClient.listUsers() })
}

export function useDeactivateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) => apiClient.deactivateUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: USERS_KEY }),
  })
}

export function useModelConfig() {
  return useQuery({ queryKey: MODEL_CONFIG_KEY, queryFn: () => apiClient.getModelConfig() })
}

export function useSetModelConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SetModelConfigRequest) => apiClient.setModelConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: MODEL_CONFIG_KEY }),
  })
}
