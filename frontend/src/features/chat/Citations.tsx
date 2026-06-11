// Citation rendering: inline [n] markers become hoverable/tappable chips resolved to their
// source, and a per-message sources panel lists them. Markers without a matching citation
// (shouldn't happen — the backend strips phantoms) render as plain text.

import { Fragment, type ReactNode } from 'react'
import type { Citation } from '../../lib/api'

function sourceLabel(c: Citation): string {
  return c.page_ref != null ? `${c.filename}, p.${c.page_ref}` : c.filename
}

/** Render assistant text, turning inline [n] markers into resolved citation chips. */
export function MessageContent({
  text,
  citations,
}: {
  text: string
  citations: Citation[]
}): ReactNode {
  console.log("RAG Stage 9: Final rendered message content", { text, citations })
  const byIndex = new Map(citations.map((c) => [c.marker_index, c]))
  const parts = text.split(/(\[\d+\])/g)
  return (
    <p className="message-content">
      {parts.map((part, i) => {
        const match = /^\[(\d+)\]$/.exec(part)
        const citation = match ? byIndex.get(Number(match[1])) : undefined
        if (match && citation) {
          return (
            <button
              key={i}
              type="button"
              className="citation-marker"
              title={sourceLabel(citation)}
              aria-label={`Source ${citation.marker_index}: ${sourceLabel(citation)}`}
            >
              {part}
            </button>
          )
        }
        return <Fragment key={i}>{part}</Fragment>
      })}
    </p>
  )
}

export function SourcesPanel({ citations }: { citations: Citation[] }): ReactNode {
  if (citations.length === 0) return null
  return (
    <div className="sources-panel" aria-label="Sources">
      {citations.map((c) => (
        <div className="source-card" key={`${c.chunk_id}-${c.marker_index}`}>
          <strong>[{c.marker_index}]</strong> {sourceLabel(c)}
        </div>
      ))}
    </div>
  )
}
