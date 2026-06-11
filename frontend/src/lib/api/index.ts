// The single shared API client instance (CLAUDE.md §10: one typed client). AuthProvider
// configures its token + refresh handler; data hooks import it for queries/mutations.

import { API_BASE } from '../config'
import { ApiClient } from './client'

export const apiClient = new ApiClient(API_BASE)

export { ApiClient } from './client'
export type {
  SessionResponse,
  SessionPage,
  MessagePage,
  MessageResponse,
  FeedbackRequest,
  RegisterRequest,
  RegisterResponse,
  RecommendationResponse,
  RecommendationPage,
  CreateRecommendationRequest,
  ReportResponse,
  ReportPage,
  CreateReportRequest,
  ExportResponse,
  UserPage,
  UserResponse,
  ModelConfigResponse,
  SetModelConfigRequest,
  BudgetResponse,
  SetBudgetRequest,
  CollectionCreate,
  CollectionResponse,
  DocumentResponse,
  DocumentStatus,
  UploadAccepted,
} from './client'
export { ApiError, humanMessage } from './errors'
export type { ErrorCode } from './errors'
export type { ChatStreamEvent, Citation } from './sse'
