// Recommendation view: a request form + the outcome. All five async states are explicit —
// idle (prompt), pending (working), error (typed message + retry), success-completed, and
// success-declined (a first-class honest state). Generation is synchronous (no polling).

import { useState, type FormEvent } from 'react'
import { ApiError } from '../../lib/api'
import { useCreateRecommendation } from './api'
import { RecommendationResult } from './RecommendationResult'

export function RecommendationsPage() {
  const create = useCreateRecommendation()
  const [query, setQuery] = useState('')

  const run = () => {
    const text = query.trim()
    if (!text || create.isPending) return
    create.mutate({ query: text, collection_scope: [] })
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    run()
  }

  return (
    <div className="feature-page">
      <h1>Recommendations</h1>
      <p className="feature-page__lead">
        Ask a decision question. Answers are grounded in your documents with cited justifications,
        or declined when the evidence is too thin — never a forced recommendation.
      </p>

      <form className="rec-form" onSubmit={submit}>
        <label htmlFor="rec-query" className="visually-hidden">
          Decision question
        </label>
        <textarea
          id="rec-query"
          placeholder="e.g. Which vendor should we choose for X, and why?"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          rows={3}
        />
        <button type="submit" className="btn btn--primary" disabled={create.isPending || !query.trim()}>
          {create.isPending ? 'Analyzing…' : 'Get recommendation'}
        </button>
      </form>

      <div className="rec-outcome" aria-live="polite">
        {create.isPending && (
          <div aria-busy="true" aria-label="Analyzing">
            <div className="skeleton" style={{ width: '70%' }} />
            <div className="skeleton" style={{ width: '90%', marginTop: '0.6rem' }} />
          </div>
        )}
        {create.isError && (
          <div className="bubble bubble--error" role="alert">
            <p>
              {create.error instanceof ApiError
                ? create.error.message
                : 'Something went wrong. Please try again.'}
            </p>
            <button type="button" className="btn" onClick={run}>
              Retry
            </button>
          </div>
        )}
        {create.isIdle && !create.isPending && (
          <div className="empty-state">
            <p>Your recommendation will appear here.</p>
          </div>
        )}
        {create.isSuccess && <RecommendationResult result={create.data} />}
      </div>
    </div>
  )
}
