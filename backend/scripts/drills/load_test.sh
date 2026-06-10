#!/usr/bin/env bash
# Reference-scale load drill (Phase 7 / Task 1) — RUN BY YOU against a running stack (not CI).
# Verifies the stack is reachable, then drives the asyncio load harness and prints per-endpoint
# p50/p95/p99 + error rate with NFR pass/miss. Record the output in docs/benchmarks/load_v1_<date>.md.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
USERS="${USERS:-100}"
DURATION="${DURATION:-60}"

echo "→ checking ${BASE_URL}/health/ready …"
if ! curl -fsS "${BASE_URL}/health/ready" >/dev/null 2>&1; then
  echo "✗ stack not ready at ${BASE_URL}. Start it (docker compose up) and seed (python -m scripts.seed_demo)." >&2
  echo "  For the 1M-chunk capacity proof, bulk-seed chunks first; this harness only drives load." >&2
  exit 1
fi

echo "→ driving load: ${USERS} users for ${DURATION}s against ${BASE_URL}"
python -m scripts.drills.load_test --base-url "${BASE_URL}" --users "${USERS}" --duration "${DURATION}"
echo "→ record these numbers in docs/benchmarks/load_v1_\$(date +%Y%m%d).md (template provided)."
