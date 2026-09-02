"""Shared FastAPI dependencies."""

from webhook_service.core.context import get_tenant_id
from webhook_service.core.exceptions import TenantRequiredError


def require_tenant() -> str:
    """Dependency for tenant-scoped routes.

    Resolves the tenant set by `RequestContextMiddleware` (from the
    `X-Tenant-ID` header today; swap in JWT-claim or API-key-derived
    resolution here later without touching route handlers).
    """
    tenant_id = get_tenant_id()
    if not tenant_id:
        raise TenantRequiredError("A tenant identifier is required for this request.")
    return tenant_id
