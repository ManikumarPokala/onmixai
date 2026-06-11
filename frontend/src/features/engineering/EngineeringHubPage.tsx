import { useState, useMemo } from 'react'
import { DOCUMENTS } from './docsData'
import { DiagramExplorer } from './DiagramExplorer'

// Custom Markdown rendering utility for showcase presentation
function renderMarkdown(text: string): React.ReactNode[] {
  const lines = text.split('\n')
  let inCodeBlock = false
  let codeBlockLines: string[] = []

  let inTable = false
  let tableHeaders: string[] = []
  let tableRows: string[][] = []

  const result: React.ReactNode[] = []

  const parseInlineStyles = (line: string): React.ReactNode[] => {
    // Bold: **text**
    // Code: `text`
    const regex = /(\*\*.*?\*\*|`.*?`)/g
    const parts = line.split(regex)
    return parts.map((part, index) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        return <strong key={index}>{part.slice(2, -2)}</strong>
      }
      if (part.startsWith('`') && part.endsWith('`')) {
        return <code key={index} style={{
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: '4px',
          padding: '0.15rem 0.35rem',
          fontSize: '0.9em',
          fontFamily: 'monospace'
        }}>{part.slice(1, -1)}</code>
      }
      return part
    })
  }

  lines.forEach((line, index) => {
    // Code blocks
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        inCodeBlock = false
        const codeContent = codeBlockLines.join('\n')
        result.push(
          <pre key={`code-${index}`} style={{
            background: 'var(--bg)',
            border: '1px solid var(--border)',
            borderRadius: '8px',
            padding: '1rem',
            overflowX: 'auto',
            fontFamily: 'monospace',
            fontSize: '0.85rem',
            lineHeight: 1.45,
            margin: '1rem 0'
          }}>
            <code>{codeContent}</code>
          </pre>
        )
        codeBlockLines = []
      } else {
        inCodeBlock = true
      }
      return
    }

    if (inCodeBlock) {
      codeBlockLines.push(line)
      return
    }

    // Tables
    if (line.trim().startsWith('|')) {
      inTable = true
      const cells = line.split('|').map(c => c.trim()).filter(Boolean)
      if (line.includes('---')) {
        // Skip separator line
        return
      }
      if (tableHeaders.length === 0) {
        tableHeaders = cells
      } else {
        tableRows.push(cells)
      }
      return
    } else if (inTable) {
      inTable = false
      // Output collected table
      const currentHeaders = tableHeaders
      const currentRows = tableRows
      tableHeaders = []
      tableRows = []
      result.push(
        <table key={`table-${index}`} className="doc-table" style={{ margin: '1rem 0', width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {currentHeaders.map((h, i) => (
                <th key={i} style={{ borderBottom: '2px solid var(--border)', padding: '0.5rem 0.75rem', textAlign: 'left', fontWeight: 'bold' }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {currentRows.map((row, ri) => (
              <tr key={ri} style={{ borderBottom: '1px solid var(--border)' }}>
                {row.map((cell, ci) => (
                  <td key={ci} style={{ padding: '0.5rem 0.75rem' }}>
                    {parseInlineStyles(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )
    }

    // Headers
    if (line.startsWith('# ')) {
      result.push(<h1 key={index} style={{ fontSize: '1.8rem', borderBottom: '1px solid var(--border)', paddingBottom: '0.5rem', marginTop: '1.5rem', marginBottom: '1rem', fontWeight: 700 }}>{line.slice(2)}</h1>)
      return
    }
    if (line.startsWith('## ')) {
      result.push(<h2 key={index} style={{ fontSize: '1.4rem', marginTop: '1.5rem', marginBottom: '0.75rem', fontWeight: 600 }}>{line.slice(3)}</h2>)
      return
    }
    if (line.startsWith('### ')) {
      result.push(<h3 key={index} style={{ fontSize: '1.1rem', marginTop: '1.25rem', marginBottom: '0.5rem', fontWeight: 600 }}>{line.slice(4)}</h3>)
      return
    }

    // Bullet lists
    if (line.trim().startsWith('- ') || line.trim().startsWith('* ')) {
      result.push(
        <ul key={index} style={{ margin: '0.5rem 0', paddingLeft: '1.5rem', listStyleType: 'disc' }}>
          <li>{parseInlineStyles(line.trim().slice(2))}</li>
        </ul>
      )
      return
    }

    // Standard paragraphs
    if (line.trim() !== '') {
      result.push(<p key={index} style={{ margin: '0.75rem 0', color: 'var(--text)', lineHeight: 1.6 }}>{parseInlineStyles(line)}</p>)
    }
  })

  // Edge case: code block or table left unclosed at EOF
  if (inCodeBlock) {
    result.push(
      <pre key="eof-code" style={{ background: 'var(--bg)', border: '1px solid var(--border)', padding: '1rem', overflowX: 'auto' }}>
        <code>{codeBlockLines.join('\n')}</code>
      </pre>
    )
  }

  return result
}

export function EngineeringHubPage() {
  const [activeTab, setActiveTab] = useState<'overview' | 'docs' | 'diagrams' | 'resources'>('overview')
  
  // Docs Tab State
  const [selectedDocId, setSelectedDocId] = useState<string>('readme')
  const [searchQuery, setSearchQuery] = useState<string>('')

  // Configurable metrics
  const METRICS = {
    docCount: DOCUMENTS.length,
    diagramCount: 5,
    apiEndpoints: 21,
    coreFeatures: 7,
    version: '1.0.0'
  }

  // Filter docs based on search
  const filteredDocs = useMemo(() => {
    return DOCUMENTS.filter(doc =>
      doc.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      doc.content.toLowerCase().includes(searchQuery.toLowerCase())
    )
  }, [searchQuery])

  const selectedDoc = useMemo(() => {
    return DOCUMENTS.find(d => d.id === selectedDocId) ?? DOCUMENTS[0]
  }, [selectedDocId])

  return (
    <div className="engineering-hub" style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* Sub Header tabs bar */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'var(--surface)', padding: '0 1.5rem', gap: '1.5rem', flexShrink: 0 }}>
        <button
          type="button"
          onClick={() => setActiveTab('overview')}
          style={{
            background: 'transparent',
            border: '0',
            borderBottom: activeTab === 'overview' ? '2px solid var(--primary)' : '2px solid transparent',
            color: activeTab === 'overview' ? 'var(--text)' : 'var(--text-dim)',
            padding: '1rem 0.25rem',
            fontSize: '0.95rem',
            fontWeight: 600
          }}
        >
          Overview
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('docs')}
          style={{
            background: 'transparent',
            border: '0',
            borderBottom: activeTab === 'docs' ? '2px solid var(--primary)' : '2px solid transparent',
            color: activeTab === 'docs' ? 'var(--text)' : 'var(--text-dim)',
            padding: '1rem 0.25rem',
            fontSize: '0.95rem',
            fontWeight: 600
          }}
        >
          Documentation
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('diagrams')}
          style={{
            background: 'transparent',
            border: '0',
            borderBottom: activeTab === 'diagrams' ? '2px solid var(--primary)' : '2px solid transparent',
            color: activeTab === 'diagrams' ? 'var(--text)' : 'var(--text-dim)',
            padding: '1rem 0.25rem',
            fontSize: '0.95rem',
            fontWeight: 600
          }}
        >
          Diagrams
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('resources')}
          style={{
            background: 'transparent',
            border: '0',
            borderBottom: activeTab === 'resources' ? '2px solid var(--primary)' : '2px solid transparent',
            color: activeTab === 'resources' ? 'var(--text)' : 'var(--text-dim)',
            padding: '1rem 0.25rem',
            fontSize: '0.95rem',
            fontWeight: 600
          }}
        >
          Resources
        </button>
      </div>

      {/* Dynamic Tabs Content */}
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {/* OVERVIEW TAB */}
        {activeTab === 'overview' && (
          <div style={{ padding: '2rem', maxWidth: '1000px', margin: '0 auto' }}>
            <section style={{ marginBottom: '2rem' }}>
              <h1 style={{ fontSize: '2rem', margin: '0 0 0.25rem 0', fontWeight: 700 }}>OnMixAI</h1>
              <strong style={{ color: 'var(--primary)', fontSize: '1.1rem', fontWeight: 600 }}>AI-Powered Interview Platform</strong>
              <p style={{ color: 'var(--text-dim)', marginTop: '1rem', lineHeight: 1.6 }}>
                An enterprise-grade software demonstration combining secure multi-tenancy, Row-Level Security, vector semantic search, hybrid reciprocal rank fusion, and LangGraph-backed report pipelines. Built for demonstrating generative AI engineering, clean monolithic packaging, and safe model execution architectures.
              </p>
            </section>

            {/* Metrics cards grid */}
            <section aria-labelledby="metrics-heading" style={{ marginBottom: '3rem' }}>
              <h2 id="metrics-heading" style={{ fontSize: '1.2rem', marginBottom: '1.25rem', fontWeight: 600 }}>Engineering Metrics</h2>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1.25rem' }}>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', textAlign: 'center' }}>
                  <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--primary)' }}>{METRICS.docCount}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Technical Documents</div>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', textAlign: 'center' }}>
                  <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--primary)' }}>{METRICS.diagramCount}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Architecture Diagrams</div>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', textAlign: 'center' }}>
                  <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--primary)' }}>{METRICS.apiEndpoints}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>API Endpoints</div>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', textAlign: 'center' }}>
                  <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--primary)' }}>{METRICS.coreFeatures}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Core Features</div>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', textAlign: 'center' }}>
                  <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--text)' }}>{METRICS.version}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Platform Version</div>
                </div>
              </div>
            </section>

            {/* Architecture Snapshot */}
            <section style={{ marginBottom: '3rem', padding: '1.5rem', border: '1px solid var(--border)', background: 'var(--surface)', borderRadius: 'var(--radius)' }}>
              <h3 style={{ margin: '0 0 1rem 0', fontSize: '1.1rem', fontWeight: 600 }}>Architecture Flow Snapshot</h3>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.75rem', fontSize: '0.9rem', color: 'var(--text-dim)' }}>
                <span style={{ color: 'var(--text)', fontWeight: 'bold' }}>Candidate UI</span>
                <span>→</span>
                <span style={{ color: 'var(--primary)', fontWeight: 'bold' }}>FastAPI Backend</span>
                <span>→</span>
                <span style={{ color: 'var(--warn)', fontWeight: 'bold' }}>LangGraph Graph</span>
                <span>→</span>
                <span style={{ color: 'var(--danger)', fontWeight: 'bold' }}>Azure OpenAI</span>
                <span>→</span>
                <span style={{ color: 'var(--text)', fontWeight: 'bold' }}>Evaluation engine</span>
                <span>→</span>
                <span style={{ color: 'var(--primary)', fontWeight: 'bold' }}>Citations & PDF export</span>
              </div>
            </section>

            {/* Tech Stack list */}
            <section style={{ marginBottom: '3rem' }}>
              <h3 style={{ fontSize: '1.2rem', marginBottom: '1.25rem', fontWeight: 600 }}>Technology Stack</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1.25rem' }}>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '1rem 1.25rem', borderRadius: 'var(--radius)' }}>
                  <strong style={{ fontSize: '0.95rem', display: 'block', marginBottom: '0.5rem', color: 'var(--primary)' }}>Frontend SPA</strong>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }}>React, TypeScript, Vite, TanStack Query, Vanilla CSS styling.</span>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '1rem 1.25rem', borderRadius: 'var(--radius)' }}>
                  <strong style={{ fontSize: '0.95rem', display: 'block', marginBottom: '0.5rem', color: 'var(--primary)' }}>Backend Gateway</strong>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }}>Python 3.12, FastAPI, SQLAlchemy async, Alembic.</span>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '1rem 1.25rem', borderRadius: 'var(--radius)' }}>
                  <strong style={{ fontSize: '0.95rem', display: 'block', marginBottom: '0.5rem', color: 'var(--primary)' }}>AI & Agent Orchestration</strong>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }}>LangGraph Workflow states, Azure OpenAI API routing.</span>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '1rem 1.25rem', borderRadius: 'var(--radius)' }}>
                  <strong style={{ fontSize: '0.95rem', display: 'block', marginBottom: '0.5rem', color: 'var(--primary)' }}>Storage & Database</strong>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }}>PostgreSQL, pgvector, Redis Task Queues, MinIO Object Storage.</span>
                </div>
              </div>
            </section>
          </div>
        )}

        {/* DOCUMENTATION TAB */}
        {activeTab === 'docs' && (
          <div className="docs-viewer-layout" style={{ display: 'grid', gridTemplateColumns: '300px 1fr', height: '100%' }}>
            {/* Left selector Sidebar */}
            <div style={{ borderRight: '1px solid var(--border)', padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.75rem', background: 'var(--surface)', overflowY: 'auto' }}>
              <input
                type="text"
                placeholder="Search documents..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  width: '100%',
                  background: 'var(--surface-2)',
                  border: '1px solid var(--border)',
                  color: 'var(--text)',
                  padding: '0.5rem 0.75rem',
                  borderRadius: '8px',
                  fontSize: '0.85rem',
                  marginBottom: '0.5rem'
                }}
              />

              <h4 style={{ margin: '0.5rem 0 0.5rem 0', fontSize: '0.85rem', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Documents List
              </h4>

              {filteredDocs.map((doc) => (
                <button
                  key={doc.id}
                  type="button"
                  className={`btn ${selectedDocId === doc.id ? 'btn--primary' : 'btn--ghost'}`}
                  onClick={() => setSelectedDocId(doc.id)}
                  style={{
                    justifyContent: 'flex-start',
                    textAlign: 'left',
                    width: '100%',
                    padding: '0.5rem 0.75rem',
                    fontSize: '0.85rem',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis'
                  }}
                >
                  {doc.title}
                </button>
              ))}

              {filteredDocs.length === 0 && (
                <p style={{ fontSize: '0.85rem', color: 'var(--text-dim)', textAlign: 'center', margin: '2rem 0' }}>
                  No matching documents found.
                </p>
              )}
            </div>

            {/* Right content view area */}
            <div style={{ padding: '2.5rem 3rem', overflowY: 'auto', background: 'var(--bg)' }}>
              <div style={{ maxWidth: '800px', margin: '0 auto' }}>
                <span style={{
                  background: 'var(--surface-2)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-dim)',
                  borderRadius: '6px',
                  padding: '0.2rem 0.5rem',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em'
                }}>
                  {selectedDoc.category}
                </span>
                <div style={{ marginTop: '1rem' }}>
                  {renderMarkdown(selectedDoc.content)}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* DIAGRAMS TAB */}
        {activeTab === 'diagrams' && (
          <div style={{ padding: '2rem', height: '100%' }}>
            <div style={{ maxWidth: '1100px', margin: '0 auto', height: '100%', display: 'flex', flexDirection: 'column' }}>
              <h2 style={{ fontSize: '1.4rem', margin: '0 0 1rem 0', fontWeight: 600 }}>Interactive Architecture Diagrams</h2>
              <div style={{ flex: 1, minHeight: 0 }}>
                <DiagramExplorer />
              </div>
            </div>
          </div>
        )}

        {/* RESOURCES TAB */}
        {activeTab === 'resources' && (
          <div style={{ padding: '2rem', maxWidth: '800px', margin: '0 auto' }}>
            <h2 style={{ fontSize: '1.4rem', marginBottom: '1.5rem', fontWeight: 600 }}>External Showcase Links</h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1.5rem' }}>
              
              {/* Card 1: Demo */}
              <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <strong style={{ fontSize: '1.1rem', fontWeight: 600 }}>Demo Walkthrough Video</strong>
                <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-dim)', lineHeight: 1.45 }}>
                  A recorded walkthrough illustrating user login, chat streaming, collection creation, resume ingestion, and evaluation reports generation.
                </p>
                <div style={{ marginTop: 'auto', paddingTop: '1rem' }}>
                  <button type="button" className="btn btn--primary btn--small" onClick={() => alert('Demo video player is offline. Link destination: loom.com')}>
                    Watch Video Demo
                  </button>
                </div>
              </div>

              {/* Card 2: Git Repo */}
              <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <strong style={{ fontSize: '1.1rem', fontWeight: 600 }}>GitHub Repository</strong>
                <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-dim)', lineHeight: 1.45 }}>
                  Access the complete modular monolith source code, DB migration files, unit tests, and Docker deployment environments.
                </p>
                <div style={{ marginTop: 'auto', paddingTop: '1rem' }}>
                  <button type="button" className="btn btn--primary btn--small" onClick={() => alert('GitHub navigation: Repository link is set to github.com/onmixai')}>
                    Go to Repository
                  </button>
                </div>
              </div>

            </div>
          </div>
        )}
      </div>
    </div>
  )
}
