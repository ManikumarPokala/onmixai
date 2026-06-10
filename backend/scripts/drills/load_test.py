"""Reference-scale load harness (Phase 7 / Task 1). Drives concurrent load across the real hot
paths and records p50/p95/p99 + error rate per endpoint, then checks each against its NFR target.

RUN BY YOU against a running stack (it is not part of CI — it needs a live API + a seeded corpus).
Seed first (``python -m scripts.seed_demo``; for the 1M-chunk capacity proof use your bulk seeder),
then:

    python -m scripts.drills.load_test --base-url http://localhost:8000 --users 100 --duration 60

Any model-dependent latency (chat) reflects the configured provider: against the deterministic stub
these numbers are pipeline/transport latency, NOT real-model latency — re-run against your real or
Azure deployment for representative chat numbers. Search p95 is real capacity and is the NFR proof.
"""

import argparse
import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

# NFR targets (seconds), from docs/performance.md. None = recorded but not gated here.
_NFR = {
    "search": 3.0,  # search p95 < 3s @ 1M chunks — the capacity proof
    "chat_first_token": 3.0,  # first token < 3s (provider-dependent; stub caveat)
    "chat_full": 15.0,  # full answer p95 < 15s (stub delay model)
    "doc_status": 1.0,
    "recommendation": None,  # async create; recorded, not gated
}


@dataclass
class Samples:
    latencies: list[float] = field(default_factory=list)
    errors: int = 0

    def pct(self, q: float) -> float:
        if not self.latencies:
            return float("nan")
        ordered = sorted(self.latencies)
        idx = min(len(ordered) - 1, int(q * len(ordered)))
        return ordered[idx]


async def _login(client: httpx.AsyncClient, base: str, org: str, email: str, pw: str) -> str:
    resp = await client.post(
        f"{base}/api/v1/auth/login", json={"org_slug": org, "email": email, "password": pw}
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


async def _timed(samples: Samples, coro: "asyncio.Future[httpx.Response] | object") -> None:
    started = time.monotonic()
    try:
        resp = await coro  # type: ignore[misc]
        if resp.status_code >= 500:
            samples.errors += 1
        else:
            samples.latencies.append(time.monotonic() - started)
    except Exception:  # noqa: BLE001 — a load driver counts any failure as an error, never crashes
        samples.errors += 1


async def _worker(
    base: str, headers: dict[str, str], deadline: float, out: dict[str, Samples]
) -> None:
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        while time.monotonic() < deadline:
            # search — the capacity-critical path
            await _timed(
                out["search"],
                client.post(f"{base}/api/v1/search", json={"query": "startup preheat temperature"}),
            )
            # chat — create a session, then stream one message (measure first-token + full)
            try:
                s = await client.post(f"{base}/api/v1/chat/sessions", json={"title": "load"})
                sid = s.json().get("id")
                if sid:
                    await _stream_chat(client, base, sid, out)
            except Exception:  # noqa: BLE001
                out["chat_full"].errors += 1
            # recommendation — async create (recorded, not gated)
            await _timed(
                out["recommendation"],
                client.post(
                    f"{base}/api/v1/recommendations", json={"question": "which procedure applies?"}
                ),
            )


async def _stream_chat(
    client: httpx.AsyncClient, base: str, sid: str, out: dict[str, Samples]
) -> None:
    started = time.monotonic()
    first_token_recorded = False
    try:
        async with client.stream(
            "POST",
            f"{base}/api/v1/chat/sessions/{sid}/messages",
            json={"content": "what is the startup preheat temperature?"},
        ) as resp:
            if resp.status_code >= 500:
                out["chat_full"].errors += 1
                return
            async for line in resp.aiter_lines():
                if line.strip() and not first_token_recorded:
                    out["chat_first_token"].latencies.append(time.monotonic() - started)
                    first_token_recorded = True
            out["chat_full"].latencies.append(time.monotonic() - started)
    except Exception:  # noqa: BLE001
        out["chat_full"].errors += 1


def _report(out: dict[str, Samples], users: int, duration: int) -> bool:
    print(f"\nload profile: {users} concurrent users, {duration}s\n")
    print(f"{'endpoint':<20}{'n':>7}{'p50':>9}{'p95':>9}{'p99':>9}{'err':>7}{'NFR':>10}")
    all_pass = True
    for name, s in out.items():
        target = _NFR.get(name)
        p95 = s.pct(0.95)
        verdict = "—"
        if target is not None and s.latencies:
            ok = p95 < target
            verdict = f"<{target}s {'PASS' if ok else 'MISS'}"
            all_pass = all_pass and ok
        n = len(s.latencies)
        print(
            f"{name:<20}{n:>7}{s.pct(0.5):>9.3f}{p95:>9.3f}{s.pct(0.99):>9.3f}{s.errors:>7}{verdict:>10}"
        )
    print(
        "\nNote: chat_* is provider-dependent — stub numbers are transport, not real-model latency."
    )
    return all_pass


async def _main(args: argparse.Namespace) -> int:
    out: dict[str, Samples] = defaultdict(Samples)
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _login(client, args.base_url, args.org, args.email, args.password)
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.monotonic() + args.duration
    await asyncio.gather(
        *(_worker(args.base_url, headers, deadline, out) for _ in range(args.users))
    )
    all_pass = _report(out, args.users, args.duration)
    print(f"\n{'ALL NFR TARGETS MET' if all_pass else 'SOME NFR TARGETS MISSED — record + plan'}.")
    return 0 if all_pass else 1


def main() -> int:
    p = argparse.ArgumentParser(description="OnMixAI reference-scale load harness (user-run).")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--users", type=int, default=100)
    p.add_argument("--duration", type=int, default=60)
    p.add_argument("--org", default="onmix-demo")
    p.add_argument("--email", default="demo@onmix.test")
    p.add_argument("--password", default="demo-operator-pw-123456")
    return asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
