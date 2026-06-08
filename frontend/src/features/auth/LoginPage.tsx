// Auth screen: sign in to an existing organization, or register a new one (toggled in place).
// Every async state is explicit — the submit button reflects pending, a typed error renders an
// inline alert (and the global toast), success navigates to the originally-attempted route (or
// /chat). Registration creates the org + owner then signs in with the same credentials. Inputs
// are labeled (implicit label association) for a11y; the org slug auto-derives from the org name
// until the user edits it. Client-side password check mirrors the backend policy (12 chars).

import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError, apiClient } from '../../lib/api'
import { useAuth } from '../../lib/auth/useAuth'
import { useToast } from '../../lib/toast/useToast'

interface LocationState {
  from?: { pathname: string }
}

// Mirrors backend identity.rules.MIN_PASSWORD_LENGTH — the same message the API would return,
// caught client-side so an obviously-too-short password never makes a round trip.
const MIN_PASSWORD_LENGTH = 12

/** Derive a URL-safe org slug from a free-text org name (until the user edits the slug). */
function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, '')
    .replace(/[\s_]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

export function LoginPage() {
  const { login } = useAuth()
  const { notifyError } = useToast()
  const navigate = useNavigate()
  const location = useLocation()

  const [mode, setMode] = useState<'signin' | 'register'>('signin')
  const [orgName, setOrgName] = useState('')
  const [orgSlug, setOrgSlug] = useState('')
  const [slugEdited, setSlugEdited] = useState(false)
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const isRegister = mode === 'register'
  const destination = (location.state as LocationState | null)?.from?.pathname ?? '/chat'

  function onOrgNameChange(value: string): void {
    setOrgName(value)
    if (!slugEdited) setOrgSlug(slugify(value))
  }

  function switchMode(): void {
    setMode(isRegister ? 'signin' : 'register')
    setError(null)
  }

  async function onSubmit(event: FormEvent): Promise<void> {
    event.preventDefault()
    setError(null)
    const slug = orgSlug.trim().toLowerCase()
    const userEmail = email.trim().toLowerCase()

    if (isRegister && password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters`)
      return
    }

    setPending(true)
    try {
      if (isRegister) {
        await apiClient.register({
          name: orgName.trim(),
          slug,
          owner_email: userEmail,
          password,
          full_name: fullName.trim(),
        })
      }
      await login(slug, userEmail, password)
      navigate(destination, { replace: true })
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : 'An unexpected error occurred. Please try again.'
      setError(message)
      notifyError(err)
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="screen-center">
      <form className="panel auth-form" onSubmit={onSubmit} aria-labelledby="auth-title">
        <h1 id="auth-title">{isRegister ? 'Register your organization' : 'Sign in to OnMixAI'}</h1>

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        {isRegister && (
          <>
            <label className="field">
              <span>Organization name</span>
              <input
                name="org_name"
                autoComplete="organization"
                value={orgName}
                onChange={(e) => onOrgNameChange(e.target.value)}
                placeholder="Acme Corporation"
                required
              />
            </label>
            <label className="field">
              <span>Organization slug</span>
              <input
                name="org_slug"
                autoComplete="off"
                value={orgSlug}
                onChange={(e) => {
                  setOrgSlug(e.target.value)
                  setSlugEdited(true)
                }}
                placeholder="acme-corporation"
                required
              />
            </label>
            <label className="field">
              <span>Your name</span>
              <input
                name="full_name"
                autoComplete="name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Ada Lovelace"
                required
              />
            </label>
          </>
        )}

        {!isRegister && (
          <label className="field">
            <span>Organization</span>
            <input
              name="org_slug"
              autoComplete="organization"
              value={orgSlug}
              onChange={(e) => setOrgSlug(e.target.value)}
              placeholder="acme"
              required
            />
          </label>
        )}

        <label className="field">
          <span>Email</span>
          <input
            name="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@acme.com"
            required
          />
        </label>

        <label className="field">
          <span>Password</span>
          <input
            name="password"
            type="password"
            autoComplete={isRegister ? 'new-password' : 'current-password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        <button type="submit" className="btn btn--primary" disabled={pending}>
          {pending
            ? isRegister
              ? 'Creating account…'
              : 'Signing in…'
            : isRegister
              ? 'Create account'
              : 'Sign in'}
        </button>

        <p className="auth-switch">
          <button type="button" className="auth-switch__link" onClick={switchMode}>
            {isRegister
              ? 'Already have an organization? Sign in'
              : 'Need an account? Register your organization'}
          </button>
        </p>
      </form>
    </div>
  )
}
