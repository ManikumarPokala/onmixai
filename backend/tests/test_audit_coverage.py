"""THE Phase-6 exit criterion: every mutating service use-case across all six domains emits an
audit event. This is an ENUMERATING test — it discovers the service methods by AST, classifies
each as mutating or read-only, and fails if any mutating method lacks an audit emission and is not
on the explicitly-justified exemption list. A new mutating method added without an emit therefore
breaks CI, so audit coverage can never silently regress (CLAUDE.md §6).

The check is static (no DB): it inspects each method's AST for a mutation marker (a write through a
repository, ``session.add/delete``, an after-commit side effect, or a job enqueue) and for an audit
marker (``self._audit.emit`` / constructing an ``AuditEvent`` / a purge record). Persistence is not
coverage — a method that writes must also record WHY.
"""

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"

# Service modules across every domain that own user/admin-initiated mutating use cases.
_SERVICE_GLOBS = (
    "identity/service.py",
    "knowledge/service.py",
    "knowledge/admin_service.py",
    "search/service.py",
    "conversation/service.py",
    "conversation/curation_service.py",
    "recommendation/service.py",
    "reports/service.py",
    "governance/service.py",
    "governance/purge_service.py",
    "ai/config_service.py",
)

# A method body containing any of these calls mutates persistent state.
_MUTATION_MARKERS = frozenset(
    {
        "add",
        "create",
        "upsert",
        "delete",
        "update",
        "requeue",
        "enqueue_reindex",
        "claim_for_processing",
        "mark_ready",
        "mark_failed",
        "mark_superseded",
        "set_active",
        "change_role",
        "grant_permission",
        "upsert_permission",
        "revoke_all_for_user",
        "decide",
        "transition",
        "set_policy",
        "set_model_config",
        "set_budget",
        "store",
        "rotate",
        "save",
        "increment",
        "bump_attempts",
        "register_after_commit",
        "update_organization",
        "set_summary",
        "upsert_if_newer",
    }
)
# A method body containing any of these records an audit event.
_AUDIT_MARKERS = frozenset({"emit"})  # self._audit.emit(...)
# Constructing an AuditEvent directly is also an audit record (system-initiated purge).
_AUDIT_NODE_NAMES = frozenset({"AuditEvent"})

# Mutating methods that legitimately do NOT emit an audit_events row — each with a reason. The list
# is intentionally small and justified; adding to it is a deliberate, reviewed act.
_EXEMPT: dict[str, str] = {
    # Auth/token lifecycle predates the audit store and is recorded as structured SECURITY logs
    # (structlog) with request context, not as per-event audit rows; auditing every login/refresh
    # is a separate product decision (would flood the org audit log).
    "AuthService.register_organization": "security-logged; org bootstrap, not an admin action",
    "AuthService.authenticate": "security-logged login (no per-login audit row by design)",
    "AuthService.refresh": "security-logged token rotation",
    # The metering gateway is the audit seam for LLM spend (budget/soft-threshold events); the chat
    # turn itself persists messages and is traced, not audit_events-logged per message.
    "ChatService.send_message_stream": "LLM spend audited in the metering gateway; turn is traced",
    # purge() DOES audit — it writes retention.* rows via the module-level _purge_event helper,
    # which the class-scoped static walk can't follow; behaviour is proven in test_retention_purge.
    "RetentionPurgeService.purge": "writes retention.* audit rows via the _purge_event helper",
}


def _service_files() -> list[Path]:
    files = [_SRC / rel for rel in _SERVICE_GLOBS]
    missing = [f for f in files if not f.exists()]
    assert not missing, f"service module(s) missing — update the glob: {missing}"
    return files


def _calls(node: ast.AST) -> set[str]:
    """All attribute/function names called anywhere under ``node`` (plus AuditEvent constructions,
    surfaced as the pseudo-name ``<AuditEvent>`` so audit-by-construction is detectable)."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            fn = n.func
            if isinstance(fn, ast.Attribute):
                names.add(fn.attr)
            elif isinstance(fn, ast.Name):
                names.add(fn.id)
                if fn.id in _AUDIT_NODE_NAMES:
                    names.add("emit")  # constructing an AuditEvent IS recording one
    return names


def _resolve(
    method: ast.AsyncFunctionDef | ast.FunctionDef, helpers: dict[str, set[str]]
) -> set[str]:
    """Calls made by ``method``, transitively following same-class private helpers it invokes — so
    a mutation/audit performed in a ``_helper`` counts for the public use-case that calls it."""
    seen: set[str] = set()
    frontier = _calls(method)
    resolved: set[str] = set(frontier)
    while frontier:
        nxt: set[str] = set()
        for name in frontier:
            if name in helpers and name not in seen:
                seen.add(name)
                new = helpers[name]
                nxt |= new
                resolved |= new
        frontier = nxt
    return resolved


def _classify() -> tuple[list[str], list[str], list[str]]:
    """Return (mutating_audited, mutating_unaudited, exempt_used) as ``Class.method`` labels."""
    audited: list[str] = []
    unaudited: list[str] = []
    exempt_used: list[str] = []
    for path in _service_files():
        tree = ast.parse(path.read_text())
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            if not cls.name.endswith("Service"):
                continue
            # Map of this class's private helpers → the calls they make (for transitive resolution).
            helpers = {
                m.name: _calls(m)
                for m in cls.body
                if isinstance(m, ast.AsyncFunctionDef | ast.FunctionDef) and m.name.startswith("_")
            }
            for method in cls.body:
                if not isinstance(method, ast.AsyncFunctionDef) or method.name.startswith("_"):
                    continue
                calls = _resolve(method, helpers)
                if not (calls & _MUTATION_MARKERS):
                    continue  # read-only use case — correctly unaudited
                label = f"{cls.name}.{method.name}"
                if label in _EXEMPT:
                    exempt_used.append(label)
                elif calls & _AUDIT_MARKERS:
                    audited.append(label)
                else:
                    unaudited.append(label)
    return audited, unaudited, exempt_used


def test_every_mutating_service_method_emits_an_audit_event() -> None:
    audited, unaudited, exempt_used = _classify()
    # The enumeration, surfaced on every run for transparency (run with -s to see it).
    lines = [f"audit coverage: {len(audited)} mutating methods audited across all six domains"]
    lines.append(f"  exempt (justified): {len(exempt_used)} — {sorted(set(exempt_used))}")
    lines += [f"  audited: {label}" for label in sorted(audited)]
    print("\n".join(lines))  # noqa: T201 — intentional enumeration diagnostic
    assert not unaudited, (
        "mutating service method(s) without an audit emission (add an emit, or justify "
        f"in _EXEMPT): {sorted(unaudited)}"
    )
    # Guard the floor: this proof must keep covering a broad surface (not silently shrink to zero).
    assert len(audited) >= 15, f"audit coverage unexpectedly small ({len(audited)}) — regression?"


def test_exemptions_are_all_still_real() -> None:
    """Every entry in the exemption list must still match a real mutating method — so the list can
    never rot into stale justifications that hide a future gap."""
    _, _, exempt_used = _classify()
    stale = set(_EXEMPT) - set(exempt_used)
    assert not stale, (
        f"exemptions no longer matching a mutating method (remove them): {sorted(stale)}"
    )
