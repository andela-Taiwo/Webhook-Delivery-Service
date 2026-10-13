# Kubernetes deployment

## Before applying

Create the secret out-of-band (never commit real credentials):

```
kubectl create secret generic webhook-backend-secrets \
  --namespace webhook-service \
  --from-literal=WEBHOOK_DATABASE_URL=postgresql+asyncpg://... \
  --from-literal=WEBHOOK_REDIS_URL=redis://... \
  --from-literal=WEBHOOK_KAFKA_BOOTSTRAP_SERVERS=... \
  --from-literal=WEBHOOK_SIGNING_SECRET_ENCRYPTION_KEY="$(openssl rand -hex 32)" \
  --from-literal=WEBHOOK_API_KEY_PEPPER="$(openssl rand -hex 32)"
```

In a real environment, replace this with External Secrets Operator or Sealed
Secrets so the secret is generated from your vault, not typed by hand.

### Required keys

The container image contains **no secrets**; every value below is injected at
runtime via `secretRef`. In `staging` and `production` the application
validates these at startup and **refuses to boot** if any is missing, shorter
than 32 characters, or still holding a development default (`localhost`,
`webhook:webhook`, `changeme`, …). A crash-looping pod blocks the rollout,
which is the intended outcome — a service that starts without a signing key
would deliver unsigned webhooks and appear healthy.

| Key | Purpose | Rotation |
| --- | --- | --- |
| `WEBHOOK_DATABASE_URL` | Postgres DSN incl. credentials | With DB credential rotation |
| `WEBHOOK_REDIS_URL` | Redis DSN | With Redis credential rotation |
| `WEBHOOK_KAFKA_BOOTSTRAP_SERVERS` | Broker list (plus SASL vars when enabled) | On broker/credential change |
| `WEBHOOK_SIGNING_SECRET_ENCRYPTION_KEY` | Envelope-encrypts subscription signing secrets at rest, so a database dump alone cannot forge webhooks | Requires re-encrypting stored secrets — plan a migration |
| `WEBHOOK_API_KEY_PEPPER` | Mixed into API key hashes; held outside the database so a stolen dump can't be attacked offline | Invalidates all existing API keys — coordinate with tenants |

Both generated keys must be at least 32 characters: `openssl rand -hex 32`.

## Apply

```
kubectl apply -k k8s/base
```

## Notes

- `deployment.yaml` sets `readOnlyRootFilesystem: true`. If a later component
  needs to write to disk (e.g. a local cache), mount an `emptyDir` at that
  path rather than loosening this.
- `configmap.yaml` points `WEBHOOK_OTEL_EXPORTER_OTLP_ENDPOINT` at an
  `otel-collector` service in an `observability` namespace — deploy the
  collector/Jaeger/Prometheus/Grafana stack there (the `docker-compose.yml`
  config files under `deploy/observability/` are a starting point) or adjust
  the endpoint.
- `overlays/` (dev/staging/production) aren't created yet — add them when
  environments actually diverge (replica counts, resource limits, image
  tags) rather than upfront.
