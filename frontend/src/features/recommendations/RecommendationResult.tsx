// Renders a recommendation outcome — completed (recommendation + alternatives + grounded
// justifications with citation chips + sources) or declined (explicit, explains insufficient
// evidence, suggests narrowing scope / uploading docs). A decline is shown honestly, never as
// an empty success.

import type { RecommendationResponse } from '../../lib/api'
import { ConfidenceBadge } from './ConfidenceBadge'

type Citation = RecommendationResponse['citations'][number]

function sourceLabel(c: Citation): string {
  return c.page_ref != null ? `${c.filename}, p.${c.page_ref}` : c.filename
}

export function RecommendationResult({ result }: { result: RecommendationResponse }) {
  if (result.status === 'declined') {
    return (
      <div className="rec-result rec-result--declined" role="note" aria-label="Declined">
        <h2>Not enough evidence to recommend</h2>
        <p>
          The available sources don’t support a confident recommendation. Try narrowing the
          collection scope, rephrasing the question, or uploading documents that cover it.
        </p>
      </div>
    )
  }

  const byMarker = new Map(result.citations.map((c) => [c.marker_index, c]))
  return (
    <div className="rec-result" aria-label="Recommendation">
      <div className="rec-result__head">
        <h2>Recommendation</h2>
        {result.confidence_band && <ConfidenceBadge band={result.confidence_band} />}
      </div>
      <p className="rec-result__text">{result.recommendation}</p>

      {result.justifications.length > 0 && (
        <section aria-label="Justifications">
          <h3>Why</h3>
          <ul className="rec-justifications">
            {result.justifications.map((j, i) => (
              <li key={i}>
                {j.claim}{' '}
                {j.citation_markers.map((n) => {
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
              </li>
            ))}
          </ul>
        </section>
      )}

      {result.alternatives.length > 0 && (
        <section aria-label="Alternatives">
          <h3>Alternatives considered</h3>
          <ul className="rec-alternatives">
            {result.alternatives.map((a, i) => (
              <li key={i}>
                <strong>{a.option}</strong> — {a.rationale}
              </li>
            ))}
          </ul>
        </section>
      )}

      {result.caveats.length > 0 && (
        <section aria-label="Caveats">
          <h3>Caveats</h3>
          <ul>
            {result.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </section>
      )}

      {result.citations.length > 0 && (
        <section className="sources-panel" aria-label="Sources">
          <h3>Sources</h3>
          {result.citations.map((c) => (
            <div className="source-card" key={`${c.chunk_id}-${c.marker_index}`}>
              <strong>[{c.marker_index}]</strong> {sourceLabel(c)}
            </div>
          ))}
        </section>
      )}
    </div>
  )
}
