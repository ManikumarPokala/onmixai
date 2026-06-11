import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../lib/auth/useAuth'
import { useToast } from '../../lib/toast/useToast'

export function SettingsPage() {
  const { orgSlug, role, logout } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
    toast.notify('Signed out successfully.', 'success')
  }

  return (
    <div className="settings-page" style={{ padding: '2rem', maxWidth: '800px', margin: '0 auto' }}>
      <header style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '2rem', margin: '0 0 0.5rem 0', fontWeight: 700 }}>Settings</h1>
        <p style={{ color: 'var(--text-dim)', margin: 0 }}>
          Manage your account profile, workspace knowledge bases, and platform preferences.
        </p>
      </header>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        {/* Section 1: Profile */}
        <section aria-labelledby="profile-heading" style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          padding: '1.5rem'
        }}>
          <h2 id="profile-heading" style={{ fontSize: '1.2rem', margin: '0 0 1.25rem 0', fontWeight: 600 }}>
            User Profile
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: '0.75rem' }}>
              <span style={{ color: 'var(--text-dim)', fontSize: '0.9rem' }}>Active Organization</span>
              <strong style={{ fontSize: '0.95rem' }}>{orgSlug}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: '0.75rem' }}>
              <span style={{ color: 'var(--text-dim)', fontSize: '0.9rem' }}>Account Role</span>
              <strong style={{ fontSize: '0.95rem', textTransform: 'capitalize' }}>{role}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', paddingBottom: '0.25rem' }}>
              <span style={{ color: 'var(--text-dim)', fontSize: '0.9rem' }}>Session Status</span>
              <span style={{
                background: 'rgba(95, 208, 138, 0.1)',
                color: '#5fd08a',
                padding: '0.15rem 0.5rem',
                borderRadius: '6px',
                fontSize: '0.75rem',
                fontWeight: 700,
                textTransform: 'uppercase'
              }}>Active</span>
            </div>
          </div>
        </section>

        {/* Section 2: Knowledge & Documents */}
        <section aria-labelledby="knowledge-heading" style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          padding: '1.5rem'
        }}>
          <h2 id="knowledge-heading" style={{ fontSize: '1.2rem', margin: '0 0 0.5rem 0', fontWeight: 600 }}>
            Knowledge Management
          </h2>
          <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem', margin: '0 0 1.25rem 0' }}>
            Upload, version, and manage candidate resumes and job criteria in collections to ground AI evaluation answers.
          </p>
          <div style={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border)',
            padding: '1rem 1.25rem',
            borderRadius: '8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '1rem'
          }}>
            <div style={{ flex: 1 }}>
              <h4 style={{ margin: '0 0 0.25rem 0', fontSize: '0.95rem', fontWeight: 600 }}>Collections & Documents</h4>
              <p style={{ margin: 0, color: 'var(--text-dim)', fontSize: '0.8rem' }}>
                Access the RAG index controls, check document ingestion status, and trigger search re-indexing.
              </p>
            </div>
            <button
              type="button"
              className="btn btn--primary btn--small"
              onClick={() => navigate('/documents')}
              style={{ flexShrink: 0 }}
            >
              Manage Documents
            </button>
          </div>
        </section>

        {/* Section 3: Interface Preferences */}
        <section aria-labelledby="pref-heading" style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          padding: '1.5rem'
        }}>
          <h2 id="pref-heading" style={{ fontSize: '1.2rem', margin: '0 0 1.25rem 0', fontWeight: 600 }}>
            Preferences
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <h4 style={{ margin: '0 0 0.25rem 0', fontSize: '0.95rem', fontWeight: 600 }}>Platform Theme</h4>
                <p style={{ margin: 0, color: 'var(--text-dim)', fontSize: '0.8rem' }}>
                  Choose visual styling mode.
                </p>
              </div>
              <select disabled defaultValue="dark" style={{
                background: 'var(--surface-2)',
                border: '1px solid var(--border)',
                color: 'var(--text)',
                borderRadius: '8px',
                padding: '0.4rem 0.6rem',
                fontSize: '0.9rem'
              }}>
                <option value="dark">Dark (Premium Showcase)</option>
                <option value="light" disabled>Light (Not available)</option>
              </select>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: '1.25rem' }}>
              <div>
                <h4 style={{ margin: '0 0 0.25rem 0', fontSize: '0.95rem', fontWeight: 600 }}>Model Priority Routing</h4>
                <p style={{ margin: 0, color: 'var(--text-dim)', fontSize: '0.8rem' }}>
                  Fallback to GPT-4o chain on API faults.
                </p>
              </div>
              <label className="admin-toggle" style={{ margin: 0 }}>
                <input type="checkbox" defaultChecked disabled style={{ transform: 'scale(1.1)' }} />
                <span style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }}>Enabled</span>
              </label>
            </div>
          </div>
        </section>

        {/* Section 4: System Actions */}
        <section style={{ display: 'flex', gap: '1rem', marginTop: '1rem' }}>
          <button type="button" className="btn btn--danger" onClick={handleLogout}>
            Sign out of all sessions
          </button>
        </section>
      </div>
    </div>
  )
}
