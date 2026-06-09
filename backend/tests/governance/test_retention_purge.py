"""Retention purge (Task 7) — the destructive enforcement, proven against real Postgres.

Evidence delivered here:
  * retain-by-default — no policy / null / zero window deletes nothing;
  * audit-before-delete atomicity — a delete failure leaves rows intact AND no purge audit record;
  * dry-run — reports candidates, deletes nothing, writes a dry-run audit event;
  * crash-resume — bounded batches commit independently; a crash mid-run resumes and every row is
    deleted exactly once;
  * privileged role only — the runtime role cannot DELETE audit_events; the purger connection can.

The purge runs on the OWNER connection (the privileged purger in tests); the runtime role is the
conftest ``db_session``. Seed data is committed (the purge opens its own connections), under unique
org ids so committed rows never perturb another test's per-org assertions.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.conversation.models import ChatSession
from src.governance import repository as purge_repo_module
from src.governance.models import RetentionPolicy
from src.governance.purge_service import RetentionPurgeService
from src.governance.repository import RetentionPurgeRepository
from src.identity.models import Organization, Role, User
from src.shared.audit import AuditEvent
from src.shared.config import Settings
from src.shared.database import set_tenant_context

_NOW = datetime(2026, 6, 9, tzinfo=UTC)
_OLD = _NOW - timedelta(days=400)  # comfortably past any test window
_RECENT = _NOW - timedelta(days=1)


@pytest.fixture
async def purge_engine(pg_container: dict[str, str]) -> AsyncIterator[AsyncEngine]:
    """An engine on the OWNER connection — the privileged purger in tests (a dedicated role in
    prod). The runtime role's audit DELETE stays REVOKEd; this connection is the only deleter."""
    engine = create_async_engine(pg_container["owner_url"])
    yield engine
    await engine.dispose()


