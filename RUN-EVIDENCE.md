# RUN-EVIDENCE — Phase 7 / V1 GA closure (user-executed)

The ordered, copy-paste runbook to turn Phase-7's built scripts into **recorded evidence**, then
tag V1. Everything here runs on **your** live Docker stack — the agent cannot execute these and has
recorded nothing in the result docs. Run each step, check the PASS, and paste your result into the
named doc/column. **Nothing is "passed" until your real output is in.** Apply the `v1.0.0` tag only
after every step below is green.

All commands run from the repo root unless noted; backend commands from `backend/`.

---

## 0. Bring up the stack + migrate

```bash
docker compose -f infra/docker-compose.yml up -d --build
# wait for health, then confirm:
curl -fsS http://localhost:8000/health/ready && echo "  ready"
```
**PASS:** `/health/ready` returns 200.

## 1. Seed — demo corpus, then bulk for capacity

```bash
cd backend
python -m scripts.seed_demo                      # demo org + the cite/refuse corpus
python -m scripts.drills.seed_bulk --count 1000000   # ~1M synthetic chunks + HNSW rebuild (minutes)
```
**PASS:** seed_demo prints the demo credentials; seed_bulk prints `seeded 1,000,000 chunks + rebuilt HNSW`.

## 2. Load test → `docs/benchmarks/load_v1_<YYYYMMDD>.md`

```bash
cd backend && bash scripts/drills/load_test.sh        # 100 users x 60s (override: USERS=, DURATION=)
```
**PASS:** prints a per-endpoint table; **search p95 < 3s** marked PASS at ~1M chunks (the binding NFR).
**Record:** copy `docs/benchmarks/load_v1_TEMPLATE.md` → `load_v1_<date>.md`; paste the table and the
**actual p95 numbers** (not "passed"). If any NFR MISSES, record the number + a remediation note.
*chat_* latency is stub/provider-dependent — note which provider you ran.*

## 3. Failure drills (6) → `docs/runbooks/failure-drills.md` (Observed column)

```bash
cd backend && bash scripts/drills/run_failure_drills.sh
```
Auto-checks print ✓/✗; steps marked `→` are manual asserts. Drive a little load / an upload where the
script says to, then inject the fault it names.
**PASS:** every drill recovers automatically — zero data loss, **zero manual DB edits**.
**Record the NUMBERS, not "recovered":** drill 1 readiness-recovery time (s); drill 2 observed
wall-clock to the typed error vs the computed bound; drill 6 the partial-state check (rows for the
interrupted turn). Paste into the Observed column + the "Numbers to record" section.

## 4. Backup / restore + DR → `docs/runbooks/backup-restore.md`

```bash
cd backend && bash scripts/drills/backup_restore.sh
```
**PASS:** integrity counts match source; `rows_visible_without_tenant_context = 0` on the restored DB
(RLS/tenancy survived); app serves on the restored DB.
**Record:** the four assertion results + the restore time vs the 4h RTO.

## 5. Live JWT rotation → `docs/runbooks/failure-drills.md` (rotation row)

```bash
cd backend && bash scripts/drills/rotate_jwt_secret_drill.sh   # Part 1 self-proves the grace window
# then execute Part 2 against the stack: set JWT_SECRET_PREVIOUS=<old>, JWT_SECRET=<new>, reload;
# confirm existing sessions keep working; after one access-TTL, clear PREVIOUS; confirm old tokens reject.
```
**PASS:** old-secret token valid in-window, rejected after the window closes; **no forced logout**.
**Record:** the live confirmation as executed-by-you (distinct from the CI unit tests).

## 6. Release smoke → clean checkout of the tag candidate

```bash
git fetch origin && git checkout <green-commit>     # the commit you will tag
docker compose -f infra/docker-compose.yml up -d --build
cd backend && python -m scripts.seed_demo
# Walk DEMO.md end-to-end in the web app, logged in as the demo operator:
#   ask: "What temperature should the Reactor R-200 jacket be preheated to during startup?"  → cited 180 °C [1]
#   ask: "What is the maximum occupational exposure limit for hydrazine?"                      → REFUSAL (no source)
cd backend && bash scripts/run-tests.sh   # or your full-gate target — confirm green on the tag commit
```
**PASS:** the demo plays login → cited answer → refusal on the running build (not just in tests);
full gates green on the commit you tag.

---

## 7. Tag v1.0.0 — YOUR final step (the agent does NOT tag)

Only after steps 1–6 are green and the numbers are pasted in:

```bash
git checkout main                       # ensure you're on the green, merged commit
git pull --ff-only
git tag -a v1.0.0 -m "OnMixAI V1.0.0 — GA"
git push origin v1.0.0
# confirm CI is green on the tag, then verify:
git describe --tags
```
**PASS:** tag `v1.0.0` on a green commit; CI green on the tag.

---

## Evidence checklist (all yours to fill)
- [ ] Load: search p95 number @ ~1M recorded; misses (if any) noted with a plan
- [ ] 6 drills: recovery numbers recorded; automatic recovery, zero data loss, zero DB surgery
- [ ] Backup/restore: `rows_visible_without_tenant_context = 0`, integrity match, restore time vs RTO
- [ ] JWT rotation: live confirmation recorded (sessions survived)
- [ ] Release smoke: DEMO.md path works on the built image
- [ ] v1.0.0 tagged on a green commit

When these are filled, send the numbers back and I'll fold them into the GA report's evidence
ledger — turning every "[user-run]" into a recorded result.
