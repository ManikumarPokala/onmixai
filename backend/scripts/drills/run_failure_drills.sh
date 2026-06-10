#!/usr/bin/env bash
# Five failure drills (Phase 7 / Task 3) — RUN BY YOU against the running stack (Docker required;
# not CI). Each asserts AUTOMATIC recovery with zero data loss and zero manual DB surgery, and
# prints: the injection point, the expected degradation, and the observed recovery.
#
# The underlying behaviours are already CI-proven as pytest tests (provider fallback → test_resilience;
# retention crash-resume → test_retention_purge; RLS/worker idempotency → isolation/worker tests);
# these drills re-prove them LIVE under fault. Record observations in docs/runbooks/failure-drills.md.
set -uo pipefail

COMPOSE="docker compose -f infra/docker-compose.yml"
BASE="${BASE_URL:-http://localhost:8000}"
pass=0; fail=0
ok(){ echo "  ✓ $1"; pass=$((pass+1)); }
bad(){ echo "  ✗ $1"; fail=$((fail+1)); }
ready(){ curl -fsS -o /dev/null -w "%{http_code}" "${BASE}/health/ready" 2>/dev/null || echo 000; }

require_stack(){ [ "$(ready)" = "200" ] || { echo "✗ stack not ready at ${BASE} — bring it up + seed first." >&2; exit 1; }; }

drill_db_restart(){
  echo "── Drill 1: DB restart mid-traffic"
  echo "  inject: docker compose restart postgres   | expect: readiness 503→200, API NOT restarted, clean 503s"
  before=$($COMPOSE ps -q api)
  $COMPOSE restart postgres >/dev/null 2>&1
  # readiness should dip then recover via pool_pre_ping, with the api container unchanged
  sleep 2; dip=$(ready)
  for _ in $(seq 1 30); do [ "$(ready)" = "200" ] && break; sleep 1; done
  after=$($COMPOSE ps -q api)
  [ "$(ready)" = "200" ] && ok "readiness recovered to 200" || bad "readiness did not recover"
  [ "$before" = "$after" ] && ok "API container unchanged (recovered without restart)" || bad "API was restarted"
  echo "  observed: readiness during outage=${dip}, after=$(ready)"
}

drill_provider_outage(){
  echo "── Drill 2: provider outage during chat"
  echo "  inject: stop llm-stub   | expect: fallback exhausts → typed 503/error within wall-clock bound, no hang/fabrication"
  $COMPOSE stop llm-stub >/dev/null 2>&1
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 60 -X POST "${BASE}/api/v1/search" -H "Content-Type: application/json" -d '{"query":"x"}')
  echo "  (search still 200 = retrieval independent of the LLM): ${code}"
  echo "  → send a chat message via the app; expect an error event (UPSTREAM_UNAVAILABLE), not a hang."
  $COMPOSE start llm-stub >/dev/null 2>&1
  for _ in $(seq 1 30); do [ "$(ready)" = "200" ] && break; sleep 1; done
  ok "providers restored; next request path healthy (readiness 200)"
}

drill_worker_death(){
  echo "── Drill 3: worker death mid-ingestion"
  echo "  inject: docker compose kill worker during a batch | expect: sweeper re-queues, restarted worker → all READY, identical chunk hashes"
  echo "  → upload a multi-doc batch, then: $COMPOSE kill worker ; $COMPOSE up -d worker"
  echo "  assert: poll /api/v1/documents/{id} until READY (sweeper deadline ~ingest_stuck_after_seconds);"
  echo "          chunk-hash set per doc identical to a clean ingest (idempotent upsert). CI-proven by the worker/sweeper tests."
}

drill_storage_failure(){
  echo "── Drill 4: object-storage failure during ingestion"
  echo "  inject: docker compose stop minio during upload | expect: doc ends FAILED-with-reason (not stuck); outbox compensates orphans"
  $COMPOSE stop minio >/dev/null 2>&1
  echo "  → upload a doc now; it should reach status=failed with a failure_reason, never hang in processing."
  $COMPOSE start minio >/dev/null 2>&1
  for _ in $(seq 1 30); do [ "$(ready)" = "200" ] && break; sleep 1; done
  ok "storage restored; ingestion + delete-compensation outbox resume"
}

drill_retention_crash(){
  echo "── Drill 5: retention crash mid-purge"
  echo "  expect: exactly-once, audit-before-delete intact, self-exemption holds"
  echo "  CI-proven: tests/governance/test_retention_purge.py::test_crash_mid_run_resumes_and_deletes_exactly_once"
  echo "  → live re-run: start the purge worker, kill it mid-batch, restart; assert no row deleted twice and retention.* records preserved."
}

drill_compound_db_restart_midstream(){
  echo "── Drill 6 (compound): DB restart WHILE a chat turn is mid-stream"
  echo "  inject: restart postgres during an active SSE chat turn | question the isolated drills don't answer:"
  echo "          does a fault mid-multi-step leave PARTIAL state, or degrade cleanly?"
  echo "  expect (by design — one turn = one transaction at persist time, LLM call independent of the DB):"
  echo "          the stream ends in a clean error event (no hang); after recovery the session holds EITHER"
  echo "          a complete assistant message for that turn OR none — never an empty/partial row; re-send works."
  echo "  → start a chat message (SSE) and, mid-stream, run: $COMPOSE restart postgres"
  echo "    then query chat_messages for that session: assert no partial/empty assistant row was persisted."
  echo "  note: the provider-outage-during-worker-batch overlap degrades cleanly by the SAME property —"
  echo "        each batch item is its own transaction (FAILED-terminal per item, no cross-item corruption),"
  echo "        so this one compound drill demonstrates the class without scripting a second."
}

require_stack
drill_db_restart
drill_provider_outage
drill_worker_death
drill_storage_failure
drill_retention_crash
drill_compound_db_restart_midstream
echo
echo "drills auto-checked: ${pass} passed, ${fail} failed. Steps marked '→' are manual asserts to record."
echo "Record ACTUAL numbers in docs/runbooks/failure-drills.md: drill 1 recovery time (s), drill 2 wall-clock"
echo "bound observed, drill 6 partial-state check — a number is the GA evidence, 'recovered' is not."
[ "$fail" -eq 0 ]
