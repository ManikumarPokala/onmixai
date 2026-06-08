#!/usr/bin/env bash
# Full backend test suite + coverage gate, run as two pytest passes.
#
# Pass 2 (PyMuPDF tests) runs in a separate, asyncio-free pytest process: PyMuPDF's
# SWIG runtime segfaults if imported into a process where pytest-asyncio's
# event-loop plugin is active (macOS/arm64). These tests are pure-synchronous
# so they run with third-party plugin autoload disabled. Coverage is combined
# across both passes and the 80% gate is enforced on the total.
# See docs/adr/0008-parser-test-isolation.md.
#
# Run from the backend/ directory (CI sets working-directory: backend).
set -euo pipefail

PYTEST="${PYTEST:-.venv/bin/pytest}"
# PyMuPDF-importing synchronous tests (parsers + the PDF-export render proof).
SYNC_PYMUPDF_TESTS=(tests/knowledge/test_parsers.py tests/reports/test_pdf_render.py)
IGNORES=()
for t in "${SYNC_PYMUPDF_TESTS[@]}"; do IGNORES+=(--ignore="$t"); done

# Pass 1 — the full async suite, minus the PyMuPDF tests, the benchmarks, and the chat eval
# (each runs in its own CI job; see tests/benchmarks and tests/eval). Writes a fresh .coverage.
"$PYTEST" --cov=src -q "${IGNORES[@]}" -m "not benchmark and not chat"

# Pass 2 — the PyMuPDF tests in isolation; append coverage and enforce the gate on the total.
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PYTEST" \
  -c pytest-parsers.ini -p pytest_cov \
  --cov=src --cov-append --cov-fail-under=80 -q \
  "${SYNC_PYMUPDF_TESTS[@]}"
