"""Health check logic.

A readiness probe that always returns "ok" is worse than having none: it
tells Kubernetes to route traffic into a pod that cannot serve it. These
tests pin the behaviour that makes /readyz meaningful.

Dependency clients are stubbed here because what's under test is the
*aggregation* logic — timeout handling, failure isolation, status mapping.
The real-infrastructure counterpart lives in test_health_integration.py.
"""

import asyncio

import pytest

from webhook_service.core.health import (
    DependencyStatus,
    HealthReport,
    check_dependency,
    run_health_checks,
)


async def _ok() -> None:
    return None


async def _fails() -> None:
    raise ConnectionError("connection refused")


async def _hangs() -> None:
    await asyncio.sleep(30)


async def test_healthy_dependency_reports_up() -> None:
    result = await check_dependency("postgres", _ok, timeout=1.0)
    assert result.name == "postgres"
    assert result.status is DependencyStatus.UP
    assert result.error is None
    assert result.latency_ms >= 0


async def test_failing_dependency_reports_down_with_reason() -> None:
    result = await check_dependency("redis", _fails, timeout=1.0)
    assert result.status is DependencyStatus.DOWN
    assert "connection refused" in (result.error or "")


async def test_hanging_dependency_times_out_rather_than_blocking() -> None:
    """A hung dependency must not hold the probe open until k8s kills the pod
    for being unresponsive — that turns 'not ready' into 'restart loop'."""
    started = asyncio.get_running_loop().time()
    result = await check_dependency("kafka", _hangs, timeout=0.1)
    elapsed = asyncio.get_running_loop().time() - started

    assert result.status is DependencyStatus.DOWN
    assert "timeout" in (result.error or "").lower()
    assert elapsed < 5, "check did not abandon the hung dependency promptly"


async def test_checks_run_concurrently_not_serially() -> None:
    """Three 200ms checks should take ~200ms, not ~600ms. Serial checks make
    the probe's worst case the sum of every dependency's timeout."""

    async def slow() -> None:
        await asyncio.sleep(0.2)

    started = asyncio.get_running_loop().time()
    report = await run_health_checks({"a": slow, "b": slow, "c": slow}, timeout=2.0)
    elapsed = asyncio.get_running_loop().time() - started

    assert report.ready is True
    assert elapsed < 0.5, f"checks appear to run serially ({elapsed:.2f}s)"


async def test_report_not_ready_when_any_dependency_is_down() -> None:
    report = await run_health_checks({"postgres": _ok, "redis": _fails}, timeout=1.0)

    assert report.ready is False
    assert report.dependencies["postgres"].status is DependencyStatus.UP
    assert report.dependencies["redis"].status is DependencyStatus.DOWN


async def test_report_ready_when_all_dependencies_are_up() -> None:
    report = await run_health_checks({"postgres": _ok, "redis": _ok}, timeout=1.0)
    assert report.ready is True


async def test_one_failure_does_not_mask_other_results() -> None:
    """Every dependency is reported, so the on-call engineer sees which one
    broke instead of only the first exception."""
    report = await run_health_checks(
        {"postgres": _ok, "redis": _fails, "kafka": _fails}, timeout=1.0
    )
    assert set(report.dependencies) == {"postgres", "redis", "kafka"}
    assert report.ready is False


async def test_empty_checks_is_ready() -> None:
    report = await run_health_checks({}, timeout=1.0)
    assert report.ready is True


def test_report_serialises_for_the_probe_response() -> None:
    report = HealthReport(
        ready=False,
        dependencies={
            "postgres": DependencyStatus.UP.result("postgres", 1.5),
            "redis": DependencyStatus.DOWN.result("redis", 0.2, "refused"),
        },
    )
    payload = report.to_dict()

    assert payload["status"] == "not_ready"
    assert payload["dependencies"]["postgres"]["status"] == "up"
    assert payload["dependencies"]["redis"]["error"] == "refused"


# --- Endpoint behaviour ---------------------------------------------------


async def test_liveness_never_touches_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness answers 'is the process alive'. Wiring dependency checks into
    it means a database blip restarts every pod — turning a partial outage
    into a total one."""
    from httpx import ASGITransport, AsyncClient

    from webhook_service.main import app

    called = False

    async def _tracked() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        "webhook_service.api.v1.endpoints.health.dependency_checks",
        lambda: {"postgres": _tracked},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert called is False, "liveness probe must not check dependencies"


async def test_readiness_returns_503_when_a_dependency_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """503 is what removes the pod from the load balancer; a 200 with a sad
    body in it would keep traffic flowing to a pod that can't serve."""
    from httpx import ASGITransport, AsyncClient

    from webhook_service.main import app

    monkeypatch.setattr(
        "webhook_service.api.v1.endpoints.health.dependency_checks",
        lambda: {"postgres": _ok, "redis": _fails},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["dependencies"]["redis"]["status"] == "down"


async def test_readiness_returns_200_when_all_dependencies_are_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from httpx import ASGITransport, AsyncClient

    from webhook_service.main import app

    monkeypatch.setattr(
        "webhook_service.api.v1.endpoints.health.dependency_checks",
        lambda: {"postgres": _ok, "redis": _ok},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
