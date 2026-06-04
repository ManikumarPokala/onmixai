# OnMixAI Product Requirements Document (PRD) v2.0

## 1. Product Overview

### Product Name

OnMixAI

### Product Category

Enterprise AI Decision Intelligence Platform

### Product Vision

OnMixAI enables organizations to transform enterprise knowledge, documents, and operational data into actionable intelligence using Generative AI, Agentic AI, and Retrieval-Augmented Generation (RAG).

### Mission

Help organizations retrieve knowledge, generate recommendations, automate decision workflows, and create business-ready outputs through AI.

### In Scope (V1)

* Document ingestion and knowledge base management
* Permission-aware semantic search
* Grounded conversational AI with citations
* Structured recommendations (single-model, retrieval-grounded)
* Report generation and PDF export
* Multi-tenant identity, RBAC, and audit logging

### Out of Scope (V1)

* Dynamic multi-agent orchestration (V2)
* SSO / SAML / OIDC federation (V2) and SCIM provisioning (V3)
* External system integrations — CRM, ERP, ticketing (V3)
* Fine-tuning or custom model training
* Real-time collaborative editing of generated reports
* Mobile applications

---

# 2. Problem Statement

Organizations generate large amounts of knowledge across:

* Product Documentation
* Technical Documentation
* Internal Knowledge Bases
* Standard Operating Procedures
* Compliance Documents
* Training Materials
* Research Reports

This information is often:

* Fragmented
* Difficult to search
* Time-consuming to analyze
* Difficult to convert into decisions

Current Process:

Search → Read → Analyze → Compare → Report → Decide

Desired Process:

Ask → Retrieve → Analyze → Recommend → Report

---

# 3. Business Goals

### Goal 1

Reduce time spent searching for information.

### Goal 2

Improve decision-making quality.

### Goal 3

Increase knowledge accessibility.

### Goal 4

Provide explainable AI recommendations.

### Goal 5

Enable enterprise-grade governance and compliance.

---

# 4. Target Users

## Primary Users

### Knowledge Workers

Employees who need information from enterprise documents.

### Technical Specialists

Engineers, consultants, researchers, analysts.

### Team Leads

Decision makers requiring summaries and recommendations.

### Operations Teams

Users requiring process and workflow intelligence.

---

# 5. Assumptions and Dependencies

### Assumptions

* Customers can provide documents in supported formats; legacy formats are converted before upload.
* Each organization operates within a single data region in V1.
* English is the primary document language in V1; multilingual support is a V2 consideration.
* LLM inference is consumed via external provider APIs; no self-hosted models in V1.

### Dependencies

* LLM provider availability (Azure OpenAI, OpenAI, Gemini, Anthropic) and their rate limits.
* Embedding model API for vector generation.
* OCR engine for scanned document processing.
* Object storage for original document files.

---

# 6. Core Product Domains

## Domain 1: Identity Intelligence

Purpose:
Manage authentication, authorization, and organizational access.

Features:

* User Registration
* Login
* JWT Authentication (access + refresh tokens)
* Role-Based Access Control
* Organization Management
* User Management

Tenancy Model:

* Shared database, shared schema.
* Every tenant-owned row carries an `org_id`.
* PostgreSQL Row-Level Security enforces tenant isolation at the database layer, in addition to application-layer scoping.

Roadmap Note:

* SSO (OIDC / SAML) — V2.
* SCIM provisioning — V3.

---

## Domain 2: Knowledge Intelligence

Purpose:
Transform enterprise documents into searchable knowledge and manage their full lifecycle.

Features:

* Document Upload (async pipeline)
* OCR Processing
* Metadata Extraction
* Chunking (format-aware: prose, tables, slides)
* Embedding Generation
* Knowledge Collections
* Document Versioning (re-upload replaces and re-indexes)
* Document Deletion (cascading purge of chunks, embeddings, and search index entries)
* Re-indexing (triggered on embedding model change or chunking config change)

Ingestion Pipeline:

Upload → Queued → Processing (OCR → Extract → Chunk → Embed) → Ready | Failed

* Processing is asynchronous via a worker queue.
* Document status is visible to the user at every stage.
* Failed documents support retry; permanent failures report a reason.
* Partial failures (e.g., 3 of 50 pages unreadable) are surfaced, not silently dropped.

