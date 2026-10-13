"""Alembic migration tests.

Running migrations against a real, empty Postgres is the only way to know
they actually apply. A migration that was never run from scratch is a
migration that works only on the developer's machine where the schema
already existed.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from webhook_service.db.migrations import (
    alembic_config_for_url,
    downgrade_to_base,
    upgrade_to_head,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def clean_db(db_engine: AsyncEngine) -> AsyncEngine:
    """Drop everything so migrations run against a genuinely empty database.

    The `db_engine` fixture created the schema via `create_all`; migrations
    must be tested from nothing, not from that.
    """
    async with db_engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    return db_engine


async def test_alembic_config_reads_url_from_settings_not_a_literal() -> None:
    """The DB URL — which carries credentials — must come from config, never
    from a value committed in alembic.ini."""
    config = alembic_config_for_url("postgresql+asyncpg://u:p@h:5432/d")
    assert config.get_main_option("sqlalchemy.url") == "postgresql+asyncpg://u:p@h:5432/d"


async def test_upgrade_to_head_on_empty_database(clean_db: AsyncEngine, database_url: str) -> None:
    """`alembic upgrade head` succeeds from an empty database."""
    await upgrade_to_head(database_url)

    async with clean_db.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert version, "alembic_version should record the applied head revision"


async def test_upgrade_is_idempotent(clean_db: AsyncEngine, database_url: str) -> None:
    """Re-running upgrade on an up-to-date database is a no-op, not an error.

    Deployments run migrations on every rollout; the second replica to start
    must not crash-loop because the first already migrated.
    """
    await upgrade_to_head(database_url)
    await upgrade_to_head(database_url)

    async with clean_db.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM alembic_version"))).scalar_one()
    assert count == 1


async def test_downgrade_to_base_reverses_migrations(
    clean_db: AsyncEngine, database_url: str
) -> None:
    """Every migration is reversible, so a bad deploy can be rolled back."""
    await upgrade_to_head(database_url)
    await downgrade_to_base(database_url)

    async with clean_db.connect() as conn:
        version = (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
    assert version is None


async def test_migrations_match_orm_models(clean_db: AsyncEngine, database_url: str) -> None:
    """No drift between migrations and models.

    If someone edits a model without generating a migration, autogenerate
    would produce a non-empty diff. Catching that here means production
    never meets a schema the models don't expect.
    """
    from webhook_service.db.migrations import detect_model_drift

    await upgrade_to_head(database_url)
    drift = await detect_model_drift(database_url)
    assert drift == [], f"models and migrations have diverged: {drift}"
