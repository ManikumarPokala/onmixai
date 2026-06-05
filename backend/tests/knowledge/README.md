# Knowledge tests

## Parser tests run in a separate pytest pass

`test_parsers.py` exercises the PDF/DOCX/PPTX/XLSX/TXT parsers, which means it
imports PyMuPDF. PyMuPDF's SWIG runtime **segfaults if imported into a process
where the `pytest-asyncio` plugin is active** (macOS/arm64). See
[ADR 0008](../../../docs/adr/0008-parser-test-isolation.md) for the full
investigation.

Consequences for writing tests here:

- **Parser tests are synchronous and live in `test_parsers.py`.** They run in a
  second, asyncio-free pytest pass driven by `scripts/run-tests.sh` (and
  `pytest-parsers.ini`). Run the whole suite with `make test`.
- **Async tests must never import PyMuPDF at module load.** `parsing/pdf.py`
  imports `pymupdf` lazily inside `parse()`, so building a `ParserRegistry` (as
  the worker tests do) is safe — only actually parsing a PDF loads it.
- **`fixtures.py` is imported only by `test_parsers.py`**, so its top-level
  `import pymupdf` is fine. Do not import `fixtures` from an async test module.

To run just the parser tests directly:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -c pytest-parsers.ini \
    tests/knowledge/test_parsers.py -q
```

## Fixtures are generated, not committed binaries

`fixtures.py` generates every valid and broken document deterministically (a
truncated PDF, a password-protected PDF, a PNG mislabeled as PDF, corrupt
DOCX/XLSX, undecodable bytes, …) rather than checking in opaque binaries, so each
fixture is reproducible and reviewable. The same generators feed the Task 10
broken-corpus drill.
