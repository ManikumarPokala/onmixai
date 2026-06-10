// Authenticated app layout: a nav rail (Chat / Documents placeholder) + the routed outlet.
// Keyboard-navigable links; the org slug and a logout control sit in the header.

import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth/useAuth'

export function AppShell() {
  const { orgSlug, isAdmin, logout } = useAuth()
  const navigate = useNavigate()

  async function onLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-brand">OnMixAI</span>
        <nav className="app-nav" aria-label="Primary">
          <NavLink to="/chat" className={({ isActive }) => (isActive ? 'is-active' : '')}>
            Chat
          </NavLink>
          <NavLink to="/recommendations" className={({ isActive }) => (isActive ? 'is-active' : '')}>
            Recommendations
          </NavLink>
          <NavLink to="/reports" className={({ isActive }) => (isActive ? 'is-active' : '')}>
            Reports
          </NavLink>
          <NavLink to="/documents" className={({ isActive }) => (isActive ? 'is-active' : '')}>
            Documents
          </NavLink>
          {isAdmin && (
            <NavLink to="/admin" className={({ isActive }) => (isActive ? 'is-active' : '')}>
              Admin
            </NavLink>
          )}
        </nav>
        <div className="app-header__end">
          {orgSlug && <span className="app-org">{orgSlug}</span>}
          <button type="button" className="btn btn--ghost" onClick={onLogout}>
            Sign out
          </button>
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}

/** Placeholder route until the Documents feature lands (Phase 5). */
export function DocumentsPlaceholder() {
  return (
    <div className="placeholder">
      <h1>Documents</h1>
      <p>Document management arrives in a later phase.</p>
    </div>
  )
}
