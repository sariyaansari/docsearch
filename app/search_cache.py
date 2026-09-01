"""
Cache-aside layer for search results.

Key design: cache key includes tenant_id, query, limit, and offset -- so
cache entries are naturally tenant-isolated (no cross-tenant leakage risk
via the cache) and pagination-safe.

Invalidation strategy: short TTL (default 30s) rather than active
invalidation on write. For a search product, slightly-stale results for a
few seconds is an acceptable trade-off against the complexity of tracking
"which cached queries could this new document affect" (that requires
either invalidating the entire tenant's cache namespace on every write, or
accepting staleness). We document the alternative -- tenant-scoped cache
invalidation on write -- in ARCHITECTURE.md.
"""
import hashlib
import json
from typing import Optional

from app.cache import get_redis
from app.config import settings


def _cache_key(tenant_id: str, query: str, limit: int, offset: int) -> str:
    raw = f"{tenant_id}:{query}:{limit}:{offset}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"search:{tenant_id}:{digest}"


async def get_cached_search(tenant_id: str, query: str, limit: int, offset: int) -> Optional[dict]:
    try:
        redis_client = get_redis()
        cached = await redis_client.get(_cache_key(tenant_id, query, limit, offset))
        return json.loads(cached) if cached else None
    except Exception:
        return None  # cache is best-effort; never fail the request over it


async def set_cached_search(tenant_id: str, query: str, limit: int, offset: int, payload: dict) -> None:
    try:
        redis_client = get_redis()
        await redis_client.set(
            _cache_key(tenant_id, query, limit, offset),
            json.dumps(payload, default=str),
            ex=settings.search_cache_ttl_seconds,
        )
    except Exception:
        pass
