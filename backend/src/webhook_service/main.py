"""FastAPI application factory and ASGI entrypoint.

Run locally with: uvicorn webhook_service.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from webhook_service.api.v1.endpoints import health
from webhook_service.api.v1.router import api_router
from webhook_service.core.config import settings
from webhook_service.core.exceptions import register_exception_handlers
from webhook_service.core.logging import configure_logging, get_logger
from webhook_service.core.telemetry import configure_tracing, instrument_app
from webhook_service.db.redis import close_redis, init_redis
from webhook_service.db.session import dispose_engine, init_engine
from webhook_service.middleware.request_context import RequestContextMiddleware

configure_logging()
configure_tracing()

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Own the process-wide client lifecycle.

    Pools are created once at startup and disposed on shutdown. Disposal
    matters on rolling deploys: it returns connections cleanly instead of
    letting Postgres and Redis discover severed sockets on their own.
    """
    logger.info("service_starting", environment=settings.environment)
    init_engine()
    init_redis()
    try:
        yield
    finally:
        logger.info("service_stopping")
        await dispose_engine()
        await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    # Health checks live at the root, not under /api/v1, so probes and load
    # balancers don't need to know about API versioning — and so /healthz
    # never moves when the API version does.
    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    if settings.metrics_enabled:
        Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    instrument_app(app)

    return app


app = create_app()
