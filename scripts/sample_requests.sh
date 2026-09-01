#!/usr/bin/env bash
# Sample API requests against the Document Search Service.
# Usage: BASE_URL=http://localhost:8000 ./scripts/sample_requests.sh
set -euo pipefail
BASE_URL="${BASE_URL:-http://localhost:8000}"
TENANT="tenant-acme"

echo "== Health check =="
curl -s "$BASE_URL/health" | python3 -m json.tool

echo -e "\n== Index a document (tenant-acme) =="
DOC=$(curl -s -X POST "$BASE_URL/documents" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT" \
  -d '{
    "title": "Distributed Systems Design Patterns",
    "content": "This document covers caching strategies, database sharding, consistent hashing, and circuit breaker patterns used in large-scale distributed search systems.",
    "metadata": {"category": "engineering", "author": "jane.doe"}
  }')
echo "$DOC" | python3 -m json.tool
DOC_ID=$(echo "$DOC" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")

echo -e "\n== Index a second document (different tenant -- isolation demo) =="
curl -s -X POST "$BASE_URL/documents" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: tenant-globex" \
  -d '{"title": "Globex Confidential Memo", "content": "This should never appear in tenant-acme search results.", "metadata": {}}' \
  | python3 -m json.tool

echo -e "\n== Search within tenant-acme (should find doc 1, NOT the Globex memo) =="
curl -s -G "$BASE_URL/search" \
  -H "X-Tenant-ID: $TENANT" \
  --data-urlencode "q=distributed caching" | python3 -m json.tool

echo -e "\n== Get document by ID =="
curl -s "$BASE_URL/documents/$DOC_ID" -H "X-Tenant-ID: $TENANT" | python3 -m json.tool

echo -e "\n== Attempt cross-tenant read (should 404 -- tenant isolation) =="
curl -s -o /dev/null -w "HTTP %{http_code}\n" "$BASE_URL/documents/$DOC_ID" -H "X-Tenant-ID: tenant-globex"

echo -e "\n== Delete document =="
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X DELETE "$BASE_URL/documents/$DOC_ID" -H "X-Tenant-ID: $TENANT"

echo -e "\nDone."
