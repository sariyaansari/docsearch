"""
Data access + search query logic, isolated from the HTTP layer so it can be
unit tested and reasoned about independently.
"""
import json
from typing import Any, Optional
from uuid import UUID

import asyncpg

from app.database import get_pool


class DocumentRepository:

    async def create_document(
        self, tenant_id: str, title: str, content: str, metadata: dict[str, Any]
    ) -> asyncpg.Record:
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                """
                INSERT INTO documents (tenant_id, title, content, metadata)
                VALUES ($1, $2, $3, $4)
                RETURNING id, tenant_id, title, content, metadata, created_at, updated_at
                """,
                tenant_id, title, content, metadata,
            )

    async def get_document(self, tenant_id: str, doc_id: UUID) -> Optional[asyncpg.Record]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # tenant_id in WHERE clause == the tenant isolation boundary.
            return await conn.fetchrow(
                """
                SELECT id, tenant_id, title, content, metadata, created_at, updated_at
                FROM documents
                WHERE id = $1 AND tenant_id = $2
                """,
                doc_id, tenant_id,
            )

    async def delete_document(self, tenant_id: str, doc_id: UUID) -> bool:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM documents WHERE id = $1 AND tenant_id = $2",
                doc_id, tenant_id,
            )
            # asyncpg returns strings like "DELETE 1"
            return result.endswith(" 1")

    async def search(
        self, tenant_id: str, query: str, limit: int, offset: int
    ) -> tuple[list[asyncpg.Record], int]:
        """
        Ranked full-text search scoped to a single tenant.

        - websearch_to_tsquery: lets users type natural queries
          ("machine learning" -project) without knowing tsquery syntax.
        - ts_rank_cd: relevance score used for ORDER BY.
        - ts_headline: generates a highlighted snippet (bonus: highlighting).
        - COUNT(*) OVER() gets total matching rows in the same query,
          avoiding a second round-trip for pagination metadata.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    id, title, metadata, created_at,
                    ts_rank_cd(search_vector, query) AS score,
                    ts_headline(
                        'english', content, query,
                        'MaxFragments=2, MaxWords=25, MinWords=10, StartSel=<mark>, StopSel=</mark>'
                    ) AS snippet,
                    COUNT(*) OVER() AS total_count
                FROM documents, websearch_to_tsquery('english', $2) AS query
                WHERE tenant_id = $1
                  AND search_vector @@ query
                ORDER BY score DESC, created_at DESC
                LIMIT $3 OFFSET $4
                """,
                tenant_id, query, limit, offset,
            )
            total = rows[0]["total_count"] if rows else 0
            return rows, total

    async def health_check(self) -> tuple[bool, float, Optional[str]]:
        import time
        start = time.perf_counter()
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True, (time.perf_counter() - start) * 1000, None
        except Exception as exc:
            return False, (time.perf_counter() - start) * 1000, str(exc)


document_repository = DocumentRepository()
