"""Shared pytest fixtures.

Infrastructure-backed tests run against *real* Postgres, Redis, and Kafka via
testcontainers rather than mocks. Transaction semantics, constraint
violations, and consumer rebalancing are exactly where a webhook delivery
system breaks, and a mock only ever confirms what we already believed about
those systems. Mocks are reserved for the outbound HTTP boundary and for
injecting clock/randomness.

Containers are session-scoped (starting Postgres per-test would make the
suite unusable); isolation between tests comes from per-test transaction
rollback instead — see the `db_session` fixture.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

# Guard the import so the fast suite (`pytest -m "not integration"`) still
# collects on a machine without Docker installed at all.
try:
    from testcontainers.community.kafka import KafkaContainer
    from testcontainers.community.postgres import PostgresContainer
    from testcontainers.community.redis import RedisContainer

    TESTCONTAINERS_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    TESTCONTAINERS_AVAILABLE = False


def _docker_available() -> bool:
    """Whether a usable Docker daemon is reachable.

    Integration tests skip rather than fail when Docker is missing, so the
    fast suite stays runnable on any laptop. CI runs with Docker present and
    sets WEBHOOK_REQUIRE_DOCKER=1 so a daemon outage there is a hard failure
    instead of silently skipping the tests that matter most.
    """
    if not TESTCONTAINERS_AVAILABLE:
        return False
    try:
        import docker

        docker.from_env().ping()
    except Exception:  # pragma: no cover - depends on the environment
        return False
    return True


def _skip_or_fail_without_docker() -> None:
    import os

    if os.environ.get("WEBHOOK_REQUIRE_DOCKER") == "1":  # pragma: no cover
        raise RuntimeError(
            "WEBHOOK_REQUIRE_DOCKER=1 but no Docker daemon is reachable; "
            "integration tests cannot be silently skipped in CI."
        )
    pytest.skip("Docker is not available; skipping infrastructure-backed test.")


# --------------------------------------------------------------------------
# Containers (session-scoped)
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container() -> Iterator["PostgresContainer"]:
    if not _docker_available():
        _skip_or_fail_without_docker()
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Iterator["RedisContainer"]:
    if not _docker_available():
        _skip_or_fail_without_docker()
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def kafka_container() -> Iterator["KafkaContainer"]:
    if not _docker_available():
        _skip_or_fail_without_docker()
    with KafkaContainer("confluentinc/cp-kafka:7.7.1") as container:
        yield container


# --------------------------------------------------------------------------
# Connection URLs
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def database_url(postgres_container: "PostgresContainer") -> str:
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
def redis_url(redis_container: "RedisContainer") -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def kafka_bootstrap_servers(kafka_container: "KafkaContainer") -> str:
    return str(kafka_container.get_bootstrap_server())


# --------------------------------------------------------------------------
# Database engine / session
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def db_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """Session-scoped engine with the full schema created once."""
    from webhook_service.db.base import Base
    from webhook_service.db.session import create_engine

    engine = create_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_connection(db_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection wrapped in a transaction that is always rolled back.

    This is what isolates tests from each other without paying to recreate
    the schema (or the container) per test: every test runs inside an outer
    transaction, and whatever it wrote disappears on rollback.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


@pytest.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """An AsyncSession bound to the rolled-back outer transaction.

    Application code under test may commit freely; those commits resolve to
    the nested transaction and are still undone when the outer one rolls back.
    """
    from webhook_service.db.session import create_session_factory

    factory = create_session_factory(bind=db_connection)
    async with factory() as session:
        yield session
