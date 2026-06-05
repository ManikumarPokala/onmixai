"""AI domain — embedding now; the LLM gateway joins it in Phase 3.

Provider SDK imports live ONLY in ``ai/adapters/`` (import-linter contract). The
rest of the codebase depends on the Protocols in this package, never on a vendor
SDK, and tests use the deterministic fakes.
"""
