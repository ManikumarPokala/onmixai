import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { renderApp } from '../../test/render'
import { backend } from '../../test/handlers'

async function signIn() {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Organization'), 'acme')
  await user.type(screen.getByLabelText('Email'), 'o@acme.test')
  await user.type(screen.getByLabelText('Password'), 'password-123456')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

/** Switch to the register view and fill the org/owner fields. Slug auto-derives from the name. */
async function fillRegister(
  user: ReturnType<typeof userEvent.setup>,
  { password = 'password-123456', orgName = 'Acme Corporation' } = {},
) {
  await user.click(screen.getByRole('button', { name: /register your organization/i }))
  await user.type(screen.getByLabelText('Organization name'), orgName)
  await user.type(screen.getByLabelText('Your name'), 'Ada Lovelace')
  await user.type(screen.getByLabelText('Email'), 'owner@acme.test')
  await user.type(screen.getByLabelText('Password'), password)
}

describe('auth flow', () => {
  it('redirects an anonymous user from a guarded route to login', () => {
    renderApp('/chat')
    expect(screen.getByText('Sign in to OnMixAI')).toBeInTheDocument()
  })

  it('logs in and lands on the guarded chat route', async () => {
    renderApp('/chat')
    await signIn()
    expect(await screen.findByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Chat' })).toBeInTheDocument()
  })

  it('shows the typed error message on bad credentials', async () => {
    renderApp('/login')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Organization'), 'acme')
    await user.type(screen.getByLabelText('Email'), 'o@acme.test')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('email or password is incorrect')
  })

  it('logs out and returns to login', async () => {
    renderApp('/chat')
    await signIn()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Sign out' }))
    await waitFor(() => expect(screen.getByText('Sign in to OnMixAI')).toBeInTheDocument())
    expect(backend.loginCalls).toBe(1)
  })
})

describe('registration flow', () => {
  it('registers a new organization then signs in and lands authed', async () => {
    renderApp('/login')
    const user = userEvent.setup()
    await fillRegister(user)
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(backend.registerCalls).toBe(1)
    expect(backend.loginCalls).toBe(1) // registration is followed by a sign-in
  })

  it('rejects a too-short password client-side, before any network call', async () => {
    renderApp('/login')
    const user = userEvent.setup()
    await fillRegister(user, { password: 'short' })
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('at least 12 characters')
    expect(backend.registerCalls).toBe(0) // never reached the backend
  })

  it('renders the typed error when the organization slug is already taken', async () => {
    renderApp('/login')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /register your organization/i }))
    await user.type(screen.getByLabelText('Organization name'), 'Acme')
    await user.clear(screen.getByLabelText('Organization slug'))
    await user.type(screen.getByLabelText('Organization slug'), 'taken') // 409 in the MSW backend
    await user.type(screen.getByLabelText('Your name'), 'Ada Lovelace')
    await user.type(screen.getByLabelText('Email'), 'owner@acme.test')
    await user.type(screen.getByLabelText('Password'), 'password-123456')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('already taken')
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument() // not authed
  })

  it('has no axe violations on either view', async () => {
    const { container } = renderApp('/login')
    expect(await axe(container)).toHaveNoViolations() // sign-in
    await userEvent.setup().click(
      screen.getByRole('button', { name: /register your organization/i }),
    )
    expect(await axe(container)).toHaveNoViolations() // register
  })
})
