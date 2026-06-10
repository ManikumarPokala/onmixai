# OnMixAI — Decision log

A curated record of the engineering decisions on OnMixAI that I'd want to defend in a review — the
ones where the *reasoning* matters more than the code. Each is **Context → Decision → Why it
matters**. (The raw, chronological in-repo ADRs are kept private; this is the narrative cut.)

OnMixAI is a multi-tenant RAG platform: tenants upload documents, then ask questions and get
**grounded, cited answers or an explicit refusal** — never a confident guess. That last property
drives most of what follows.

---

## 1. A query planner mis-estimate, not a missing index, was the search bottleneck

**Context.** Hybrid search (vector + full-text, fused) over an HNSW index was running ~440 ms p95
on a tenant corpus — far over budget — even though the HNSW index existed. The easy assumption
("add an index / tune ef_search") would have been wrong.

**Decision.** I read the query plan instead of guessing. `EXPLAIN` showed the planner *mis-estimating
the row count* behind the per-tenant ACL join, deciding the HNSW index wasn't worth it, and falling
back to a sequential scan + exact distance sort. The fix was to make the ACL-filtered candidate set
legible to the planner (so the row estimate was right and the index path was chosen), not to add
hardware or indexes. p95 dropped to ~2–15 ms. I then froze the win with a **plan-assertion test**:
CI runs `EXPLAIN` on the hot search query and fails if it sees a sequential scan on a tenant table.

**Why it matters.** The slow path was a *planner* decision, and the durable fix was a *CI gate that
asserts on the plan shape*, so a future schema or statistics change can't silently regress us back
to a seq scan. Performance work that ends in a regression guard, not just a faster number.

---

## 2. Confidence comes from retrieval, never from the model's self-report

**Context.** A grounded-answer system needs a "should I even answer this?" signal. The tempting
source is the model itself ("how confident are you?"). That number is uncalibrated and gameable —
exactly the wrong thing to gate a safety-relevant answer on.

**Decision.** Confidence is derived **structurally from retrieval** — the count and similarity of
the chunks the permission-aware retriever returns — and the answer schema has **no model-confidence
field at all**, so a model self-rating can't leak into the decision even by accident. Below the
retrieval-confidence threshold the pipeline refuses *before* generation (no spend, no chance to
fabricate). The property is monotone: strictly worse retrieval can never produce a more confident
answer.

**Why it matters.** It makes "we don't know" a first-class, *grounded* outcome rather than a model
mood. The enforcement is structural (the field doesn't exist), not a code-review convention.

---

## 3. Cite-or-refuse, validated on the terminal text — even when streaming

**Context.** Answers stream token-by-token for UX. But grounding ("every claim cites a real
retrieved source, or we refuse") can't be judged from a prefix — the citations and any fabrication
only exist once the whole answer is assembled.

**Decision.** Streaming yields tokens live, but the **grounding verdict is computed on the assembled
terminal text**: the pipeline emits a single terminal `AnsweredTurn` (with validated citations,
phantom markers stripped, persisted citations = the validated set only) or a `Refusal` — never a
half-validated answer. A low-confidence turn streams *nothing* and refuses before generation. An
infrastructure failure (provider outage, budget block) is a propagated error, **not** a content
refusal — the turn stays re-askable.

**Why it matters.** Users get streaming responsiveness without giving up the cite-or-refuse
guarantee, and "couldn't reach the model" is never silently dressed up as "I refuse." The
distinction between a *content* refusal and an *infrastructure* failure is explicit in the types.

---

## 4. Retain-by-default, and audit⟺deletion is biconditional

**Context.** Data-retention purging is destructive and one of its targets is the immutable audit
log. The dangerous failure modes are (a) deleting data nobody asked to delete, and (b) deleting
data without a record of having done so.

**Decision.** Two invariants, enforced in code and tests:
- **Retain-by-default.** A null / zero / missing retention window yields *no cutoff*, so the safe
  outcome (delete nothing) is the *default* outcome — the dangerous operation must be explicitly
  configured, never fallen into.
- **Audit ⟺ deletion, atomically.** Each bounded batch is one transaction that writes the purge
  audit record *and* deletes the rows it names. A deletion can never exist without its audit record,
  and the record can never claim a deletion that didn't commit — strictly stronger than the usual
  "log first, hope" pattern. Batches commit independently, so a crash mid-run resumes and deletes
  each row exactly once. And the purge **exempts its own `retention.*` records** from audit purging,
  so the deletion history can never erase itself.

**Why it matters.** For a safety/compliance story, "the safe default is the default, and every
deletion is provably audited" is the sentence that matters — and it's a biconditional, not a
best-effort log. (Flagged and kept the atomic variant over a weaker "over-counting" sketch on
review — the stronger guarantee was the right call.)

---

## 5. Three database roles, so the app *cannot* tamper with its own audit trail

**Context.** Audit immutability is easy to claim and easy to undermine — if the application's own DB
role can `UPDATE`/`DELETE` audit rows, a compromised app process can rewrite history, and the
retention purge needs to delete audit rows *somehow*.

**Decision.** Three roles, least-privilege:
- the **migration owner** (schema only),
- the **runtime role** the app connects as — `NOSUPERUSER`, `NOBYPASSRLS`, and explicitly **REVOKEd
  `UPDATE`/`DELETE` on `audit_events`** (plus a `BEFORE UPDATE` reject trigger): the app can append
  and read audit rows, never alter them;
- a dedicated **purger role** (its own connection) that the retention job uses — the *only* path
  with delete authority on the audit store, never `BYPASSRLS`, so RLS still scopes every delete per
  tenant.

The tenant-isolation suite runs as the runtime role so RLS *and* application scoping are both
exercised, and a test proves the runtime role is denied `DELETE` on `audit_events` while the purger
connection succeeds.

**Why it matters.** Immutability isn't a promise in a docstring — it's a privilege the running
application *does not hold*. Deletion authority lives in a separate, narrowly-scoped role reached
through a separate connection. That's the difference a security reviewer looks for.

---

*Cross-cutting theme:* every one of these ends in a **structural guarantee or a CI gate**, not a
convention — a mis-estimate caught by a plan assertion, a confidence field that doesn't exist, a
biconditional transaction, a privilege the app lacks, an enumerating test that fails on any
unaudited mutation. The goal throughout was to make the safe behaviour the one the system *can't
avoid*, not the one it's supposed to remember.
