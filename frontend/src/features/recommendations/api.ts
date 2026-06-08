// Recommendation data hooks (TanStack Query). Generation is synchronous (the POST returns the
// completed-or-declined recommendation), so creation is a mutation; the list is a query.

import { useMutation, useQuery } from '@tanstack/react-query'
import { apiClient } from '../../lib/api'
import type { CreateRecommendationRequest } from '../../lib/api'

export const recommendationKeys = {
  list: ['recommendations'] as const,
}

export function useCreateRecommendation() {
  return useMutation({
    mutationFn: (body: CreateRecommendationRequest) => apiClient.createRecommendation(body),
  })
}

export function useRecommendations() {
  return useQuery({
    queryKey: recommendationKeys.list,
    queryFn: () => apiClient.listRecommendations(),
  })
}
