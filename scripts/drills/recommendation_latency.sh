#!/usr/bin/env bash
# Recommendation latency drill (Phase 5, Task 9). Starts the in-process llm_stub with an
# injected per-call structured-completion delay and measures the recommendation pipeline's
# end-to-end p50/p95 over RECOMMENDATION_LATENCY_N turns (a single blocking structured call
# dominates). Gate: p95 < 10s (a generous mechanics-regression guard). Numbers + method
# recorded in docs/benchmarks/recommendation_latency.md (stub caveat: real-provider numbers
# re-measured later).
#
# Run from the repo root:  bash scripts/drills/recommendation_latency.sh
set -euo pipefail

cd "$(dirname "$0")/../.."

export STUB_JSON_MS="${STUB_JSON_MS:-600}"                       # modeled structured-call latency
export RECOMMENDATION_LATENCY_N="${RECOMMENDATION_LATENCY_N:-100}"

PYTHON="${PYTHON:-backend/.venv/bin/python}"
PYTHONPATH=backend exec "$PYTHON" scripts/drills/recommendation_latency.py
