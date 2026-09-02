"""Model registry.

Every model module must be imported here. Alembic autogenerate works off
`Base.metadata`, which is only populated by importing the modules that define
the tables — a model that isn't imported is a model autogenerate will propose
dropping.

Tables land here from Phase 1 onward (tenants, api_keys, subscriptions,
events, outbox, deliveries, delivery_attempts).
"""

__all__: list[str] = []
