"""Async SQLAlchemy session management.

These run against a real Postgres because the behaviour under test *is*
Postgres behaviour: that a commit persists, that a rollback discards, and
that the FastAPI dependency does the right one at the right time.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from webhook_service.db.session import create_engine, create_session_factory

pytestmark = pytest.mark.integration


async def test_engine_connects_and_executes(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_session_executes_queries(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT 42"))
    assert result.scalar_one() == 42


async def test_create_engine_rejects_sync_driver() -> None:
    """A sync driver URL silently blocks the event loop under load; refuse it
    at construction time rather than discovering it in production."""
    with pytest.raises(ValueError, match="async"):
        create_engine("postgresql://user:pass@localhost:5432/db")


async def test_session_factory_does_not_expire_on_commit(
    db_connection: AsyncConnection,
) -> None:
    """expire_on_commit=False — otherwise every attribute access after a
    commit triggers a lazy refresh, which raises in async contexts and
    quietly costs a round-trip per attribute when it doesn't."""
    factory = create_session_factory(bind=db_connection)
    assert factory.kw["expire_on_commit"] is False


async def test_get_session_commits_on_success(db_engine: AsyncEngine) -> None:
    """The dependency commits when the handler returns normally, so route
    code never has to remember to."""
    from webhook_service.db.session import get_session

    async with db_engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS _commit_probe (id int primary key)"))

    try:
        agen = get_session(engine=db_engine)
        session = await agen.__anext__()
        await session.execute(text("INSERT INTO _commit_probe (id) VALUES (1)"))
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()

        async with db_engine.connect() as conn:
            count = (await conn.execute(text("SELECT count(*) FROM _commit_probe"))).scalar_one()
        assert count == 1
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS _commit_probe"))


async def test_get_session_rolls_back_on_exception(db_engine: AsyncEngine) -> None:
    """A handler that raises must not leave a partial write behind."""
    from webhook_service.db.session import get_session

    async with db_engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS _rollback_probe (id int primary key)"))

    try:
        agen = get_session(engine=db_engine)
        session = await agen.__anext__()
        await session.execute(text("INSERT INTO _rollback_probe (id) VALUES (1)"))

        with pytest.raises(RuntimeError, match="handler blew up"):
            await agen.athrow(RuntimeError("handler blew up"))

        async with db_engine.connect() as conn:
            count = (await conn.execute(text("SELECT count(*) FROM _rollback_probe"))).scalar_one()
        assert count == 0
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS _rollback_probe"))


async def test_rollback_isolation_between_tests_part_one(db_session: AsyncSession) -> None:
    """Paired with part two: proves the fixture's rollback actually isolates.

    If it didn't, part two would see this row and fail — which is the only
    way to be sure the isolation mechanism works rather than assuming it.
    """
    await db_session.execute(text("CREATE TABLE IF NOT EXISTS _isolation_probe (id int)"))
    await db_session.execute(text("INSERT INTO _isolation_probe (id) VALUES (1)"))
    await db_session.commit()

    count = (await db_session.execute(text("SELECT count(*) FROM _isolation_probe"))).scalar_one()
    assert count == 1


async def test_rollback_isolation_between_tests_part_two(db_session: AsyncSession) -> None:
    exists = (
        await db_session.execute(text("SELECT to_regclass('_isolation_probe') IS NOT NULL"))
    ).scalar_one()
    assert exists is False, "previous test's write leaked; fixture isolation is broken"
