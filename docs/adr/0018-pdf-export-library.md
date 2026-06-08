# ADR 0018 — PDF export library: fpdf2 (programmatic), not weasyprint/chromium

Status: Accepted (2026-06-08)

## Context

Reports export to PDF (Task 7): a 30-page structured report — titled sections with inline
`[n]` citation markers, a sources/footnotes list resolving each marker to its source, and a
generation-metadata footer (model, prompt version, generated_at). The render must be:

- **server-side and deterministic** (no client/browser; same input → same bytes-ish output, so
  a swept re-export is content-identical),
- **operationally light** (it runs in an ARQ worker and in CI/dev sandboxes),
- **fast enough** for the < 10 min / 30-page budget,
- **text-extractable** (the pause evidence + tests assert citations + the metadata footer are
  present by extracting text from the generated PDF).

## Options considered

- **WeasyPrint (HTML/CSS → PDF).** Rich CSS layout, the spec's first suggestion. But it depends
  on **Pango + Cairo + GObject system libraries** (not pip-installable). In this repo's dev
  sandbox the import fails outright (`cannot load library 'libgobject'`), and CI would need an
  `apt-get install libpango/…` step on every backend job. Heavy operational surface for a
  structured-sections report that needs no rich CSS.
- **Headless Chromium (e.g. playwright/pyppeteer → PDF).** The richest layout, but ships/needs a
  full **browser binary** (~hundreds of MB), a sandbox, and process management in the worker —
  the heaviest dependency of the three, and overkill here.
- **fpdf2 (programmatic PDF).** Pure-Python, **zero system/browser dependencies**, deterministic,
  fast, and the output is real text (extractable with PyMuPDF, already a dependency). The cost is
  no HTML/CSS engine — layout is built programmatically (headings, body, footnotes, a per-page
  footer), which is exactly the structure our reports have.

## Decision

**Use fpdf2 and render the report programmatically** (title → sections with inline `[n]` markers
→ a numbered Sources list → a generation-metadata footer on every page). The report's structured
`content` (sections + resolved citations) and `generation_metadata` are the deterministic inputs;
the renderer is a pure function `content → bytes`, called from the export worker.

## Consequences

- **No system deps.** The worker, CI, and the dev sandbox render PDFs with nothing but the Python
  wheel — no Pango/Cairo, no browser. The timing drill and the text-extraction proof run anywhere.
- **Deterministic + idempotent.** Same `content` → same rendered body, so a sweeper-recovered
  re-export produces the same document (the worker's content is the hashed artifact; the
  metadata footer's generated_at is informational, not part of the hashed report body).
- **Layout ceiling.** fpdf2 has no CSS engine: complex multi-column / richly-styled layouts are
  out of reach. Our reports (sections + footnotes + footer) don't need them, and that simplicity
  is itself a feature until a requirement forces otherwise. **Revisit trigger (concrete):** the
  *first customer requirement for branded / rich-layout reports* — logos, multi-column, charts,
  themed templates — is when WeasyPrint (pay the Pango/Cairo system-dep cost explicitly in CI) or
  a chromium renderer earns its complexity. Until that signal exists, fpdf2 stays; this ADR is
  updated rather than silently worked around.
- **Deterministic creation date.** The PDF's internal `/CreationDate` is pinned to a fixed
  constant so the bytes are deterministic (idempotent re-export). It is NOT the generation
  time — the real `generated_at` lives in the on-page metadata footer (and the report row's
  `generation_metadata`). Don't read `/CreationDate` as "when this report was generated".
- Citations render as a numbered **Sources** list (endnotes) keyed by the same `[n]` markers used
  inline; the metadata footer carries model + prompt version + generated_at on every page.
