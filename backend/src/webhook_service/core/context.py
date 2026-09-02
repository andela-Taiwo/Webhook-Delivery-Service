"""Request-scoped context vars.

Populated by middleware early in the request lifecycle, then read by the
logging layer (to stamp every log line) and by anything downstream that
needs "which tenant/request am I handling" without threading it through
every function signature.
"""

from contextvars import ContextVar

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_ctx: ContextVar[str | None] = ContextVar("tenant_id", default=None)


def get_request_id() -> str | None:
    return request_id_ctx.get()


def get_tenant_id() -> str | None:
    return tenant_id_ctx.get()
