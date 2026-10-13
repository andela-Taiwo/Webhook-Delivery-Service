"""Readiness against real Postgres, Redis, and Kafka.

The unit tests stub the clients to pin aggregation logic. These prove the
actual probe functions work against the real engines — that the SELECT, the
PING, and the metadata fetch are correct calls, and that a genuinely
unreachable dependency is reported as down rather than raising.
"""

import pytest

from webhook_service.core.health import DependencyStatus, check_dependency, run_health_checks

pytestmark = pytest.mark.integration


async def test_postgres_check_passes_against_real_database(database_url: str) -> None:
    from webhook_service.api.v1.endpoints.health import _check_postgres
    from webhook_service.db import session as session_module

    # init_engine is idempotent, so a singleton left over from another test
    # would silently keep pointing at the default URL instead of the container.
    await session_module.dispose_engine()
    session_module.init_engine(database_url)
    try:
        result = await check_dependency("postgres", _check_postgres, timeout=5.0)
        assert result.status is DependencyStatus.UP, result.error
    finally:
        await session_module.dispose_engine()


async def test_redis_check_passes_against_real_redis(redis_url: str) -> None:
    from webhook_service.api.v1.endpoints.health import _check_redis
    from webhook_service.db import redis as redis_module

    await redis_module.close_redis()
    redis_module.init_redis(redis_url)
    try:
        result = await check_dependency("redis", _check_redis, timeout=5.0)
        assert result.status is DependencyStatus.UP, result.error
    finally:
        await redis_module.close_redis()


async def test_kafka_check_passes_against_real_broker(
    kafka_bootstrap_servers: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from webhook_service.api.v1.endpoints.health import _check_kafka
    from webhook_service.core.config import settings

    monkeypatch.setattr(settings, "kafka_bootstrap_servers", kafka_bootstrap_servers)
    monkeypatch.setattr(settings, "kafka_request_timeout_ms", 10000)

    result = await check_dependency("kafka", _check_kafka, timeout=30.0)
    assert result.status is DependencyStatus.UP, result.error


async def test_unreachable_postgres_reports_down_not_raises() -> None:
    """An unreachable dependency must degrade the probe, never 500 it."""
    from webhook_service.db import session as session_module

    async def check_dead_postgres() -> None:
        from sqlalchemy import text

        engine = session_module.create_engine(
            # Port 1 is reserved and never listening.
            "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nothing"
        )
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    result = await check_dependency("postgres", check_dead_postgres, timeout=5.0)
    assert result.status is DependencyStatus.DOWN
    assert result.error


async def test_full_report_ready_with_all_real_dependencies(
    database_url: str, redis_url: str, kafka_bootstrap_servers: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from webhook_service.api.v1.endpoints.health import dependency_checks
    from webhook_service.core.config import settings
    from webhook_service.db import redis as redis_module
    from webhook_service.db import session as session_module

    await session_module.dispose_engine()
    await redis_module.close_redis()
    session_module.init_engine(database_url)
    redis_module.init_redis(redis_url)
    monkeypatch.setattr(settings, "kafka_bootstrap_servers", kafka_bootstrap_servers)
    monkeypatch.setattr(settings, "kafka_request_timeout_ms", 10000)

    try:
        report = await run_health_checks(dependency_checks(), timeout=30.0)
        assert report.ready is True, report.to_dict()
        assert set(report.dependencies) == {"postgres", "redis", "kafka"}
    finally:
        await session_module.dispose_engine()
        await redis_module.close_redis()
