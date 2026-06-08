# Report PDF export — Phase 5, Task 7

Drill: `scripts/drills/report_export_30pg.sh` (→ `scripts/drills/report_export_30pg.py`).

## What is measured

The **export render** — turning a report's structured `content` (sections + resolved
citations) + `generation_metadata` into a PDF (fpdf2, ADR 0018). Generation (the LangGraph
graph) is stub-fast; the render is the worker's measurable work. The drill renders a 40-section
report, asserts the **< 10 min** budget, and proves — by extracting the PDF text with
PyMuPDF — that citations render as notes (`[n] filename, p.X`) and the generation-metadata
footer (model · prompt version · generated_at) is present on the page.

## Recorded run

Local dev (Apple silicon), 2026-06-08:

| Metric | Value | Budget |
|---|---|---|
| pages | 45 | ≥ 30 ✓ |
| size | 38 KB | — |
| render time | 569 ms | < 10 min ✓ |
| citations render | ✓ (text-extracted) | required |
| metadata footer | ✓ (text-extracted) | required |

The render is **deterministic** (fixed PDF creation date; same `content` → byte-identical PDF),
so a sweeper-recovered re-export is content-identical (asserted by content hash in
`tests/reports/test_export_worker.py`).

## Caveat / revisit trigger

This measures the **render**, which dominates export wall-clock; the end-to-end "generate +
export" also includes LangGraph generation, which is **stub-fast** here. Real-model generation
time is re-measured when a provider is configured (same caveat as the chat/recommendation
latency notes). At 569 ms for 45 pages the render has ~1000× headroom under the 10 min budget,
so the budget is governed by generation latency, not the render.
