"""
Multi-tenancy resolution.

Approach: header-based tenant identification via `X-Tenant-ID`.

Why header-based over path-based (e.g. /tenants/{id}/documents):
- Keeps URL structure clean and RESTful around the resource (documents),
  not the tenant.
- Matches how most API gateways / auth proxies inject tenant context today
  (e.g. derived from a validated JWT claim and re-injected as a header by
  an upstream gateway) -- this mirrors a realistic production topology.
- Trivial to swap the *source* of tenant_id (header -> JWT claim -> mTLS
  client cert CN) without changing route signatures.

For /search, tenant is also accepted as a query param (per the spec's
`GET /search?q={query}&tenant={tenantId}`) as an alternative to the header,
with header taking precedence if both are supplied.
"""
from fastapi import Header, HTTPException, Query


async def get_tenant_id(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    tenant: str | None = Query(default=None, description="Alternative to X-Tenant-ID header"),
) -> str:
    tenant_id = x_tenant_id or tenant
    if not tenant_id or not tenant_id.strip():
        raise HTTPException(
            status_code=400,
            detail="Missing tenant identifier. Provide 'X-Tenant-ID' header or 'tenant' query param.",
        )
    tenant_id = tenant_id.strip()
    if len(tenant_id) > 128:
        raise HTTPException(status_code=400, detail="tenant_id exceeds maximum length")
    return tenant_id
