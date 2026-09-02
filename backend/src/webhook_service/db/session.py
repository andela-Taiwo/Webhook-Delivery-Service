"""Async engine, session factory, and the FastAPI session dependency.

One engine per process, created at startup and disposed at shutdown. The
engine owns a connection pool; creating engines per-request would open a new
pool each time and exhaust Postgres' connection limit quickly.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from webhook_service.core.config import settings

# Drivers that actually speak asyncio. A sync driver on an async engine
# blocks the event loop, which under load looks like unexplained latency
# across every unrelated request the worker is handling.
ASYNC_DRIVERS = ("asyncpg", "psycopg", "aiosqlite")


def create_engine(url: str | None = None, **kwargs: object) -> AsyncEngine:
    """Build the async engine.

    `pool_pre_ping` costs one cheap round-trip per checkout and buys immunity
    to connections killed underneath us by a Postgres failover or an idle
    timeout on a proxy — the alternative is a burst of errors after every
    infrastructure blip.
    """
    url = url or settings.database_url

    if "+" not in url.split("://", 1)[0] or not any(d in url for d in ASYNC_DRIVERS):
        raise ValueError(
            f"database_url must use an async driver (one of {', '.join(ASYNC_DRIVERS)}), "
            f"e.g. postgresql+asyncpg://... — got {url.split('://', 1)[0]}://"
        )

    options: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout_seconds,
        "pool_recycle": settings.db_pool_recycle_seconds,
        "echo": settings.db_echo,
    }
    options.update(kwargs)
    return create_async_engine(url, **options)


def create_session_factory(
    bind: AsyncEngine | AsyncConnection | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to an engine (production) or a connection (tests).

    `expire_on_commit=False` because the default expires every loaded
    attribute on commit, so the next attribute access issues a lazy refresh —
    which raises MissingGreenlet in async code, and costs a silent round-trip
    where it doesn't.
    """
    return async_sessionmaker(
        bind=bind if bind is not None else get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


# --- Process-wide singletons, initialised in the app lifespan -------------

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(url)
        _session_factory = create_session_factory(_engine)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        return init_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


async def dispose_engine() -> None:
    """Close the pool on shutdown so in-flight connections are returned
    cleanly instead of being severed when the process exits."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def get_session(engine: AsyncEngine | None = None) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session with commit/rollback handled here.

    Commit-on-success/rollback-on-error lives in one place rather than in
    every route handler, so a handler that forgets to commit is not a class
    of bug this codebase can have. Handlers that need finer control can still
    manage nested transactions explicitly.
    """
    factory = create_session_factory(engine) if engine is not None else get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
