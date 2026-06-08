import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { axe } from 'jest-axe'
import { RecommendationsPage } from './RecommendationsPage'
import { renderWithProviders, seedAuth } from '../../test/render'
import { server } from '../../test/server'

const API = '*/api/v1'

const COMPLETED = {
  id: 'rec-1',
  status: 'completed',
  confidence_band: 'high',
  recommendation: 'Choose Vendor A.',
  alternatives: [{ option: 'Vendor B', rationale: 'cheaper' }],
  justifications: [{ claim: 'A has the better SLA', citation_markers: [1] }],
  caveats: ['limited data'],
  citations: [
    {
      marker_index: 1,
      chunk_id: 'c1',
      document_id: 'd1',
      collection_id: 'col1',
      filename: 'guide.pdf',
      page_ref: 7,
    },
  ],
  decline_reason: null,
  prompt_version: '1.0.0',
  created_at: '2026-06-08T00:00:00Z',
}

const DECLINED = {
  id: 'rec-2',
  status: 'declined',
  confidence_band: null,
  recommendation: null,
  alternatives: [],
  justifications: [],
  caveats: [],
  citations: [],
  decline_reason: 'INSUFFICIENT_EVIDENCE',
  prompt_version: null,
  created_at: '2026-06-08T00:00:00Z',
}

function postReturns(body: Record<string, unknown>, status = 200): void {
  server.use(http.post(`${API}/recommendations`, () => HttpResponse.json(body, { status })))
}

async function ask(text = 'which vendor?'): Promise<void> {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Decision question'), text)
  await user.click(screen.getByRole('button', { name: 'Get recommendation' }))
}

describe('recommendation view', () => {
  it('renders a completed recommendation with evidence-strength band and resolved citations', async () => {
    seedAuth()
    postReturns(COMPLETED)
    renderWithProviders(<RecommendationsPage />, '/recommendations')
    await ask()

    expect(await screen.findByText('Choose Vendor A.')).toBeInTheDocument()
    // The band is labeled as EVIDENCE strength, not model certainty (honesty in the UI).
    const badge = screen.getByTitle(/strength of the retrieved evidence/i)
    expect(badge).toHaveTextContent('Evidence strength')
    expect(badge).toHaveTextContent('High')
    // Citation resolves to its source.
    expect(
      screen.getByRole('button', { name: 'Source 1: guide.pdf, p.7' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Sources')).toHaveTextContent('guide.pdf, p.7')
    expect(screen.queryByRole('note')).not.toBeInTheDocument() // not declined
  })

  it('renders a declined recommendation distinctly (no forced recommendation)', async () => {
    seedAuth()
    postReturns(DECLINED)
    renderWithProviders(<RecommendationsPage />, '/recommendations')
    await ask('something unanswerable')

    const declined = await screen.findByRole('note', { name: 'Declined' })
    expect(declined).toHaveTextContent('Not enough evidence')
    expect(screen.queryByTitle(/strength of the retrieved evidence/i)).not.toBeInTheDocument()
  })

  it('shows a typed error with retry', async () => {
    seedAuth()
    server.use(
      http.post(`${API}/recommendations`, () =>
        HttpResponse.json(
          { error: { code: 'UPSTREAM_UNAVAILABLE', message: 'x', request_id: 'r' } },
          { status: 503 },
        ),
      ),
    )
    renderWithProviders(<RecommendationsPage />, '/recommendations')
    await ask()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('AI service is temporarily unavailable')
    expect(alert.querySelector('button')).toHaveTextContent('Retry')
  })

  it('has no axe violations on a completed recommendation', async () => {
    seedAuth()
    postReturns(COMPLETED)
    const { container } = renderWithProviders(<RecommendationsPage />, '/recommendations')
    await ask()
    await screen.findByText('Choose Vendor A.')
    expect(await axe(container)).toHaveNoViolations()
  })
})
