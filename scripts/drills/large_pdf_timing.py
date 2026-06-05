#!/usr/bin/env python
"""100-page PDF timing drill (Sprint 2 exit criterion 1).

Uploads a 100-page text PDF through the live API and asserts it reaches READY in
under 5 minutes locally, printing the elapsed time. Assumes the dev stack is up
(API + worker + embeddings backend).

Run from the repo root:  backend/.venv/bin/python scripts/drills/large_pdf_timing.py
"""

from __future__ import annotations

import sys
import time
import uuid

import httpx
import pymupdf

from _common import create_collection, register_and_login, upload, wait_for_terminal

_PAGES = 100
_BUDGET_SECONDS = 300.0


def _make_pdf(pages: int) -> bytes:
    document = pymupdf.open()
    try:
        for index in range(pages):
            document.new_page().insert_text(
                (72, 72), f"Page {index + 1} body text with several words to chunk and embed."
            )
        return bytes(document.tobytes())
    finally:
        document.close()


def main() -> int:
    client = httpx.Client(timeout=60.0)
    token, _org_id = register_and_login(client, f"pdfdrill-{uuid.uuid4().hex[:8]}")
    collection_id = create_collection(client, token, "PDF timing")

    data = _make_pdf(_PAGES)
    print(f"[upload] {_PAGES}-page PDF, {len(data)} bytes")
    start = time.monotonic()
    document_id = upload(
        client, token, collection_id, filename="large.pdf", data=data, content_type="application/pdf"
    )
    final = wait_for_terminal(client, token, document_id, timeout=_BUDGET_SECONDS + 30)
    elapsed = time.monotonic() - start

    ok = final == "ready" and elapsed < _BUDGET_SECONDS
    print(f"[result] document {document_id} -> {final.upper()} in {elapsed:.1f}s (budget {_BUDGET_SECONDS:.0f}s)")
    if not ok:
        print("[result] FAIL: not READY within budget")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
