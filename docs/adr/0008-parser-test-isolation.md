# ADR 0008 — Parser Tests Run Isolated from the pytest-asyncio Plugin

Status: Accepted (2026-06-05)

## Context

The document parsers (Task 6) use PyMuPDF for PDF parsing. PyMuPDF wraps the
native MuPDF library through a SWIG-generated extension. On macOS/arm64 (and
potentially other platforms), importing `pymupdf` into a Python process where the
`pytest-asyncio` plugin is active reliably segfaults — `EXC_BAD_ACCESS
(address=0x20)` inside `_PyObject_New`, with no Python-level frame. The crash is
at PyMuPDF *import*, before any parsing runs.

This was isolated empirically:

- The production worker process imports the full stack (SQLAlchemy, asyncpg,
  pgvector→numpy, arq→redis) **and** PyMuPDF with no crash — `import src.worker`
  succeeds. Replicating that exact import set in a plain `python -c` also succeeds.
- Under `pytest`, a trivial `def test(): import pymupdf` crashes. Disabling
  third-party plugin autoload (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`) makes it pass.
- Bisecting the autoloaded plugins, **`pytest-asyncio`** is the trigger: enabling
  only it reproduces the segfault; PyMuPDF coexists with `pytest-cov` and every
  other plugin.

The conflict is between two native runtimes (MuPDF's SWIG layer and whatever
event-loop/interpreter state pytest-asyncio establishes), not a defect in our
code. It does not affect production: the worker runs PyMuPDF in a process that has
no pytest-asyncio in it.

## Decision

The synchronous parser tests (`tests/knowledge/test_parsers.py`) run in a
**second, dedicated pytest process** with third-party plugin autoload disabled,
separate from the main async suite:

- `pymupdf` is imported **lazily**, inside `PdfParser.parse()` and the PDF test
  fixtures, never at module top level. This keeps it out of the async test graph —
  the worker tests build the `ParserRegistry` but only `parse()` touches PyMuPDF,
  so they collect and run under pytest-asyncio without loading it.
- `scripts/run-tests.sh` runs the suite as two passes and is the single entry
  point used by both `make test` and CI:
  1. `pytest --cov=src --ignore=tests/knowledge/test_parsers.py` — the full async
     suite.
  2. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -c pytest-parsers.ini -p pytest_cov
     --cov-append --cov-fail-under=80 tests/knowledge/test_parsers.py` — the
     parser tests, asyncio-free, appending coverage and enforcing the 80% gate on
     the combined total.
- `pytest-parsers.ini` is the isolated config (no `asyncio_mode`, silences the
  PyMuPDF SWIG `DeprecationWarning`s).

## Consequences

- The parser tests are pure-synchronous by construction, which is appropriate:
  parsing is CPU-bound work and the tests exercise pure functions over bytes.
- Coverage is combined across the two passes, so the 80% gate is unaffected.
- The split is deterministic and platform-independent (the second pass simply
  never has pytest-asyncio in-process), so CI on Linux behaves identically.
- New parser tests go in `test_parsers.py`; any async test must **not** import
  PyMuPDF at module load. The lazy-import boundary in `parsing/pdf.py` enforces
  this for production code. See `tests/knowledge/README.md`.
