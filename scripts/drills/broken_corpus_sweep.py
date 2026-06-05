#!/usr/bin/env python
"""Broken-corpus sweep drill (Sprint 2 exit criterion 4 / robustness checklist).

Uploads every broken fixture through the live API and asserts each terminates in
FAILED with a human-readable reason, and that none is left stuck in PROCESSING.
Assumes the dev stack is up.

Run from the repo root:  backend/.venv/bin/python scripts/drills/broken_corpus_sweep.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import httpx
import psycopg

# Make the backend importable so the drill reuses the committed fixture generators.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from _common import (  # noqa: E402
    PG,
    create_collection,
    document,
    register_and_login,
    upload,
    wait_for_terminal,
)
from tests.knowledge import fixtures  # noqa: E402

_PDF = "application/pdf"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_TXT = "text/plain"

# (label, content_type, fixture bytes) — the full broken corpus.
_CORPUS = [
    ("truncated_pdf", _PDF, fixtures.truncated_pdf),
    ("password_pdf", _PDF, fixtures.password_pdf),
    ("png_as_pdf", _PDF, fixtures.png_as_pdf),
    ("zero_byte_pdf", _PDF, fixtures.zero_byte),
    ("corrupt_docx", _DOCX, fixtures.corrupt_docx),
    ("corrupt_xlsx", _XLSX, fixtures.corrupt_xlsx),
    ("zero_byte_txt", _TXT, fixtures.zero_byte),
    ("garbage_txt", _TXT, fixtures.garbage_txt),
]


def _stuck_processing(org_id: str) -> int:
    """Count this org's documents still PROCESSING (RLS-scoped by the org GUC)."""
    with psycopg.connect(PG, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.current_org_id', %s, false)", (org_id,))
        return conn.execute(
            "SELECT count(*) FROM documents WHERE status = 'processing'"
        ).fetchone()[0]


def main() -> int:
    client = httpx.Client(timeout=60.0)
    token, org_id = register_and_login(client, f"corpus-{uuid.uuid4().hex[:8]}")
    collection_id = create_collection(client, token, "Broken corpus")

    uploaded = [
        (label, upload(client, token, collection_id, filename=label, data=make(), content_type=ct))
        for label, ct, make in _CORPUS
    ]

    ok = True
    print(f"{'fixture':<16} {'terminal':<8} reason")
    print("-" * 70)
    for label, document_id in uploaded:
        wait_for_terminal(client, token, document_id, timeout=90)
        doc = document(client, token, document_id)
        reason = doc.get("failure_reason") or ""
        terminal = doc["status"].upper()
        if terminal != "FAILED" or not reason:
            ok = False
        print(f"{label:<16} {terminal:<8} {reason}")
    print("-" * 70)

    stuck = _stuck_processing(org_id)
    print(f"[result] stuck PROCESSING: {stuck}")
    if stuck != 0:
        ok = False
    print(f"[result] every fixture FAILED with a reason, none stuck: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
