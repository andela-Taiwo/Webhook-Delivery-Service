# Backend

Python 3.12 / FastAPI. Managed with [uv](https://docs.astral.sh/uv/).

## Layout

```
src/webhook_service/
  main.py            FastAPI app factory (uvicorn entrypoint)
  core/
    config.py         Settings (env vars, prefixed WEBHOOK_) + startup secret validation
    context.py         Request-scoped contextvars (request_id, tenant_id)
    logging.py          structlog -> JSON, trace-correlated
    telemetry.py        OpenTelemetry tracer + FastAPI instrumentation
    exceptions.py         AppError hierarchy + handlers
    health.py              Concurrent dependency checks behind /readyz
  middleware/
    request_context.py    Correlation ID, tenant resolution, access log
  api/
    deps.py                require_tenant() and friends
    v1/
      router.py             Aggregates endpoint routers
      endpoints/
        health.py             /healthz, /readyz
  db/
    base.py                Declarative Base, naming conventions, TimestampMixin
    session.py              Async engine, session factory, get_session dependency
    redis.py                 Redis client lifecycle
    migrations.py             Programmatic Alembic + model-drift detection
    models/                    Model registry (tables land here from Phase 1)
  schemas/     (empty — Pydantic I/O models)
  services/    (empty — dispatcher, signing, retry logic)
  kafka/       (empty — producer/consumer wrappers)
  workers/     (empty — standalone delivery worker entrypoint)
alembic/       Migration environment + versions/
tests/
```

Remaining empty package directories are placeholders for the next components
(event ingestion, dispatcher, delivery workers) — kept in the tree now so the
shape of the system is visible from day one.

## Database

```
make migrate                          # alembic upgrade head
make migration m="add subscriptions"  # autogenerate a revision
```

The database URL is never written into `alembic.ini`; `alembic/env.py` reads
it from `Settings` at runtime, so credentials stay out of committed files.
`tests/test_migrations.py` runs migrations from an empty database on every
CI run and fails the build if the models and migrations have drifted apart.

## Run locally

```
uv sync --group dev
uv run uvicorn webhook_service.main:app --reload
```

Copy `.env.example` to `.env` first if you want to override defaults
(`WEBHOOK_LOG_JSON=false` gives readable console logs instead of JSON).

## Test / lint

```
make test        # full suite with coverage
make test-fast   # skips Docker-backed tests (-m "not integration")
make check       # everything CI runs: lint, types, tests, secret scan
```

Tests marked `integration` run against **real** Postgres, Redis, and Kafka
started by [testcontainers](https://testcontainers.com/) — transaction
semantics and consumer rebalancing are where webhook systems actually break,
and a mock only confirms what we already assumed. They skip automatically if
Docker isn't running; CI sets `WEBHOOK_REQUIRE_DOCKER=1` so a daemon outage
there fails the build instead of silently skipping them.

Isolation comes from per-test transaction rollback (see `tests/conftest.py`)
rather than recreating the schema, so the suite stays fast.

## Secrets

No secret is ever committed or baked into an image. Values come from the
environment at runtime — in Kubernetes, from the Secret referenced by
`secretRef` (see `k8s/README.md` for the required keys).

In `staging` and `production` the app **refuses to start** if a required
secret is missing, shorter than 32 characters, or still set to a development
default such as `localhost` or `webhook:webhook`. A crash-looping pod blocks
the rollout, which is the point: a service that boots without a signing key
would deliver unsigned webhooks and look perfectly healthy doing it. Locally,
no secrets are needed at all.

## Health probes

- `/healthz` — liveness. Touches nothing external; if it checked dependencies,
  a database blip would restart every pod and turn a partial outage total.
- `/readyz` — readiness. Checks Postgres, Redis, and Kafka concurrently with a
  timeout, and returns 503 plus per-dependency detail when any is down, so the
  pod leaves the load-balancer pool without being killed.

Both are mounted at the root rather than under `/api/v1`, so probes don't have
to be reconfigured when the API version changes.

## Multi-tenancy

Every request is expected to carry an `X-Tenant-ID` header. The
`RequestContextMiddleware` resolves it into a contextvar; route handlers
that need a tenant declare `tenant_id: str = Depends(require_tenant)` and
get a 400 automatically if it's missing. This header-based resolution is a
placeholder — swap in JWT-claim or API-key-derived tenant resolution in
`api/deps.py` once auth exists, without touching route handlers.

## Observability

- Logs: structured JSON via structlog, one line per event, stamped with
  `request_id`, `tenant_id`, and — inside a span — `trace_id`/`span_id` for
  log-to-trace pivoting.
- Traces: OpenTelemetry, exported via OTLP/gRPC to whatever collector
  `WEBHOOK_OTEL_EXPORTER_OTLP_ENDPOINT` points at.
- Metrics: Prometheus format at `/metrics` (RED metrics — rate, errors,
  duration — via `prometheus-fastapi-instrumentator`).

Run `docker compose up` from the repo root to get Postgres, Redis, Kafka,
an OTel Collector, Jaeger, Prometheus, and Grafana alongside the backend.
