import { useState, useEffect, useRef } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth/useAuth'

export function AppShell() {
  const { orgSlug, role, isAdmin, logout } = useAuth()
  const navigate = useNavigate()
  
  // Navigation drawer state
  const [drawerOpen, setDrawerOpen] = useState(false)
  // Profile menu dropdown state
  const [profileOpen, setProfileOpen] = useState(false)

  const dropdownRef = useRef<HTMLDivElement>(null)

  // Handle outside clicks to close the profile dropdown
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setProfileOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Close drawer on Escape key press
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setDrawerOpen(false)
        setProfileOpen(false)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  async function onLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-shell">
      {/* Platform Header */}
      <header className="app-header">
        <button
          type="button"
          className="btn btn--ghost hamburger-btn"
          onClick={() => setDrawerOpen(true)}
          aria-label="Open Navigation Menu"
          style={{ fontSize: '1.25rem', padding: '0.25rem 0.5rem', display: 'flex', alignItems: 'center' }}
        >
          ☰
        </button>
        
        <span
          className="app-brand"
          style={{ cursor: 'pointer', fontSize: '1.1rem', fontWeight: 700 }}
          onClick={() => navigate('/home')}
        >
          OnMixAI
        </span>

        {/* Profile/User Menu on Header Right */}
        <div className="app-header__end" ref={dropdownRef} style={{ position: 'relative' }}>
          <button
            type="button"
            className="btn btn--ghost profile-trigger"
            onClick={() => setProfileOpen(!profileOpen)}
            aria-label="User profile menu"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              borderRadius: '50%',
              width: '32px',
              height: '32px',
              padding: 0,
              justifyContent: 'center',
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              fontWeight: 'bold',
              color: 'var(--primary)',
              textTransform: 'uppercase'
            }}
          >
            {orgSlug ? orgSlug[0] : 'U'}
          </button>

          {/* Profile Dropdown Menu */}
          {profileOpen && (
            <div
              className="profile-dropdown"
              style={{
                position: 'absolute',
                top: '40px',
                right: 0,
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                padding: '0.75rem',
                minWidth: '200px',
                boxShadow: '0 8px 30px rgba(0,0,0,0.3)',
                zIndex: 100
              }}
            >
              <div style={{ padding: '0.25rem 0.5rem 0.5rem 0.5rem', borderBottom: '1px solid var(--border)', marginBottom: '0.5rem' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  Signed in as
                </div>
                <div style={{ fontWeight: 600, fontSize: '0.9rem', color: 'var(--text)' }}>
                  {orgSlug}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--primary)', textTransform: 'capitalize', marginTop: '0.15rem' }}>
                  {role} Role
                </div>
              </div>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => {
                  setProfileOpen(false)
                  navigate('/settings')
                }}
                style={{ width: '100%', textAlign: 'left', border: 0, padding: '0.4rem 0.5rem', borderRadius: '6px', fontSize: '0.85rem' }}
              >
                Settings
              </button>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => {
                  setProfileOpen(false)
                  navigate('/admin')
                }}
                style={{ width: '100%', textAlign: 'left', border: 0, padding: '0.4rem 0.5rem', borderRadius: '6px', fontSize: '0.85rem', display: isAdmin ? 'block' : 'none' }}
              >
                Administration
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--danger-ghost"
                onClick={() => {
                  setProfileOpen(false)
                  onLogout()
                }}
                style={{ width: '100%', textAlign: 'left', border: 0, padding: '0.4rem 0.5rem', borderRadius: '6px', fontSize: '0.85rem', marginTop: '0.25rem', color: 'var(--danger)' }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Hamburger Navigation Drawer Overlay */}
      {drawerOpen && (
        <div
          className="drawer-backdrop"
          onClick={() => setDrawerOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.4)',
            zIndex: 900,
            backdropFilter: 'blur(2px)'
          }}
        >
          {/* Drawer Panel */}
          <div
            className="drawer-panel"
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              bottom: 0,
              width: '280px',
              background: 'var(--surface)',
              borderRight: '1px solid var(--border)',
              display: 'flex',
              flexDirection: 'column',
              padding: '1.25rem',
              boxShadow: '10px 0 30px rgba(0, 0, 0, 0.3)'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
              <span style={{ fontSize: '1.1rem', fontWeight: 700 }}>Menu</span>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => setDrawerOpen(false)}
                style={{ padding: '0.25rem 0.5rem', fontSize: '1.1rem', border: 0 }}
                aria-label="Close menu"
              >
                ✕
              </button>
            </div>

            <nav className="drawer-nav" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <NavLink
                to="/home"
                onClick={() => setDrawerOpen(false)}
                className={({ isActive }) => `drawer-nav-item ${isActive ? 'is-active' : ''}`}
              >
                Home
              </NavLink>
              <NavLink
                to="/chat"
                onClick={() => setDrawerOpen(false)}
                className={({ isActive }) => `drawer-nav-item ${isActive ? 'is-active' : ''}`}
              >
                Chat
              </NavLink>
              <NavLink
                to="/recommendations"
                onClick={() => setDrawerOpen(false)}
                className={({ isActive }) => `drawer-nav-item ${isActive ? 'is-active' : ''}`}
              >
                Recommendations
              </NavLink>
              <NavLink
                to="/reports"
                onClick={() => setDrawerOpen(false)}
                className={({ isActive }) => `drawer-nav-item ${isActive ? 'is-active' : ''}`}
              >
                Reports
              </NavLink>
              <NavLink
                to="/engineering"
                onClick={() => setDrawerOpen(false)}
                className={({ isActive }) => `drawer-nav-item ${isActive ? 'is-active' : ''}`}
              >
                Engineering Hub
              </NavLink>
              {isAdmin && (
                <NavLink
                  to="/admin"
                  onClick={() => setDrawerOpen(false)}
                  className={({ isActive }) => `drawer-nav-item ${isActive ? 'is-active' : ''}`}
                >
                  Admin
                </NavLink>
              )}
              <NavLink
                to="/settings"
                onClick={() => setDrawerOpen(false)}
                className={({ isActive }) => `drawer-nav-item ${isActive ? 'is-active' : ''}`}
              >
                Settings
              </NavLink>
            </nav>

            <div style={{ marginTop: 'auto', paddingTop: '1.5rem', borderTop: '1px solid var(--border)', fontSize: '0.85rem', color: 'var(--text-dim)' }}>
              <div>Workspace: <strong>{orgSlug}</strong></div>
              <div style={{ textTransform: 'capitalize' }}>Role: <strong>{role}</strong></div>
            </div>
          </div>
        </div>
      )}

      {/* Main Routed Content Page */}
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
