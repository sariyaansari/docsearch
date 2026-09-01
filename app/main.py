import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.cache import close_redis, get_redis
from app.config import settings
from app.database import close_pool, init_db
from app.rate_limit import enforce_rate_limit
from app.repository import document_repository
from app.schemas import (
    DocumentCreate,
    DocumentResponse,
    HealthDependency,
    HealthResponse,
    SearchResponse,
    SearchResultItem,
)
from app.search_cache import get_cached_search, set_cached_search
from app.tenancy import get_tenant_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_pool()
    await close_redis()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Prototype of a multi-tenant, horizontally-scalable document search "
        "service. See ARCHITECTURE.md in the repo for design rationale."
    ),
    lifespan=lifespan,
)

# Permissive CORS for local development / demo purposes only, so the included
# static search-ui.html (opened directly as a file, or served separately)
# can call this API from the browser. A production deployment should
# restrict `allow_origins` to the specific frontend domain(s) that need
# access, not "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak stack traces / internals to the client.
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": "An unexpected error occurred."},
    )


# ---------------------------------------------------------------------------
# POST /documents
# ---------------------------------------------------------------------------
@app.post("/documents", response_model=DocumentResponse, status_code=201, tags=["documents"])
async def create_document(
    payload: DocumentCreate,
    tenant_id: str = Depends(get_tenant_id),
):
    await enforce_rate_limit(None, tenant_id)
    record = await document_repository.create_document(
        tenant_id=tenant_id,
        title=payload.title,
        content=payload.content,
        metadata=payload.metadata,
    )
    return DocumentResponse(
        id=record["id"],
        tenant_id=record["tenant_id"],
        title=record["title"],
        content=record["content"],
        metadata=record["metadata"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


# ---------------------------------------------------------------------------
# GET /search
# ---------------------------------------------------------------------------
@app.get("/search", response_model=SearchResponse, tags=["search"])
async def search_documents(
    q: str,
    tenant_id: str = Depends(get_tenant_id),
    limit: int = settings.search_default_limit,
    offset: int = 0,
):
    await enforce_rate_limit(None, tenant_id)

    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' must not be empty.")
    limit = max(1, min(limit, settings.search_max_limit))
    offset = max(0, offset)

    start = time.perf_counter()

    cached = await get_cached_search(tenant_id, q, limit, offset)
    if cached is not None:
        cached["took_ms"] = round((time.perf_counter() - start) * 1000, 2)
        cached["cached"] = True
        return SearchResponse(**cached)

    rows, total = await document_repository.search(tenant_id, q, limit, offset)
    results = [
        SearchResultItem(
            id=row["id"],
            title=row["title"],
            snippet=row["snippet"],
            score=float(row["score"]),
            metadata=row["metadata"],
            created_at=row["created_at"],
        )
        for row in rows
    ]

    response = SearchResponse(
        query=q,
        tenant_id=tenant_id,
        total_results=total,
        limit=limit,
        offset=offset,
        took_ms=round((time.perf_counter() - start) * 1000, 2),
        cached=False,
        results=results,
    )

    await set_cached_search(tenant_id, q, limit, offset, response.model_dump(mode="json"))
    return response


# ---------------------------------------------------------------------------
# GET /documents/{id}
# ---------------------------------------------------------------------------
@app.get("/documents/{doc_id}", response_model=DocumentResponse, tags=["documents"])
async def get_document(doc_id: UUID, tenant_id: str = Depends(get_tenant_id)):
    await enforce_rate_limit(None, tenant_id)
    record = await document_repository.get_document(tenant_id, doc_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return DocumentResponse(
        id=record["id"],
        tenant_id=record["tenant_id"],
        title=record["title"],
        content=record["content"],
        metadata=record["metadata"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


# ---------------------------------------------------------------------------
# DELETE /documents/{id}
# ---------------------------------------------------------------------------
@app.delete("/documents/{doc_id}", status_code=204, tags=["documents"])
async def delete_document(doc_id: UUID, tenant_id: str = Depends(get_tenant_id)):
    await enforce_rate_limit(None, tenant_id)
    deleted = await document_repository.delete_document(tenant_id, doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found.")
    return None


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health_check():
    deps: dict[str, HealthDependency] = {}

    db_ok, db_latency, db_err = await document_repository.health_check()
    deps["postgres"] = HealthDependency(
        status="up" if db_ok else "down", latency_ms=round(db_latency, 2), detail=db_err
    )

    redis_ok, redis_latency, redis_err = True, 0.0, None
    try:
        start = time.perf_counter()
        await get_redis().ping()
        redis_latency = (time.perf_counter() - start) * 1000
    except Exception as exc:
        redis_ok, redis_err = False, str(exc)
    deps["redis"] = HealthDependency(
        status="up" if redis_ok else "down", latency_ms=round(redis_latency, 2), detail=redis_err
    )

    if db_ok and redis_ok:
        overall = "healthy"
    elif db_ok:
        overall = "degraded"  # Redis down: cache/rate-limit fail open, DB still serves
    else:
        overall = "unhealthy"  # DB down: cannot serve core functionality

    return HealthResponse(status=overall, dependencies=deps)


@app.get("/ui", tags=["ops"])
async def search_console():
    """Visual search console (search-ui.html) served directly by the API,
    so there's nothing separate to locate or open manually."""
    return FileResponse(Path(__file__).parent / "static" / "search-ui.html")


@app.get("/", tags=["ops"])
async def root():
    return {
        "service": settings.app_name,
        "status": "running",
        "docs": "/docs",
        "ui": "/ui",
    }
