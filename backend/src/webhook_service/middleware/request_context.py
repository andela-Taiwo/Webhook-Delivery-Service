"""Per-request middleware: correlation ID, tenant resolution, access log.

Order matters — this should be the outermost middleware so request_id and
tenant_id are set before any other code (including OTel instrumentation
and route handlers) runs.
"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from webhook_service.core.config import settings
from webhook_service.core.context import request_id_ctx, tenant_id_ctx
from webhook_service.core.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Routes that don't require a tenant to be resolved.
PUBLIC_PATHS = {"/healthz", "/readyz", "/livez", "/metrics", "/docs", "/openapi.json"}


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER, str(uuid.uuid4()))
        request_id_ctx.set(request_id)

        tenant_id = request.headers.get(settings.tenant_header)
        tenant_id_ctx.set(tenant_id)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        response.headers[REQUEST_ID_HEADER] = request_id

        if request.url.path not in PUBLIC_PATHS:
            logger.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )

        return response
