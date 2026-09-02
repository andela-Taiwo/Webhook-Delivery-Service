# Webhook delivery service

Multi-tenant webhook delivery platform: event producers -> Kafka -> dispatcher
-> delivery workers -> subscriber endpoints, with retries, circuit breaking,
and a dead-letter queue for exhausted deliveries.

## Status

See [PLAN.md](PLAN.md) for the architecture and the phased build order.

**Phase 0 (foundations) complete.** FastAPI app with structured logging,
OpenTelemetry tracing, Prometheus metrics, and tenant-context middleware;
async SQLAlchemy session management; Alembic migrations with model-drift
detection; testcontainers-backed integration tests against real
Postgres/Redis/Kafka; a readiness probe that actually checks its
dependencies; fail-fast secret validation for deployed environments; and a
CI pipeline gating lint, types, tests, secret scanning, and image scanning.

**Next: Phase 1** — tenants and API-key authentication. Then the subscription
registry, event ingestion, dispatcher, delivery workers, and the
React/TypeScript dashboard.

## Repo layout

```
backend/    FastAPI service (see backend/README.md)
frontend/   React/TypeScript dashboard — not started yet
k8s/base/   Kubernetes manifests (kustomize)
deploy/     Local observability stack config (OTel Collector, Prometheus, Grafana)
docker-compose.yml   Full local stack: backend + Postgres + Redis + Kafka + observability
```

## Quickstart

```
docker compose up --build
```

Migrations run automatically (a one-shot `migrate` service) before the backend
starts — the same order a real deployment uses, and keeping it separate stops
N replicas racing to migrate the same database.

- API: http://localhost:8000 (`/healthz`, `/readyz`, `/metrics`, `/docs`)
- Jaeger UI: http://localhost:16686
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000

Host ports are overridable so the stack doesn't collide with services already
running on your machine (Postgres and Redis default to non-standard host ports
for exactly that reason):

```
BACKEND_HOST_PORT=18080 POSTGRES_HOST_PORT=55432 docker compose up
```

Or run just the backend without the full stack — see `backend/README.md`.
