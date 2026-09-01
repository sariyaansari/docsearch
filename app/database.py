"""
Postgres access layer.

Design notes (see ARCHITECTURE.md for full rationale):
- We use asyncpg directly (no ORM) for full control over the tsvector /
  GIN-index query plans that full-text search relies on.
- tenant_id is included in EVERY query's WHERE clause -- this is the
  application-level tenant isolation boundary. In production this would
  be additionally enforced with Postgres Row-Level Security (RLS) as a
  defense-in-depth layer (see production readiness doc).
"""
import json

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # asyncpg returns jsonb columns as raw strings by default; register a
    # codec so they come back as native Python dicts everywhere we query.
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            command_timeout=10,
            init=_init_connection,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector   TSVECTOR GENERATED ALWAYS AS (
                        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                        setweight(to_tsvector('english', coalesce(content, '')), 'B')
                    ) STORED,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tenant isolation + lookup performance
CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents (tenant_id);

-- Full-text search (GIN over generated tsvector) -- the core of sub-second
-- search across millions of rows.
CREATE INDEX IF NOT EXISTS idx_documents_search_vector
    ON documents USING GIN (search_vector);

-- Trigram index enables fuzzy / typo-tolerant search (ILIKE, similarity())
CREATE INDEX IF NOT EXISTS idx_documents_title_trgm
    ON documents USING GIN (title gin_trgm_ops);

-- Composite index used heavily by GET /documents/{id} + DELETE (tenant-scoped PK lookup)
CREATE INDEX IF NOT EXISTS idx_documents_tenant_id_pk ON documents (tenant_id, id);
"""


async def init_db() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
