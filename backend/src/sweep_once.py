"""One-shot sweeper entrypoint (composition root): `python -m src.sweep_once`.

Re-queues stuck PROCESSING documents and exits. Used by the operations runbook
and the failure drills. Registers all model tables (see src/worker.py).
"""

import asyncio
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from src.identity import models as _identity_models  # noqa: F401 - register identity tables
from src.knowledge import models as _knowledge_models  # noqa: F401 - register knowledge tables
from src.knowledge.worker import ingest_startup, sweep_stuck_documents
from src.shared.config import get_settings
from src.worker import _make_tenant_lister


async def main() -> None:
    ctx: dict[str, Any] = {}
    await ingest_startup(ctx)
    ctx["tenant_lister_factory"] = _make_tenant_lister
    ctx["redis"] = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await sweep_stuck_documents(ctx)
    finally:
        await ctx["redis"].aclose()


if __name__ == "__main__":
    asyncio.run(main())
