"""Liveness / readiness probes.

Kept separate from `/metrics` and excluded from tracing + access logs (see
`middleware/request_context.py`) so k8s probe traffic doesn't flood the
observability backend.

The split between the two is deliberate and load-bearing:

- `/healthz` (liveness) answers "is this process alive?" and touches nothing
  external. If it checked dependencies, a database blip would restart every
  pod simultaneously — converting a partial outage into a total one.
- `/readyz` (readiness) answers "can this pod serve traffic right now?" and
  does check dependencies, returning 503 so the pod leaves the load-balancer
  pool without being killed.
"""

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from webhook_service.core.config import settings
from webhook_service.core.health import HealthCheck, run_health_checks

router = APIRouter(tags=["health"])


async def _check_postgres() -> None:
    from webhook_service.db.session import get_engine

    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    from webhook_service.db.redis import get_redis

    await get_redis().ping()


async def _check_kafka() -> None:
    """Fetch cluster metadata — proves the broker is reachable and responding,
    not merely that a TCP connection can be opened."""
    from aiokafka.admin import AIOKafkaAdminClient

    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        request_timeout_ms=settings.kafka_request_timeout_ms,
    )
    try:
        await admin.start()
        await admin.list_topics()
    finally:
        await admin.close()


def dependency_checks() -> dict[str, HealthCheck]:
    """The dependencies readiness verifies.

    A function rather than a module constant so tests can substitute it and
    so the set can become configurable per deployment later.
    """
    return {
        "postgres": _check_postgres,
        "redis": _check_redis,
        "kafka": _check_kafka,
    }


@router.get("/healthz", summary="Liveness probe")
async def liveness() -> dict[str, str]:
    """Is the process up? No dependency checks by design — see module docstring."""
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe")
async def readiness(response: Response) -> dict[str, Any]:
    """Can the service handle traffic? Checks Postgres, Redis, and Kafka."""
    report = await run_health_checks(
        dependency_checks(),
        timeout=settings.health_check_timeout_seconds,
    )
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report.to_dict()
