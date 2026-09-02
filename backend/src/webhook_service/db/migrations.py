"""Programmatic access to Alembic.

Wrapping Alembic here (rather than shelling out to the CLI) lets tests and
the application drive migrations with an explicit URL, and gives one place
to express the drift check that keeps models and migrations honest.
"""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from webhook_service.core.config import settings

# backend/src/webhook_service/db/migrations.py -> backend/
BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
ALEMBIC_DIR = BACKEND_ROOT / "alembic"


def alembic_config_for_url(url: str | None = None) -> Config:
    """Build an Alembic config pointed at `url`.

    The URL is injected here rather than read from alembic.ini so credentials
    stay out of committed files.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url or settings.database_url)
    return config


async def _run_with_connection(url: str, fn: object) -> None:
    """Run an Alembic command against a fresh connection.

    Alembic's command layer is synchronous, so it runs inside `run_sync` on
    an async connection that we own and dispose here.
    """
    engine = create_async_engine(url, poolclass=None)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(fn)  # type: ignore[arg-type]
    finally:
        await engine.dispose()


async def upgrade_to_head(url: str | None = None, revision: str = "head") -> None:
    """Apply migrations up to `revision`. Idempotent when already current."""
    target_url = url or settings.database_url

    def _upgrade(connection: Connection) -> None:
        config = alembic_config_for_url(target_url)
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    await _run_with_connection(target_url, _upgrade)


async def downgrade_to_base(url: str | None = None, revision: str = "base") -> None:
    """Reverse migrations down to `revision`."""
    target_url = url or settings.database_url

    def _downgrade(connection: Connection) -> None:
        config = alembic_config_for_url(target_url)
        config.attributes["connection"] = connection
        command.downgrade(config, revision)

    await _run_with_connection(target_url, _downgrade)


async def detect_model_drift(url: str | None = None) -> list[str]:
    """Diff the live schema against the ORM models.

    Returns human-readable descriptions of any difference. An empty list means
    migrations fully express the models. Used as a CI gate so a model edit
    without a matching migration fails the build instead of production.
    """
    target_url = url or settings.database_url

    # Importing the registry is what populates Base.metadata; the module is
    # imported for that side effect, not for a name.
    import webhook_service.db.models  # noqa: F401
    from webhook_service.db.base import Base

    def _compare(connection: Connection) -> list[str]:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        diffs = compare_metadata(context, Base.metadata)
        # Scratch tables created by tests aren't part of the model surface.
        return [str(d) for d in diffs if not (isinstance(d, tuple) and _is_scratch_table_diff(d))]

    engine = create_async_engine(target_url, poolclass=None)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_compare)
    finally:
        await engine.dispose()


def _is_scratch_table_diff(diff: tuple[object, ...]) -> bool:
    """True for diffs about test scratch tables (leading-underscore names)."""
    for element in diff:
        name = getattr(element, "name", None)
        if isinstance(name, str) and name.startswith("_"):
            return True
        if isinstance(element, str) and element.startswith("_"):
            return True
    return False
