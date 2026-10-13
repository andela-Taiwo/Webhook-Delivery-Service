"""Dependency health checking for the readiness probe.

Checks are supplied as name -> zero-arg async callable, run concurrently,
and individually bounded by a timeout. Every dependency is reported even
when others fail, so a failing probe says *which* dependency broke rather
than just "not ready".
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from webhook_service.core.logging import get_logger

logger = get_logger(__name__)

HealthCheck = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DependencyResult:
    name: str
    status: "DependencyStatus"
    latency_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 2),
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


class DependencyStatus(Enum):
    UP = "up"
    DOWN = "down"

    def result(self, name: str, latency_ms: float, error: str | None = None) -> DependencyResult:
        return DependencyResult(name=name, status=self, latency_ms=latency_ms, error=error)


@dataclass(frozen=True, slots=True)
class HealthReport:
    ready: bool
    dependencies: dict[str, DependencyResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "dependencies": {name: r.to_dict() for name, r in self.dependencies.items()},
        }


async def check_dependency(name: str, check: HealthCheck, timeout: float) -> DependencyResult:
    """Run one check, converting any failure into a DOWN result.

    Every exception is caught deliberately: a health check that raises would
    turn a degraded dependency into a 500 from the probe itself, which is
    strictly less informative than a structured "down" answer.
    """
    started = time.perf_counter()
    try:
        await asyncio.wait_for(check(), timeout=timeout)
    except TimeoutError:
        elapsed = (time.perf_counter() - started) * 1000
        return DependencyStatus.DOWN.result(name, elapsed, f"timeout after {timeout}s")
    except Exception as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return DependencyStatus.DOWN.result(name, elapsed, f"{type(exc).__name__}: {exc}")

    elapsed = (time.perf_counter() - started) * 1000
    return DependencyStatus.UP.result(name, elapsed)


async def run_health_checks(checks: dict[str, HealthCheck], timeout: float) -> HealthReport:
    """Run all checks concurrently and aggregate them.

    Concurrent rather than serial: serially, the probe's worst case is the
    sum of every dependency's timeout, which can exceed the probe deadline
    and get the pod killed instead of merely marked unready.
    """
    if not checks:
        return HealthReport(ready=True, dependencies={})

    results = await asyncio.gather(
        *(check_dependency(name, check, timeout) for name, check in checks.items())
    )

    by_name = {result.name: result for result in results}
    ready = all(r.status is DependencyStatus.UP for r in results)

    if not ready:
        logger.warning(
            "readiness_check_failed",
            failed=[r.name for r in results if r.status is DependencyStatus.DOWN],
        )

    return HealthReport(ready=ready, dependencies=by_name)
