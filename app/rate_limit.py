"""
Per-tenant rate limiting.

Algorithm: fixed-window counter in Redis.
    key = "ratelimit:{tenant_id}:{current_window_start}"
    INCR key; if first increment, set TTL = window_seconds.
    Reject if count > limit.

This is O(1) per request and cheap, at the cost of allowing up to 2x the
limit at window boundaries (a well-known fixed-window trade-off). For a
production SLA-backed API we'd switch to a sliding-window-log or
token-bucket implemented via a Redis Lua script for atomicity -- documented
in the production readiness doc.

Fails OPEN on Redis errors: if the rate limiter's backing store is down, we
choose to let traffic through rather than take the whole API down over a
non-critical dependency. This trade-off is explicit and documented.
"""
import time

from fastapi import HTTPException, Request

from app.cache import get_redis
from app.config import settings


async def enforce_rate_limit(request: Request, tenant_id: str) -> None:
    redis_client = get_redis()
    window = int(time.time() // settings.rate_limit_window_seconds)
    key = f"ratelimit:{tenant_id}:{window}"

    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, settings.rate_limit_window_seconds)
    except Exception:
        # Fail open -- see module docstring.
        return

    if count > settings.rate_limit_requests:
        retry_after = settings.rate_limit_window_seconds - (
            int(time.time()) % settings.rate_limit_window_seconds
        )
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded for tenant '{tenant_id}': "
                f"{settings.rate_limit_requests} requests / "
                f"{settings.rate_limit_window_seconds}s"
            ),
            headers={"Retry-After": str(retry_after)},
        )
