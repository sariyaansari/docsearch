"""
Centralized configuration. All values overridable via environment variables
(12-factor style) so the same image runs unmodified in docker-compose,
Render, Fly.io, etc.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Database ---
    database_url: str = "postgresql://docsearch:docsearch@localhost:5432/docsearch"
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    # --- Redis (cache + rate limiter backend) ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Caching ---
    search_cache_ttl_seconds: int = 30  # short TTL: freshness > hit-rate for a search demo

    # --- Rate limiting (per tenant, fixed-window in Redis) ---
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # --- Search ---
    search_default_limit: int = 10
    search_max_limit: int = 100

    # --- App ---
    app_name: str = "Distributed Document Search Service"
    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
