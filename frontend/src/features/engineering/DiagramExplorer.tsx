import { useState, useRef } from 'react'
import type { MouseEvent, ReactNode } from 'react'

interface DiagramInfo {
  id: string
  title: string
  description: string
  children: ReactNode
}

// Diagrams Data declared statically outside the component to prevent re-creation and fix React Ref lint issues
const DIAGRAMS: DiagramInfo[] = [
  {
    id: 'system-arch',
    title: 'System Architecture',
    description: 'OnMixAI is constructed as a modular monolith. The React frontend communicates with the FastAPI API Gateway. Business domains (Identity, Knowledge, Search, Conversation, Recommendation, Reports) are isolated in separate backend folders. A background worker queue (ARQ + Redis) handles async document processing, and pgvector handles similarity ranking within Postgres.',
    children: (
      <>
        {/* React Frontend */}
        <g transform="translate(40, 200)">
          <rect width="140" height="80" rx="8" fill="#20242f" stroke="#4f7cff" strokeWidth="2" />
          <text x="70" y="35" fill="#ffffff" fontWeight="bold" fontSize="13" textAnchor="middle">React SPA</text>
          <text x="70" y="55" fill="#a2a8b6" fontSize="10" textAnchor="middle">Vite + TS Client</text>
        </g>

        {/* FastAPI API Gateway */}
        <g transform="translate(240, 150)">
          <rect width="180" height="180" rx="8" fill="#20242f" stroke="#4f7cff" strokeWidth="2" />
          <text x="90" y="30" fill="#ffffff" fontWeight="bold" fontSize="14" textAnchor="middle">FastAPI Gateway</text>
          <line x1="10" y1="45" x2="170" y2="45" stroke="#2c313d" strokeWidth="1.5" />
          
          {/* Routers */}
          <rect x="20" y="60" width="140" height="30" rx="4" fill="#181b24" stroke="#2c313d" />
          <text x="90" y="78" fill="#e7e9ee" fontSize="11" textAnchor="middle">API Routers</text>

          {/* Services */}
          <rect x="20" y="105" width="140" height="30" rx="4" fill="#181b24" stroke="#2c313d" />
          <text x="90" y="123" fill="#e7e9ee" fontSize="11" textAnchor="middle">Service Domains</text>

          {/* Repositories */}
          <rect x="20" y="140" width="140" height="30" rx="4" fill="#181b24" stroke="#2c313d" />
          <text x="90" y="158" fill="#e7e9ee" fontSize="11" textAnchor="middle">Repositories</text>
        </g>

        {/* Postgres with pgvector */}
        <g transform="translate(520, 70)">
          <rect width="200" height="90" rx="8" fill="#20242f" stroke="#5fd08a" strokeWidth="2" />
          <text x="100" y="30" fill="#ffffff" fontWeight="bold" fontSize="14" textAnchor="middle">PostgreSQL DB</text>
          <text x="100" y="50" fill="#5fd08a" fontSize="11" textAnchor="middle">Row-Level Security (RLS)</text>
          <text x="100" y="70" fill="#a2a8b6" fontSize="10" textAnchor="middle">pgvector HNSW index</text>
        </g>

        {/* Redis Task Queue */}
        <g transform="translate(520, 200)">
          <rect width="200" height="80" rx="8" fill="#20242f" stroke="#ffb84d" strokeWidth="2" />
          <text x="100" y="30" fill="#ffffff" fontWeight="bold" fontSize="14" textAnchor="middle">Redis + ARQ</text>
          <text x="100" y="55" fill="#a2a8b6" fontSize="11" textAnchor="middle">Async Processing Queue</text>
        </g>

        {/* S3 Object Storage */}
        <g transform="translate(520, 320)">
          <rect width="200" height="80" rx="8" fill="#20242f" stroke="#4f7cff" strokeWidth="2" />
          <text x="100" y="30" fill="#ffffff" fontWeight="bold" fontSize="14" textAnchor="middle">S3 / MinIO Storage</text>
          <text x="100" y="55" fill="#a2a8b6" fontSize="11" textAnchor="middle">Resumes & Exports</text>
        </g>

        {/* LLM Gateway & Azure OpenAI */}
        <g transform="translate(240, 380)">
          <rect width="180" height="75" rx="8" fill="#20242f" stroke="#ff5d6c" strokeWidth="2" />
          <text x="90" y="30" fill="#ffffff" fontWeight="bold" fontSize="13" textAnchor="middle">AI Gateway (LiteLLM)</text>
          <text x="90" y="55" fill="#ff5d6c" fontSize="11" textAnchor="middle">Azure OpenAI Fallback</text>
        </g>

        {/* Connectors & Arrows */}
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#a2a8b6" />
          </marker>
        </defs>

        {/* React to Gateway */}
        <line x1="180" y1="240" x2="230" y2="240" stroke="#a2a8b6" strokeWidth="2" markerEnd="url(#arrow)" />
        
        {/* Gateway to Postgres */}
        <path d="M 420 180 L 470 180 L 470 115 L 510 115" fill="none" stroke="#a2a8b6" strokeWidth="2" markerEnd="url(#arrow)" />

        {/* Gateway to Redis */}
        <line x1="420" y1="240" x2="510" y2="240" stroke="#a2a8b6" strokeWidth="2" markerEnd="url(#arrow)" />

        {/* Gateway to Storage */}
        <path d="M 420 300 L 470 300 L 470 360 L 510 360" fill="none" stroke="#a2a8b6" strokeWidth="2" markerEnd="url(#arrow)" />

        {/* Gateway to AI */}
        <line x1="330" y1="330" x2="330" y2="370" stroke="#a2a8b6" strokeWidth="2" markerEnd="url(#arrow)" />
      </>
    )
  },
  {
    id: 'ai-pipeline',
    title: 'AI Ingestion & Retrieval Pipeline',
    description: 'The RAG cycle: Documents are chunked (preserving table layouts) and stored with 1536-dimension vectors in pgvector. At search time, natural language is query-embedded, combined with full-text search, and merged via Reciprocal Rank Fusion (RRF) before being scoped by tenant ID and ACL constraints.',
    children: (
      <>
        {/* Ingestion Stream */}
        <text x="120" y="40" fill="#5fd08a" fontWeight="bold" fontSize="13">1. INGESTION CYCLE</text>
        <g transform="translate(40, 60)">
          <rect width="180" height="40" rx="6" fill="#20242f" stroke="#5fd08a" />
          <text x="90" y="24" fill="#ffffff" fontSize="11" textAnchor="middle">Upload Document File</text>
        </g>
        <g transform="translate(40, 130)">
          <rect width="180" height="40" rx="6" fill="#20242f" stroke="#5fd08a" />
          <text x="90" y="24" fill="#ffffff" fontSize="11" textAnchor="middle">Format Chunking (Prose/Tables)</text>
        </g>
        <g transform="translate(40, 200)">
          <rect width="180" height="40" rx="6" fill="#20242f" stroke="#5fd08a" />
          <text x="90" y="24" fill="#ffffff" fontSize="11" textAnchor="middle">Batch 1536 Vector Embedding</text>
        </g>
        <g transform="translate(40, 270)">
          <rect width="180" height="40" rx="6" fill="#20242f" stroke="#5fd08a" />
          <text x="90" y="24" fill="#ffffff" fontSize="11" textAnchor="middle">pgvector Database Index</text>
        </g>

        {/* Retrieval Stream */}
        <text x="480" y="40" fill="#4f7cff" fontWeight="bold" fontSize="13">2. RETRIEVAL & GENERATION CYCLE</text>
        <g transform="translate(380, 60)">
          <rect width="180" height="40" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="90" y="24" fill="#ffffff" fontSize="11" textAnchor="middle">User Natural Query</text>
        </g>
        <g transform="translate(380, 130)">
          <rect width="180" height="40" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="90" y="24" fill="#ffffff" fontSize="11" textAnchor="middle">Hybrid Search (Vector + FTS)</text>
        </g>
        <g transform="translate(380, 200)">
          <rect width="180" height="40" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="90" y="24" fill="#ffffff" fontSize="11" textAnchor="middle">RRF Reciprocal Fusion</text>
        </g>
        <g transform="translate(380, 270)">
          <rect width="180" height="40" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="90" y="24" fill="#ffffff" fontSize="11" textAnchor="middle">Prompt Injection Guardrails</text>
        </g>
        <g transform="translate(590, 160)">
          <rect width="170" height="80" rx="8" fill="#20242f" stroke="#ff5d6c" strokeWidth="2" />
          <text x="85" y="30" fill="#ffffff" fontWeight="bold" fontSize="12" textAnchor="middle">Azure OpenAI</text>
          <text x="85" y="50" fill="#a2a8b6" fontSize="10" textAnchor="middle">JSON Schema Answer</text>
          <text x="85" y="65" fill="#a2a8b6" fontSize="10" textAnchor="middle">+ Citations Check</text>
        </g>

        {/* Connectors */}
        <defs>
          <marker id="arrow-green" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#5fd08a" />
          </marker>
          <marker id="arrow-blue" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#4f7cff" />
          </marker>
        </defs>

        <line x1="130" y1="100" x2="130" y2="120" stroke="#5fd08a" strokeWidth="1.5" markerEnd="url(#arrow-green)" />
        <line x1="130" y1="170" x2="130" y2="190" stroke="#5fd08a" strokeWidth="1.5" markerEnd="url(#arrow-green)" />
        <line x1="130" y1="240" x2="130" y2="260" stroke="#5fd08a" strokeWidth="1.5" markerEnd="url(#arrow-green)" />

        <line x1="470" y1="100" x2="470" y2="120" stroke="#4f7cff" strokeWidth="1.5" markerEnd="url(#arrow-blue)" />
        <line x1="470" y1="170" x2="470" y2="190" stroke="#4f7cff" strokeWidth="1.5" markerEnd="url(#arrow-blue)" />
        <line x1="470" y1="240" x2="470" y2="260" stroke="#4f7cff" strokeWidth="1.5" markerEnd="url(#arrow-blue)" />
        
        {/* Vector DB link to Search */}
        <path d="M 220 290 L 300 290 L 300 150 L 370 150" fill="none" stroke="#5fd08a" strokeWidth="1.5" strokeDasharray="4" markerEnd="url(#arrow-green)" />
        
        {/* RRF to LLM */}
        <path d="M 560 290 L 675 290 L 675 250" fill="none" stroke="#4f7cff" strokeWidth="1.5" markerEnd="url(#arrow-blue)" />
      </>
    )
  },
  {
    id: 'langgraph-workflow',
    title: 'LangGraph Report Workflow',
    description: 'The LangGraph pipeline runs as a linear, state-driven execution flow. Errors are written directly into the shared state rather than throwing exceptions, ensuring stable execution.',
    children: (
      <>
        {/* START */}
        <g transform="translate(80, 210)">
          <circle cx="30" cy="30" r="30" fill="#20242f" stroke="#4f7cff" strokeWidth="2" />
          <text x="30" y="35" fill="#ffffff" fontWeight="bold" fontSize="11" textAnchor="middle">START</text>
        </g>

        {/* Node 1: knowledge_agent */}
        <g transform="translate(200, 180)">
          <rect width="180" height="90" rx="6" fill="#20242f" stroke="#5fd08a" strokeWidth="2" />
          <text x="90" y="25" fill="#ffffff" fontWeight="bold" fontSize="13" textAnchor="middle">knowledge_agent</text>
          <text x="90" y="48" fill="#a2a8b6" fontSize="10" textAnchor="middle">Retrieve context docs</text>
          <text x="90" y="68" fill="#a2a8b6" fontSize="10" textAnchor="middle">Check minimum evidence</text>
        </g>

        {/* Node 2: report_agent */}
        <g transform="translate(480, 180)">
          <rect width="180" height="90" rx="6" fill="#20242f" stroke="#ff5d6c" strokeWidth="2" />
          <text x="90" y="25" fill="#ffffff" fontWeight="bold" fontSize="13" textAnchor="middle">report_agent</text>
          <text x="90" y="48" fill="#a2a8b6" fontSize="10" textAnchor="middle">Structured LLM call</text>
          <text x="90" y="68" fill="#a2a8b6" fontSize="10" textAnchor="middle">Grounding citation filter</text>
        </g>

        {/* END */}
        <g transform="translate(710, 210)">
          <circle cx="30" cy="30" r="30" fill="#20242f" stroke="#ffb84d" strokeWidth="2" />
          <text x="30" y="35" fill="#ffffff" fontWeight="bold" fontSize="11" textAnchor="middle">END</text>
        </g>

        {/* Arrows */}
        <line x1="140" y1="240" x2="190" y2="240" stroke="#a2a8b6" strokeWidth="2" markerEnd="url(#arrow)" />
        
        {/* Branch logic for insufficient evidence */}
        <path d="M 290 270 L 290 340 L 740 340 L 740 280" fill="none" stroke="#ffb84d" strokeWidth="1.5" strokeDasharray="4" markerEnd="url(#arrow)" />
        <text x="515" y="330" fill="#ffb84d" fontSize="10" textAnchor="middle">Fail: INSUFFICIENT_EVIDENCE</text>

        <line x1="380" y1="225" x2="470" y2="225" stroke="#5fd08a" strokeWidth="2" markerEnd="url(#arrow)" />
        
        {/* Failed grounding route */}
        <path d="M 570 270 L 570 300 L 725 300 L 740 275" fill="none" stroke="#ffb84d" strokeWidth="1.5" strokeDasharray="4" />
        <text x="650" y="295" fill="#ffb84d" fontSize="9" textAnchor="middle">NO_GROUNDED_SECTIONS</text>

        <line x1="660" y1="225" x2="700" y2="225" stroke="#ff5d6c" strokeWidth="2" markerEnd="url(#arrow)" />
      </>
    )
  },
  {
    id: 'deploy-arch',
    title: 'Deployment Architecture',
    description: 'The React application runs as a statically built Single Page Application served globally via Vercel Edge CDN. The FastAPI service runs inside Docker containers hosted on Render, scaling with traffic. PostgreSQL databases are hosted on managed instances (Supabase or AWS RDS), with MinIO/S3 object stores hosting media resources.',
    children: (
      <>
        {/* Vercel Frontend */}
        <g transform="translate(60, 200)">
          <rect width="180" height="100" rx="8" fill="#20242f" stroke="#4f7cff" strokeWidth="2" />
          <text x="90" y="35" fill="#ffffff" fontWeight="bold" fontSize="14" textAnchor="middle">Vercel CDN</text>
          <text x="90" y="60" fill="#a2a8b6" fontSize="11" textAnchor="middle">Frontend SPA Static Assets</text>
          <text x="90" y="80" fill="#a2a8b6" fontSize="10" textAnchor="middle">Global Edge Routing</text>
        </g>

        {/* Render Backend */}
        <g transform="translate(310, 150)">
          <rect width="200" height="180" rx="8" fill="#20242f" stroke="#ff5d6c" strokeWidth="2" />
          <text x="100" y="35" fill="#ffffff" fontWeight="bold" fontSize="14" textAnchor="middle">Render Services</text>
          <text x="100" y="55" fill="#ff5d6c" fontSize="11" textAnchor="middle">Docker Containers</text>
          <line x1="15" y1="70" x2="185" y2="70" stroke="#2c313d" strokeWidth="1.5" />
          
          {/* API Web App */}
          <rect x="25" y="85" width="150" height="35" rx="4" fill="#181b24" stroke="#2c313d" />
          <text x="100" y="107" fill="#e7e9ee" fontSize="11" textAnchor="middle">FastAPI Web App</text>

          {/* Ingestion Workers */}
          <rect x="25" y="130" width="150" height="35" rx="4" fill="#181b24" stroke="#2c313d" />
          <text x="100" y="152" fill="#e7e9ee" fontSize="11" textAnchor="middle">ARQ Background Worker</text>
        </g>

        {/* Database Layer */}
        <g transform="translate(580, 180)">
          <rect width="180" height="130" rx="8" fill="#20242f" stroke="#5fd08a" strokeWidth="2" />
          <text x="90" y="35" fill="#ffffff" fontWeight="bold" fontSize="14" textAnchor="middle">Data Layer</text>
          <line x1="15" y1="50" x2="165" y2="50" stroke="#2c313d" strokeWidth="1.5" />
          
          {/* Supabase DB */}
          <rect x="20" y="65" width="140" height="25" rx="4" fill="#181b24" stroke="#2c313d" />
          <text x="90" y="81" fill="#e7e9ee" fontSize="10" textAnchor="middle">Postgres (Supabase)</text>

          {/* AWS S3 */}
          <rect x="20" y="95" width="140" height="25" rx="4" fill="#181b24" stroke="#2c313d" />
          <text x="90" y="111" fill="#e7e9ee" fontSize="10" textAnchor="middle">AWS S3 Storage</text>
        </g>

        {/* Connectors */}
        <line x1="240" y1="240" x2="300" y2="240" stroke="#a2a8b6" strokeWidth="2" markerEnd="url(#arrow)" />
        <line x1="510" y1="240" x2="570" y2="240" stroke="#a2a8b6" strokeWidth="2" markerEnd="url(#arrow)" />
      </>
    )
  },
  {
    id: 'db-schema',
    title: 'Database Schema (ERD)',
    description: 'The database ERD showing relations scoped by tenant organization context. Organizations act as root tenants. All major tables (Users, Collections, Documents, Sessions, Messages, Recommendations, Reports) store org_id to trigger Row-Level Security checks.',
    children: (
      <>
        {/* Table: Organizations */}
        <g transform="translate(40, 40)">
          <rect width="180" height="80" rx="6" fill="#20242f" stroke="#4f7cff" strokeWidth="2" />
          <text x="10" y="25" fill="#ffffff" fontWeight="bold" fontSize="12">organizations</text>
          <line x1="0" y1="35" x2="180" y2="35" stroke="#2c313d" />
          <text x="10" y="52" fill="#5fd08a" fontSize="11">id: UUID [PK]</text>
          <text x="10" y="70" fill="#e7e9ee" fontSize="10">slug: VARCHAR [U]</text>
        </g>

        {/* Table: Users */}
        <g transform="translate(260, 40)">
          <rect width="180" height="110" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="10" y="25" fill="#ffffff" fontWeight="bold" fontSize="12">users</text>
          <line x1="0" y1="35" x2="180" y2="35" stroke="#2c313d" />
          <text x="10" y="52" fill="#5fd08a" fontSize="11">id: UUID [PK]</text>
          <text x="10" y="70" fill="#ffb84d" fontSize="10">org_id: UUID [FK]</text>
          <text x="10" y="85" fill="#e7e9ee" fontSize="10">email: VARCHAR</text>
          <text x="10" y="100" fill="#e7e9ee" fontSize="10">role: RoleEnum</text>
        </g>

        {/* Table: Collections */}
        <g transform="translate(40, 180)">
          <rect width="180" height="90" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="10" y="25" fill="#ffffff" fontWeight="bold" fontSize="12">collections</text>
          <line x1="0" y1="35" x2="180" y2="35" stroke="#2c313d" />
          <text x="10" y="52" fill="#5fd08a" fontSize="11">id: UUID [PK]</text>
          <text x="10" y="70" fill="#ffb84d" fontSize="10">org_id: UUID [FK]</text>
          <text x="10" y="85" fill="#e7e9ee" fontSize="10">name: VARCHAR</text>
        </g>

        {/* Table: Documents */}
        <g transform="translate(260, 180)">
          <rect width="180" height="120" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="10" y="25" fill="#ffffff" fontWeight="bold" fontSize="12">documents</text>
          <line x1="0" y1="35" x2="180" y2="35" stroke="#2c313d" />
          <text x="10" y="52" fill="#5fd08a" fontSize="11">id: UUID [PK]</text>
          <text x="10" y="70" fill="#ffb84d" fontSize="10">collection_id: UUID [FK]</text>
          <text x="10" y="85" fill="#ffb84d" fontSize="10">org_id: UUID [FK]</text>
          <text x="10" y="100" fill="#e7e9ee" fontSize="10">filename: VARCHAR</text>
          <text x="10" y="114" fill="#e7e9ee" fontSize="10">status: DocStatus</text>
        </g>

        {/* Table: Sessions */}
        <g transform="translate(480, 40)">
          <rect width="180" height="90" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="10" y="25" fill="#ffffff" fontWeight="bold" fontSize="12">chat_sessions</text>
          <line x1="0" y1="35" x2="180" y2="35" stroke="#2c313d" />
          <text x="10" y="52" fill="#5fd08a" fontSize="11">id: UUID [PK]</text>
          <text x="10" y="70" fill="#ffb84d" fontSize="10">user_id: UUID [FK]</text>
          <text x="10" y="85" fill="#ffb84d" fontSize="10">org_id: UUID [FK]</text>
        </g>

        {/* Table: Messages */}
        <g transform="translate(480, 180)">
          <rect width="180" height="110" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="10" y="25" fill="#ffffff" fontWeight="bold" fontSize="12">chat_messages</text>
          <line x1="0" y1="35" x2="180" y2="35" stroke="#2c313d" />
          <text x="10" y="52" fill="#5fd08a" fontSize="11">id: UUID [PK]</text>
          <text x="10" y="70" fill="#ffb84d" fontSize="10">session_id: UUID [FK]</text>
          <text x="10" y="85" fill="#e7e9ee" fontSize="10">content: TEXT</text>
          <text x="10" y="100" fill="#e7e9ee" fontSize="10">citations: JSONB</text>
        </g>

        {/* Table: Recommendations */}
        <g transform="translate(40, 340)">
          <rect width="180" height="110" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="10" y="25" fill="#ffffff" fontWeight="bold" fontSize="12">recommendations</text>
          <line x1="0" y1="35" x2="180" y2="35" stroke="#2c313d" />
          <text x="10" y="52" fill="#5fd08a" fontSize="11">id: UUID [PK]</text>
          <text x="10" y="70" fill="#ffb84d" fontSize="10">org_id: UUID [FK]</text>
          <text x="10" y="85" fill="#e7e9ee" fontSize="10">fit_score: FLOAT</text>
          <text x="10" y="100" fill="#e7e9ee" fontSize="10">outcome: JSONB</text>
        </g>

        {/* Table: Reports */}
        <g transform="translate(260, 340)">
          <rect width="180" height="110" rx="6" fill="#20242f" stroke="#4f7cff" />
          <text x="10" y="25" fill="#ffffff" fontWeight="bold" fontSize="12">evaluation_reports</text>
          <line x1="0" y1="35" x2="180" y2="35" stroke="#2c313d" />
          <text x="10" y="52" fill="#5fd08a" fontSize="11">id: UUID [PK]</text>
          <text x="10" y="70" fill="#ffb84d" fontSize="10">org_id: UUID [FK]</text>
          <text x="10" y="85" fill="#e7e9ee" fontSize="10">status: ReportStatus</text>
          <text x="10" y="100" fill="#e7e9ee" fontSize="10">citations: JSONB</text>
        </g>

        {/* Relations Arrows */}
        <path d="M 220 80 L 260 80" stroke="#a2a8b6" strokeWidth="1" strokeDasharray="3" />
        <path d="M 130 120 L 130 180" stroke="#a2a8b6" strokeWidth="1" strokeDasharray="3" />
        <path d="M 220 230 L 260 230" stroke="#a2a8b6" strokeWidth="1" strokeDasharray="3" />
        <path d="M 440 85 L 480 85" stroke="#a2a8b6" strokeWidth="1" strokeDasharray="3" />
        <path d="M 570 130 L 570 180" stroke="#a2a8b6" strokeWidth="1" strokeDasharray="3" />
      </>
    )
  }
]

