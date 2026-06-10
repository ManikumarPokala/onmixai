"""Bulk-seed N synthetic chunks into the demo org for the reference-scale load test (Phase 7 /
Task 1). RUN BY YOU against the live stack, AFTER `python -m scripts.seed_demo` (it needs the demo
org). Mirrors the benchmark's approach: drop the HNSW index, batch-insert chunks with random
embeddings, rebuild the index with the configured params, ANALYZE. Not part of CI.

    python -m scripts.drills.seed_bulk --count 1000000 --batch 5000

The chunks are synthetic (random vectors, ``term{i % 500}`` keyword) — enough for a real HNSW +
FTS capacity test of /search at scale. It is a capacity proof, not a quality one.
"""

import argparse
import asyncio
import sys
from uuid import uuid4


async def _run(count: int, batch: int) -> int:
    import numpy as np
    from sqlalchemy import insert, select, text

    from scripts.demo_corpus import DEMO_ORG_SLUG
    from src.identity.models import Organization, User
    from src.knowledge.models import Chunk, Collection, Document, DocumentStatus
    from src.shared.config import get_embedding_dimension, get_index_params, get_settings
    from src.shared.database import get_sessionmaker, set_tenant_context

    if get_settings().env == "prod":
        print("✗ refusing to bulk-seed in a production environment (ENV=prod).", file=sys.stderr)
        return 2

    dim = get_embedding_dimension()
    params = get_index_params()
    maker = get_sessionmaker()

    # Locate the demo org + owner (seed_demo must have run) and create a load-test doc to attach to.
    async with maker() as s:
        org = (
            await s.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
        ).scalar_one_or_none()
        if org is None:
            print(
                "✗ demo org not found — run `python -m scripts.seed_demo` first.", file=sys.stderr
            )
            return 1
        await set_tenant_context(s, org.id)
        owner = (await s.execute(select(User).where(User.org_id == org.id))).scalars().first()
        assert owner is not None
        collection = Collection(org_id=org.id, name="Load test", created_by=owner.id)
        s.add(collection)
        await s.flush()
        document = Document(
            org_id=org.id,
            collection_id=collection.id,
            filename="bulk.txt",
            content_type="text/plain",
            size_bytes=count,
            storage_key=f"{org.id}/bulk",
            content_hash="bulk-doc",
            status=DocumentStatus.READY,
            created_by=owner.id,
        )
        s.add(document)
        await s.commit()
        org_id, doc_id = org.id, document.id

    print(f"→ bulk-seeding {count:,} chunks (dim={dim}, batch={batch}) into '{DEMO_ORG_SLUG}' …")
    # Drop the HNSW index for a fast bulk load, then rebuild once at the end (benchmark pattern).
    async with maker() as s:
        await s.execute(text("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw"))
        await s.commit()
    rng = np.random.default_rng(42)
    for start in range(0, count, batch):
        size = min(batch, count - start)
        vecs = rng.random((size, dim)).tolist()
        async with maker() as s:
            await set_tenant_context(s, org_id)
            await s.execute(
                insert(Chunk),
                [
                    {
                        "id": uuid4(),
                        "org_id": org_id,
                        "document_id": doc_id,
                        "seq": start + i,
                        "content": f"load chunk {start + i} term{(start + i) % 500}",
                        "content_hash": f"bulk-{start + i}",
                        "token_count": 4,
                        "chunk_metadata": {},
                        "embedding": vecs[i],
                    }
                    for i in range(size)
                ],
            )
            await s.commit()
        if (start // batch) % 20 == 0:
            print(f"  {start + size:,}/{count:,}")

    print("→ rebuilding HNSW index + ANALYZE …")
    async with maker() as s:
        await s.execute(text("SET statement_timeout = 0"))
        await s.execute(
            text(
                "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
                "USING hnsw (embedding vector_cosine_ops) "
                f"WITH (m = {params.hnsw_m}, ef_construction = {params.hnsw_ef_construction})"
            )
        )
        await s.execute(text("ANALYZE chunks"))
        await s.commit()
    print(
        f"✓ seeded {count:,} chunks + rebuilt HNSW. Ready for `bash scripts/drills/load_test.sh`."
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Bulk-seed synthetic chunks for the load test (user-run)."
    )
    p.add_argument("--count", type=int, default=1_000_000)
    p.add_argument("--batch", type=int, default=5000)
    args = p.parse_args()
    return asyncio.run(_run(args.count, args.batch))


if __name__ == "__main__":
    raise SystemExit(main())
