// Route table. /login is public; everything under the shell requires auth. The index
// redirects to /chat. Chat is the focus of Phase 4; Documents is a placeholder.

import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '../components/AppShell'
import { AdminPage } from '../features/admin/AdminPage'
import { LoginPage } from '../features/auth/LoginPage'
import { ChatPage } from '../features/chat/ChatPage'
import { RecommendationsPage } from '../features/recommendations/RecommendationsPage'
import { ReportsPage } from '../features/reports/ReportsPage'
import { DocumentsPage } from '../features/documents/DocumentsPage'
import { HomePage } from '../features/home/HomePage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { EngineeringHubPage } from '../features/engineering/EngineeringHubPage'
import { RequireAdmin } from '../lib/auth/RequireAdmin'
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
        <Route index element={<Navigate to="/home" replace />} />
        <Route path="/home" element={<HomePage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:sessionId" element={<ChatPage />} />
        <Route path="/recommendations" element={<RecommendationsPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/engineering" element={<EngineeringHubPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <AdminPage />
            </RequireAdmin>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/home" replace />} />
    </Routes>
  )
}
