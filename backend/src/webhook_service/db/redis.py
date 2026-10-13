"""Redis client lifecycle.

Redis holds only ephemeral state — circuit-breaker status, rate-limit
counters, the idempotency cache. Nothing whose loss corrupts the system
lives here; a Redis flush degrades behaviour briefly and never permanently.
"""

from redis.asyncio import ConnectionPool, Redis

from webhook_service.core.config import settings

_pool: ConnectionPool | None = None
_client: Redis | None = None


def init_redis(url: str | None = None) -> Redis:
    global _pool, _client
    if _client is None:
        _pool = ConnectionPool.from_url(
            url or settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            health_check_interval=30,
        )
        _client = Redis(connection_pool=_pool)
    return _client


def get_redis() -> Redis:
    if _client is None:
        return init_redis()
    return _client


async def close_redis() -> None:
    global _pool, _client
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None
