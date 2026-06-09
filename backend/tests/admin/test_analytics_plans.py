"""Plan assertions (CLAUDE.md §7): the analytics aggregates are index-backed — never a
sequential scan on token_usage_events / documents / audit_events."""

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.database import set_tenant_context

_QUERIES = {
    "token_usage_events": (
        "SELECT feature, COALESCE(SUM(total_tokens),0) FROM token_usage_events "
        "WHERE org_id = :org AND created_at >= now() - interval '30 days' AND created_at < now() "
        "GROUP BY feature"
    ),
    "documents": (
        "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM documents "
        "WHERE org_id = :org AND superseded = false"
    ),
    "audit_events": (
        "SELECT COUNT(DISTINCT actor_user_id) FROM audit_events "
        "WHERE org_id = :org AND created_at >= now() - interval '30 days' AND created_at < now()"
    ),
}


async def test_analytics_queries_are_index_backed(db_session: AsyncSession) -> None:
    org = uuid4()
    await set_tenant_context(db_session, org)
    # enable_seqscan = off makes the planner reveal whether a usable index exists for each
    # predicate; with the org_id-leading indexes it never falls back to a sequential scan.
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    for table, query in _QUERIES.items():
        plan = "\n".join(
            r[0] for r in (await db_session.execute(text(f"EXPLAIN {query}"), {"org": org})).all()
        )
        assert f"Seq Scan on {table}" not in plan, f"{table}:\n{plan}"
