import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../lib/auth/useAuth'

export function HomePage() {
  const { orgSlug, role } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="home-page" style={{ padding: '2rem', maxWidth: '1200px', margin: '0 auto' }}>
      {/* Welcome Banner */}
      <section className="home-banner" style={{
        background: 'linear-gradient(135deg, var(--surface) 0%, var(--surface-2) 100%)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        padding: '2.5rem',
        marginBottom: '2rem',
        position: 'relative',
        overflow: 'hidden'
      }}>
        <h1 style={{ fontSize: '2.2rem', margin: '0 0 0.5rem 0', fontWeight: 700 }}>
          Welcome to OnMixAI
        </h1>
        <p style={{ color: 'var(--text-dim)', fontSize: '1.1rem', margin: '0 0 1.5rem 0', maxWidth: '700px' }}>
          An enterprise Generative AI decision intelligence platform that turns documents into grounded, auditable answers, evaluations, and structured feedback reports.
        </p>
        <div style={{ display: 'flex', gap: '1.5rem', fontSize: '0.9rem', color: 'var(--text-dim)' }}>
          <div>Organization: <strong style={{ color: 'var(--text)' }}>{orgSlug}</strong></div>
          <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: '1.5rem' }}>
            Role: <strong style={{ color: 'var(--text)', textTransform: 'capitalize' }}>{role}</strong>
          </div>
        </div>
      </section>

      {/* Main Actions Grid */}
      <section aria-labelledby="actions-heading" style={{ marginBottom: '3rem' }}>
        <h2 id="actions-heading" style={{ fontSize: '1.4rem', marginBottom: '1.5rem', fontWeight: 600 }}>
          Explore Platform Capabilities
        </h2>
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
          gap: '1.5rem'
        }}>
          {/* Action 1: Chat */}
          <div className="action-card" onClick={() => navigate('/chat')} style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            padding: '1.5rem',
            cursor: 'pointer',
            transition: 'transform 0.2s, border-color 0.2s'
          }}>
            <div style={{ fontSize: '1.8rem', marginBottom: '0.75rem' }}>💬</div>
            <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.1rem', fontWeight: 600 }}>AI Interview Simulator</h3>
            <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem', margin: 0 }}>
              Engage in multi-turn simulated interview sessions with contextual, cited answers from uploaded candidate resumes.
            </p>
          </div>

          {/* Action 2: Recommendations */}
          <div className="action-card" onClick={() => navigate('/recommendations')} style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            padding: '1.5rem',
            cursor: 'pointer',
            transition: 'transform 0.2s, border-color 0.2s'
          }}>
            <div style={{ fontSize: '1.8rem', marginBottom: '0.75rem' }}>🎯</div>
            <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.1rem', fontWeight: 600 }}>AI Recommendations</h3>
            <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem', margin: 0 }}>
              Generate structured evaluation recommendations, alternative suggestions, and confidence indicators derived from search evidence.
            </p>
          </div>

          {/* Action 3: Reports */}
          <div className="action-card" onClick={() => navigate('/reports')} style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            padding: '1.5rem',
            cursor: 'pointer',
            transition: 'transform 0.2s, border-color 0.2s'
          }}>
            <div style={{ fontSize: '1.8rem', marginBottom: '0.75rem' }}>📊</div>
            <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.1rem', fontWeight: 600 }}>Evaluation Reports</h3>
            <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem', margin: 0 }}>
              Assemble comprehensive evaluation reports backed by LangGraph pipelines, with full source citations and PDF export logs.
            </p>
          </div>

          {/* Action 4: Engineering Hub */}
          <div className="action-card" onClick={() => navigate('/engineering')} style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            padding: '1.5rem',
            cursor: 'pointer',
            transition: 'transform 0.2s, border-color 0.2s',
            boxShadow: '0 0 10px rgba(79, 124, 255, 0.1)'
          }}>
            <div style={{ fontSize: '1.8rem', marginBottom: '0.75rem' }}>🛠️</div>
            <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.1rem', fontWeight: 600, color: 'var(--primary)' }}>Engineering Hub</h3>
            <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem', margin: 0 }}>
              Inspect detailed system designs, interactive pipeline flow diagrams, LLM safety guardrails, and core architectural records.
            </p>
          </div>
        </div>
      </section>

      {/* RAG Workflow Steps */}
      <section aria-labelledby="workflow-heading" style={{
        padding: '2rem',
        border: '1px solid var(--border)',
        background: 'var(--surface)',
        borderRadius: 'var(--radius)'
      }}>
        <h2 id="workflow-heading" style={{ fontSize: '1.4rem', marginBottom: '1.5rem', fontWeight: 600 }}>
          End-to-End Decision Pipeline
        </h2>
        <div className="workflow-steps" style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: '2rem',
          position: 'relative'
        }}>
          <div className="step-item" style={{ position: 'relative' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: '0.5rem' }}>
              <span style={{
                background: 'var(--primary)',
                color: 'var(--primary-text)',
                borderRadius: '50%',
                width: '24px',
                height: '24px',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 'bold',
                fontSize: '0.8rem',
                marginRight: '0.75rem'
              }}>1</span>
              <h4 style={{ margin: 0, fontWeight: 600 }}>Knowledge Ingestion</h4>
            </div>
            <p style={{ color: 'var(--text-dim)', fontSize: '0.8rem', margin: 0, paddingLeft: '2rem' }}>
              Upload files asynchronously. The worker parses, chunks (prose/tables), generates vector embeddings, and registers multi-tenant RLS contexts.
            </p>
          </div>

          <div className="step-item" style={{ position: 'relative' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: '0.5rem' }}>
              <span style={{
                background: 'var(--primary)',
                color: 'var(--primary-text)',
                borderRadius: '50%',
                width: '24px',
                height: '24px',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 'bold',
                fontSize: '0.8rem',
                marginRight: '0.75rem'
              }}>2</span>
              <h4 style={{ margin: 0, fontWeight: 600 }}>Hybrid Retrieval & RRF</h4>
            </div>
            <p style={{ color: 'var(--text-dim)', fontSize: '0.8rem', margin: 0, paddingLeft: '2rem' }}>
              Queries perform permission-aware searches combining semantic similarity with keyword matches, fused via Reciprocal Rank Fusion (RRF).
            </p>
          </div>

          <div className="step-item" style={{ position: 'relative' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: '0.5rem' }}>
              <span style={{
                background: 'var(--primary)',
                color: 'var(--primary-text)',
                borderRadius: '50%',
                width: '24px',
                height: '24px',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 'bold',
                fontSize: '0.8rem',
                marginRight: '0.75rem'
              }}>3</span>
              <h4 style={{ margin: 0, fontWeight: 600 }}>LangGraph Execution</h4>
            </div>
            <p style={{ color: 'var(--text-dim)', fontSize: '0.8rem', margin: 0, paddingLeft: '2rem' }}>
              A structured agent graph coordinates information gathering and evaluation nodes to validate source references and ensure strict grounding.
            </p>
          </div>
        </div>
      </section>
    </div>
  )
}