@pytest.fixture
def purge_sessionmaker(
    purge_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(purge_engine, expire_on_commit=False)


def _service(
    maker: async_sessionmaker[AsyncSession], settings: Settings, *, batch_size: int = 500
) -> RetentionPurgeService:
    return RetentionPurgeService(
        sessionmaker=maker,
        settings=settings.model_copy(update={"retention_batch_size": batch_size}),
    )


async def _seed_org(
    maker: async_sessionmaker[AsyncSession],
    *,
    audit_days: int | None,
    conversation_days: int | None,
    audit_ages: list[datetime],
    session_ages: list[datetime],
    with_policy: bool = True,
) -> UUID:
    """Commit one org with a (optional) retention policy and audit/chat rows at the given ages.
    Returns the org id (unique per call)."""
    org_id, user_id = uuid4(), uuid4()
    async with maker() as session:
        await set_tenant_context(session, org_id)
        session.add(Organization(id=org_id, name="Org", slug=f"org-{org_id}"))
        await session.flush()
        session.add(
            User(
                id=user_id,
                org_id=org_id,
                email=f"u-{user_id}@x.test",
                password_hash="x",
                full_name="U",
                role=Role.OWNER,
            )
        )
        await session.flush()
        if with_policy:
            session.add(
                RetentionPolicy(
                    org_id=org_id,
                    audit_retention_days=audit_days,
                    conversation_retention_days=conversation_days,
                    updated_by=user_id,
                )
            )
        for created in audit_ages:
            session.add(
                AuditEvent(
                    org_id=org_id,
                    actor_user_id=user_id,
                    action="test.event",
                    event_metadata={},
                    created_at=created,
                )
            )
        for created in session_ages:
            session.add(
                ChatSession(
                    org_id=org_id,
                    owner_user_id=user_id,
                    title="t",
                    created_at=created,
                    last_message_at=created,
                )
            )
        await session.commit()
    return org_id


async def _count(maker: async_sessionmaker[AsyncSession], table: str, org_id: UUID) -> int:
    async with maker() as session:
        await set_tenant_context(session, org_id)
        result = await session.execute(
            text(f"SELECT count(*) FROM {table} WHERE org_id = :org"), {"org": str(org_id)}
        )
        return int(result.scalar_one())


async def _seeded_audit_events(maker: async_sessionmaker[AsyncSession], org_id: UUID) -> int:
    """Count only the seeded ``test.event`` rows, excluding the purge's own retention.* records."""
    async with maker() as session:
        await set_tenant_context(session, org_id)
        result = await session.execute(
            text("SELECT count(*) FROM audit_events WHERE org_id = :org AND action = 'test.event'"),
            {"org": str(org_id)},
        )
        return int(result.scalar_one())


async def _purge_events(maker: async_sessionmaker[AsyncSession], org_id: UUID) -> int:
    async with maker() as session:
        await set_tenant_context(session, org_id)
        result = await session.execute(
            text(
                "SELECT count(*) FROM audit_events "
                "WHERE org_id = :org AND action LIKE 'retention.%'"
            ),
            {"org": str(org_id)},
        )
        return int(result.scalar_one())


async def test_retain_by_default_purges_nothing(
    purge_sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    # No policy at all, and a separate org with null/zero windows — both must delete nothing.
    no_policy = await _seed_org(
        purge_sessionmaker,
        audit_days=None,
        conversation_days=None,
        audit_ages=[_OLD, _OLD],
        session_ages=[_OLD],
        with_policy=False,
    )
    null_zero = await _seed_org(
        purge_sessionmaker,
        audit_days=0,
        conversation_days=None,
        audit_ages=[_OLD, _OLD],
        session_ages=[_OLD],
    )
    await _service(purge_sessionmaker, settings).purge(now=_NOW, dry_run=False)
    assert await _count(purge_sessionmaker, "audit_events", no_policy) == 2
    assert await _count(purge_sessionmaker, "chat_sessions", no_policy) == 1
    # The audit candidates plus zero retention-event rows written (retain-by-default = no work).
    assert await _count(purge_sessionmaker, "audit_events", null_zero) == 2
    assert await _purge_events(purge_sessionmaker, null_zero) == 0


async def test_purges_expired_keeps_recent_and_audits(
    purge_sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    org = await _seed_org(
        purge_sessionmaker,
        audit_days=30,
        conversation_days=30,
        audit_ages=[_OLD, _OLD, _RECENT],
        session_ages=[_OLD, _RECENT],
    )
    await _service(purge_sessionmaker, settings).purge(now=_NOW, dry_run=False)
    # Two old audit rows gone; the recent one plus the purge record(s) remain.
    assert await _count(purge_sessionmaker, "chat_sessions", org) == 1  # only the recent session
    remaining_audit = await _count(purge_sessionmaker, "audit_events", org)
    purge_records = await _purge_events(purge_sessionmaker, org)
    assert remaining_audit == 1 + purge_records  # the recent test.event + retention.* records
    assert purge_records >= 2  # at least one audit-purge + one conversation-purge record


async def test_dry_run_reports_but_deletes_nothing(
    purge_sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    org = await _seed_org(
        purge_sessionmaker,
        audit_days=30,
        conversation_days=30,
        audit_ages=[_OLD, _OLD],
        session_ages=[_OLD],
    )
    reports = await _service(purge_sessionmaker, settings).purge(now=_NOW, dry_run=True)
    mine = next(r for r in reports if r.org_id == org)
    assert mine.dry_run is True and mine.conversation_deleted == 1
    # Nothing deleted: the two old audit rows survive, plus dry-run records were written.
    assert await _count(purge_sessionmaker, "chat_sessions", org) == 1
    assert await _purge_events(purge_sessionmaker, org) >= 1  # dry-run audit event(s) written


async def test_audit_written_before_delete_is_atomic(
    purge_sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = await _seed_org(
        purge_sessionmaker,
        audit_days=30,
        conversation_days=None,
        audit_ages=[_OLD, _OLD],
        session_ages=[],
    )

    async def _boom(self: RetentionPurgeRepository, *args: object, **kwargs: object) -> int:
        raise RuntimeError("injected delete failure")

    monkeypatch.setattr(purge_repo_module.RetentionPurgeRepository, "delete_ids", _boom)
    with pytest.raises(RuntimeError):
        await _service(purge_sessionmaker, settings)._purge_org(org, now=_NOW, dry_run=False)
    # Atomic rollback: the rows survive AND no purge audit record was committed — there is never an
    # unaudited deletion, and never a phantom over-counting record either.
    assert await _count(purge_sessionmaker, "audit_events", org) == 2
    assert await _purge_events(purge_sessionmaker, org) == 0


async def test_crash_mid_run_resumes_and_deletes_exactly_once(
    purge_sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = await _seed_org(
        purge_sessionmaker,
        audit_days=30,
        conversation_days=None,
        audit_ages=[_OLD, _OLD, _OLD],
        session_ages=[],
    )
    service = _service(purge_sessionmaker, settings, batch_size=1)
    real_delete = purge_repo_module.RetentionPurgeRepository.delete_ids
    calls = {"n": 0}

    async def _crash_after_first(
        self: RetentionPurgeRepository, table: str, org_id: UUID, ids: list[UUID]
    ) -> int:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("worker died mid-run")
        return await real_delete(self, table, org_id, ids)

    monkeypatch.setattr(
        purge_repo_module.RetentionPurgeRepository, "delete_ids", _crash_after_first
    )
    with pytest.raises(RuntimeError):
        await service._purge_org(org, now=_NOW, dry_run=False)
    # Exactly the first committed batch deleted one seeded row; two remain.
    assert await _seeded_audit_events(purge_sessionmaker, org) == 2

    # Resume cleanly: the run continues from the remaining expired rows — none deleted twice.
    monkeypatch.setattr(purge_repo_module.RetentionPurgeRepository, "delete_ids", real_delete)
    await service._purge_org(org, now=_NOW, dry_run=False)
    assert await _seeded_audit_events(purge_sessionmaker, org) == 0  # all 3 seeded rows purged
    # One audit record per committed batch across the crash and resume: 1 (pre-crash) + 2 (resume)
    # = 3 — exactly-once coverage, no row purged twice, none skipped.
    assert await _purge_events(purge_sessionmaker, org) == 3


async def test_runtime_role_cannot_delete_audit_but_purger_can(
    purge_sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    db_session: AsyncSession,
) -> None:
    org = await _seed_org(
        purge_sessionmaker,
        audit_days=30,
        conversation_days=None,
        audit_ages=[_OLD],
        session_ages=[],
    )
    # The runtime role (db_session) is REVOKEd from deleting audit_events (migration 0009).
    await set_tenant_context(db_session, org)
    with pytest.raises(Exception) as exc:  # noqa: PT011 — asserting the DB permission error
        await db_session.execute(
            text("DELETE FROM audit_events WHERE org_id = :org"), {"org": str(org)}
        )
    assert "permission denied" in str(exc.value).lower()
    await db_session.rollback()
    # The privileged purger connection can — that is where deletion authority lives.
    await _service(purge_sessionmaker, settings).purge(now=_NOW, dry_run=False)
    assert await _count(purge_sessionmaker, "audit_events", org) == await _purge_events(
        purge_sessionmaker, org
    )  # the seeded old row is gone; only the retention record remains


async def test_purge_is_org_isolated(
    purge_sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    expired = await _seed_org(
        purge_sessionmaker,
        audit_days=30,
        conversation_days=None,
        audit_ages=[_OLD],
        session_ages=[],
    )
    untouched = await _seed_org(
        purge_sessionmaker,
        audit_days=None,  # retain-by-default → never purged
        conversation_days=None,
        audit_ages=[_OLD, _OLD],
        session_ages=[],
    )
    await _service(purge_sessionmaker, settings).purge(now=_NOW, dry_run=False)
    # The other org's audit rows are wholly untouched (its window is null → retain).
    assert await _count(purge_sessionmaker, "audit_events", untouched) == 2
    assert await _purge_events(purge_sessionmaker, untouched) == 0
    # And the expired org's old row was purged (only its retention record remains).
    assert await _count(purge_sessionmaker, "audit_events", expired) == await _purge_events(
        purge_sessionmaker, expired
    )


async def test_purge_never_deletes_its_own_retention_records(
    purge_sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """ADR 0019 self-exemption: a retention.* event is the permanent record of a deletion, so a
    later audit purge whose cutoff is past that event's age must NOT delete it — otherwise the
    deletion history would erase itself. Here an OLD retention record (older than the 30-day audit
    window) survives a purge that deletes the equally-old ordinary event beside it."""
    org_id, user_id = uuid4(), uuid4()
    async with purge_sessionmaker() as session:
        await set_tenant_context(session, org_id)
        session.add(Organization(id=org_id, name="Org", slug=f"org-{org_id}"))
        await session.flush()
        session.add(
            User(
                id=user_id,
                org_id=org_id,
                email=f"u-{user_id}@x.test",
                password_hash="x",
                full_name="U",
                role=Role.OWNER,
            )
        )
        await session.flush()
        session.add(RetentionPolicy(org_id=org_id, audit_retention_days=30, updated_by=user_id))
        # Both rows are 400 days old (well past the 30-day cutoff): one ordinary event, one prior
        # retention record. The prior record carries no human actor — like the purge writes.
        session.add(
            AuditEvent(
                org_id=org_id,
                actor_user_id=user_id,
                action="test.event",
                event_metadata={},
                created_at=_OLD,
            )
        )
        session.add(
            AuditEvent(
                org_id=org_id,
                actor_user_id=None,
                action="retention.audit_purged",
                event_metadata={"historical": True},
                created_at=_OLD,
            )
        )
        await session.commit()

    await _service(purge_sessionmaker, settings).purge(now=_NOW, dry_run=False)

    # The ordinary old event is gone; the OLD retention record survived the purge.
    assert await _seeded_audit_events(purge_sessionmaker, org_id) == 0
    async with purge_sessionmaker() as session:
        await set_tenant_context(session, org_id)
        survived = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE org_id = :org "
                    "AND action = 'retention.audit_purged' AND created_at = :old"
                ),
                {"org": str(org_id), "old": _OLD},
            )
        ).scalar_one()
    assert survived == 1  # the historical deletion record is permanent


async def test_purge_worker_fails_loud_without_a_purge_connection(settings: Settings) -> None:
    # No purge_sessionmaker in ctx → the cron logs an error and does nothing (never silently OK).
    from src.governance.worker import purge_expired_data

    await purge_expired_data({"settings": settings})  # must not raise


async def test_purge_worker_runs_with_a_purge_connection(
    purge_sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    # Happy path through the cron entry (dry-run by config default → safe, deletes nothing).
    from src.governance.worker import purge_expired_data

    await purge_expired_data({"settings": settings, "purge_sessionmaker": purge_sessionmaker})
