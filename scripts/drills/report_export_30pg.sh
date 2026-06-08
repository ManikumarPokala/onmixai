#!/usr/bin/env bash
# 30-page report export timing drill (Phase 5, Task 7). Renders a 30+ page report to PDF,
# asserts the < 10 min budget, and proves (via PyMuPDF text extraction) that citations + the
# generation-metadata footer render. Run standalone — NOT under pytest (PyMuPDF segfaults with
# the pytest-asyncio plugin active; ADR 0008). Numbers recorded in docs/benchmarks/.
#
# Run from the repo root:  bash scripts/drills/report_export_30pg.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
PYTHON="${PYTHON:-backend/.venv/bin/python}"
PYTHONPATH=backend exec "$PYTHON" scripts/drills/report_export_30pg.py
