# ADR 0011 — Prompt Templates Live In-Repo, Versioned As Code

Status: Accepted (2026-06-07)

## Context

Phase 3 introduces prompt templates (`grounded_answer`, `eval_judge_faithfulness`,
and the Phase 4–5 templates to come). A prompt is a behavioral contract with the model:
a change to it can change every answer's quality, faithfulness, and safety as much as a
code change. The options were a database table (editable at runtime) or files in the
repository.

## Decision

Templates are **code**, not data. Each lives under `src/ai/prompts/<name>/` as a
`template.md` (role-sectioned body with `{variable}` slots) + a `meta.yaml` (name,
semver `version`, declared variables, owner feature, `body_sha256`, changelog). The
`PromptRegistry` loads + validates them at startup and **fails fast** on a duplicate
name, an undeclared variable used in the body, a declared-but-unused variable, a
non-semver version, or a `body_sha256` that doesn't match `template.md`. Rendering is
strict — provided variables must equal the declared set exactly. The rendered
`template_version` flows into the trace and (via `trace_id`) the usage event.

Reasons templates-in-repo wins over a DB table:

- **Reviewed like code.** A prompt change is a diff in a PR, reviewed by the owning
  team, with the changelog and version bump visible — not a silent runtime edit.
- **Gated by CI.** The body-hash pin means editing `template.md` without bumping
  `meta.yaml` (version + hash) fails a registry test. The generation eval job
  (path-triggered on `src/ai/**`) re-scores on every template change (ADR 0013).
- **Atomic deploy.** The template ships with the code that renders and consumes it, so
  a render call and its template can never be out of sync across a deploy.
- **No tenant-editable prompts this phase.** Per-org prompt customization is a future,
  separately-designed feature; until then a single reviewed set is the safer default.

## Consequences

- Changing a prompt requires a code change + version bump + (real-model) eval — the
  intended friction for something that moves model behavior.
- The registry is process-wide and immutable at runtime (`lru_cache`); a bad template
  fails the app at startup rather than at first use.
- Template assets are read from the source tree (editable/source installs). A future
  wheel build must include them as package data; noted, not needed for CI/source runs.
- If per-tenant prompts are ever required, they layer on top (an override resolved
  against this base), they do not replace the in-repo set.
