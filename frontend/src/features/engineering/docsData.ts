export interface DocumentItem {
  id: string
  title: string
  category: string
  content: string
}

export const DOCUMENTS: DocumentItem[] = [
  {
    id: 'readme',
    title: 'README - OnMixAI Project',
    category: 'Core Documentation',
    content: `# OnMixAI

OnMixAI is a multi-tenant platform that turns an organization's documents into grounded, cited answers. It ingests documents, indexes them for hybrid semantic + keyword retrieval, and exposes that knowledge through grounded chat, structured recommendations, and exportable reports — with each tenant's data isolated at the database level.

## Features

- **Organizations & auth** — multi-tenant accounts with JWT authentication (short-lived access tokens, rotating refresh tokens).
- **Document ingestion** — upload → parse → chunk → embed, processed asynchronously by a worker queue, with per-document lifecycle and status.
- **Hybrid retrieval** — vector similarity (pgvector HNSW) combined with full-text search, filtered by tenant and per-collection permissions *before* ranking.
- **Grounded chat** — streamed answers that cite their sources or decline when the evidence is insufficient; never fabricated.
- **Recommendations** — structured decision output (recommendation, alternatives, justifications) with a confidence signal derived from retrieval evidence.
- **Reports** — multi-section, cited reports with PDF export.
- **Tenant isolation** — every tenant-owned table is protected by Postgres row-level security, with application-level scoping as defense in depth.

## Architecture

A modular monolith. The backend (FastAPI) is organized by domain — identity, knowledge, search, conversation, recommendation, reports — each following a \`router → service → repository\` layering, with cross-domain calls going through service interfaces.

- **Postgres** (with the \`pgvector\` extension) is the system of record.
- **Redis** backs the background worker queue (document ingestion, report generation, PDF export, and scheduled sweepers).
- An **S3-compatible object store** holds uploaded files and generated exports.
- LLM and embedding calls go through a single gateway behind a provider-agnostic interface, so providers can be swapped without touching feature code.

The frontend is a **React + TypeScript** app (Vite) that talks to the backend through a typed client generated from the API's OpenAPI schema.`
  },
  {
    id: 'prd',
    title: 'PRD - Product Requirements',
    category: 'Core Documentation',
    content: `# OnMixAI Product Requirements Document (PRD) v2.0

## 1. Product Overview
OnMixAI is an Enterprise AI Decision Intelligence Platform. It enables organizations to transform enterprise knowledge, documents, and operational data into actionable intelligence using Generative AI, Agentic AI, and Retrieval-Augmented Generation (RAG).

### V1 Scope
- Document ingestion and knowledge base management
- Permission-aware semantic search
- Grounded conversational AI with citations
- Structured recommendations (single-model, retrieval-grounded)
- Report generation and PDF export
- Multi-tenant identity, RBAC, and audit logging

### V2/V3 (Out of Scope)
- Dynamic multi-agent orchestration (V2)
- SSO / SAML / OIDC federation (V2) and SCIM provisioning (V3)
- External CRM, ERP, and ticketing connectors (V3)
- Fine-tuning or custom model training

## 2. Target Users
- **Knowledge Workers**: Employees searching for information in enterprise documents.
- **Technical Specialists**: Engineers, analysts, and researchers comparing candidate criteria.
- **Team Leads**: Decision makers requiring evaluation summaries and reports.

## 3. Core Domains
- **Identity**: Multi-tenant authorization, user management, and JWT flows.
- **Knowledge**: Asynchronous upload, OCR, file formats (PDF, DOCX, PPTX, XLSX), chunking, and metadata parsing.
- **Search**: Hybrid reciprocal rank fusion (RRF) with RLS permission checks.
- **Conversation**: Streaming SSE chat with citations and user feedback.
- **AI Gateway**: Provider-agnostic completes, fallbacks, token budgets, and LLM tracing.
- **Agentic Workflows**: Multi-step LangGraph processing for reports and evaluations.`
  },
  {
    id: 'architecture',
    title: 'Architecture & System Structure',
    category: 'System Architecture',
    content: `# Architecture & Codebase Layering

OnMixAI uses a **Modular Monolith** architecture style. The backend codebase is decoupled into clean domain modules, preventing circular dependencies and spaghetti imports.

## Domain Decoupling

The backend is separated into standalone domain modules:
- \`src/identity/\`: Users, organizations, roles, and session access control.
- \`src/knowledge/\`: Document parsers, chunkers, and collections database.
- \`src/search/\`: Vector queries, hybrid index scans, and RRF calculations.
- \`src/conversation/\`: Chat sessions, histories, and message state.
- \`src/recommendation/\`: Decision criteria analysis and fit outputs.
- \`src/reports/\`: LangGraph reports generation and PDF exports.
- \`src/shared/\`: Database connection helpers, global configurations, custom error handling, and security middlewares.

## Architectural Layering Rules

Inside each domain module, code is structured into strict horizontal layers:
1. **Router (\`router.py\`):** Handles HTTP-specific logic, input schema validation (via Pydantic), and wraps service calls. No database or business rules are placed here.
2. **Service (\`service.py\`):** The orchestrator of business use cases. Follows the **6-Step Service Method Pattern** (Authorize → Load → Check Invariants → Mutate → Audit → Return DTO).
3. **Repository (\`repository.py\`):** Executes SQL queries via SQLAlchemy async sessions. Banned from containing business logic.
4. **Rules (\`rules.py\`):** Pure, I/O-free utility functions that check business logic, calculations, and invariants. Allows 100% deterministic unit testing.

## Dependency Direction

Cross-domain imports are strictly enforced:
- A router can only call its domain service.
- A service can only import other domain services — it is **strictly forbidden** to import repositories, models, or internal logic of other domains directly.
- Circular domain dependencies are caught at compile-time/CI via \`import-linter\` contracts.`
  },
  {
    id: 'system-design',
    title: 'System Design & Storage',
    category: 'System Architecture',
    content: `# System Design, Multi-Tenancy & Data Storage

OnMixAI is built to support secure, multi-tenant enterprise deployments out of the box.

## Multi-Tenancy & Postgres RLS

To prevent tenant data leakage, Postgres **Row-Level Security (RLS)** is enforced on every database table:
- Every table has a mandatory \`org_id\` column that is indexed.
- RLS policies are registered in database migrations. At database connection time, the runtime role context is initialized with the actor's \`org_id\`.
- PostgreSQL automatically filters out all records belonging to other tenants. Even if application code misses a tenant constraint, the database blocks cross-tenant reads or writes.
- RLS context is established via application-level session context (\`set_tenant_context\`).

## Vector Search (pgvector)

Instead of introducing separate, complex vector database infrastructure, OnMixAI leverages PostgreSQL with the \`pgvector\` extension:
- Vector embeddings of size 1536 (generated via OpenAI text-embedding-ada-002 or equivalent) are stored directly in the \`document_chunks\` table.
- Hierarchical Navigable Small World (**HNSW**) indices are constructed on the embedding column, supporting fast cosine similarity distance operations.
- Cosine similarity filters are fused directly inside SQL queries alongside organization ID predicates, ensuring security checks execute *before* similarity ranking.

## Background Worker Queue (ARQ)

For long-running tasks, OnMixAI deploys asynchronous workers:
- **ARQ** (Redis-backed async job queue) handles heavy operations.
- Tasks include: Ingesting uploaded files (OCR, text extraction, chunking, embedding generation), report graph execution, and rendering HTML templates to PDFs via \`fpdf2\`.
- Ingestion jobs claim document slots using compare-and-set database transactions, preventing double-processing by separate worker pods.`
  },
  {
    id: 'ai-pipeline',
    title: 'AI Ingestion & Retrieval Pipeline',
    category: 'AI Engineering',
    content: `# AI Ingestion & Retrieval Pipeline

The RAG (Retrieval-Augmented Generation) pipeline consists of two primary cycles: Ingestion and Retrieval.

## The Ingestion Pipeline

1. **Upload & Storage:** Original files (PDFs, TXT, DOCX, XLSX, PPTX) are streamed to an S3 bucket (or MinIO for local dev).
2. **Parsing:** Formats are extracted using dedicated parsers (e.g. \`PyMuPDF\` for PDFs, OCR fallback via \`pytesseract\` if pages are scanned).
3. **Chunking:** Format-aware chunkers slice the document. Tabular sheets maintain their table structures; prose is chunked using sliding windows to preserve context boundaries.
4. **Embedding Generation:** Batch embedding requests are sent to the AI Gateway, generating 1536-dimension vectors.
5. **Database Indexing:** Embeddings are upserted into pgvector columns, and HNSW indices are updated.

## The Retrieval & Generation Pipeline

1. **Permission Check:** Extract the user's \`org_id\` and authorized collection permissions.
2. **Hybrid Search:** Combine two search paths:
   - **Vector Path:** Compute cosine similarity on the pgvector index.
   - **Full-Text Path:** Compute keyword matches on the BM25-based text search index.
3. **Reciprocal Rank Fusion (RRF):** Combine the two result lists using the RRF algorithm:
   $$RRF\\_Score(d) = \\sum_{m \\in M} \\frac{1}{k + r_m(d)}$$
   (where $k = 60$, and $r_m(d)$ is the rank of document $d$ in search path $m$).
4. **Prompt Injection & PII Guardrails:** Filter retrieved contexts to neutralize injection markers and redact sensitive PII (emails, phone numbers).
5. **LLM Generation:** Send structured prompt contexts to Azure OpenAI via the gateway, specifying required output JSON schemas.
6. **Citations & Grounding:** Map response sections back to actual source chunk IDs. Refuse answers if the similarity scores or evidence grounds fall below defined confidence thresholds.`
  },
  {
    id: 'langgraph-workflow',
    title: 'LangGraph Evaluation Workflow',
    category: 'AI Engineering',
    content: `# LangGraph Report Evaluation Workflow

OnMixAI structures complex report compilation workflows as a linear agent graph using **LangGraph**.

## Graph Nodes Structure

The graph is compiled as an unconditional, linear sequence of nodes:
\`START\` → \`knowledge_agent\` → \`report_agent\` → \`END\`

\`\`\`
          +---------+
          |  START  |
          +----+----+
               |
               v
     +---------+---------+
     |  knowledge_agent  |
     | (Retrieve Context)|
     +---------+---------+
               |
               v
     +---------+---------+
     |   report_agent    |
     | (Generate & Ground) |
     +---------+---------+
               |
               v
          +----+----+
          |   END   |
          +---------+
\`\`\`

### 1. The Knowledge Agent
- Responsible for permission-aware vector search retrieval and context assembly.
- Does **not** perform any LLM operations, protecting token consumption.
- If retrieved relevant source counts fall below the organization limit (\`report_min_sources\`), the node immediately writes \`error = INSUFFICIENT_EVIDENCE\` to the shared graph state and terminates.

### 2. The Report Agent
- Checks if upstream nodes wrote errors to state. If present, it passes through immediately without spending LLM tokens.
- Otherwise, it renders the prompt template and executes a structured completions call (Azure OpenAI) via the gateway, requesting a JSON response matching the \`ReportContent\` schema.
- **Grounding Validation (post-processing):** Checks all citations written by the LLM. If the LLM referenced source numbers that do not exist in the context, those citation markers are stripped.
- If an entire section contains unsubstantiated or hallucinated facts, the section is dropped.
- If zero sections survive grounding, the agent sets \`error = NO_GROUNDED_SECTIONS\`, declining to return a report. Otherwise, it compiles the output JSON along with verified attribution citations.`
  },
  {
    id: 'api-documentation',
    title: 'API Reference Documentation',
    category: 'API & Integration',
    content: `# API Endpoint Reference

The OnMixAI backend exposes REST routes under the \`/api/v1\` prefix.

## Authentication & Identity

### POST \`/auth/register\`
Creates a new tenant user account.
- **Request Body:** \`{"org_slug": "acme", "email": "user@acme.com", "password": "...", "full_name": "..."}\`
- **Response:** 200 OK with success confirmation.

### POST \`/auth/login\`
Logs a user in, returning access and refresh tokens.
- **Request Body:** \`{"org_slug": "acme", "email": "user@acme.com", "password": "..."}\`
- **Response:** \`{"access_token": "...", "refresh_token": "...", "expires_in": 3600}\` (tokens are memory-only).

---

## Chat & Simulation

### GET \`/chat/sessions\`
Lists active interview chat sessions for the authenticated user, paginated via cursors.

### POST \`/chat/sessions/{session_id}/messages\`
Streams interview interactions in real-time.
- **Request Headers:** \`Accept: text/event-stream\` (SSE connection)
- **Response:** Streamed SSE chunks containing partial tokens, followed by a final metadata chunk containing citations.

---

## Collections & Knowledge Management

### GET \`/collections\`
Lists all collections the active organization has permission to view.

### POST \`/collections/{collection_id}/documents\`
Uploads a document file to the collection.
- **Request:** Multipart FormData containing a file binary.
- **Response:** 202 Accepted. Returns \`{"document_id": "...", "status": "queued"}\` and delegates chunking/embeddings to ARQ background worker.

### GET \`/collections/{collection_id}/documents\`
Lists all active, indexed documents inside the selected collection, returning filenames, sizes, version numbers, and statuses (queued, processing, ready, failed).`
  },
  {
    id: 'engineering-decisions',
    title: 'Consolidated Engineering Decisions',
    category: 'API & Integration',
    content: `# Consolidated Engineering Decisions Log

This document records the architectural tradeoffs and technical decisions that guide the construction of OnMixAI, aligned with the Architectural Decision Records (ADRs 0001 - 0019).

## 1. Core Platform Decisions

### Why React & TypeScript
We selected React for the frontend to enable high-fidelity, interactive, and responsive components (like our chat streaming and diagram explorer). TypeScript strictly gates type safety, enforcing that schema shapes compiled from the backend OpenAPI definitions are never violated at runtime.

### Why FastAPI & Python
FastAPI provides asynchronous request handling, high concurrency support via ASGI servers, and automatic OpenAPI schema extraction. Python is the industry standard for AI integrations, with mature packages for document parsing, PDF compilation, vector calculations, and LangGraph pipelines.

### Why LangGraph
LangGraph enables structured, state-driven workflow control. Standard langchain agents can behave non-deterministically, leading to loops or escaping errors. LangGraph models the evaluation pipeline as a linear, structured state machine where errors are handled as typed data states, ensuring stable enterprise operations.

### Why Azure OpenAI
Azure OpenAI offers strict data privacy policies, private endpoints, and enterprise SLAs. The LLM Gateway is designed to route requests to Azure models, with automated local caching and token budgeting limits.

---

## 2. Security Design
- **Authentication:** Standard JWT token exchange. Access tokens expire within 15 minutes to reduce hijack windows.
- **Authorization:** Granular RBAC (Owner, Admin, Member) permissions validated at service boundaries.
- **Prompt Injection Mitigation:** System instructions and document contents are scrubbed for control strings and delimiter escapes before being injected into prompt variables.
- **Input Validation:** Strict parsing of fields via Pydantic on the backend, rejecting arbitrary payload attributes.
- **Data Protection:** PostgreSQL row-level security (RLS) is forced on all tenant tables, isolating organization data at the database level.
- **Rate Limiting:** Bounded limits enforced via Redis token-bucket middleware on public endpoints.

---

## 3. Testing Strategy
- **Unit Testing:** Fast, mock-driven tests for repositories and logic rules (\`tests/knowledge/test_service.py\`).
- **Integration Testing:** Uses real PostgreSQL instances to assert RLS query constraints.
- **API Testing:** End-to-end endpoint tests via \`httpx.AsyncClient\` verifying payload schemas and error status codes.
- **AI Evaluation Testing:** Golden Q&A evaluation runner (\`make eval-retrieval\` and \`make eval-generation\`) measuring retrieval MRR and LLM faithfulness scores in CI.

---

## 4. Cost Optimization
- **Token Optimization:** System prompts use compact templates, and the first node of LangGraph checks context eligibility prior to invoking costly LLM models.
- **Embedding Cache:** Computes content hash on document blocks; chunk embeddings are skipped if content hashes match existing records.

---

## 5. Deployment & Operations
- **Deployment:** Frontend is static SPA deployed to Vercel CDN; FastAPI API and workers run on Render web services.
- **Monitoring:** API routes log in structured JSON via \`structlog\`, tracing requests via unique \`X-Request-ID\` headers. Langfuse captures LLM traces (prompt version, tokens consumed, latency metrics, and citation matches).
- **System Health:** A dedicated health route (\`/health\`) probes PostgreSQL, Redis, and MinIO connectivity.

---

## 6. Future Roadmap
- **Enterprise SSO:** Support SAML 2.0 / OIDC user pools.
- **Agent Orchestration (V2):** Introduce dynamic conditional routing to delegate research and compliance checks to specialized agents.
- **Scale:** Transition PostgreSQL vectors to distributed setups as collection sizes approach the 10-million milestone.`
  }
]