Limits (defaults, configurable per organization):

* Max file size: 50 MB
* Max pages per document: 2,000
* Max documents per organization: quota-based per plan

Supported Formats:

* PDF (including scanned, via OCR)
* DOCX
* PPTX
* XLSX
* TXT

---

## Domain 3: Search Intelligence

Purpose:
Provide intelligent, permission-aware enterprise search.

Features:

* Semantic Search
* Vector Search
* Hybrid Search (vector + keyword, reciprocal rank fusion)
* Permission-Aware Retrieval
* Metadata Filtering
* Result Ranking
* Source Attribution

Permission-Aware Retrieval:

* Access control is enforced at vector query time, not post-generation.
* Every chunk carries the ACL context of its parent document and collection.
* Search queries filter by `org_id` and the requesting user's collection permissions before similarity ranking.
* A user can never retrieve, see, or have generated content grounded on documents they lack access to.

---

## Domain 4: Conversation Intelligence

Purpose:
Manage multi-turn conversational interactions with the knowledge base.

Features:

* Chat Sessions (create, resume, archive, delete)
* Conversation History (persisted per user)
* Context Assembly (last N turns + rolling summary for long sessions)
* Follow-up Question Handling (query rewriting using conversation context)
* Per-Message Citations (persisted with each response)
* Feedback per Message (thumbs up/down + comment)

---

## Domain 5: AI Intelligence

Purpose:
Manage AI interactions, model orchestration, and AI safety.

Features:

* Prompt Templates (versioned, with rollback; every response logs the template version used)
* Model Management and Routing
* Structured Outputs (JSON schema validation with bounded retry on failure)
* AI Guardrails
* Evaluation Framework
* Cost Controls

Model Routing:

* All LLM calls go through a unified gateway layer (provider abstraction).
* Per-organization default model with an ordered fallback chain on provider error, timeout, or rate limit.
* Provider health checks; unhealthy providers are skipped.

Supported Models:

* Azure OpenAI
* OpenAI
* Gemini
* Claude

Guardrails (explicit):

* Prompt injection filtering on retrieved content and user input
* PII redaction (configurable per organization)
* Grounding enforcement — responses must cite retrieved sources or refuse
* Low-confidence refusal — below a retrieval-score threshold, the system states it cannot answer rather than guessing
* Output schema validation for structured responses

Evaluation Framework:

* Versioned golden Q&A set per knowledge domain
* Retrieval: recall@k and MRR against the golden set
* Generation: faithfulness and answer-relevance scoring (LLM-as-judge with a versioned rubric)
* Regression runs on prompt template or model changes before promotion

Cost Controls:

* Token metering per organization and per user
* Monthly token budgets per organization with soft warning and hard cap
* Embedding cache keyed by content hash (no re-embedding of unchanged content)
* Optional semantic response cache for repeated queries

---

## Domain 6: Agent Intelligence

Purpose:
Execute multi-step AI workflows.

V1 Scope — Fixed Sequential Pipeline:

Knowledge Agent → Report Agent

* Knowledge Agent: retrieves and assembles grounded context.
* Report Agent: generates structured, citation-backed outputs.
* The pipeline is a fixed LangGraph graph; no dynamic planning in V1.

V2 Scope — Full Multi-Agent Workflows:

* Research Agent: deep analysis across retrieved information.
* Recommendation Agent: multi-option recommendation workflows.
* Compliance Agent: validates outputs against organization rules and policies.
* Dynamic orchestration with conditional routing between agents.

---

## Domain 7: Recommendation Intelligence

Purpose:
Transform information into actionable decisions.

V1 Implementation:

* Recommendations are produced as a single-model, retrieval-grounded structured output (not a multi-agent workflow).

Workflow:

User Query
→ Permission-Aware Knowledge Retrieval
→ Context Analysis
→ Grounding Validation
→ Recommendation

Outputs:

* Recommendations
* Alternatives
* Justifications (with source citations)
* Confidence Indicators

Confidence Indicators:

* Derived from retrieval relevance scores and a groundedness check — not from model self-reported confidence.
* Presented as calibrated bands (High / Medium / Low) with the contributing sources listed.
* Below the Low threshold, the system declines to recommend and explains why.

V2 Extension:

* Multi-agent recommendation workflow with Research and Compliance agent validation.

