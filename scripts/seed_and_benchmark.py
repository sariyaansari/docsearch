"""
Seeds sample documents across a few tenants, then runs a simple concurrent
benchmark against GET /search to produce p50/p95/p99 latency numbers for
the README / architecture doc's "performance benchmarks" bonus section.

Usage:
    python scripts/seed_and_benchmark.py --base-url http://localhost:8000 --docs 500 --requests 200 --concurrency 20
"""
import argparse
import asyncio
import random
import statistics
import time

import httpx

SAMPLE_TITLES = [
    "Quarterly Financial Report", "Employee Onboarding Guide", "Product Roadmap 2026",
    "Incident Postmortem: API Outage", "Customer Success Playbook", "Security Audit Findings",
    "Machine Learning Pipeline Design", "Marketing Campaign Analysis", "Database Migration Plan",
    "Sales Enablement Deck", "Compliance Policy Update", "Engineering Architecture Review",
]
SAMPLE_BODY_WORDS = (
    "search engine distributed system database cache latency throughput scalability "
    "tenant isolation elasticsearch postgres redis kubernetes microservice api gateway "
    "load balancer replication sharding consistency availability partition tolerance "
    "monitoring observability tracing metrics logging performance optimization index"
).split()


def random_content(word_count: int = 120) -> str:
    return " ".join(random.choices(SAMPLE_BODY_WORDS, k=word_count))


async def seed(base_url: str, num_docs: int, tenants: list[str]):
    # Seeding intentionally paces itself under the default per-tenant rate
    # limit (100 req / 60s) rather than bypassing it -- this script hits the
    # real API just like any other client would.
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(num_docs):
            tenant = random.choice(tenants)
            payload = {
                "title": f"{random.choice(SAMPLE_TITLES)} #{i}",
                "content": random_content(),
                "metadata": {"category": random.choice(["finance", "eng", "hr", "sales"])},
            }
            resp = await client.post(
                f"{base_url}/documents", json=payload, headers={"X-Tenant-ID": tenant}
            )
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2))
                await asyncio.sleep(retry_after + 0.1)
                resp = await client.post(
                    f"{base_url}/documents", json=payload, headers={"X-Tenant-ID": tenant}
                )
            resp.raise_for_status()
        print(f"Seeded {num_docs} documents across tenants: {tenants}")


async def bench_one(client: httpx.AsyncClient, base_url: str, tenant: str) -> float:
    term = random.choice(SAMPLE_BODY_WORDS)
    start = time.perf_counter()
    resp = await client.get(
        f"{base_url}/search", params={"q": term}, headers={"X-Tenant-ID": tenant}
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    resp.raise_for_status()
    return elapsed_ms


async def benchmark(base_url: str, num_requests: int, concurrency: int, tenants: list[str]):
    latencies: list[float] = []
    sem = asyncio.Semaphore(concurrency)

    async def worker():
        async with sem:
            async with httpx.AsyncClient(timeout=10) as client:
                latencies.append(await bench_one(client, base_url, random.choice(tenants)))

    start = time.perf_counter()
    await asyncio.gather(*[worker() for _ in range(num_requests)])
    total_time = time.perf_counter() - start

    latencies.sort()
    def pct(p):
        idx = min(int(len(latencies) * p / 100), len(latencies) - 1)
        return latencies[idx]

    print(f"\n--- Benchmark results ({num_requests} requests, concurrency={concurrency}) ---")
    print(f"Total wall time : {total_time:.2f}s")
    print(f"Throughput      : {num_requests / total_time:.1f} req/s")
    print(f"p50 latency     : {pct(50):.1f} ms")
    print(f"p95 latency     : {pct(95):.1f} ms")
    print(f"p99 latency     : {pct(99):.1f} ms")
    print(f"mean latency    : {statistics.mean(latencies):.1f} ms")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--docs", type=int, default=500)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--skip-seed", action="store_true")
    args = parser.parse_args()

    tenants = ["tenant-acme", "tenant-globex", "tenant-initech"]

    if not args.skip_seed:
        await seed(args.base_url, args.docs, tenants)

    await benchmark(args.base_url, args.requests, args.concurrency, tenants)


if __name__ == "__main__":
    asyncio.run(main())
