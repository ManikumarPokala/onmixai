// Route table. /login is public; everything under the shell requires auth. The index
// redirects to /chat. Chat is the focus of Phase 4; Documents is a placeholder.

import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell, DocumentsPlaceholder } from '../components/AppShell'
import { LoginPage } from '../features/auth/LoginPage'
import { ChatPage } from '../features/chat/ChatPage'
import { RecommendationsPage } from '../features/recommendations/RecommendationsPage'
import { ReportsPage } from '../features/reports/ReportsPage'
import { RequireAuth } from '../lib/auth/RequireAuth'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:sessionId" element={<ChatPage />} />
        <Route path="/recommendations" element={<RecommendationsPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/documents" element={<DocumentsPlaceholder />} />
      </Route>
      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  )
}
