import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp } from '../../test/render'
import { backend } from '../../test/handlers'

async function signIn() {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Organization'), 'acme')
  await user.type(screen.getByLabelText('Email'), 'o@acme.test')
  await user.type(screen.getByLabelText('Password'), 'password-123456')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
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
