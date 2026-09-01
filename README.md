# Distributed Document Search Service

A prototype multi-tenant document search service built with **FastAPI + PostgreSQL Full-Text Search + Redis**, demonstrating enterprise architectural patterns (multi-tenancy, caching, rate limiting, horizontal scalability) at a scope appropriate for a technical assessment.

📄 Full architecture design, production-readiness analysis, and experience showcase: **[DOCUMENTATION.md](./DOCUMENTATION.md)**

## Features

- REST API: index, search, retrieve, and delete documents
- Full-text search with relevance ranking, natural-language queries, and highlighted snippets
- Header-based multi-tenancy (`X-Tenant-ID`) with strict per-query isolation
- Redis-backed search-result caching (cache-aside, tenant-scoped)
- Per-tenant rate limiting (Redis fixed-window, fails open on Redis outage)
- Dependency-aware health check (`/health`)
- Fuzzy-search-ready (`pg_trgm` trigram index) — bonus foundation
- Docker Compose for one-command local setup, including an optional 2-replica profile to demo horizontal scaling

## Quick Start (Local, Docker)

```bash
git clone <your-repo-url>
cd docsearch
docker compose up --build
```

The API is now live at `http://localhost:8000`. Interactive docs: `http://localhost:8000/docs`.

**Visual search console:** open `http://localhost:8000/ui` in your browser — the API serves this directly, so there's nothing separate to open or locate. Index documents and search with live, highlighted results, no command line needed.

(A standalone copy also exists at `search-ui.html` in this repo, which works the same way if opened directly as a file — useful if you want to point it at a different environment, like a deployed URL, by editing its "API URL" field.)

**To browse the raw database** (see tenant isolation and the full-text search index for yourself), open `http://localhost:8080` — that's [Adminer](https://www.adminer.org), a lightweight Postgres web UI included in the compose file. Log in with:
- System: `PostgreSQL`
- Server: `postgres`
- Username: `docsearch`
- Password: `docsearch`
- Database: `docsearch`

Run the sample requests (indexing, search, tenant-isolation check, delete):
```bash
BASE_URL=http://localhost:8000 bash scripts/sample_requests.sh
```

Or import `scripts/postman_collection.json` into Postman.

To demo horizontal scaling locally (2 app replicas behind the same DB/cache):
```bash
docker compose --profile scale up --build
# app-2 is now reachable at http://localhost:8001
```

## Quick Start (Local, without Docker)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit if your local Postgres/Redis differ
# Ensure Postgres and Redis are running locally, matching .env
uvicorn app.main:app --reload
```

## Seeding Data & Benchmarking

```bash
python scripts/seed_and_benchmark.py --base-url http://localhost:8000 --docs 2000 --requests 300 --concurrency 20
```

Prints throughput and p50/p95/p99 latency. See DOCUMENTATION.md §2.5 for a real run's results and a notable finding about connection-pool sizing under load.

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/documents` | Index a document. Body: `{"title", "content", "metadata"}` |
| `GET` | `/search?q=...&limit=&offset=` | Ranked full-text search |
| `GET` | `/documents/{id}` | Retrieve a document |
| `DELETE` | `/documents/{id}` | Remove a document |
| `GET` | `/health` | Health + dependency status |

All endpoints (except `/health`) require a tenant identifier, via either:
- Header: `X-Tenant-ID: your-tenant-id`, or
- Query param: `?tenant=your-tenant-id`

Full request/response schemas: `/docs` (Swagger) once the app is running.

## Deploying for Free (so the interviewer can hit a live URL)

This stack fits comfortably into free tiers. Total cost: **$0**.

