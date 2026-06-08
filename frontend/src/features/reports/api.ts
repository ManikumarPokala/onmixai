// Report + export data hooks (TanStack Query). Generation + export are async, so the detail
// queries POLL while the row is queued/generating and stop once it reaches a terminal state.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../lib/api'
import type { CreateReportRequest, ExportResponse, ReportResponse } from '../../lib/api'

export const reportKeys = {
  list: ['reports'] as const,
  detail: (id: string) => ['report', id] as const,
  export: (reportId: string, exportId: string) => ['export', reportId, exportId] as const,
}

const POLL_MS = 1500
const stillRunning = (status: string | undefined): number | false =>
  status === 'queued' || status === 'generating' ? POLL_MS : false

export function useReports() {
  return useQuery({ queryKey: reportKeys.list, queryFn: () => apiClient.listReports() })
}

export function useCreateReport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateReportRequest) => apiClient.createReport(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: reportKeys.list }),
  })
}

export function useReport(id: string | undefined) {
  return useQuery({
    queryKey: reportKeys.detail(id ?? ''),
    queryFn: () => apiClient.getReport(id as string),
    enabled: Boolean(id),
    refetchInterval: (query) => stillRunning((query.state.data as ReportResponse | undefined)?.status),
  })
}

export function useCreateExport() {
  return useMutation({
    mutationFn: (reportId: string) => apiClient.createExport(reportId),
  })
}

export function useExport(reportId: string | undefined, exportId: string | undefined) {
  return useQuery({
    queryKey: reportKeys.export(reportId ?? '', exportId ?? ''),
    queryFn: () => apiClient.getExport(reportId as string, exportId as string),
    enabled: Boolean(reportId && exportId),
    refetchInterval: (query) => stillRunning((query.state.data as ExportResponse | undefined)?.status),
  })
}