---

## Domain 8: Report Intelligence

Purpose:
Generate business-ready deliverables.

Outputs:

* Executive Summaries
* Technical Reports
* Recommendation Reports
* Compliance Reports
* PDF Exports

All generated reports embed source citations and the generation metadata (model, prompt version, timestamp).

---

## Domain 9: Governance Intelligence

Purpose:
Provide enterprise-grade trust, auditability, and AI observability.

Features:

* Audit Logging (immutable, append-only)
* Prompt Tracking (template version per response)
* Response Tracking
* Source Tracking (which chunks grounded which response)
* Feedback Collection
* Activity Monitoring

AI Observability:

* End-to-end trace per request: retrieval → context assembly → model call(s) → output (Langfuse or equivalent)
* Per-step latency breakdown
* Token usage dashboards per organization, user, and feature
* Retrieval quality monitoring (score distributions, refusal rates)
* Feedback signals routed into the evaluation golden set curation process

---

## Domain 10: Administration Intelligence

Purpose:
Manage platform operations.

Features:

* User Administration
* Organization Administration
* Knowledge Base Administration (quotas, limits, retention)
* AI Configuration (default model, fallback chain, guardrail settings, budgets)
* Usage Analytics
* System Monitoring

---

# 7. Functional Requirements

### FR-001

Users shall upload documents to knowledge collections; uploads are processed asynchronously with visible status (Queued / Processing / Ready / Failed).

### FR-002

The system shall process uploaded documents (OCR, extraction, chunking) and generate embeddings; failed processing shall support retry and report a failure reason.

### FR-003

Users shall search knowledge using natural language.

### FR-004

The system shall retrieve relevant knowledge using hybrid search (vector + keyword with rank fusion).

### FR-005

The system shall enforce access control at retrieval time; results and generated content shall never be grounded on documents the requesting user cannot access.

### FR-006

The system shall generate grounded AI responses; responses that cannot be grounded in retrieved sources shall be refused with an explanation.

### FR-007

The system shall provide source citations on every generated response, persisted with the message.

### FR-008

The system shall support multi-turn chat sessions with persisted history and context-aware follow-up handling.

### FR-009

The system shall generate recommendations as structured outputs with alternatives, justifications, citations, and confidence bands.

### FR-010

The system shall generate downloadable reports (PDF) embedding citations and generation metadata.

### FR-011

Users shall be able to update (re-upload) and delete documents; deletion shall cascade to all derived chunks, embeddings, and index entries.

### FR-012

The system shall track all AI interactions: prompt template version, model, token usage, retrieved sources, and latency per request.

### FR-013

The system shall meter token usage per organization and enforce configurable monthly budgets (soft warning, hard cap).

### FR-014

The system shall route LLM calls through a provider abstraction with an ordered fallback chain on provider failure.

### FR-015

Administrators shall manage organizations, users, knowledge bases, quotas, AI configuration, and budgets.

---

# 8. Non-Functional Requirements

### Performance

Measured at reference load: 100 concurrent users, 1M indexed chunks per organization.

* Search response: p95 < 3 seconds
* AI chat: first token < 3 seconds; full response p95 < 15 seconds
* Document ingestion: 100-page text PDF ready within 5 minutes of upload

### Scalability

* Multi-tenant: shared schema with `org_id` + PostgreSQL Row-Level Security
* Stateless API layer; horizontal scaling of API and ingestion workers independently
* Ingestion throughput scales with worker count

### Security

* JWT Authentication (short-lived access tokens, refresh rotation)
* RBAC Authorization, enforced per request
* Permission-aware retrieval (ACL filtering at vector query time)
* Encryption in transit (TLS 1.2+) and at rest (database and object storage)
* Immutable audit logging

### Reliability

* 99.9% service availability target
* RPO: 24 hours (daily automated backups, point-in-time recovery where supported)
* RTO: 4 hours
* Graceful degradation: if LLM providers are unavailable, search remains functional

### Data Retention

* Audit logs: 365 days default, configurable per organization
* Deleted documents: purged from active systems immediately; removed from backups on backup rotation
* Conversation history: retained until user/admin deletion, subject to org retention policy

### Maintainability

* Modular Monolith Architecture
* Domain Driven Design
* Clean Architecture
* Prompt templates, evaluation rubrics, and golden sets are versioned artifacts

