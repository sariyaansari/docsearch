"""
Minimal smoke tests exercising tenant isolation and the core CRUD/search flow
against a running instance. Not a full pytest+fixtures suite (out of scope
for a time-boxed prototype per the assignment's own guidance to focus on
architectural thinking over exhaustive implementation), but enough to prove
the critical correctness property -- tenant isolation -- is enforced.

Run against a live instance:
    BASE_URL=http://localhost:8000 python tests/test_smoke.py
"""
import os
import uuid

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")


def test_health():
    resp = httpx.get(f"{BASE_URL}/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("healthy", "degraded")
    print("PASS: health check")


def test_index_search_isolation_delete():
    tenant_a = f"tenant-a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant-b-{uuid.uuid4().hex[:8]}"
    unique_term = f"uniqueterm{uuid.uuid4().hex[:8]}"

    # Index a doc for tenant A containing a unique search term
    resp = httpx.post(
        f"{BASE_URL}/documents",
        json={"title": "Test Doc", "content": f"This contains {unique_term} inside it.", "metadata": {}},
        headers={"X-Tenant-ID": tenant_a},
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]

    # Tenant A can find it
    resp = httpx.get(f"{BASE_URL}/search", params={"q": unique_term}, headers={"X-Tenant-ID": tenant_a})
    assert resp.status_code == 200
    assert resp.json()["total_results"] == 1
    print("PASS: tenant A finds its own document")

    # Tenant B cannot find it (isolation)
    resp = httpx.get(f"{BASE_URL}/search", params={"q": unique_term}, headers={"X-Tenant-ID": tenant_b})
    assert resp.status_code == 200
    assert resp.json()["total_results"] == 0
    print("PASS: tenant B cannot see tenant A's document (search isolation)")

    # Tenant B cannot fetch it by ID either
    resp = httpx.get(f"{BASE_URL}/documents/{doc_id}", headers={"X-Tenant-ID": tenant_b})
    assert resp.status_code == 404
    print("PASS: tenant B gets 404 on tenant A's document ID (direct-access isolation)")

    # Tenant A can delete it
    resp = httpx.delete(f"{BASE_URL}/documents/{doc_id}", headers={"X-Tenant-ID": tenant_a})
    assert resp.status_code == 204
    print("PASS: delete succeeds for owning tenant")


def test_missing_tenant_rejected():
    resp = httpx.get(f"{BASE_URL}/search", params={"q": "anything"})
    assert resp.status_code == 400
    print("PASS: request without tenant identifier is rejected")


if __name__ == "__main__":
    test_health()
    test_index_search_isolation_delete()
    test_missing_tenant_rejected()
    print("\nAll smoke tests passed.")
