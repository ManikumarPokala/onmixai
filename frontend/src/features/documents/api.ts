import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../lib/api'
import type { CollectionCreate } from '../../lib/api'

export const documentKeys = {
  collections: ['collections'] as const,
  documents: (collectionId: string) => ['documents', collectionId] as const,
}

export function useCollections() {
  return useQuery({
    queryKey: documentKeys.collections,
    queryFn: () => apiClient.listCollections(),
  })
}

export function useCreateCollection() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CollectionCreate) => apiClient.createCollection(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: documentKeys.collections }),
  })
}

export function useDeleteCollection() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (collectionId: string) => apiClient.deleteCollection(collectionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: documentKeys.collections }),
  })
}

export function useDocuments(collectionId: string) {
  return useQuery({
    queryKey: documentKeys.documents(collectionId),
    queryFn: () => apiClient.listDocuments(collectionId),
    enabled: !!collectionId,
    refetchInterval: (query) => {
      const docs = query.state.data
      if (docs && Array.isArray(docs)) {
        const hasProcessing = docs.some(
          (d) => d.status === 'queued' || d.status === 'processing'
        )
        return hasProcessing ? 3000 : false
      }
      return false
    },
  })
}

export function useUploadDocument(collectionId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ file }: { file: File }) => apiClient.uploadDocument(collectionId, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: documentKeys.documents(collectionId) }),
  })
}

export function useUploadDocumentVersion(collectionId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ documentId, file }: { documentId: string; file: File }) =>
      apiClient.uploadDocumentVersion(documentId, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: documentKeys.documents(collectionId) }),
  })
}

export function useReindexDocument(collectionId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (documentId: string) => apiClient.reindexDocument(documentId),
    onSuccess: () => qc.invalidateQueries({ queryKey: documentKeys.documents(collectionId) }),
  })
}

export function useDeleteDocument(collectionId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (documentId: string) => apiClient.deleteDocument(documentId),
    onSuccess: () => qc.invalidateQueries({ queryKey: documentKeys.documents(collectionId) }),
  })
}