---

# 9. Security and Compliance

* GDPR-aligned: right to erasure honored via cascading document and conversation deletion
* Data residency: single region per deployment in V1; region selection at organization onboarding
* SOC 2 Type II: targeted post-V1; audit logging, access controls, and change management designed to satisfy SOC 2 criteria from day one
* PII handling: configurable redaction in prompts and logs
* Penetration testing prior to first enterprise deployment

---

# 10. Technical Architecture

## Architecture Style

* Domain Driven Design (DDD)
* Clean Architecture
* Modular Monolith (domains as bounded modules; extraction path to services along domain boundaries if scale demands)

## AI Pattern

* Retrieval-Augmented Generation (RAG)
* Agentic AI (fixed sequential pipeline in V1, dynamic orchestration in V2)

## Backend

* FastAPI
* Python
* Async worker queue for ingestion (Celery or ARQ + Redis)

## Frontend

* React
* TypeScript
* Tailwind CSS

## Database

* PostgreSQL (Row-Level Security for tenant isolation)

## Vector Search

* pgvector (chosen over a dedicated vector DB for operational simplicity and transactional consistency between vectors, metadata, and ACLs; sufficient below ~10M vectors per deployment)

## LLM Gateway

* Provider abstraction layer (LiteLLM or equivalent) — routing, fallback, retries, metering

## Agent Framework

* LangGraph

## Observability

* Structured logging + metrics (Prometheus/Grafana or managed equivalent)
* LLM tracing (Langfuse or equivalent)

## Storage

* Object storage for original documents (S3-compatible)

---

# 11. Risks and Mitigations

### Risk 1 — LLM provider instability or rate limits

Mitigation: multi-provider fallback chain, retry with backoff, request queuing.

### Risk 2 — Hallucination undermining trust

Mitigation: grounding enforcement, citation requirement, low-confidence refusal, faithfulness evaluation gate before prompt/model promotion.

### Risk 3 — Retrieval quality degradation at scale

Mitigation: hybrid search with rank fusion, golden-set regression testing, retrieval quality dashboards.

### Risk 4 — Tenant data leakage

Mitigation: defense in depth — application scoping, Postgres RLS, ACL filtering at vector query time, isolation tests in CI.

### Risk 5 — Unbounded LLM cost

Mitigation: per-org token budgets with hard caps, embedding cache, semantic response cache.

### Risk 6 — Ingestion pipeline failures on malformed documents

Mitigation: async processing with retries, partial-failure surfacing, per-format parser fallbacks.

---

# 12. Success Metrics

### Adoption

* ≥ 5 active organizations within 6 months of V1 GA
* ≥ 60% weekly active rate among provisioned users

### Productivity

* Average time-to-answer reduced from ~15 minutes (manual search) to < 2 minutes
* Report generation time reduced from hours to < 10 minutes

### Quality

* Retrieval: recall@5 ≥ 0.85 on golden set
* Generation: faithfulness ≥ 0.9 on evaluation rubric
* User satisfaction (per-message feedback): ≥ 80% positive
* Refusal correctness: < 5% wrong-refusal rate on answerable golden-set queries

### Platform

* Uptime ≥ 99.9%
* Search p95 < 3s at reference load
* Ingestion success rate ≥ 98% (excluding malformed files)

---

# 13. Roadmap

## V1

* Identity: Authentication, RBAC, multi-tenant isolation
* Knowledge Base: async ingestion, document lifecycle (upload / version / delete)
* Permission-Aware Hybrid Search
* AI Chat: multi-turn sessions, grounded responses, citations
* Recommendations: single-model, retrieval-grounded structured outputs
* Reports: generation + PDF export (fixed Knowledge → Report agent pipeline)
* Governance: audit logging, AI tracing, token metering and budgets

## V2

* Full Multi-Agent Workflows (Research, Recommendation, Compliance agents; dynamic orchestration)
* SSO (OIDC / SAML)
* Advanced Recommendations (multi-option, policy-validated)
* Analytics Dashboard
* Multilingual document support

## V3

* Enterprise Integrations (CRM, ERP, ticketing, SharePoint/Drive connectors)
* SCIM Provisioning
* Workflow Automation
* Industry-Specific Modules
