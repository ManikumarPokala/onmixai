// Login: org slug + email + password. Every async state is explicit — the submit button
// reflects pending, a typed error renders an inline alert (and the global toast), success
// navigates to the originally-attempted route (or /chat). Inputs are labeled for a11y.

import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../../lib/api'
import { useAuth } from '../../lib/auth/useAuth'
import { useToast } from '../../lib/toast/useToast'

interface LocationState {
  from?: { pathname: string }
}

export function LoginPage() {
  const { login } = useAuth()
  const { notifyError } = useToast()
  const navigate = useNavigate()
  const location = useLocation()
  const [orgSlug, setOrgSlug] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const destination = (location.state as LocationState | null)?.from?.pathname ?? '/chat'

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setPending(true)
    try {
      await login(orgSlug.trim(), email.trim(), password)
      navigate(destination, { replace: true })
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : 'Unable to sign in. Please try again.'
      setError(message)
      notifyError(err)
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="screen-center">
      <form className="panel auth-form" onSubmit={onSubmit} aria-labelledby="login-title">
        <h1 id="login-title">Sign in to OnMixAI</h1>

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <label className="field">
          <span>Organization</span>
          <input
            name="org_slug"
            autoComplete="organization"
            value={orgSlug}
            onChange={(e) => setOrgSlug(e.target.value)}
            required
          />
        </label>

        <label className="field">
          <span>Email</span>
          <input
            name="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>

        <label className="field">
          <span>Password</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        <button type="submit" className="btn btn--primary" disabled={pending}>
          {pending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
