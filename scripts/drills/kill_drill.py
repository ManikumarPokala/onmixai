#!/usr/bin/env python
"""Worker kill-drill (Sprint 2 exit criterion / robustness checklist).

Proves: a worker killed mid-task leaves the document recoverable, the sweeper
re-queues it, and the recovered chunk set is byte-for-byte identical to an
uninterrupted ingest.

Assumes the dev stack is up with a chaos delay so the kill lands mid-task, e.g.:

    INGEST_CHAOS_DELAY_SECONDS=6 INGEST_STUCK_AFTER_SECONDS=3 \\
        docker compose -f infra/docker-compose.yml up -d --build

Run from the repo root:  backend/.venv/bin/python scripts/drills/kill_drill.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import httpx
import psycopg

API = "http://localhost:8008"
PG = "postgresql://onmixai_app:onmixai_app@localhost:5440/onmixai"
COMPOSE = ["docker", "compose", "-f", "infra/docker-compose.yml"]
CONTENT = (("paragraph one. " * 80) + "\n\n" + ("paragraph two. " * 80)).encode()
# A chaos delay so the kill lands mid-task; the worker must keep it across restart.
COMPOSE_ENV = {**os.environ, "INGEST_CHAOS_DELAY_SECONDS": "6", "INGEST_STUCK_AFTER_SECONDS": "3"}


def _login(client: httpx.Client, slug: str) -> tuple[str, str]:
    email = f"o@{slug}.test"
    client.post(
        f"{API}/api/v1/auth/register",
        json={
            "name": slug,
            "slug": slug,
            "owner_email": email,
            "password": "password-123456",
            "full_name": "O",
        },
    )
    token = client.post(
        f"{API}/api/v1/auth/login",
        json={"org_slug": slug, "email": email, "password": "password-123456"},
    ).json()["access_token"]
    org_id = client.get(f"{API}/api/v1/users/me", headers=_auth(token)).json()["org_id"]
    return token, org_id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload(client: httpx.Client, token: str, collection_id: str) -> str:
    response = client.post(
        f"{API}/api/v1/collections/{collection_id}/documents",
        headers=_auth(token),
        files={"file": ("d.txt", CONTENT, "text/plain")},
    )
    return response.json()["document_id"]


def _status(client: httpx.Client, token: str, document_id: str) -> str:
    return client.get(f"{API}/api/v1/documents/{document_id}", headers=_auth(token)).json()[
        "status"
    ]


def _wait_status(
    client: httpx.Client, token: str, document_id: str, target: str, timeout: float
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _status(client, token, document_id) == target:
            return
        time.sleep(0.1)
    raise SystemExit(f"timeout waiting for {document_id} to reach {target}")


def _chunk_hashes(org_id: str, document_id: str) -> set[str]:
    with psycopg.connect(PG, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.current_org_id', %s, false)", (org_id,))
        rows = conn.execute(
            "SELECT content_hash FROM chunks WHERE document_id = %s", (document_id,)
        ).fetchall()
    return {row[0] for row in rows}


def _compose(*args: str, text: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*COMPOSE, *args], check=True, capture_output=True, text=text, env=COMPOSE_ENV
    )


def main() -> int:
    client = httpx.Client(timeout=30.0)
    # Ensure the worker is running with the chaos delay so the kill lands mid-task.
    _compose("up", "-d", "worker")
    time.sleep(3)

    token, org_id = _login(client, f"drill-{uuid.uuid4().hex[:8]}")
    collection_id = client.post(
        f"{API}/api/v1/collections", headers=_auth(token), json={"name": "Drill"}
    ).json()["id"]

    # 1) Uninterrupted baseline.
    baseline_id = _upload(client, token, collection_id)
    _wait_status(client, token, baseline_id, "ready", timeout=60)
    baseline = _chunk_hashes(org_id, baseline_id)
    print(f"[baseline] document {baseline_id} READY with {len(baseline)} chunks")

    # 2) Recovery run: kill the worker mid-task.
    recover_id = _upload(client, token, collection_id)
    _wait_status(client, token, recover_id, "processing", timeout=30)
    print(
        f"[kill] {time.strftime('%H:%M:%S')} document {recover_id} is PROCESSING — docker kill worker"
    )
    _compose("kill", "worker")

    _compose("up", "-d", "worker")
    time.sleep(4)  # let the stale-claim threshold elapse
    print("[sweep] running one-shot sweeper")
    sweep = subprocess.run(
        [
            *COMPOSE,
            "run",
            "--rm",
            "-e",
            "INGEST_STUCK_AFTER_SECONDS=0",
            "worker",
            "python",
            "-m",
            "src.sweep_once",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=COMPOSE_ENV,
    )
    for line in (sweep.stdout + sweep.stderr).splitlines():
        if "sweep_requeued" in line or "sweep_failed" in line:
            print(f"[sweep] {line}")

    _wait_status(client, token, recover_id, "ready", timeout=60)
    recovered = _chunk_hashes(org_id, recover_id)
    print(f"[recovered] document {recover_id} READY with {len(recovered)} chunks")

    identical = baseline == recovered and len(baseline) > 0
    print(
        f"[result] chunk-hash sets identical: {identical} "
        f"(baseline={len(baseline)}, recovered={len(recovered)})"
    )
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main())
