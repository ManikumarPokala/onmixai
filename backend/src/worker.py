"""ARQ worker entrypoint (composition root): `arq src.worker.WorkerSettings`.

Imports every model module so all tables register on Base.metadata (the worker
queries documents/chunks whose foreign keys target identity tables). The
knowledge domain itself must not import identity's models (import-linter); that
wiring belongs here at the composition root, like src/main.py.
"""

from arq import cron
from arq.connections import RedisSettings

from src.identity import models as _identity_models  # noqa: F401 - register identity tables
from src.knowledge import models as _knowledge_models  # noqa: F401 - register knowledge tables
from src.knowledge.worker import ingest_document, ingest_startup, sweep_stuck_documents
from src.shared.config import get_settings


class WorkerSettings:
    functions = [ingest_document]
    cron_jobs = [cron(sweep_stuck_documents, minute=set(range(0, 60, 5)), run_at_startup=False)]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = ingest_startup
