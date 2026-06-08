// One report's lifecycle, shown honestly: queued/generating (working), failed (the reason —
// including INSUFFICIENT_EVIDENCE / NO_GROUNDED_SECTIONS content declines), or ready (sections
// with citation chips + sources + the PDF export/download control). Never an empty success.

import { useState } from 'react'
import { apiClient } from '../../lib/api'
import type { ReportResponse } from '../../lib/api'
import { useCreateExport, useExport, useReport } from './api'

type Citation = ReportResponse['citations'][number]

function sourceLabel(c: Citation): string {
  return c.page_ref != null ? `${c.filename}, p.${c.page_ref}` : c.filename
}

export function ReportViewer({ reportId }: { reportId: string }) {
  const { data: report, isLoading, isError } = useReport(reportId)

  if (isLoading) return <div className="skeleton" style={{ width: '80%' }} aria-busy="true" />
  if (isError || !report)
    return (
      <div className="bubble bubble--error" role="alert">
        We couldn’t load this report.
      </div>
    )

  if (report.status === 'queued' || report.status === 'generating') {
    return (
      <div className="report-status" aria-live="polite">
        <div className="skeleton" style={{ width: '60%' }} />
        <p>Generating “{report.title}”…</p>
      </div>
    )
  }

  if (report.status === 'failed') {
    return (
      <div className="bubble bubble--refusal" role="note" aria-label="Report failed">
        <h3>{report.title}</h3>
        <p>This report could not be generated: {report.failure_reason ?? 'unknown reason'}.</p>
        <p>Try a broader scope or a different question.</p>
      </div>
    )
  }

  const byMarker = new Map(report.citations.map((c) => [c.marker_index, c]))
  return (
    <article className="report" aria-label="Report">
      <h2>{report.title}</h2>
      {report.sections.map((section, i) => (
        <section key={i}>
          <h3>{section.heading}</h3>
          <p>
            {section.body}{' '}
            {section.citation_markers.map((n) => {
              const c = byMarker.get(n)
              return (
                <button
                  key={n}
                  type="button"
                  className="citation-marker"
                  title={c ? sourceLabel(c) : `Source ${n}`}
                  aria-label={c ? `Source ${n}: ${sourceLabel(c)}` : `Source ${n}`}
                >
                  [{n}]
                </button>
              )
            })}
          </p>
        </section>
      ))}

      {report.citations.length > 0 && (
        <section className="sources-panel" aria-label="Sources">
          <h3>Sources</h3>
          {report.citations.map((c) => (
            <div className="source-card" key={`${c.chunk_id}-${c.marker_index}`}>
              <strong>[{c.marker_index}]</strong> {sourceLabel(c)}
            </div>
          ))}
        </section>
      )}

      <ExportControls reportId={reportId} />
    </article>
  )
}

function ExportControls({ reportId }: { reportId: string }) {
  const create = useCreateExport()
  const [exportId, setExportId] = useState<string | null>(null)
  const { data: exportRow } = useExport(reportId, exportId ?? undefined)

  const onExport = async () => {
    const created = await create.mutateAsync(reportId)
    setExportId(created.id)
  }

  const onDownload = async () => {
    if (!exportId) return
    const blob = await apiClient.downloadExport(reportId, exportId)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `report-${reportId}.pdf`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const status = exportRow?.status
  return (
    <div className="export-controls">
      {!exportId && (
        <button type="button" className="btn" onClick={onExport} disabled={create.isPending}>
          {create.isPending ? 'Starting export…' : 'Export to PDF'}
        </button>
      )}
      {exportId && status !== 'ready' && status !== 'failed' && (
        <span aria-live="polite">Preparing PDF…</span>
      )}
      {status === 'ready' && (
        <button type="button" className="btn btn--primary" onClick={onDownload}>
          Download PDF
        </button>
      )}
      {status === 'failed' && (
        <span role="alert">Export failed: {exportRow?.failure_reason ?? 'unknown'}.</span>
      )}
    </div>
  )
}
