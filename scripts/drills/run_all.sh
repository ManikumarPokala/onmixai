#!/usr/bin/env bash
# Sprint 2 exit drills — runs all three against the live dev stack and prints
# timings. Bring the stack up first:
#
#   docker compose -f infra/docker-compose.yml up -d --build
#
# Run from the repo root:  bash scripts/drills/run_all.sh
#
# Order matters: the timing and corpus drills run against a normally-configured
# worker; the kill drill recreates the worker with a chaos delay, so it runs last.
set -euo pipefail

PY="${PY:-backend/.venv/bin/python}"
DRILLS="scripts/drills"

echo "==> checking the API is up (localhost:8008)"
curl -fsS -m 5 localhost:8008/health >/dev/null || {
  echo "API not reachable — start the stack: docker compose -f infra/docker-compose.yml up -d --build" >&2
  exit 1
}

run() {
  echo
  echo "============================================================"
  echo "==> $1"
  echo "============================================================"
  local start end
  start=$(date +%s)
  "$PY" "$DRILLS/$1"
  end=$(date +%s)
  echo "==> $1 completed in $((end - start))s"
}

run large_pdf_timing.py
run broken_corpus_sweep.py
run kill_drill.py

echo
echo "==> all drills passed"