| Component | Service | Free tier notes |
|---|---|---|
| Database | [Neon](https://neon.tech) | Serverless Postgres, generous free tier, supports the `pg_trgm` extension used here |
| Cache | [Upstash Redis](https://upstash.com) | Free tier, standard Redis protocol (used here via TLS) |
| App hosting | [Render](https://render.com) (Web Service, free tier) | Free tier sleeps after ~15 min inactivity, wakes on the next request (~30-60s cold start) |
| Image registry | GitHub Container Registry (ghcr.io) | Free, reuses the GitHub account you already need for submission — no separate Docker Hub signup |

### Option A — automated script (recommended)

`deploy/deploy.sh` provisions Neon, Upstash, and Render via their APIs and deploys the app, end to end, from one command. It's idempotent — safe to re-run if a step fails partway (it skips anything already provisioned).

**1. Prepare credentials, in this order** (each one unblocks the next — full detail on where to get each is in the file itself):

| # | Credential | Where |
|---|---|---|
| 1 | GitHub Personal Access Token (`repo`, `write:packages`, `read:packages`) | github.com/settings/tokens |
| 2 | Neon API key | console.neon.tech → Account Settings → API Keys |
| 3 | Upstash email + API key | console.upstash.com → Account → Management API |
| 4 | Render API key + Workspace ID | dashboard.render.com → Account Settings → API Keys |

```bash
cp deploy/.env.deploy.example deploy/.env.deploy
# edit deploy/.env.deploy and fill in the 4 credentials above
```

⚠️ **`deploy/.env.deploy` is git-ignored on purpose — never commit it.** The script itself (`deploy.sh`) contains no secrets and is safe to commit; only the filled-in credentials file is sensitive.

**2. Run it:**

```bash
# Stand up and smoke-test the stack locally first (optional but recommended):
./deploy/deploy.sh local

# Provision Neon + Upstash + Render and deploy:
./deploy/deploy.sh cloud
```

The script prints the live URL at the end and polls `/health` until it responds. If any cloud step fails, it prints the raw API error and a link to that provider's current API reference — provider APIs occasionally change field names, and re-running the script after fixing `.env.deploy` picks up where it left off rather than starting over.

To re-check a previously deployed instance: `./deploy/deploy.sh cloud verify`

### Option B — manual dashboard setup

If you'd rather click through each provider's UI (or the script hits an API change it doesn't handle):

1. **Database** — sign up at Neon, create a project, copy the connection string.
2. **Cache** — sign up at Upstash, create a Redis database, copy the connection string.
3. **Push this repo to GitHub.**
4. **Deploy to Render:**
   - New → Web Service → connect your GitHub repo (or use the included `render.yaml` blueprint via New → Blueprint)
   - Environment: Docker (picks up the `Dockerfile` automatically)
   - Add environment variables: `DATABASE_URL`, `REDIS_URL`
   - Deploy — Render gives you a public URL like `https://your-service.onrender.com`
5. **Verify:** `curl https://your-service.onrender.com/health`

### Free-tier caveats to mention proactively in your interview

- Render's free web service **spins down after ~15 minutes of inactivity** and takes ~30-60s to wake on the next request — a free-tier limitation, not an architectural flaw, worth stating explicitly.
- Neon/Upstash free tiers cap storage and request volume well below production scale — fine for a demo of thousands of documents. The same code runs unmodified against production-tier instances; only the connection strings change.

## Project Structure

```
docsearch/
├── app/
│   ├── main.py          # FastAPI app, all endpoints
│   ├── config.py        # Settings (env-var driven)
│   ├── database.py      # Postgres pool + schema DDL
│   ├── cache.py          # Redis client
│   ├── rate_limit.py    # Per-tenant rate limiting
│   ├── repository.py    # Document CRUD + search queries
│   ├── search_cache.py  # Search result cache-aside logic
│   ├── tenancy.py       # Tenant resolution dependency
│   ├── schemas.py       # Pydantic request/response models
│   └── static/
│       └── search-ui.html  # served at GET /ui by the app itself
├── docs/
│   ├── architecture-diagram.svg       # embedded in DOCUMENTATION.md §1.1
│   └── production-scale-diagram.svg   # embedded in DOCUMENTATION.md §1.3
├── scripts/
│   ├── sample_requests.sh
│   ├── postman_collection.json
│   └── seed_and_benchmark.py
├── docker-compose.yml
├── Dockerfile
├── render.yaml
├── search-ui.html         # standalone copy of the same console; point its API URL field at any environment
├── deploy/
│   ├── deploy.sh              # automated local + cloud deployment script
│   └── .env.deploy.example    # credentials template (copy to .env.deploy)
├── requirements.txt
├── DOCUMENTATION.md      # Architecture + production readiness + experience showcase
└── README.md
```

## Assumptions & Simplifications (documented per assignment instructions)

- Tenant identity is trusted directly from the `X-Tenant-ID` header. In production this would be derived from a verified JWT claim at the API gateway — see DOCUMENTATION.md §2.3.
- PostgreSQL FTS is used instead of Elasticsearch for the prototype (deployability on free infra, sufficient for the stated scale) — full trade-off rationale in DOCUMENTATION.md §1.3.
- No message queue in the prototype (not needed given Postgres FTS's synchronous indexing) — its production role is designed and documented in DOCUMENTATION.md §1.7.
- Auth (JWT validation) is out of scope for the prototype's code — only tenant isolation is implemented — per the assignment's "mock external dependencies where appropriate" guidance.