export function DiagramExplorer() {
  const [activeDiagram, setActiveDiagram] = useState<string>('system-arch')
  const [zoom, setZoom] = useState<number>(1)
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 })
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false)
  const [activeDragging, setActiveDragging] = useState<boolean>(false)
  
  const dragStart = useRef<{ x: number; y: number }>({ x: 0, y: 0 })
  const svgRef = useRef<SVGSVGElement>(null)

  // Zoom handlers
  const handleZoomIn = () => setZoom((z) => Math.min(z + 0.15, 3))
  const handleZoomOut = () => setZoom((z) => Math.max(z - 0.15, 0.4))
  const handleReset = () => {
    setZoom(1)
    setPan({ x: 0, y: 0 })
  }

  // Pan handlers
  const handleMouseDown = (e: MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return // Left click only
    setActiveDragging(true)
    dragStart.current = { x: e.clientX - pan.x, y: e.clientY - pan.y }
  }

  const handleMouseMove = (e: MouseEvent<HTMLDivElement>) => {
    if (!activeDragging) return
    setPan({
      x: e.clientX - dragStart.current.x,
      y: e.clientY - dragStart.current.y
    })
  }

  const handleMouseUpOrLeave = () => {
    setActiveDragging(false)
  }

  // Download SVG
  const handleDownload = () => {
    if (!svgRef.current) return
    const svgData = new XMLSerializer().serializeToString(svgRef.current)
    const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${activeDiagram}.svg`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  const currentDiagram = DIAGRAMS.find((d) => d.id === activeDiagram) ?? DIAGRAMS[0]

  return (
    <div className="diagram-explorer" style={{ display: 'grid', gridTemplateColumns: '300px 1fr', height: '650px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
      {/* Side selection bar */}
      <div style={{ padding: '1.25rem', borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: '0.75rem', overflowY: 'auto' }}>
        <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.85rem', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Select Diagram
        </h4>
        {DIAGRAMS.map((d) => (
          <button
            key={d.id}
            type="button"
            className={`btn ${activeDiagram === d.id ? 'btn--primary' : 'btn--ghost'}`}
            onClick={() => {
              setActiveDiagram(d.id)
              setZoom(1)
              setPan({ x: 0, y: 0 })
            }}
            style={{
              justifyContent: 'flex-start',
              textAlign: 'left',
              width: '100%',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              padding: '0.5rem 0.75rem',
              fontSize: '0.9rem'
            }}
          >
            {d.title}
          </button>
        ))}

        <div style={{ marginTop: 'auto', paddingTop: '1.5rem', borderTop: '1px solid var(--border)' }}>
          <h5 style={{ margin: '0 0 0.5rem 0', fontSize: '0.85rem', color: 'var(--text-dim)', fontWeight: 600 }}>Diagram Description</h5>
          <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-dim)', lineHeight: 1.45 }}>
            {currentDiagram.description}
          </p>
        </div>
      </div>

      {/* Main interactive SVG viewer */}
      <div style={{ display: 'flex', flexDirection: 'column', position: 'relative', background: 'var(--bg)', overflow: 'hidden' }}>
        {/* Controls Toolbar */}
        <div style={{ display: 'flex', gap: '0.5rem', padding: '0.75rem', borderBottom: '1px solid var(--border)', background: 'var(--surface)', zIndex: 10 }}>
          <button type="button" className="btn btn--ghost btn--small" onClick={handleZoomIn}>🔍 In</button>
          <button type="button" className="btn btn--ghost btn--small" onClick={handleZoomOut}>🔍 Out</button>
          <button type="button" className="btn btn--ghost btn--small" onClick={handleReset}>🔄 Reset</button>
          <button type="button" className="btn btn--ghost btn--small" onClick={() => setIsFullscreen(!isFullscreen)}>
            {isFullscreen ? '📴 Exit Full' : '📺 Fullscreen'}
          </button>
          <button type="button" className="btn btn--ghost btn--small" onClick={handleDownload} style={{ marginLeft: 'auto' }}>
            📥 Download SVG
          </button>
        </div>

        {/* Interactive canvas area */}
        <div
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUpOrLeave}
          onMouseLeave={handleMouseUpOrLeave}
          style={{
            flex: 1,
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            overflow: 'hidden',
            padding: '2rem',
            userSelect: 'none'
          }}
        >
          <svg
            ref={svgRef}
            viewBox="0 0 800 500"
            style={{
              width: '100%',
              height: '100%',
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: 'center center',
              transition: activeDragging ? 'none' : 'transform 0.15s ease-out',
              cursor: activeDragging ? 'grabbing' : 'grab'
            }}
          >
            {/* Background container rect */}
            <rect width="800" height="500" rx="10" fill="#181b24" stroke="#2c313d" strokeWidth="2" />
            {currentDiagram.children}
          </svg>
        </div>

        {/* Fullscreen Overlay */}
        {isFullscreen && (
          <div style={{
            position: 'fixed',
            inset: 0,
            background: 'var(--bg)',
            zIndex: 1000,
            display: 'flex',
            flexDirection: 'column'
          }}>
            <div style={{ display: 'flex', gap: '0.5rem', padding: '0.75rem', borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
              <strong style={{ alignSelf: 'center', marginLeft: '0.5rem' }}>{currentDiagram.title}</strong>
              <button type="button" className="btn btn--ghost btn--small" onClick={handleZoomIn} style={{ marginLeft: 'auto' }}>🔍 In</button>
              <button type="button" className="btn btn--ghost btn--small" onClick={handleZoomOut}>🔍 Out</button>
              <button type="button" className="btn btn--ghost btn--small" onClick={handleReset}>🔄 Reset</button>
              <button type="button" className="btn btn--danger btn--small" onClick={() => setIsFullscreen(false)}>Close Fullscreen</button>
            </div>
            <div
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUpOrLeave}
              onMouseLeave={handleMouseUpOrLeave}
              style={{
                flex: 1,
                position: 'relative',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                overflow: 'hidden',
                userSelect: 'none'
              }}
            >
              <svg
                viewBox="0 0 800 500"
                style={{
                  width: '100%',
                  height: '100%',
                  transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                  transformOrigin: 'center center',
                  transition: activeDragging ? 'none' : 'transform 0.15s ease-out',
                  cursor: activeDragging ? 'grabbing' : 'grab'
                }}
              >
                {/* Background container rect */}
                <rect width="800" height="500" rx="10" fill="#181b24" stroke="#2c313d" strokeWidth="2" />
                {currentDiagram.children}
              </svg>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
