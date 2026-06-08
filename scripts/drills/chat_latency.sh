#!/usr/bin/env bash
# Chat streaming latency drill (Phase 4, Task 9). Starts the in-process llm_stub with an
# injected streaming delay model and measures first-token + full-response p50/p95 over
# CHAT_LATENCY_N turns through the real grounded pipeline's streaming path. Gates:
# first-token p95 < 3s, full p95 < 15s. Numbers + method recorded in
# docs/benchmarks/chat_latency.md (stub caveat: real-provider numbers re-measured later).
#
# Run from the repo root:  bash scripts/drills/chat_latency.sh
set -euo pipefail

cd "$(dirname "$0")/../.."

export STUB_STREAM_FIRST_MS="${STUB_STREAM_FIRST_MS:-400}" # modeled time-to-first-token
export STUB_STREAM_TOKEN_MS="${STUB_STREAM_TOKEN_MS:-25}"  # modeled per-token delay
export CHAT_LATENCY_N="${CHAT_LATENCY_N:-100}"

PYTHON="${PYTHON:-backend/.venv/bin/python}"
PYTHONPATH=backend exec "$PYTHON" scripts/drills/chat_latency.py
