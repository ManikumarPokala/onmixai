import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { axe } from 'jest-axe'
import { ReportsPage } from './ReportsPage'
import { renderWithProviders, seedAuth } from '../../test/render'
import { server } from '../../test/server'

const API = '*/api/v1'

type Status = 'queued' | 'generating' | 'ready' | 'failed'

function makeReport(status: Status, failureReason: string | null = null) {
  const ready = status === 'ready'
  return {
    id: 'r-1',
    report_type: 'executive_summary',
    title: 'Q3 Review',
    status,
    failure_reason: failureReason,
    sections: ready ? [{ heading: 'Overview', body: 'Revenue grew.', citation_markers: [1] }] : [],
    citations: ready
      ? [
          {
            marker_index: 1,
            chunk_id: 'c1',
            document_id: 'd1',
            collection_id: 'col1',
            filename: 'guide.pdf',
            page_ref: 7,
          },
        ]
      : [],
    generation_metadata: ready ? { model: 'stub' } : null,
    created_at: '2026-06-08T00:00:00Z',
    updated_at: '2026-06-08T00:00:00Z',
  }
}

function exportRow(status: Status) {
  return {
    id: 'e-1',
    report_id: 'r-1',
    format: 'pdf',
    status,
    failure_reason: null,
    created_at: '2026-06-08T00:00:00Z',
  }
}

async function build(): Promise<void> {
  const user = userEvent.setup()
  await user.selectOptions(screen.getByLabelText('Type'), 'executive_summary')
  await user.type(screen.getByLabelText('Title'), 'Q3 Review')
  await user.type(screen.getByLabelText('Question'), 'summarize Q3')
  await user.click(screen.getByRole('button', { name: 'Generate report' }))
}

describe('report builder', () => {
  it('polls a generating report and renders it when ready', async () => {
    seedAuth()
    let row = makeReport('generating')
    server.use(
      http.get(`${API}/reports`, () => HttpResponse.json({ reports: [], next_cursor: null })),
      http.post(`${API}/reports`, () => HttpResponse.json(makeReport('queued'), { status: 201 })),
      http.get(`${API}/reports/:id`, () => HttpResponse.json(row)),
    )
    renderWithProviders(<ReportsPage />, '/reports')
    await build()

    expect(await screen.findByText(/Generating/)).toBeInTheDocument()
    row = makeReport('ready') // worker finishes; the poll picks it up
    expect(await screen.findByText('Revenue grew.', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Source 1: guide.pdf, p.7' })).toBeInTheDocument()
  })

  it('shows a failed report with its reason honestly', async () => {
    seedAuth()
    server.use(
      http.get(`${API}/reports`, () => HttpResponse.json({ reports: [], next_cursor: null })),
      http.post(`${API}/reports`, () => HttpResponse.json(makeReport('queued'), { status: 201 })),
      http.get(`${API}/reports/:id`, () =>
        HttpResponse.json(makeReport('failed', 'NO_GROUNDED_SECTIONS')),
      ),
    )
    renderWithProviders(<ReportsPage />, '/reports')
    await build()
    const note = await screen.findByRole('note', { name: 'Report failed' })
    expect(note).toHaveTextContent('NO_GROUNDED_SECTIONS')
  })

  it('exports to PDF and downloads it', async () => {
    seedAuth()
    // jsdom doesn't implement object URLs; add the statics without replacing the URL ctor.
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    let downloadHit = false
    let exp = exportRow('queued')
    server.use(
      http.get(`${API}/reports`, () => HttpResponse.json({ reports: [], next_cursor: null })),
      http.post(`${API}/reports`, () => HttpResponse.json(makeReport('queued'), { status: 201 })),
      http.get(`${API}/reports/:id`, () => HttpResponse.json(makeReport('ready'))),
      http.post(`${API}/reports/:id/exports`, () => HttpResponse.json(exp, { status: 202 })),
      http.get(`${API}/reports/:id/exports/:eid`, () => HttpResponse.json(exp)),
      http.get(`${API}/reports/:id/exports/:eid/download`, () => {
        downloadHit = true
        return HttpResponse.arrayBuffer(new Uint8Array([0x25, 0x50, 0x44, 0x46]).buffer, {
          headers: { 'Content-Type': 'application/pdf' },
        })
      }),
    )
    renderWithProviders(<ReportsPage />, '/reports')
    await build()
    await screen.findByText('Revenue grew.')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Export to PDF' }))
    exp = exportRow('ready') // export worker finishes
    const download = await screen.findByRole('button', { name: 'Download PDF' }, { timeout: 4000 })
    await user.click(download)
    // Wait for the whole download path to finish (blob → object URL) so the authed fetch
    // resolves while the handler is still installed — not after teardown.
    await waitFor(() => expect(downloadHit).toBe(true))
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled())
  })

  it('has no axe violations on a ready report', async () => {
    seedAuth()
    server.use(
      http.get(`${API}/reports`, () => HttpResponse.json({ reports: [], next_cursor: null })),
      http.post(`${API}/reports`, () => HttpResponse.json(makeReport('queued'), { status: 201 })),
      http.get(`${API}/reports/:id`, () => HttpResponse.json(makeReport('ready'))),
    )
    const { container } = renderWithProviders(<ReportsPage />, '/reports')
    await build()
    await screen.findByText('Revenue grew.')
    expect(await axe(container)).toHaveNoViolations()
  })
})
