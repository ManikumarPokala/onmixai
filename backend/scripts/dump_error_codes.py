"""Emit the canonical set of backend error codes to ``frontend/error-codes.json``.

Every client-facing failure is an ``AppError`` subclass (or the framework validation / 500
handler) rendered as ``{"error": {code, ...}}`` (shared/errors.py). A code reaches the envelope
one of four ways — this script collects all four by AST-scanning ``src/`` (no imports, no
instantiation), restricted to error-construction contexts so it stays false-positive-free:

  1. literal at the call site:      ``ConflictError("ORG_SLUG_TAKEN")`` / ``_envelope("…")``
  2. literal in a class's own init: ``super().__init__("RECOMMENDATION_NOT_FOUND", …)``
  3. module/class constant alias:   ``_INVALID_CREDENTIALS = "INVALID_CREDENTIALS"``, raised by name
  4. default parameter value:       ``def __init__(self, *, code: str = "UPSTREAM_REJECTED")``

The frontend's ``errors.test.ts`` reads the result and fails if any backend code lacks a
human-message mapping — turning "we mapped the codes" into a gate (a new backend error can't
ship with a generic-fallback UI). A CI step regenerates this file and diffs it, the same
lockstep discipline as the OpenAPI snapshot. Time: O(n) over source AST nodes.

Run: ``python -m scripts.dump_error_codes`` (from backend/).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
_OUT = Path(__file__).resolve().parents[2] / "frontend" / "error-codes.json"
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")  # UPPER_SNAKE, ≥3 chars
# Non-AppError callees that still build a client-facing envelope (shared/errors.py handlers).
_ENVELOPE_FUNCS = {"_envelope"}


def _string_const(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if _CODE_RE.match(node.value):
            return node.value
    return None


def _apperror_subclasses(trees: list[ast.Module]) -> set[str]:
    """Transitive closure of class names deriving from AppError (across all modules)."""
    bases_of: dict[str, set[str]] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases_of[node.name] = {b.id for b in node.bases if isinstance(b, ast.Name)}
    subclasses = {"AppError"}
    changed = True
    while changed:  # fixpoint over a finite class set
        changed = False
        for name, bases in bases_of.items():
            if name not in subclasses and bases & subclasses:
                subclasses.add(name)
                changed = True
    return subclasses


def _const_aliases(trees: list[ast.Module]) -> dict[str, str]:
    """Map every ``NAME = "UPPER_SNAKE"`` assignment to its value (source 3 resolution)."""
    aliases: dict[str, str] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = _string_const(node.value)
                if value is not None:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            aliases[target.id] = value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                value = _string_const(node.value)
                if value is not None:
                    aliases[node.target.id] = value
    return aliases


def _code_from_call(call: ast.Call, aliases: dict[str, str]) -> str | None:
    """The code from a constructor/helper call: first positional or ``code=`` keyword, as a
    string literal (sources 1-2) or a constant alias resolved by name (source 3)."""
    candidates: list[ast.expr] = []
    if call.args:
        candidates.append(call.args[0])
    candidates += [kw.value for kw in call.keywords if kw.arg == "code"]
    for node in candidates:
        literal = _string_const(node)
        if literal is not None:
            return literal
        if isinstance(node, ast.Name) and node.id in aliases:
            return aliases[node.id]
    return None


def collect_codes() -> list[str]:
    trees = [ast.parse(p.read_text(), str(p)) for p in sorted(_SRC.rglob("*.py"))]
    subclasses = _apperror_subclasses(trees)
    aliases = _const_aliases(trees)
    codes: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            # source 4: a ``code`` parameter with a literal default in any __init__
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                args = node.args
                n = len(args.defaults)  # defaults align to the TAIL of positional args
                positional = zip(args.args[len(args.args) - n :], args.defaults, strict=True)
                kwonly = [
                    (kw, kw_default)
                    for kw, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                    if kw_default is not None
                ]
                defaulted = [*positional, *kwonly]
                for arg, default in defaulted:
                    if arg.arg == "code":
                        literal = _string_const(default)
                        if literal is not None:
                            codes.add(literal)
            # sources 1-3: a call to an AppError subclass, super().__init__, or _envelope
            if isinstance(node, ast.Call):
                func = node.func
                is_subclass_call = isinstance(func, ast.Name) and func.id in subclasses
                is_envelope = isinstance(func, ast.Name) and func.id in _ENVELOPE_FUNCS
                is_super_init = isinstance(func, ast.Attribute) and func.attr == "__init__"
                if is_subclass_call or is_envelope or is_super_init:
                    code = _code_from_call(node, aliases)
                    if code is not None:
                        codes.add(code)
    return sorted(codes)


def main() -> int:
    codes = collect_codes()
    _OUT.write_text(json.dumps(codes, indent=2) + "\n")
    print(f"wrote {len(codes)} error codes to {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
