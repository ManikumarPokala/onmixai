# OnMixAI v1.0.0 — Release Verification Record

**Audit date:** 2026-06-10
**Release commit:** `2283e7a0be487aadb4852e475264a6cb44b9273f` (`2283e7a`)
**Tag:** `v1.0.0` (annotated) — `OnMixAI V1.0.0 — GA`
**Repository:** `ManikumarPokala/onmixai`

This document is the authoritative, evidence-based record of what was verified at
the v1.0.0 release, and — equally — what was **not** independently verified. Every
PASS below is backed by command output captured during the release audit. Items
that could not be independently proven are recorded as **UNVERIFIED** rather than
asserted.

> **Release verification and performance-evidence verification are separate
> concerns.** The release itself is fully verified and reproducible. Two
> performance-evidence items remain open as documented evidence gaps. They are
> **not** release failures.

---

## Final audit outcome

| Concern | Status |
|---|---|
| **Release verification** | ✅ **VERIFIED AND PUBLISHED** — fully evidenced, reproducible, CI-validated |
| **Performance-evidence verification** | ⚠️ **PARTIALLY VERIFIED** — summary metrics exist; two evidence artifacts are not independently verifiable |

Summary: OnMixAI v1.0.0 has been released, published, CI-validated, merged to
`main`, tagged on `origin`, and independently reproduced from a clean public clone.
The release is verified. Two performance-evidence caveats remain open because they
require access to the live benchmark environment and supporting artifacts that are
not currently available for independent verification.

---

## Release verification evidence

### 1. Release commit
- **SHA:** `2283e7a0be487aadb4852e475264a6cb44b9273f`
- **Subject:** `docs: V1.0.0 closure fixes, failure drills, load tests & evidence`

### 2. CI validation
- Re-verified via the GitHub check-runs REST API against the release commit.
- **Result:** `total_count = 30` → **all 30 check-runs `success`**.
- The suite is 15 jobs, each recorded twice (once on the `pull_request` event, once
  on the `push: main` event): `lint`, `typecheck`, `contracts`, `test`,
  `isolation`, `migrations`, `openapi-sync`, `security`, `frontend`, `benchmarks`,
  `eval`, `eval-chat`, `eval-generation`, `eval-recommendation`, `eval-report`.
- **Zero non-success conclusions.**

### 3. Main-branch merge verification
- `origin/main` = `2283e7a0be487aadb4852e475264a6cb44b9273f`.
- Merge was fast-forward, preserving the release SHA (no re-tag required).
- Local `main` == `origin/main` == release commit.

### 4. Published `v1.0.0` tag verification
- `refs/tags/v1.0.0` → tag object `1b034db604f43fa060b3fc0fdf44a9c72f00ced0`.
- `refs/tags/v1.0.0^{}` (peeled) → `2283e7a0be487aadb4852e475264a6cb44b9273f` — **matches the release commit**.
- Tag type: annotated; subject: `OnMixAI V1.0.0 — GA`.
- `git merge-base --is-ancestor v1.0.0 origin/main` → **OK** (tag is reachable from `main`, not dangling).

### 5. Clean-clone reproducibility verification
- A fresh `git clone` of the public repository into a temporary directory succeeded.
- `v1.0.0` tag is present in the fresh clone.
- `git checkout v1.0.0` resolves to `HEAD = 2283e7a0be487aadb4852e475264a6cb44b9273f` — **MATCH**.
- `git merge-base --is-ancestor v1.0.0 origin/main` in the clone → **OK**.
- `git fsck --connectivity-only` → clean.
- Release artifacts present in the clone: `CHANGELOG.md`, `DECISIONS.md`, `DEMO.md`,
  `RUN-EVIDENCE.md`, `backend/src/main.py`, `backend/scripts/drills/load_test.py`,
  `frontend/package.json`.
- **Conclusion:** a third party cloning this repository obtains exactly the commit
  that was tagged.

---

## Release gates — all passed

| Gate | Status | Evidence |
|---|---|---|
| Working tree clean at release | ✅ PASS | `git status --porcelain` = 0 lines |
| Release commit pushed to origin | ✅ PASS | `origin/main = 2283e7a` |
| CI green on release commit | ✅ PASS | 30/30 check-runs `success` |
| Release commit merged to main (SHA preserved) | ✅ PASS | fast-forward; `origin/main = 2283e7a` |
| Main-branch CI green | ✅ PASS | included in the 30/30 (`push: main` event) |
| Public `v1.0.0` tag published | ✅ PASS | `refs/tags/v1.0.0^{} → 2283e7a` |
| Tag reachable from main | ✅ PASS | `merge-base --is-ancestor` → OK |
| Clean-clone reproducibility | ✅ PASS | clone → checkout v1.0.0 → 2283e7a; fsck clean |
| Third-party reproducibility | ✅ PASS | independent clone resolves to the tagged release |

---

## Performance Evidence Caveats

The following two items are recorded as **UNVERIFIED**. They are **evidence gaps,
not release failures** — the release is verified and published regardless of their
status. They remain UNVERIFIED because closing them requires access to the live
benchmark environment and supporting artifacts that were not independently
available at audit time.

| Item | Claim on file | Status | Why it is not independently verifiable |
|---|---|---|---|
| **Corpus scale (~1M chunks)** | `docs/benchmarks/load_v1_20260610.md` records `Corpus: ~1M chunks (bulk-seeded)` | ⚠️ **UNVERIFIED** | Latency figures alone cannot distinguish a ~1M-chunk corpus from the small demo corpus; no row-count artifact was preserved. |
| **Raw load-test stdout artifact** | `docs/benchmarks/load_v1_20260610.md` records `search n=6355 p50=0.294 p95=0.594 p99=0.865 err=0 PASS` | ⚠️ **UNVERIFIED** | Only the summarized markdown line exists; the raw `load_test` stdout was not captured/committed. |

### Closure requirements

These caveats are closed by capturing the supporting artifacts against the live
environment and committing them.

**Corpus-scale evidence** — run against the seeded database and record the output:

```sql
SELECT count(*) FROM chunks;
```

**Raw load-test stdout artifact** — re-run the load test and preserve the raw
output as a committed artifact:

```bash
./load_test.sh | tee docs/benchmarks/load_v1_raw_<date>.txt
```

Once both artifacts exist and are committed, the two items above can be reclassified
from UNVERIFIED to PASS, and the performance-evidence status updated from PARTIALLY
VERIFIED to VERIFIED.

---

## Defensible final position

- **Release:** ✅ VERIFIED AND PUBLISHED — all release gates passed; independently
  reproducible from a clean clone.
- **Performance evidence:** ⚠️ PARTIALLY VERIFIED — summary metrics exist; corpus
  scale and raw benchmark capture remain documented as UNVERIFIED pending artifact
  capture.

The remaining work is evidence strengthening, not release completion.
