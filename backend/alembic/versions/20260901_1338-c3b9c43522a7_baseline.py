"""baseline

Establishes the extensions the schema depends on, before any tables exist.
Domain tables (tenants, api_keys, subscriptions, events, outbox, deliveries,
delivery_attempts) arrive in later revisions.

`pgcrypto` provides `gen_random_uuid()`, used as the server-side default for
primary keys. Generating IDs in the database rather than the application
means rows inserted by a backfill script or by psql get valid keys too, and
keeps ID generation correct when several writers are involved.

Revision ID: c3b9c43522a7
Revises:
Create Date: 2026-09-01 13:38:09.273518

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3b9c43522a7"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    # Left in place on downgrade: other schemas in the same database may rely
    # on it, and dropping a shared extension to reverse our own migration is
    # a far worse outcome than leaving one unused extension behind.
    pass
