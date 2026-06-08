// Report builder: pick a type + title + question → create (queued) → the viewer polls the
// lifecycle and renders the result (or the failure reason) + the PDF export/download flow. A
// sidebar lists the user's reports.

import { useState, type FormEvent } from 'react'
import type { CreateReportRequest } from '../../lib/api'
import { useCreateReport, useReports } from './api'
import { ReportViewer } from './ReportViewer'

const TYPES: { value: CreateReportRequest['report_type']; label: string }[] = [
  { value: 'executive_summary', label: 'Executive summary' },
  { value: 'technical', label: 'Technical' },
  { value: 'recommendation', label: 'Recommendation' },
]

export function ReportsPage() {
  const reports = useReports()
  const create = useCreateReport()
  const [selected, setSelected] = useState<string | null>(null)
  const [reportType, setReportType] = useState<CreateReportRequest['report_type']>('executive_summary')
  const [title, setTitle] = useState('')
  const [query, setQuery] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!title.trim() || !query.trim() || create.isPending) return
    const report = await create.mutateAsync({
      report_type: reportType,
      title: title.trim(),
      query: query.trim(),
      collection_scope: [],
    })
    setSelected(report.id)
    setTitle('')
    setQuery('')
  }

  return (
    <div className="reports-layout">
      <aside className="report-list" aria-label="Reports">
        <form className="report-form" onSubmit={submit}>
          <h2>New report</h2>
          <label className="field">
            <span>Type</span>
            <select
              value={reportType}
              onChange={(e) => setReportType(e.target.value as CreateReportRequest['report_type'])}
            >
              {TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Title</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} required />
          </label>
          <label className="field">
            <span>Question</span>
            <textarea value={query} onChange={(e) => setQuery(e.target.value)} rows={2} required />
          </label>
          <button type="submit" className="btn btn--primary" disabled={create.isPending}>
            {create.isPending ? 'Creating…' : 'Generate report'}
          </button>
        </form>

        {reports.isLoading ? (
          <div className="skeleton" style={{ margin: '0.5rem' }} aria-busy="true" />
        ) : reports.data && reports.data.reports.length > 0 ? (
          <ul className="session-list__items">
            {reports.data.reports.map((r) => (
              <li key={r.id} className={`session-item ${r.id === selected ? 'is-active' : ''}`}>
                <button
                  type="button"
                  className="session-item__open"
                  aria-current={r.id === selected ? 'page' : undefined}
                  onClick={() => setSelected(r.id)}
                >
                  {r.title} <span className="report-status-tag">({r.status})</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-state">No reports yet.</p>
        )}
      </aside>

      <section className="report-pane">
        {selected ? (
          <ReportViewer reportId={selected} />
        ) : (
          <div className="empty-state" style={{ margin: 'auto' }}>
            <h2>Build a report</h2>
            <p>Pick a type and ask a question, or select a report on the left.</p>
          </div>
        )}
      </section>
    </div>
  )
}
